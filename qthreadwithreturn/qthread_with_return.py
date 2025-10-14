"""PySide6 高级线程工具库 - 带返回值的线程和线程池执行器。

本模块提供了两个核心类，用于简化 PySide6/Qt 应用中的多线程编程：

1. QThreadWithReturn: 带返回值的线程类
   - 支持获取线程执行结果
   - 支持灵活的回调机制（无参数、单参数、多参数）
   - 支持超时控制和取消操作
   - 自动处理 Qt 事件循环
   - 线程安全的资源管理

2. QThreadPoolExecutor: 线程池执行器
   - API 兼容 concurrent.futures.ThreadPoolExecutor
   - 支持 submit、as_completed 等标准接口
   - 自动管理线程生命周期
   - 支持线程初始化器和命名

基本用法示例:
    # 单线程执行
    thread = QThreadWithReturn(lambda x: x * 2, 5)
    # 通常情况下这个 callback 连接到需要更新 UI 的槽函数
    thread.add_done_callback(lambda result: print(f"结果: {result}"))
    thread.start()
    result = thread.result()  # 返回 10

    # 线程池执行
    pool = QThreadPoolExecutor(max_workers=4)
    pool.add_done_callback(lambda: print("所有任务完成"))
    futures = [pool.submit(time.sleep, 1) for _ in range(4)]
    for future in futures:
        future.add_done_callback(lambda: print("任务完成"))
    # 任务完成后自动触发回调，shutdown() 是可选的
"""

import contextlib
import inspect
import sys
import threading
import time
from concurrent.futures import CancelledError, TimeoutError
from typing import Callable, Any, Optional

from PySide6.QtCore import QThread, QObject, Signal, QTimer, QMutex, QWaitCondition


class QThreadWithReturn(QObject):
    """带返回值的 Qt 线程类。

    提供类似 concurrent.futures.Future 的 API，支持在 Qt 线程中
    执行函数并获取返回值。

    主要特性:
        - 支持获取线程执行结果
        - 灵活的回调机制（支持无参数、单参数、多参数）
        - 支持超时控制
        - 支持优雅取消和强制终止
        - 自动处理 Qt 事件循环
        - 线程安全的状态管理

    使用示例:
        >>> # 简单使用
        >>> thread = QThreadWithReturn(lambda: "Hello")
        >>> thread.start()
        >>> print(thread.result())  # 输出: Hello
        ...
        >>> # 带参数和回调
        >>> def task(x, y):
        ...     return x + y
        >>> thread = QThreadWithReturn(task, 3, y=4)
        >>> thread.add_done_callback(lambda r: print(f"Result: {r}"))
        >>> thread.start()
        ...
        >>> # 多返回值自动解包
        >>> def multi_return():
        ...     return 1, 2, 3
        >>> thread = QThreadWithReturn(multi_return)
        >>> thread.add_done_callback(lambda a, b, c: print(f"{a}, {b}, {c}"))
        >>> thread.start()

    Signals:
        finished_signal: 任务完成时发射（不论成功或失败）。
        result_ready_signal(object): 任务成功完成时发射，携带结果。
    """

    # 新增信号：任务完成和有结果
    finished_signal = Signal()
    result_ready_signal = Signal(object)

    def __init__(
            self,
            func: Callable,
            *args,
            initializer: Optional[Callable] = None,
            initargs: tuple = (),
            thread_name: Optional[str] = None,
            **kwargs,
    ):
        super().__init__()
        self._func: Callable = func
        self._args: tuple = args
        self._kwargs: dict = kwargs
        self._initializer: Optional[Callable] = initializer
        self._initargs: tuple = initargs
        self._thread_name: Optional[str] = thread_name

        # 线程相关
        self._thread: Optional[QThread] = None
        self._worker: Optional["_Worker"] = None

        # 回调函数 - 支持多个回调（与标准库行为一致）
        self._done_callbacks: list = []  # [(callback, param_count), ...]
        self._failure_callbacks: list = []  # [(callback, param_count), ...]

        # 状态管理
        self._result: Any = None
        self._exception: Optional[Exception] = None
        self._is_cancelled: bool = False
        self._is_finished: bool = False
        self._is_force_stopped: bool = False
        self._thread_really_finished: bool = False  # 真正的线程完成状态

        # 超时管理
        self._timeout_timer: Optional[QTimer] = None
        self._timeout_ms: int = -1

        # 线程同步
        self._mutex: QMutex = QMutex()
        self._wait_condition: QWaitCondition = QWaitCondition()

        # 备用同步机制（用于无Qt应用时）
        self._completion_event = threading.Event()

        # 信号连接状态跟踪
        self._signals_connected: bool = False

        # SECURITY FIX: Cleanup re-entry protection
        self._cleanup_in_progress: bool = False
        self._cleanup_lock: threading.Lock = threading.Lock()

        # THREAD SAFETY FIX: Callback list protection
        self._callbacks_lock: threading.Lock = threading.Lock()

        # STACK OVERFLOW FIX: Prevent recursive _on_finished calls
        self._in_on_finished: bool = False
        self._in_on_error: bool = False

    def add_done_callback(self, callback: Callable) -> None:
        """添加任务成功完成后的回调函数。

        回调函数会在主线程中执行，支持以下几种形式：
        - 无参数: callback()
        - 单参数: callback(result)
        - 多参数: callback(a, b, c) - 当返回值是元组时自动解包

        可以多次调用此方法添加多个回调，它们会按添加顺序依次执行。

        Args:
            callback: 回调函数。参数数量会自动检测。

        Note:
            - 类方法的 self 参数会被自动忽略
            - 如果返回值是元组且回调有多个参数，会自动解包
            - 多个回调会按注册顺序依次执行（与标准库行为一致）

        Example:
            >>> thread.add_done_callback(lambda: print("Done!"))  # 无参数
            >>> thread.add_done_callback(lambda x: print(x))      # 单参数
            >>> thread.add_done_callback(lambda a, b: print(a+b)) # 多参数
            >>> # 可以添加多个回调
            >>> thread.add_done_callback(lambda x: print(f"First: {x}"))
            >>> thread.add_done_callback(lambda x: print(f"Second: {x}"))
        """
        param_count = self._validate_callback(callback, "done_callback")
        with self._callbacks_lock:
            self._done_callbacks.append((callback, param_count))

    def add_failure_callback(self, callback: Callable) -> None:
        """添加任务失败后的回调函数。

        回调函数会在主线程中执行，支持：
        - 无参数: callback()
        - 单参数: callback(exception)

        可以多次调用此方法添加多个回调，它们会按添加顺序依次执行。

        Args:
            callback: 回调函数。

        Note:
            - 失败回调只支持 0 或 1 个参数，因为异常对象只有一个
            - 多个回调会按注册顺序依次执行（与标准库行为一致）

        Example:
            >>> thread.add_failure_callback(lambda: print("Failed!"))
            >>> thread.add_failure_callback(lambda e: print(f"Error: {e}"))
            >>> # 可以添加多个回调
            >>> thread.add_failure_callback(lambda: print("Cleanup 1"))
            >>> thread.add_failure_callback(lambda e: print(f"Cleanup 2: {e}"))
        """
        param_count = self._validate_callback(callback, "failure_callback")
        with self._callbacks_lock:
            self._failure_callbacks.append((callback, param_count))

    add_exception_callback = add_failure_callback  # 别名

    def cancel(self, force_stop: bool = False) -> bool:
        """取消线程执行。

        Args:
            force_stop: 如果为 True，强制终止线程；否则尝试优雅退出。

        Returns:
            bool: 如果成功取消返回 True，如果线程已完成返回 False。

        Note:
            优雅取消需要线程内部检查 QThread.isInterruptionRequested()。
            强制终止可能导致资源泄漏，请谨慎使用。

        Example:
            >>> thread.cancel()  # 优雅取消
            >>> thread.cancel(force_stop=True)  # 强制终止
        """
        # 如果线程还没启动，直接标记为已取消和已完成
        if self._is_finished or self._is_force_stopped or self._thread_really_finished:
            return False

        if not self._thread:
            self._is_cancelled = True
            self._is_finished = True
            # Fix #2: Clear callbacks in early cancel path
            self._clear_callbacks()
            self.finished_signal.emit()
            return True

        if not self._thread.isRunning():
            self._is_cancelled = True
            self._is_finished = True
            self.finished_signal.emit()
            return True

        self._is_cancelled = True
        if self._worker:
            self._worker._should_stop = True

        # 请求线程中断
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            if force_stop:
                # 分阶段强制停止策略
                self._is_force_stopped = True

                # 第一阶段：请求中断（100ms）
                if self._thread.wait(100):
                    # 优雅退出成功
                    self._thread_really_finished = True
                    self._clear_callbacks()
                    self._cleanup_resources()
                    return True

                # 第二阶段：quit（200ms）
                with contextlib.suppress(AttributeError, RuntimeError):
                    self._thread.quit()
                    if self._thread.wait(200):
                        # 半优雅退出成功
                        self._thread_really_finished = True
                        self._clear_callbacks()
                        self._cleanup_resources()
                        return True

                # 第三阶段：terminate（最后手段）
                with contextlib.suppress(AttributeError, RuntimeError):
                    self._thread.terminate()
                    if not self._thread.wait(2000):
                        # 即使 wait 超时，也标记为已完成（线程可能已死锁）
                        print(
                            "Warning: Thread did not terminate after 2 seconds, marking as finished anyway",
                            file=sys.stderr
                        )
                    self._thread_really_finished = True
                # 清理资源
                self._clear_callbacks()
                self._cleanup_resources()

                # POOL CLEANUP FIX: Emit finished_signal so pool can remove future from active set
                # Without this, shutdown(wait=True) hangs forever waiting for force-stopped futures
                self.finished_signal.emit()
            else:
                # 优雅取消路径
                with contextlib.suppress(AttributeError):
                    self._thread.quit()
                    self._thread.wait(100)

        # 确保在取消时也清理资源
        if not force_stop:
            self._cleanup_resources()

        return True

    def start(self, timeout_ms: int = -1) -> None:
        """启动线程执行任务。

        Args:
            timeout_ms: 超时时间（毫秒）。<=0 表示无超时。

        Raises:
            RuntimeError: 如果线程已在运行。
            TypeError: 如果 timeout_ms 不是数字类型。

        Note:
            超时后会自动调用 cancel(force_stop=True)。

        Example:
            >>> thread.start()        # 无超时
            >>> thread.start(5000)    # 5秒超时
        """
        # 验证 timeout_ms 参数类型
        if not isinstance(timeout_ms, (int, float)):
            raise TypeError(f"timeout_ms must be a number, got {type(timeout_ms).__name__}")

        if self._thread and self._thread.isRunning():
            raise RuntimeError("Thread is already running")

        # 重置状态
        self._is_cancelled = False
        self._is_finished = False
        self._is_force_stopped = False
        self._thread_really_finished = False
        self._result = None
        self._exception = None

        # 创建工作线程和worker对象
        self._thread = QThread()
        if self._thread_name:
            self._thread.setObjectName(self._thread_name)
        self._worker = self._Worker(
            self._func,
            self._args,
            self._kwargs,
            self._initializer,
            self._initargs,
            self._thread_name,
        )

        # 将worker移动到线程中
        self._worker.moveToThread(self._thread)

        # 设置直接回调方法（用于无Qt应用时）
        self._worker._parent_result_callback = self._on_finished
        self._worker._parent_error_callback = self._on_error

        # 检查是否有Qt应用来决定使用信号还是直接启动
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()

        if app is not None:
            # 有Qt应用，使用正常的信号连接
            from PySide6.QtCore import Qt

            self._thread.started.connect(self._worker._run, Qt.QueuedConnection)
            self._worker._finished_signal.connect(
                self._on_finished, Qt.QueuedConnection
            )
            self._worker._error_signal.connect(self._on_error, Qt.QueuedConnection)
            self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
            self._signals_connected = True  # 标记信号已连接

            # 设置超时定时器
            if timeout_ms >= 0:
                self._timeout_timer = QTimer()
                self._timeout_timer.timeout.connect(self._on_timeout)
                self._timeout_timer.setSingleShot(True)
                # 对于0超时，使用最小的正数值（1ms）
                actual_timeout = max(1, timeout_ms)
                self._timeout_timer.start(actual_timeout)

            # 启动线程
            self._thread.start()
        else:
            # 没有Qt应用，使用标准线程
            import threading

            def run_worker():
                try:
                    self._worker._run()
                finally:
                    self._thread_really_finished = True
                    # 在标准线程模式下也要调用清理
                    self._on_thread_finished()

            # 设置超时处理
            if timeout_ms >= 0:

                def timeout_handler():
                    import time

                    actual_timeout = max(0.001, timeout_ms / 1000.0)
                    time.sleep(actual_timeout)
                    if not self._is_finished:
                        self.cancel(force_stop=True)

                timeout_thread = threading.Thread(target=timeout_handler, daemon=True)
                timeout_thread.start()

            # 启动工作线程
            work_thread = threading.Thread(target=run_worker, daemon=True)
            work_thread.start()

    def result(self, timeout_ms: int = -1) -> Any:
        """获取任务执行结果。

        阻塞直到任务完成，并返回结果。如果在主线程调用，
        会自动处理 Qt 事件以避免界面冻结。

        Args:
            timeout_ms: 等待超时时间（毫秒）。<=0 表示无限等待。

        Returns:
            Any: 任务的返回值。

        Raises:
            CancelledError: 如果任务被取消。
            TimeoutError: 如果超时。
            Exception: 任务执行时抛出的异常。
            TypeError: 如果 timeout_ms 不是数字类型。

        Example:
            >>> try:
            ...     result = thread.result(timeout_ms=5000)  # 5秒超时
            ...     print(f"Result: {result}")
            ... except TimeoutError:
            ...     print("Task timed out")
            ... except Exception as e:
            ...     print(f"Task failed: {e}")
        """
        # 验证 timeout_ms 参数类型
        if not isinstance(timeout_ms, (int, float)):
            raise TypeError(f"timeout_ms must be a number, got {type(timeout_ms).__name__}")

        # 转换为整数毫秒
        from PySide6.QtWidgets import QApplication
        import threading

        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()

        # STRESS TEST FIX: Fast path for already-completed futures
        # Improves concurrent result() access by avoiding polling
        if self._is_finished:
            if self._exception:
                raise self._exception
            return self._result

        app = QApplication.instance()
        start_time = time.monotonic()

        # 如果没有Qt应用，使用事件等待机制
        if app is None:
            wait_timeout = None
            if timeout_ms > 0:
                wait_timeout = timeout_ms / 1000.0  # 转换为秒
            if wait_timeout is not None and not self._completion_event.wait(wait_timeout):
                raise TimeoutError()
        else:
            # STRESS TEST FIX: Hybrid approach for high concurrency
            # Check completion event first (faster), then poll with events
            while not self._is_finished and not self._completion_event.wait(0.001):
                if self._is_cancelled or self._is_force_stopped:
                    raise CancelledError()

                # Process events for main thread
                if threading.current_thread() == threading.main_thread():
                    app.processEvents()

                # STRESS TEST FIX: Increased sleep times to reduce CPU load under concurrency
                # Higher minimums prevent busy-waiting when many threads call result()
                elapsed_ms = (time.monotonic() - start_time) * 1000  # 转换为毫秒
                if elapsed_ms < 500:
                    time.sleep(0.005)  # 5ms (was 1ms) - less aggressive under load
                elif elapsed_ms < 2000:
                    time.sleep(0.020)  # 20ms (was 10ms)
                else:
                    time.sleep(0.050)  # 50ms (unchanged)

                if timeout_ms > 0 and elapsed_ms > timeout_ms:
                    raise TimeoutError()

        # Final state checks after wait
        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()
        if self._exception:
            raise self._exception

        # 确保线程完成后进行清理
        if self._is_finished and not self._thread_really_finished:
            self._on_thread_finished()

        return self._result

    def exception(self, timeout_ms: int = -1) -> Optional[BaseException]:
        """获取任务执行时抛出的异常。

        Args:
            timeout_ms: 等待超时时间（毫秒）。<=0 表示无限等待。

        Returns:
            Optional[BaseException]: 如果任务失败返回异常对象，成功返回 None。

        Raises:
            CancelledError: 如果任务被取消。
            TimeoutError: 如果超时。
            TypeError: 如果 timeout_ms 不是数字类型。

        Example:
            >>> exc = thread.exception()
            >>> if exc:
            ...     print(f"Task failed with: {exc}")
        """
        # 验证 timeout_ms 参数类型
        if not isinstance(timeout_ms, (int, float)):
            raise TypeError(f"timeout_ms must be a number, got {type(timeout_ms).__name__}")

        # 转换为整数毫秒
        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()
        if not self._is_finished:
            if not self.wait(timeout_ms):
                raise TimeoutError()
        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()
        return self._exception

    def running(self) -> bool:
        """检查任务是否正在运行。

        Returns:
            bool: 如果任务正在执行返回 True。
        """
        if self._thread_really_finished or self._is_force_stopped:
            return False
        return self._thread is not None and self._thread.isRunning()

    def done(self) -> bool:
        """检查任务是否已完成。

        Returns:
            bool: 如果任务已完成（成功、失败或取消）返回 True。
        """
        return self._is_finished

    def cancelled(self) -> bool:
        """检查任务是否被取消。

        Returns:
            bool: 如果任务被取消返回 True。
        """
        return self._is_cancelled

    def wait(self, timeout_ms: int = -1, force_stop: bool = False) -> bool:
        """等待任务完成。

        Args:
            timeout_ms: 超时时间（毫秒）。<=0 表示无限等待。
            force_stop: 如果为 True，超时后强制终止线程；否则优雅退出。

        Returns:
            bool: 如果任务在超时前完成返回 True，否则返回 False。

        Raises:
            TypeError: 如果 timeout_ms 不是数字类型。

        Example:
            >>> if thread.wait(5000):  # 等待5秒
            ...     print("Task completed")
            ... else:
            ...     print("Task still running")
            ...
            >>> # 强制停止模式
            >>> if thread.wait(5000, force_stop=True):
            ...     print("Task completed")
            ... else:
            ...     print("Task was force stopped")
        """
        # 验证 timeout_ms 参数类型
        if not isinstance(timeout_ms, (int, float)):
            raise TypeError(f"timeout_ms must be a number, got {type(timeout_ms).__name__}")

        # 转换为整数毫秒
        if not self._thread:
            return True

        # 如果线程已经完成，直接返回True
        if self._is_finished or self._thread_really_finished:
            return True

        # 如果线程已被取消或强制停止，返回True
        if self._is_cancelled or self._is_force_stopped:
            return True

        # 等待线程真正完成
        # 改进：对于无限等待（<=0），使用更大的超时值，但不是无限大
        wait_timeout = 60000 if timeout_ms <= 0 else max(1, timeout_ms)
        # 使用Qt线程的wait方法，增加安全检查
        try:
            if self._thread and self._thread.isRunning():
                result = self._thread.wait(wait_timeout)
            else:
                result = True  # 线程未运行或已结束
        except Exception:
            # 如果Qt wait失败，检查状态
            result = (
                    self._is_finished
                    or self._thread_really_finished
                    or not self._thread
                    or not self._thread.isRunning()
            )

        # 如果等待超时但需要强制停止
        if not result and force_stop and self._thread and self._thread.isRunning():
            # 强制终止线程
            with contextlib.suppress(Exception):
                # 先尝试请求中断
                self._thread.requestInterruption()
                if self._worker:
                    self._worker._should_stop = True

                # 等待短时间看是否响应中断
                if self._thread.wait(100):
                    result = True
                else:
                    # 强制终止
                    self._thread.terminate()
                    if self._thread.wait(1000):
                        result = True
                        self._is_force_stopped = True
                        self._thread_really_finished = True
                        self._cleanup_resources()
        # 确保状态同步
        if result:
            self._thread_really_finished = True

        return result

    def _validate_callback(self, callback: Callable, callback_name: str) -> int:
        """验证回调函数的参数数量，返回需要的参数个数"""
        if not callable(callback):
            raise TypeError(f"{callback_name} must be callable")

        try:
            sig = inspect.signature(callback)
            params = list(sig.parameters.values())

            # 过滤掉self参数（类方法的第一个参数）
            if params and params[0].name == "self":
                params = params[1:]

            # 计算必需参数的数量（没有默认值的参数）
            required_param_count = len(
                [
                    p
                    for p in params
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                       and p.default is p.empty
                ]
            )

            # 改进：检查是否有可变参数 (*args, **kwargs)
            has_var_positional = any(p.kind == p.VAR_POSITIONAL for p in params)
            has_var_keyword = any(p.kind == p.VAR_KEYWORD for p in params)

            # 如果有可变参数，允许任意数量的参数
            if has_var_positional or has_var_keyword:
                # 对于可变参数函数，返回所需的最小参数数量
                return required_param_count

            # Note: For failure_callback, we allow multiple parameters but will only pass the exception
            # The validation should not restrict the parameter count, only ensure proper handling during execution

            return required_param_count

        except Exception as e:
            raise ValueError(f"Cannot inspect {callback_name} signature: {e}") from e

    def _on_finished(self, result: Any) -> None:
        """处理线程完成信号"""
        # STACK OVERFLOW FIX: Prevent recursive re-entry
        # When processEvents() is called, it may trigger queued _on_finished() calls
        # This creates infinite recursion: _on_finished → processEvents → _on_finished...
        if self._in_on_finished or self._cleanup_in_progress:
            return

        if self._is_cancelled or self._is_force_stopped:
            return

        # Set re-entry guard
        self._in_on_finished = True
        try:
            self._on_finished_impl(result)
        finally:
            self._in_on_finished = False

    def _on_finished_impl(self, result: Any) -> None:
        """Internal implementation of _on_finished"""
        self._mutex.lock()
        try:
            self._result = result
            self._is_finished = True
            self._wait_condition.wakeAll()
            # 发射有结果信号
            self.result_ready_signal.emit(result)
        finally:
            self._mutex.unlock()

        # STRESS TEST FIX: Set completion event FIRST before any other operations
        # This ensures concurrent result() calls can proceed immediately
        self._completion_event.set()

        self._cleanup_timeout_timer()

        # 请求线程退出
        if self._thread:
            self._thread.quit()

        # Fix #3: Fix non-Qt mode QTimer usage - check for Qt application before using QTimer
        # STRESS TEST FIX: Execute callbacks with immediate event processing
        # Execute all registered callbacks in order (standard library behavior)
        if self._done_callbacks:
            try:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance()
                # Create a copy to avoid modification during iteration
                with self._callbacks_lock:
                    callbacks_copy = list(self._done_callbacks)
                for callback, callback_params in callbacks_copy:
                    if app is not None:
                        # Qt mode: schedule in event loop
                        QTimer.singleShot(
                            0,
                            lambda r=result,
                                   cb=callback,
                                   cp=callback_params: self._execute_callback_safely(
                                cb, r, cp, "done_callback"
                            ),
                        )
                        # STACK OVERFLOW FIX: REMOVED app.processEvents() call
                        # Explicit processEvents() causes cascading recursion across multiple instances:
                        # Instance A: processEvents() → triggers Instance B: _on_finished() → processEvents() → ...
                        # Qt's event loop will naturally process these callbacks without explicit forcing.
                    else:
                        # Non-Qt mode: execute directly
                        self._execute_callback_safely(
                            callback, result, callback_params, "done_callback"
                        )
            except Exception as e:
                print(f"Error scheduling done callbacks: {e}", file=sys.stderr)

        # 发射任务完成信号
        self.finished_signal.emit()

        # STACK OVERFLOW FIX: REMOVED app.processEvents() call
        # Explicit processEvents() causes cascading recursion across multiple instances:
        # Instance A: processEvents() → triggers Instance B: _on_finished() → processEvents() → ...
        # Qt's event loop will naturally process these callbacks without explicit forcing.

    def _execute_callback_safely(
            self, callback: Callable, result: Any, param_count: int, callback_name: str
    ) -> None:
        """安全执行回调函数（避免竞态条件）"""
        try:
            if callback and not self._is_cancelled and not self._is_force_stopped:
                self._call_callback_with_result(
                    callback, result, param_count, callback_name
                )
        except Exception as e:
            print(f"Error in {callback_name}: {e}", file=sys.stderr)

    def _execute_done_callback(self, result: Any) -> None:
        """在主线程中执行完成回调（兼容性方法）"""
        try:
            if self._done_callbacks and not self._is_cancelled and not self._is_force_stopped:
                # Execute all callbacks in order
                with self._callbacks_lock:
                    callbacks_copy = list(self._done_callbacks)
                for callback, callback_params in callbacks_copy:
                    try:
                        self._call_callback_with_result(
                            callback,
                            result,
                            callback_params,
                            "done_callback",
                        )
                    except Exception as e:
                        print(f"Error in done callback: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error executing done callbacks: {e}", file=sys.stderr)

    def _on_error(self, exception: Exception) -> None:
        """处理线程错误信号"""
        # STACK OVERFLOW FIX: Prevent recursive re-entry
        # Same as _on_finished() - prevent recursion from processEvents()
        if self._in_on_error or self._cleanup_in_progress:
            return

        if self._is_cancelled or self._is_force_stopped:
            return

        # Set re-entry guard
        self._in_on_error = True
        try:
            self._on_error_impl(exception)
        finally:
            self._in_on_error = False

    def _on_error_impl(self, exception: Exception) -> None:
        """Internal implementation of _on_error"""
        self._mutex.lock()
        try:
            self._exception = exception
            self._is_finished = True
            self._wait_condition.wakeAll()
        finally:
            self._mutex.unlock()

        # 设置完成事件（备用同步机制）
        self._completion_event.set()

        self._cleanup_timeout_timer()

        # 请求线程退出
        if self._thread:
            self._thread.quit()

        # Fix #3: Fix non-Qt mode QTimer usage - check for Qt application before using QTimer
        # Execute all registered failure callbacks in order (standard library behavior)
        if self._failure_callbacks:
            try:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance()
                # Create a copy to avoid modification during iteration
                with self._callbacks_lock:
                    callbacks_copy = list(self._failure_callbacks)
                for callback, callback_params in callbacks_copy:
                    if app is not None:
                        # Qt mode: schedule in event loop
                        QTimer.singleShot(
                            0,
                            lambda exc=exception,
                                   cb=callback,
                                   cp=callback_params: self._execute_failure_callback_safely(
                                cb, exc, cp
                            ),
                        )
                    else:
                        # Non-Qt mode: execute directly
                        self._execute_failure_callback_safely(
                            callback, exception, callback_params
                        )
            except Exception as e:
                print(f"Error scheduling failure callbacks: {e}", file=sys.stderr)

        # COUNTER LOCK FIX: Emit finished_signal for failed tasks too
        # Pool completion handler needs to be called regardless of success/failure
        self.finished_signal.emit()

        # STACK OVERFLOW FIX: REMOVED app.processEvents() call
        # Same reasoning as in _on_finished_impl: explicit processEvents() creates
        # cascading cross-instance recursion under high load (1000+ concurrent tasks).
        # Qt's event loop will naturally process these callbacks.

    def _execute_failure_callback(self, exception: Exception) -> None:
        """在主线程中执行失败回调"""
        try:
            if self._failure_callbacks and not self._is_cancelled and not self._is_force_stopped:
                # Execute all failure callbacks in order
                with self._callbacks_lock:
                    callbacks_copy = list(self._failure_callbacks)
                for callback, callback_params in callbacks_copy:
                    try:
                        # 对于异常回调，总是传递异常对象（如果callback需要的话）
                        if callback_params == 0:
                            callback()
                        else:
                            callback(exception)
                    except Exception as e:
                        print(f"Error in failure callback: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error executing failure callbacks: {e}", file=sys.stderr)

    def _execute_failure_callback_safely(
            self, callback: Callable, exception: Exception, param_count: int
    ) -> None:
        """安全执行失败回调函数（避免竞态条件）"""
        try:
            if callback and not self._is_cancelled and not self._is_force_stopped:
                # 对于异常回调，总是传递异常对象（如果callback需要的话）
                if param_count == 0:
                    callback()
                else:
                    # 多参数情况：只传递异常作为第一个参数，其他参数使用默认值
                    callback(exception)
        except Exception as e:
            print(f"Error in failure callback: {e}", file=sys.stderr)

    def _on_thread_finished(self) -> None:
        """处理线程真正完成的信号 - SECURITY HARDENED"""
        self._thread_really_finished = True

        # CRITICAL FIX: Defer signal disconnection to allow queued _on_finished() to execute
        # The issue: _on_thread_finished() is called first, disconnecting worker signals
        # before the queued _on_finished() slot can run, breaking all callbacks.
        # Solution: Use QTimer to defer cleanup, ensuring _on_finished() executes first.
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer

        app = QApplication.instance()
        if app is not None:
            # Defer cleanup by 50ms to ensure queued _on_finished() AND callbacks complete
            # This is safe because the thread has already finished
            # Need enough time for: _on_finished() → callback scheduling → callback execution
            QTimer.singleShot(50, self._perform_delayed_cleanup)
        else:
            # No Qt app - no event loop, safe to cleanup immediately
            self._perform_delayed_cleanup()

    def _perform_delayed_cleanup(self) -> None:
        """Execute cleanup after ensuring _on_finished() has completed"""
        # COUNTER LOCK FIX: Don't disconnect pool connection
        # The pool manages its own connection lifecycle, and disconnecting it here
        # prevents the pool's completion handler from being called

        # Fix #7: Remove unnecessary signal disconnection to eliminate C++ warnings
        # Class-level signals (finished_signal, result_ready_signal) are automatically
        # disconnected by Qt when the object is destroyed via deleteLater().
        # Attempting to disconnect signals with no connections causes Qt C++ warnings
        # that cannot be suppressed by Python's contextlib.suppress.
        # Pool connections are already explicitly disconnected at line 226.
        # Worker signal disconnection is handled separately below (lines 1248-1253).

        # 确保在线程真正完成时清理所有资源
        self._cleanup_resources()

        # Fix #6: Replace deleteLater() with mode-aware cleanup
        from PySide6.QtWidgets import QApplication

        has_qt_app = QApplication.instance() is not None

        # 清理对象引用
        if self._worker:
            # 只有当信号实际连接时才尝试断开
            if self._signals_connected:
                # 使用 warnings 模块抑制 RuntimeWarning
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    with contextlib.suppress(RuntimeError, TypeError):
                        self._worker._finished_signal.disconnect()
                    with contextlib.suppress(RuntimeError, TypeError):
                        self._worker._error_signal.disconnect()
            # 清理worker的父级回调引用
            if hasattr(self._worker, "_parent_result_callback"):
                self._worker._parent_result_callback = None
            if hasattr(self._worker, "_parent_error_callback"):
                self._worker._parent_error_callback = None

            # Mode-aware cleanup: only use deleteLater() in Qt mode
            if has_qt_app:
                with contextlib.suppress(RuntimeError, AttributeError):
                    self._worker.deleteLater()
            self._worker = None

        if self._thread:
            # 只有当信号实际连接时才尝试断开
            if self._signals_connected:
                # 使用 warnings 模块抑制 RuntimeWarning
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    with contextlib.suppress(RuntimeError, TypeError):
                        self._thread.started.disconnect()
                    with contextlib.suppress(RuntimeError, TypeError):
                        self._thread.finished.disconnect()
            # Mode-aware cleanup: only use deleteLater() in Qt mode
            if has_qt_app:
                with contextlib.suppress(RuntimeError, AttributeError):
                    self._thread.deleteLater()
            self._thread = None

    def _on_timeout(self) -> None:
        """处理超时"""
        self.cancel(force_stop=True)

    def _call_callback_with_result(
            self, callback: Callable, result: Any, param_count: int, callback_name: str
    ) -> None:
        """根据回调函数的参数数量调用回调，支持返回值解包"""
        if param_count == 0:
            # 无参数回调，不传递任何参数
            callback()
        elif isinstance(result, tuple):
            # 结果是元组，可以解包
            result_count = len(result)
            if result_count == param_count:
                # 参数数量匹配，解包传递
                callback(*result)
            elif param_count == 1:
                # 回调只需要一个参数，传递整个元组
                callback(result)
            else:
                # 参数数量不匹配
                raise ValueError(
                    f"{callback_name} expects {param_count} arguments, "
                    f"but the function returned {result_count} values: {result}"
                )
        elif param_count == 1:
            # 回调需要一个参数，直接传递
            callback(result)
        else:
            # 回调需要多个参数，但结果不是元组
            raise ValueError(
                f"{callback_name} expects {param_count} arguments, "
                f"but the function returned a single value: {result}"
            )

    def _cleanup_timeout_timer(self) -> None:
        """清理超时定时器"""
        if self._timeout_timer:
            # 确保定时器完全停止
            if self._timeout_timer.isActive():
                self._timeout_timer.stop()
            # 断开所有信号连接
            with contextlib.suppress(RuntimeError, TypeError):
                self._timeout_timer.timeout.disconnect()
            self._timeout_timer.deleteLater()
            self._timeout_timer = None

    def _clear_callbacks(self) -> None:
        """Fix #2: Clear callback references to break circular refs"""
        with self._callbacks_lock:
            self._done_callbacks.clear()
            self._failure_callbacks.clear()

    def _cleanup_resources(self) -> None:
        """清理资源 - 增强版：支持 force_stop 场景的完善清理"""
        # CRITICAL SECURITY FIX: Prevent concurrent cleanup (deadlock/double-free)
        if not hasattr(self, "_cleanup_lock"):
            return  # Object being destroyed, skip cleanup

        # Non-blocking check: if cleanup already in progress, skip
        if not self._cleanup_lock.acquire(blocking=False):
            return  # Another thread is cleaning up

        try:
            # Re-entry guard: check if cleanup already completed
            if self._cleanup_in_progress:
                return
            self._cleanup_in_progress = True

            # 1. 清理超时定时器
            self._cleanup_timeout_timer()

            # 2. 处理 mutex（带超时保护）
            mutex_locked = False
            try:
                if hasattr(self, "_mutex") and self._mutex is not None:
                    try:
                        # Try to lock with timeout to prevent deadlock
                        mutex_locked = self._mutex.tryLock(100)  # 100ms timeout

                        if mutex_locked:
                            if not self._is_finished:
                                self._is_finished = True
                            if (
                                    hasattr(self, "_wait_condition")
                                    and self._wait_condition is not None
                            ):
                                self._wait_condition.wakeAll()
                        else:
                            # Failed to acquire mutex - log warning but continue cleanup
                            print(
                                "Warning: Failed to acquire mutex during cleanup (possible deadlock avoided)",
                                file=sys.stderr,
                            )
                    except Exception as e:
                        # Mutex operation failed - log but continue
                        print(
                            f"Warning: Mutex operation failed during cleanup: {e}",
                            file=sys.stderr,
                        )
                    finally:
                        if mutex_locked:
                            try:
                                self._mutex.unlock()
                            except Exception as e:
                                # CRITICAL: Unlock failed - this is serious
                                print(
                                    f"CRITICAL: Mutex unlock failed: {e} - potential deadlock",
                                    file=sys.stderr,
                                )
            except Exception as e:
                print(f"Error in mutex cleanup: {e}", file=sys.stderr)

            # 3. 设置完成事件
            with contextlib.suppress(Exception):
                if (
                        hasattr(self, "_completion_event")
                        and self._completion_event is not None
                ):
                    self._completion_event.set()

            # 4. 断开 worker 信号（force_stop 特别重要）
            if hasattr(self, "_worker") and self._worker is not None:
                if self._signals_connected:
                    # 使用 warnings 模块抑制 RuntimeWarning
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        with contextlib.suppress(RuntimeError, TypeError):
                            self._worker._finished_signal.disconnect()
                        with contextlib.suppress(RuntimeError, TypeError):
                            self._worker._error_signal.disconnect()

                # 清理回调引用
                if hasattr(self._worker, "_parent_result_callback"):
                    self._worker._parent_result_callback = None
                if hasattr(self._worker, "_parent_error_callback"):
                    self._worker._parent_error_callback = None

            # 5. 断开 thread 信号
            if hasattr(self, "_thread") and self._thread is not None and self._signals_connected:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    with contextlib.suppress(RuntimeError, TypeError):
                        self._thread.started.disconnect()
                    with contextlib.suppress(RuntimeError, TypeError):
                        self._thread.finished.disconnect()

            # STACK OVERFLOW FIX: REMOVED processEvents() loop
            # The processEvents() calls here cause cascading cross-instance recursion:
            # _cleanup_resources() → processEvents() → _perform_delayed_cleanup() → _cleanup_resources() → ...
            # With 1000 concurrent tasks, this creates infinite recursion and stack overflow.
            # Qt's event loop will naturally process deleteLater() calls without explicit forcing.
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()

            # 7. 延迟清理 Qt 对象
            if app is not None:
                if hasattr(self, "_worker") and self._worker is not None:
                    with contextlib.suppress(RuntimeError, AttributeError):
                        self._worker.deleteLater()
                if hasattr(self, "_thread") and self._thread is not None:
                    with contextlib.suppress(RuntimeError, AttributeError):
                        self._thread.deleteLater()

            # 8. 清除 Python 引用
            self._worker = None
            self._thread = None

            # 9. 清除回调引用（只在 force_stop 时）
            # CRITICAL: Do NOT clear callbacks here during normal completion!
            # Callbacks are captured in QTimer closures and clearing them serves no purpose.
            # Only clear in early-cancel paths where callbacks won't execute.
            if self._is_force_stopped:
                with self._callbacks_lock:
                    self._done_callbacks.clear()
                    self._failure_callbacks.clear()

        finally:
            # Always release cleanup lock
            with contextlib.suppress(Exception):
                self._cleanup_lock.release()

    class _Worker(QObject):
        """内部工作类"""

        _finished_signal = Signal(object)
        _error_signal = Signal(Exception)

        def __init__(
                self,
                func: Callable,
                args: tuple,
                kwargs: dict,
                initializer: Optional[Callable] = None,
                initargs: tuple = (),
                thread_name: Optional[str] = None,
        ):
            super().__init__()
            self._func = func
            self._args = args
            self._kwargs = kwargs
            self._initializer = initializer
            self._initargs = initargs
            self._should_stop = False
            self._thread_name = thread_name

        def _run(self) -> None:
            """执行工作函数"""
            try:
                from PySide6.QtCore import QThread
                from PySide6.QtWidgets import QApplication

                if self._thread_name:
                    QThread.currentThread().setObjectName(self._thread_name)

                if self._should_stop:
                    return

                if self._initializer:
                    with contextlib.suppress(Exception):
                        self._initializer(*self._initargs)
                result = self._func(*self._args, **self._kwargs)
                if not self._should_stop:
                    # 检查是否有Qt应用，决定使用信号还是直接调用
                    app = QApplication.instance()
                    if app is not None:
                        self._finished_signal.emit(result)
                    else:
                        # 没有Qt应用时，直接调用父对象的方法
                        self._direct_result_callback(result)
            except Exception as e:
                if not self._should_stop:
                    # 检查是否有Qt应用，决定使用信号还是直接调用
                    from PySide6.QtWidgets import QApplication

                    app = QApplication.instance()
                    if app is not None:
                        self._error_signal.emit(e)
                    else:
                        # 没有Qt应用时，直接调用父对象的方法
                        self._direct_error_callback(e)

        def _direct_result_callback(self, result):
            """直接结果回调（无Qt应用时使用）"""
            # 这个方法由父类设置
            if hasattr(self, "_parent_result_callback"):
                self._parent_result_callback(result)

        def _direct_error_callback(self, exception):
            """直接错误回调（无Qt应用时使用）"""
            # 这个方法由父类设置
            if hasattr(self, "_parent_error_callback"):
                self._parent_error_callback(exception)

    def _set_running_state(self) -> None:
        """设置为运行状态（用于 QThreadPoolExecutor）"""
        self._mutex.lock()
        try:
            self._is_finished = False
            self._is_cancelled = False
        finally:
            self._mutex.unlock()

    def _set_result(self, result: Any) -> None:
        """直接设置结果（用于 QThreadPoolExecutor）"""
        self._mutex.lock()
        try:
            if not self._is_cancelled:
                self._result = result
                self._is_finished = True
                self._wait_condition.wakeAll()
        finally:
            self._mutex.unlock()

        # 设置完成事件（备用同步机制）
        self._completion_event.set()

        # 发射完成信号
        self.finished_signal.emit()

        # 调用完成回调 - 执行所有回调
        if self._done_callbacks and not self._is_cancelled:
            with self._callbacks_lock:
                callbacks_to_execute = list(self._done_callbacks)
            for callback, callback_params in callbacks_to_execute:
                try:
                    self._call_callback_with_result(
                        callback,
                        result,
                        callback_params,
                        "done_callback",
                    )
                except Exception as e:
                    print(f"Error in done callback: {e}", file=sys.stderr)

    def _set_exception(self, exception: Exception) -> None:
        """直接设置异常（用于 QThreadPoolExecutor）"""
        self._mutex.lock()
        try:
            if not self._is_cancelled:
                self._exception = exception
                self._is_finished = True
                self._wait_condition.wakeAll()
        finally:
            self._mutex.unlock()

        # 设置完成事件（备用同步机制）
        self._completion_event.set()

        # 发射完成信号
        self.finished_signal.emit()

        # 调用失败回调 - 执行所有回调
        if self._failure_callbacks and not self._is_cancelled:
            with self._callbacks_lock:
                callbacks_to_execute = list(self._failure_callbacks)
            for callback, callback_params in callbacks_to_execute:
                try:
                    # 对于异常回调，总是传递异常对象（如果callback需要的话）
                    if callback_params == 0:
                        callback()
                    else:
                        callback(exception)
                except Exception as e:
                    print(f"Error in failure callback: {e}", file=sys.stderr)
