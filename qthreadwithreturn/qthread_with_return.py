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
    with QThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(time.sleep, 1) for _ in range(4)]
        for future in futures:
            future.add_done_callback(lambda: print("完成"))
            future.result()
"""

import queue
import threading
import time
import sys
import weakref
from typing import Callable, Any, Optional, Iterable, Iterator, Set, Tuple
import inspect

from PySide6.QtCore import QThread, QObject, Signal, QTimer, QMutex, QWaitCondition, Qt
from concurrent.futures import CancelledError, TimeoutError


class QThreadPoolExecutor:
    """PySide6 线程池执行器。
    
    提供与 concurrent.futures.ThreadPoolExecutor 兼容的 API，
    用于管理和执行多个并发任务。
    
    主要特性:
        - 自动管理线程池大小
        - 支持任务队列和并发控制
        - 返回的 Future 对象支持灵活的回调机制
        - 支持 with 语句进行资源管理
        - 支持线程命名和初始化
    
    使用示例:
        >>> with QThreadPoolExecutor(max_workers=4) as pool:
        ...     future = pool.submit(lambda x: x ** 2, 5)
        ...     print(future.result())  # 输出: 25
        ...
        ...     # 批量执行
        ...     futures = [pool.submit(str.upper, x) for x in ['a', 'b', 'c']]
        ...     results = [f.result() for f in futures]
        ...     print(results)  # 输出: ['A', 'B', 'C']
    
    Attributes:
        无公开属性，所有状态通过方法访问。
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        thread_name_prefix: str = "",
        initializer: Optional[Callable] = None,
        initargs: Tuple = (),
    ):
        """初始化线程池执行器。
        
        Args:
            max_workers: 最大工作线程数。如果为 None，默认为 CPU 核心数 * 5。
            thread_name_prefix: 线程名称前缀，用于调试和日志记录。
            initializer: 每个工作线程启动时调用的初始化函数。
            initargs: 传递给 initializer 的参数元组。
        
        Raises:
            ValueError: 当 max_workers <= 0 时。
        
        Example:
            >>> def init_worker(name):
            ...     print(f"Worker {name} initialized")
            >>> pool = QThreadPoolExecutor(
            ...     max_workers=2,
            ...     thread_name_prefix="Worker",
            ...     initializer=init_worker,
            ...     initargs=("Test",)
            ... )
        """
        import os

        self._max_workers = max_workers or min((os.cpu_count() or 1) * 2, 32)  # 限制最大线程数避免资源耗尽
        if self._max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._thread_name_prefix = thread_name_prefix
        self._initializer = initializer
        self._initargs = initargs
        self._shutdown = False
        self._shutdown_lock = threading.Lock()
        self._active_futures: Set[QThreadWithReturn] = set()
        self._pending_tasks: list = []
        self._running_workers: int = 0
        self._thread_counter = 0

    def __enter__(self):
        """支持 with 语句。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 语句时自动关闭线程池。"""
        self.shutdown(wait=True)

    def submit(self, fn: Callable, /, *args, **kwargs) -> "QThreadWithReturn":
        """提交任务到线程池执行。
        
        Args:
            fn: 要执行的可调用对象。
            *args: 传递给 fn 的位置参数。
            **kwargs: 传递给 fn 的关键字参数。
        
        Returns:
            QThreadWithReturn: 代表异步执行结果的 Future 对象。
        
        Raises:
            RuntimeError: 当线程池已关闭时。
        
        Example:
            >>> pool = QThreadPoolExecutor(max_workers=2)
            >>> future = pool.submit(sum, [1, 2, 3])
            >>> future.add_done_callback(lambda r: print(f"Sum: {r}"))
            >>> print(future.result())  # 输出: 6
        """
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._thread_counter += 1
            thread_name = None
            if self._thread_name_prefix:
                thread_name = (
                    f"{self._thread_name_prefix}-Worker-{self._thread_counter}"
                )
            future = QThreadWithReturn(
                fn,
                *args,
                initializer=self._initializer,
                initargs=self._initargs,
                thread_name=thread_name,
                **kwargs,
            )
            self._pending_tasks.append(future)
            self._try_start_tasks()
            return future

    def _try_start_tasks(self):
        # 启动等待队列中的任务，直到达到最大并发数
        while self._pending_tasks and self._running_workers < self._max_workers:
            future = self._pending_tasks.pop(0)
            self._active_futures.add(future)
            self._running_workers += 1

            # 连接信号，任务完成时减少计数
            # 使用闭包捕获当前的future引用
            def make_on_finished(fut):
                def on_finished():
                    self._running_workers -= 1
                    self._active_futures.discard(fut)
                    self._try_start_tasks()
                return on_finished

            # 使用弱引用连接避免循环引用
            import weakref
            weak_self = weakref.ref(self)
            def make_safe_on_finished(fut):
                def safe_on_finished():
                    strong_self = weak_self()
                    if strong_self is not None:
                        strong_self._running_workers -= 1
                        strong_self._active_futures.discard(fut)
                        strong_self._try_start_tasks()
                return safe_on_finished
            
            future.finished_signal.connect(make_safe_on_finished(future))
            future.start()

    def _start_worker_if_needed(self):
        """如果线程池未满，则启动新线程。（已废弃，使用QThreadWithReturn）"""
        # 这个方法已经不再使用，因为我们直接使用QThreadWithReturn
        pass

    def _worker(self):
        """工作线程主循环。（已废弃，使用QThreadWithReturn）"""
        # 这个方法已经不再使用，因为我们直接使用QThreadWithReturn
        pass


    def shutdown(
        self,
        wait: bool = True,
        *,
        cancel_futures: bool = False,
        force_stop: bool = False,
    ) -> None:
        """关闭线程池。
        
        Args:
            wait: 如果为 True，阻塞直到所有运行中的任务完成。
            cancel_futures: 如果为 True，取消所有待处理的任务。
            force_stop: 如果为 True，强制终止运行中的线程。
        
        Note:
            shutdown 后不能再提交新任务。
            使用 with 语句时会自动调用 shutdown(wait=True)。
        
        Example:
            >>> pool = QThreadPoolExecutor(max_workers=2)
            >>> # 提交一些任务...
            >>> pool.shutdown(wait=True, cancel_futures=True)
        """
        with self._shutdown_lock:
            if self._shutdown:
                return  # 避免重复shutdown
            self._shutdown = True
            
            if cancel_futures:
                # 取消待处理任务
                pending_copy = list(self._pending_tasks)
                for future in pending_copy:
                    try:
                        future.cancel(force_stop=force_stop)
                    except Exception:
                        pass  # 忽略取消时的异常
                self._pending_tasks.clear()
                
            # 处理活跃任务
            active_copy = list(self._active_futures)
            if force_stop:
                for future in active_copy:
                    try:
                        future.cancel(force_stop=True)
                    except Exception:
                        pass  # 忽略强制停止时的异常
                        
        if wait:
            # 等待所有活跃任务完成
            import time
            start_time = time.time()
            timeout_per_task = 30.0
            
            for future in active_copy:
                try:
                    # 动态调整超时时间，避免总时间过长
                    elapsed = time.time() - start_time
                    remaining_timeout = max(1.0, timeout_per_task - elapsed)
                    future.result(timeout=remaining_timeout)
                except Exception:
                    pass  # 忽略获取结果时的异常
                    
            # 清理剩余引用
            self._active_futures.clear()
            self._pending_tasks.clear()

    @staticmethod
    def as_completed(
        fs: Iterable["QThreadWithReturn"], timeout: Optional[float] = None
    ) -> Iterator["QThreadWithReturn"]:
        """返回一个迭代器，按完成顺序生成 Future 对象。
        
        Args:
            fs: QThreadWithReturn 对象的可迭代集合。
            timeout: 等待的最大秒数。
        
        Yields:
            QThreadWithReturn: 按完成顺序返回的 Future 对象。
        
        Raises:
            TimeoutError: 如果在 timeout 秒内没有 Future 完成。
        
        Example:
            >>> futures = [pool.submit(task, i) for i in range(5)]
            >>> for future in QThreadPoolExecutor.as_completed(futures):
            ...     result = future.result()
            ...     print(f"Task completed with result: {result}")
        """
        import time

        futures = set(fs)
        done = set()
        start_time = time.monotonic() if timeout is not None else None
        while futures:
            for fut in list(futures):
                if fut.done():
                    futures.remove(fut)
                    done.add(fut)
                    yield fut
            if not futures:
                break
            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed > timeout:
                    raise TimeoutError()
            # 处理Qt事件，避免阻塞
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                app.processEvents()
            time.sleep(0.01)


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

        # 回调函数
        self._done_callback: Optional[Callable] = None
        self._failure_callback: Optional[Callable] = None
        self._done_callback_params: int = 0  # 记录done_callback需要的参数数量
        self._failure_callback_params: int = 0  # 记录failure_callback需要的参数数量

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

    def add_done_callback(self, callback: Callable) -> None:
        """添加任务成功完成后的回调函数。
        
        回调函数会在主线程中执行，支持以下几种形式：
        - 无参数: callback()
        - 单参数: callback(result)
        - 多参数: callback(a, b, c) - 当返回值是元组时自动解包
        
        Args:
            callback: 回调函数。参数数量会自动检测。
        
        Note:
            - 类方法的 self 参数会被自动忽略
            - 如果返回值是元组且回调有多个参数，会自动解包
            - 后设置的回调会覆盖之前的回调
        
        Example:
            >>> thread.add_done_callback(lambda: print("Done!"))  # 无参数
            >>> thread.add_done_callback(lambda x: print(x))      # 单参数
            >>> thread.add_done_callback(lambda a, b: print(a+b)) # 多参数
        """
        param_count = self._validate_callback(callback, "done_callback")
        self._done_callback = callback
        self._done_callback_params = param_count

    def add_failure_callback(self, callback: Callable) -> None:
        """添加任务失败后的回调函数。
        
        回调函数会在主线程中执行，支持：
        - 无参数: callback()
        - 单参数: callback(exception)
        
        Args:
            callback: 回调函数。
        
        Note:
            失败回调只支持 0 或 1 个参数，因为异常对象只有一个。
        
        Example:
            >>> thread.add_failure_callback(lambda: print("Failed!"))
            >>> thread.add_failure_callback(lambda e: print(f"Error: {e}"))
        """
        param_count = self._validate_callback(callback, "failure_callback")
        self._failure_callback = callback
        self._failure_callback_params = param_count

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
                self._is_force_stopped = True
                try:
                    if not self._thread.wait(100):
                        self._thread.terminate()
                        self._thread.wait(1000)
                except AttributeError:
                    pass
                self._thread_really_finished = True
                self._cleanup_resources()
            else:
                try:
                    self._thread.quit()
                    self._thread.wait(100)
                except AttributeError:
                    pass
        
        # 确保在取消时也清理资源
        if not force_stop:
            self._cleanup_resources()
        
        return True

    def start(self, timeout_ms: int = -1) -> None:
        """启动线程执行任务。
        
        Args:
            timeout_ms: 超时时间（毫秒）。-1 表示无超时。
        
        Raises:
            RuntimeError: 如果线程已在运行。
        
        Note:
            超时后会自动调用 cancel(force_stop=True)。
        
        Example:
            >>> thread.start()        # 无超时
            >>> thread.start(5000)    # 5秒超时
        """
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
            self._worker._finished_signal.connect(self._on_finished, Qt.QueuedConnection)
            self._worker._error_signal.connect(self._on_error, Qt.QueuedConnection)
            self._thread.finished.connect(self._on_thread_finished, Qt.QueuedConnection)
            self._signals_connected = True  # 标记信号已连接

            # 设置超时定时器
            if timeout_ms >= 0:
                self._timeout_timer = QTimer()
                self._timeout_timer.timeout.connect(self._on_timeout)
                self._timeout_timer.setSingleShot(True)
                # 对于0超时，使用最小的正数值（1ms）
                actual_timeout = max(1, timeout_ms) if timeout_ms >= 0 else timeout_ms
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
                    actual_timeout = max(0.001, timeout_ms / 1000.0) if timeout_ms >= 0 else None
                    if actual_timeout is not None:
                        time.sleep(actual_timeout)
                        if not self._is_finished:
                            self.cancel(force_stop=True)
                
                timeout_thread = threading.Thread(target=timeout_handler, daemon=True)
                timeout_thread.start()
                
            # 启动工作线程
            work_thread = threading.Thread(target=run_worker, daemon=True)
            work_thread.start()

    def result(self, timeout: Optional[float] = None) -> Any:
        """获取任务执行结果。
        
        阻塞直到任务完成，并返回结果。如果在主线程调用，
        会自动处理 Qt 事件以避免界面冻结。
        
        Args:
            timeout: 等待超时时间（秒）。None 表示无限等待。
        
        Returns:
            Any: 任务的返回值。
        
        Raises:
            CancelledError: 如果任务被取消。
            TimeoutError: 如果超时。
            Exception: 任务执行时抛出的异常。
        
        Example:
            >>> try:
            ...     result = thread.result(timeout=5.0)
            ...     print(f"Result: {result}")
            ... except TimeoutError:
            ...     print("Task timed out")
            ... except Exception as e:
            ...     print(f"Task failed: {e}")
        """
        from PySide6.QtWidgets import QApplication
        import threading

        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()
        if not self._is_finished:
            app = QApplication.instance()
            start_time = time.monotonic()
            
            # 如果没有Qt应用，使用事件等待机制
            if app is None:
                wait_timeout = timeout if timeout is not None else None
                if self._completion_event.wait(wait_timeout):
                    # 事件被设置，检查是否真的完成
                    pass  # 继续到下面的检查
                else:
                    # 超时
                    if timeout is not None:
                        raise TimeoutError()
            else:
                # 有Qt应用，使用事件循环处理
                while not self._is_finished:
                    if self._is_cancelled or self._is_force_stopped:
                        raise CancelledError()
                    if app and threading.current_thread() == threading.main_thread():
                        app.processEvents()
                    # 动态调整睡眠时间，起初短一点，逐渐增加
                    elapsed = time.monotonic() - start_time
                    if elapsed < 1.0:
                        time.sleep(0.001)  # 1ms
                    elif elapsed < 5.0:
                        time.sleep(0.01)   # 10ms
                    else:
                        time.sleep(0.05)   # 50ms
                    if timeout is not None and elapsed > timeout:
                        raise TimeoutError()
        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()
        if self._exception:
            raise self._exception
        
        # 确保线程完成后进行清理
        if self._is_finished and not self._thread_really_finished:
            self._on_thread_finished()
            
        return self._result

    def exception(self, timeout: Optional[float] = None) -> Optional[BaseException]:
        """获取任务执行时抛出的异常。
        
        Args:
            timeout: 等待超时时间（秒）。
        
        Returns:
            Optional[BaseException]: 如果任务失败返回异常对象，成功返回 None。
        
        Raises:
            CancelledError: 如果任务被取消。
            TimeoutError: 如果超时。
        
        Example:
            >>> exc = thread.exception()
            >>> if exc:
            ...     print(f"Task failed with: {exc}")
        """
        if self._is_cancelled or self._is_force_stopped:
            raise CancelledError()
        if not self._is_finished:
            if not self.wait(int(timeout * 1000) if timeout is not None else -1):
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
            timeout_ms: 超时时间（毫秒）。-1 表示无限等待。
            force_stop: 如果为 True，超时后强制终止线程；否则优雅退出。
        
        Returns:
            bool: 如果任务在超时前完成返回 True，否则返回 False。
        
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
        if not self._thread:
            return True

        # 如果线程已经完成，直接返回True
        if self._is_finished or self._thread_really_finished:
            return True

        # 如果线程已被取消或强制停止，返回True
        if self._is_cancelled or self._is_force_stopped:
            return True

        # 等待线程真正完成
        # 改进：对于无限等待（负数），使用更大的超时值，但不是无限大
        if timeout_ms < 0:
            wait_timeout = 60000  # 1分钟，而不是无限等待避免死锁
        else:
            wait_timeout = max(1, timeout_ms)  # 确保至少1ms

        # 使用Qt线程的wait方法
        try:
            result = self._thread.wait(wait_timeout)
        except Exception:
            # 如果Qt wait失败，检查状态
            result = self._is_finished or self._thread_really_finished

        # 如果等待超时但需要强制停止
        if not result and force_stop and self._thread and self._thread.isRunning():
            # 强制终止线程
            try:
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
            except Exception:
                # 忽略强制终止时的异常
                pass

        # 确保状态同步
        if result:
            self._thread_really_finished = True

        return result

    def _validate_callback(
        self, callback: Callable, callback_name: str
    ) -> int:
        """验证回调函数的参数数量，返回需要的参数个数"""
        if not callable(callback):
            raise TypeError(f"{callback_name} must be callable")

        try:
            sig = inspect.signature(callback)
            params = list(sig.parameters.values())
            
            # 过滤掉self参数（类方法的第一个参数）
            if params and params[0].name == 'self':
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
            raise ValueError(f"Cannot inspect {callback_name} signature: {e}")

    def _on_finished(self, result: Any) -> None:
        """处理线程完成信号"""
        if self._is_cancelled or self._is_force_stopped:
            return

        self._mutex.lock()
        try:
            self._result = result
            self._is_finished = True
            self._wait_condition.wakeAll()
            # 发射有结果信号
            self.result_ready_signal.emit(result)
        finally:
            self._mutex.unlock()
        
        # 设置完成事件（备用同步机制）
        self._completion_event.set()

        self._cleanup_timeout_timer()

        # 请求线程退出
        if self._thread:
            self._thread.quit()

        # 在主线程中调用回调函数
        if self._done_callback:
            try:
                # 保存回调引用和参数，避免竞态条件下被清理
                callback = self._done_callback
                callback_params = self._done_callback_params
                QTimer.singleShot(0, lambda r=result, cb=callback, cp=callback_params: 
                                 self._execute_callback_safely(cb, r, cp, "done_callback"))
            except Exception as e:
                print(f"Error scheduling done callback: {e}", file=sys.stderr)

        # 发射任务完成信号
        self.finished_signal.emit()

    def _execute_callback_safely(self, callback: Callable, result: Any, param_count: int, callback_name: str) -> None:
        """安全执行回调函数（避免竞态条件）"""
        try:
            if callback and not self._is_cancelled and not self._is_force_stopped:
                self._call_callback_with_result(callback, result, param_count, callback_name)
        except Exception as e:
            print(f"Error in {callback_name}: {e}", file=sys.stderr)
    
    def _execute_done_callback(self, result: Any) -> None:
        """在主线程中执行完成回调（兼容性方法）"""
        try:
            if (
                self._done_callback
                and not self._is_cancelled
                and not self._is_force_stopped
            ):
                self._call_callback_with_result(self._done_callback, result, self._done_callback_params, "done_callback")
        except Exception as e:
            print(f"Error in done callback: {e}", file=sys.stderr)

    def _on_error(self, exception: Exception) -> None:
        """处理线程错误信号"""
        if self._is_cancelled or self._is_force_stopped:
            return

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

        # 在主线程中调用失败回调函数
        if self._failure_callback:
            try:
                # 保存回调引用和参数，避免竞态条件下被清理
                callback = self._failure_callback
                callback_params = self._failure_callback_params
                QTimer.singleShot(0, lambda exc=exception, cb=callback, cp=callback_params: 
                                 self._execute_failure_callback_safely(cb, exc, cp))
            except Exception as e:
                print(f"Error scheduling failure callback: {e}", file=sys.stderr)

    def _execute_failure_callback(self, exception: Exception) -> None:
        """在主线程中执行失败回调"""
        try:
            if (
                self._failure_callback
                and not self._is_cancelled
                and not self._is_force_stopped
            ):
                # 对于异常回调，总是传递异常对象（如果callback需要的话）
                if self._failure_callback_params == 0:
                    self._failure_callback()
                elif self._failure_callback_params == 1:
                    self._failure_callback(exception)
                else:
                    # 多参数情况：只传递异常作为第一个参数，其他参数使用默认值
                    self._failure_callback(exception)
        except Exception as e:
            print(f"Error in failure callback: {e}", file=sys.stderr)
    
    def _execute_failure_callback_safely(self, callback: Callable, exception: Exception, param_count: int) -> None:
        """安全执行失败回调函数（避免竞态条件）"""
        try:
            if callback and not self._is_cancelled and not self._is_force_stopped:
                # 对于异常回调，总是传递异常对象（如果callback需要的话）
                if param_count == 0:
                    callback()
                elif param_count == 1:
                    callback(exception)
                else:
                    # 多参数情况：只传递异常作为第一个参数，其他参数使用默认值
                    callback(exception)
        except Exception as e:
            print(f"Error in failure callback: {e}", file=sys.stderr)

    def _on_thread_finished(self) -> None:
        """处理线程真正完成的信号"""
        self._thread_really_finished = True
        
        # 确保在线程真正完成时清理所有资源
        self._cleanup_resources()

        # 清理对象引用
        if self._worker:
            # 只有当信号实际连接时才尝试断开
            if self._signals_connected:
                try:
                    self._worker._finished_signal.disconnect()
                    self._worker._error_signal.disconnect()
                except (RuntimeError, TypeError):
                    pass  # 信号可能已经断开或对象已被删除
            
            # 清理worker的父级回调引用
            if hasattr(self._worker, '_parent_result_callback'):
                self._worker._parent_result_callback = None
            if hasattr(self._worker, '_parent_error_callback'):
                self._worker._parent_error_callback = None
                
            # 尝试删除对象
            try:
                self._worker.deleteLater()
            except (RuntimeError, AttributeError):
                pass  # 非Qt模式下可能没有deleteLater
            self._worker = None
            
        if self._thread:
            # 只有当信号实际连接时才尝试断开
            if self._signals_connected:
                try:
                    self._thread.started.disconnect()
                    self._thread.finished.disconnect()
                except (RuntimeError, TypeError):
                    pass  # 信号可能已经断开或对象已被删除
            
            # 尝试删除对象
            try:
                self._thread.deleteLater()
            except (RuntimeError, AttributeError):
                pass  # 非Qt模式下可能没有deleteLater
            self._thread = None

    def _on_timeout(self) -> None:
        """处理超时"""
        self.cancel(force_stop=True)

    def _call_callback_with_result(self, callback: Callable, result: Any, param_count: int, callback_name: str) -> None:
        """根据回调函数的参数数量调用回调，支持返回值解包"""
        if param_count == 0:
            # 无参数回调，不传递任何参数
            callback()
        else:
            # 尝试解包结果
            if isinstance(result, tuple):
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
            else:
                # 结果不是元组
                if param_count == 1:
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
            try:
                self._timeout_timer.timeout.disconnect()
            except (RuntimeError, TypeError):
                pass  # 信号可能已经断开
            self._timeout_timer.deleteLater()
            self._timeout_timer = None

    def _cleanup_resources(self) -> None:
        """清理资源"""
        self._cleanup_timeout_timer()

        self._mutex.lock()
        try:
            if not self._is_finished:
                self._is_finished = True
            self._wait_condition.wakeAll()
        finally:
            self._mutex.unlock()
            
        # 清理回调函数引用，避免潜在的循环引用
        self._done_callback = None
        self._failure_callback = None

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
                    try:
                        self._initializer(*self._initargs)
                    except Exception:
                        # 初始化器异常不影响主任务执行
                        pass
                    
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
            if hasattr(self, '_parent_result_callback'):
                self._parent_result_callback(result)
                
        def _direct_error_callback(self, exception):
            """直接错误回调（无Qt应用时使用）"""
            # 这个方法由父类设置
            if hasattr(self, '_parent_error_callback'):
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
        
        # 调用完成回调
        if self._done_callback and not self._is_cancelled:
            try:
                self._call_callback_with_result(self._done_callback, result, self._done_callback_params, "done_callback")
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
        
        # 调用失败回调
        if self._failure_callback and not self._is_cancelled:
            try:
                # 对于异常回调，总是传递异常对象（如果callback需要的话）
                if self._failure_callback_params == 0:
                    self._failure_callback()
                elif self._failure_callback_params == 1:
                    self._failure_callback(exception)
                else:
                    # 多参数情况：只传递异常作为第一个参数，其他参数使用默认值
                    self._failure_callback(exception)
            except Exception as e:
                print(f"Error in failure callback: {e}", file=sys.stderr)
