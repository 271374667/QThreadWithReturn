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
from typing import Callable, Any, Optional, Iterable, Iterator, Set, Tuple

from PySide6.QtCore import QThread, QObject, Signal, QTimer, QMutex, QWaitCondition


class QThreadPoolExecutor:
    """PySide6 线程池执行器。

    提供与 concurrent.futures.ThreadPoolExecutor 兼容的 API，
    用于管理和执行多个并发任务。

    主要特性:
        - 自动管理线程池大小
        - 支持任务队列和并发控制
        - 返回的 Future 对象支持灵活的回调机制
        - 支持池级别完成回调和任务级别失败回调
        - 支持线程命名和初始化

    使用示例:
        >>> # 基本用法（不使用 with 语句）
        >>> pool = QThreadPoolExecutor(max_workers=4)
        >>> future = pool.submit(lambda x: x ** 2, 5)
        >>> print(future.result())  # 输出: 25
        ...
        >>> # 批量执行
        >>> futures = [pool.submit(str.upper, x) for x in ['a', 'b', 'c']]
        >>> results = [f.result() for f in futures]
        >>> print(results)  # 输出: ['A', 'B', 'C']
        >>> pool.shutdown(wait=True)
        ...
        >>> # 使用池级别回调
        >>> pool = QThreadPoolExecutor(max_workers=2)
        >>> pool.add_done_callback(lambda: print("所有任务完成"))
        >>> pool.add_failure_callback(lambda e: print(f"任务失败: {e}"))
        >>> # 提交任务...
        >>> pool.shutdown(wait=True)

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

        self._max_workers = max_workers or min(
            (os.cpu_count() or 1) * 2, 32
        )  # 限制最大线程数避免资源耗尽
        if self._max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._thread_name_prefix = thread_name_prefix
        self._initializer = initializer
        self._initargs = initargs
        self._shutdown = False
        self._shutdown_lock = threading.Lock()
        # COUNTER LOCK FIX: Add dedicated lock for atomic counter operations
        # This fixes the counter desynchronization bug when multiple tasks complete simultaneously
        self._counter_lock = threading.Lock()
        self._active_futures: Set[QThreadWithReturn] = set()
        self._pending_tasks: list = []
        self._running_workers: int = 0
        self._thread_counter = 0

        # Callback management
        self._done_callbacks: list[Callable] = []
        self._failure_callbacks: list[Tuple[Callable, int]] = []  # (callback, param_count)
        self._callbacks_lock = threading.Lock()
        self._done_callbacks_executed = False  # Track if done callbacks already fired

        # GC BUG FIX: Self-reference to prevent garbage collection
        # Keep pool alive while there's pending work or callbacks to execute
        # This prevents premature GC when pool is a local variable (e.g., in GUI event handlers)
        # The reference is released after all work completes and done callbacks execute
        self._self_reference: Optional["QThreadPoolExecutor"] = None

    def __enter__(self):
        """不建议使用with上下文，会导致阻塞UI界面"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 语句时自动关闭线程池。"""
        try:
            # 如果有异常，强制关闭避免hang
            if exc_type is not None:
                self.shutdown(wait=False, force_stop=True)
            else:
                self.shutdown(wait=True)
        except Exception:
            # 确保即使shutdown失败也不会抛出异常
            with contextlib.suppress(Exception):
                self.shutdown(wait=False, force_stop=True)

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
        # COUNTER LOCK FIX: Check capacity atomically to prevent race conditions
        # SHUTDOWN FIX: Allow starting pending tasks during shutdown (unless cancelled)
        # Standard ThreadPoolExecutor behavior: pending tasks execute unless cancel_futures=True

        # GC BUG FIX: Keep pool alive while there's work to do
        # Set self-reference when pool has active or pending work to prevent premature GC
        # This is critical when pool is a local variable in GUI event handlers
        if not self._self_reference and (self._active_futures or self._pending_tasks):
            self._self_reference = self

        while self._pending_tasks:
            can_start = False
            with self._counter_lock:
                if self._running_workers < self._max_workers:
                    can_start = True
                    # Pre-increment counter to reserve slot
                    # This prevents race where multiple threads try to start tasks simultaneously
                    self._running_workers += 1

            if not can_start:
                break  # Pool is full, stop trying

            # Pop task AFTER we've reserved a slot
            future = self._pending_tasks.pop(0)

            # GC BUG FIX: Use strong reference to keep pool alive until all tasks complete
            # The circular reference (pool → future → signal → closure → pool) is properly
            # broken by signal disconnection (line 220) and future removal (line 214).
            # Using weak reference causes pool to be GC'd when it's a local variable,
            # preventing pending tasks from being scheduled after active tasks complete.
            # This matches the behavior of concurrent.futures.ThreadPoolExecutor.
            strong_self = self  # Strong reference to pool

            def make_safe_on_finished(fut):
                def safe_on_finished():
                    # strong_self is always valid because pool is kept alive
                    try:
                        # COUNTER LOCK FIX: Use dedicated lock for atomic counter operations
                        # This prevents race conditions when multiple tasks complete simultaneously
                        with strong_self._counter_lock:
                            strong_self._running_workers = max(
                                0, strong_self._running_workers - 1
                            )
                            strong_self._active_futures.discard(fut)

                        # NEW: Check for task failure and call failure callbacks
                        try:
                            exc = fut.exception()
                            if exc is not None:
                                strong_self._call_failure_callbacks(exc)
                        except CancelledError:
                            # Task was cancelled, not a failure
                            pass
                        except Exception as e:
                            # Ignore exceptions from exception() call itself
                            pass

                        # MEMORY LEAK FIX: Disconnect signal immediately after completion
                        # This breaks the circular reference: future → signal → closure → pool
                        with contextlib.suppress(RuntimeError, TypeError):
                            if hasattr(fut, "_pool_connection"):
                                fut.finished_signal.disconnect(fut._pool_connection)
                                del fut._pool_connection
                            if hasattr(fut, "_pool_managed"):
                                del fut._pool_managed

                        # NEW: Check if pool is complete and call done callbacks
                        if strong_self._is_pool_complete():
                            strong_self._execute_done_callbacks()

                        # SHUTDOWN FIX: Start pending tasks even during shutdown
                        # Standard ThreadPoolExecutor behavior: pending tasks execute unless cancel_futures=True
                        # The check for shutdown is now inside _try_start_tasks (it checks if tasks were cancelled)
                        strong_self._try_start_tasks()
                    except Exception as e:
                        # COUNTER LOCK FIX: Emergency counter correction with lock protection
                        print(
                            f"Warning: Error in task completion handler: {e}",
                            file=sys.stderr,
                        )
                        with contextlib.suppress(Exception):
                            with strong_self._counter_lock:
                                strong_self._running_workers = max(
                                    0, strong_self._running_workers - 1
                                )

                return safe_on_finished

            try:
                # Connect signal
                connection = future.finished_signal.connect(
                    make_safe_on_finished(future)
                )
                future._pool_connection = connection
                # COUNTER LOCK FIX: Mark as pool-managed to prevent cleanup from disconnecting
                future._pool_managed = True

                # Add to active set
                with self._counter_lock:
                    self._active_futures.add(future)

                # Start thread LAST
                future.start()

            except Exception as e:
                # STRESS TEST FIX: Rollback on failure
                print(f"Error starting task: {e}", file=sys.stderr)
                with contextlib.suppress(Exception):
                    # Disconnect signal if connected
                    if hasattr(future, "_pool_connection"):
                        with contextlib.suppress(RuntimeError, TypeError):
                            future.finished_signal.disconnect(future._pool_connection)
                            del future._pool_connection
                    # COUNTER LOCK FIX: Rollback state with lock protection
                    with self._counter_lock:
                        self._active_futures.discard(future)
                        self._running_workers = max(0, self._running_workers - 1)

                    # Re-add to pending (retry once)
                    self._pending_tasks.insert(0, future)
                break  # Stop after error

    def shutdown(
            self,
            wait: bool = False,
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
            - shutdown 后不能再提交新任务
            - 在PySide6应用中，建议使用 wait=False 避免UI冻结
            - 池级别完成回调会在所有任务完成后自动触发

        Example:
            >>> pool = QThreadPoolExecutor(max_workers=2)
            >>> # 提交一些任务...
            >>> pool.shutdown(wait=True, cancel_futures=True)
            ...
            >>> # UI应用中推荐使用异步关闭
            >>> pool.shutdown(wait=False)  # 不阻塞主线程
        """
        # SHUTDOWN FIX: Allow wait logic to proceed even if already shut down
        # This handles the case where shutdown is called twice:
        # 1st: shutdown(wait=False, cancel_futures=True) - cancels pending
        # 2nd: shutdown(wait=True, force_stop=True) - waits for active tasks
        already_shutdown = False
        with self._shutdown_lock:
            if self._shutdown:
                already_shutdown = True  # Remember this for later
            else:
                self._shutdown = True

            # Only cancel and force-stop if NOT already shutdown
            if not already_shutdown and cancel_futures:
                pending_copy = list(self._pending_tasks)
                for future in pending_copy:
                    with contextlib.suppress(Exception):
                        future.cancel(force_stop=force_stop)
                self._pending_tasks.clear()

            # Copy active futures BEFORE any disconnection (even if already shutdown)
            active_copy = list(self._active_futures)

            # Only force-stop if requested AND not already shutdown
            if not already_shutdown and force_stop:
                for future in active_copy:
                    with contextlib.suppress(Exception):
                        future.cancel(force_stop=True)
        if wait:
            # 等待所有活跃任务完成，使用非阻塞检查避免deadlock
            import time

            start_time = time.time()
            max_wait_time = 10.0  # 减少最大等待时间避免死锁

            # 使用轮询而不是阻塞等待来避免Qt事件循环死锁
            # SHUTDOWN FIX: Wait for both active AND pending tasks (unless cancelled)
            # Standard behavior: shutdown(wait=True) waits for all tasks, not just active ones
            while (active_copy or self._pending_tasks) and (time.time() - start_time) < max_wait_time:
                completed_futures = []
                for future in active_copy[:]:  # 创建副本避免修改时迭代
                    try:
                        if future.done():
                            completed_futures.append(future)
                        else:
                            # 非阻塞检查，避免调用result()导致的死锁
                            future.wait(50, force_stop=False)  # 50ms非阻塞检查
                    except Exception:
                        completed_futures.append(future)  # 出错也视为完成

                # 移除已完成的future
                for future in completed_futures:
                    active_copy.remove(future)

                # SHUTDOWN FIX: Only break if BOTH active and pending are empty
                # Don't exit early if there are still pending tasks to process
                if not active_copy and not self._pending_tasks:
                    break

                # CRITICAL FIX: Process Qt events to allow signal delivery
                # Without this, queued signals from task completion never get processed
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.processEvents()

                # 短暂休眠避免CPU占用过高
                time.sleep(0.01)

            # CRITICAL FIX: Only clear references after wait loop completes
            # shutdown(wait=False) should NOT destroy pending tasks - tasks continue executing
            # Standard ThreadPoolExecutor behavior: tasks complete after shutdown unless cancelled
            self._active_futures.clear()
            self._pending_tasks.clear()

            # CRITICAL FIX: Process Qt events before disconnecting signals (only if we waited)
            # With Qt.QueuedConnection, signal emissions are queued and need event processing
            # Must process events BEFORE disconnecting to allow queued slots to execute
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
                # Extra processing to ensure all queued callbacks complete
                import time
                time.sleep(0.05)  # 50ms for callbacks to execute
                app.processEvents()

            # CRITICAL FIX: Disconnect signals AFTER wait loop completes AND after processing events
            # This allows tasks to finish and trigger their completion handlers
            # before we clean up the signal connections
            for future in active_copy:
                with contextlib.suppress(RuntimeError, TypeError):
                    if hasattr(future, "_pool_connection"):
                        future.finished_signal.disconnect(future._pool_connection)
                        del future._pool_connection
                    if hasattr(future, "_pool_managed"):
                        del future._pool_managed

        # NEW: After shutdown completes, check if we need to fire done callbacks
        # This handles the case where pool is shut down with no tasks (empty pool)
        # or after wait=True completes with all tasks finished
        if self._is_pool_complete():
            self._execute_done_callbacks()

    def add_done_callback(self, callback: Callable) -> None:
        """添加池级别完成回调，当所有任务完成时执行。

        回调函数会在主线程中执行（如果存在Qt应用），在以下情况触发：
        - 所有活跃任务已完成
        - 所有待处理任务已处理

        可以注册多个回调，它们将按注册顺序执行。

        Args:
            callback: 回调函数，签名为 callback()，无参数。

        Note:
            - 回调在主线程执行，可安全更新UI
            - 不需要调用 shutdown() 即可触发回调（所有任务完成时自动触发）
            - 如果添加回调时池已完成，回调会立即执行
            - 多个回调按注册顺序依次执行
            - 池的自引用机制确保即使池是局部变量，也会等待回调执行完毕

        Example:
            >>> pool = QThreadPoolExecutor(max_workers=2)
            >>> pool.add_done_callback(lambda: print("所有任务完成！"))
            >>> # 提交任务...
            >>> # 任务完成后自动触发回调，无需调用 shutdown()
        """
        with self._callbacks_lock:
            self._done_callbacks.append(callback)

    def add_failure_callback(self, callback: Callable) -> None:
        """添加任务级别失败回调，当任何任务失败时执行。

        回调函数会在主线程中执行（如果存在Qt应用），对每个失败的任务调用一次。
        可以注册多个回调。

        Args:
            callback: 回调函数，签名为 callback(exception) 或 callback()。

        Note:
            - 回调在主线程执行，可安全更新UI
            - 每个失败任务触发一次所有注册的回调
            - 支持无参数和单参数两种签名

        Example:
            >>> pool = QThreadPoolExecutor(max_workers=2)
            >>> pool.add_failure_callback(lambda e: print(f"任务失败: {e}"))
            >>> pool.add_failure_callback(lambda: print("检测到失败"))
            >>> future = pool.submit(lambda: 1/0)  # 触发两个回调
        """
        # 验证回调签名（0或1个参数）
        param_count = self._validate_callback(callback, "failure_callback")
        if param_count > 1:
            raise ValueError(
                "failure_callback must accept 0 or 1 parameter, "
                f"but {param_count} parameters were detected"
            )
        with self._callbacks_lock:
            self._failure_callbacks.append((callback, param_count))

    add_exception_callback = add_done_callback  # 别名

    def _is_pool_complete(self) -> bool:
        """检查线程池是否已完成所有工作。

        Returns:
            bool: 如果没有活跃任务且没有待处理任务，返回True。

        Note:
            不再要求池已关闭 (shutdown)，这样即使没有调用 shutdown()，
            当所有任务完成时也会触发 done callbacks。这对于 GUI 中的
            局部变量池特别有用。
        """
        return (
                len(self._active_futures) == 0
                and len(self._pending_tasks) == 0
        )

    def _execute_done_callbacks(self) -> None:
        """执行所有池级别完成回调。

        Note:
            - 只执行一次，通过 _done_callbacks_executed 标志防止重复
            - 在主线程（Qt应用存在时）或当前线程执行
        """
        # 防止重复执行
        if self._done_callbacks_executed:
            return
        self._done_callbacks_executed = True

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()

        # 复制回调列表避免迭代时修改
        with self._callbacks_lock:
            callbacks_copy = list(self._done_callbacks)

        for callback in callbacks_copy:
            try:
                if app is not None:
                    # Qt模式：在主线程事件循环中执行
                    QTimer.singleShot(0, callback)
                else:
                    # 非Qt模式：直接执行
                    callback()
            except Exception as e:
                print(f"Error in pool done callback: {e}", file=sys.stderr)

        # GC BUG FIX: Release self-reference AFTER done callbacks execute
        # Pool no longer needs to keep itself alive once all work is complete and callbacks fired
        # This allows Python GC to collect the pool if there are no external references
        # CRITICAL: In Qt mode, callbacks are scheduled via QTimer.singleShot(0, ...)
        # We must delay the self-reference release until those callbacks actually execute
        if app is not None:
            # Qt mode: Delay self-reference release until after callbacks execute
            # Use longer delay (100ms) to ensure all callbacks complete
            QTimer.singleShot(100, self._release_self_reference)
        else:
            # Non-Qt mode: Callbacks already executed, release immediately
            self._self_reference = None

    def _release_self_reference(self) -> None:
        """Release pool self-reference after callbacks have executed."""
        self._self_reference = None

    def _validate_callback(self, callback: Callable, name: str) -> int:
        """验证回调函数签名并返回参数数量。

        Args:
            callback: 要验证的回调函数。
            name: 回调名称，用于错误消息。

        Returns:
            int: 回调函数需要的必需参数数量。

        Raises:
            TypeError: 如果callback不可调用。
            ValueError: 如果无法检查callback签名。
        """
        if not callable(callback):
            raise TypeError(f"{name} must be callable")

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

            # 检查是否有可变参数 (*args, **kwargs)
            has_var_positional = any(p.kind == p.VAR_POSITIONAL for p in params)
            has_var_keyword = any(p.kind == p.VAR_KEYWORD for p in params)

            # 如果有可变参数，返回最小参数数量
            if has_var_positional or has_var_keyword:
                return required_param_count

            return required_param_count

        except Exception as e:
            raise ValueError(f"Cannot inspect {name} signature: {e}") from e

    def _call_failure_callbacks(self, exception: Exception) -> None:
        """执行所有任务失败回调。

        Args:
            exception: 任务抛出的异常对象。
        """
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()

        # 复制回调列表避免迭代时修改
        with self._callbacks_lock:
            callbacks_copy = list(self._failure_callbacks)

        for callback, param_count in callbacks_copy:
            try:
                if app is None:
                    # 非Qt模式：直接执行
                    if param_count == 0:
                        callback()
                    else:
                        callback(exception)
                elif param_count == 0:
                    QTimer.singleShot(0, callback)
                else:
                    # 使用lambda捕获exception，避免闭包问题
                    QTimer.singleShot(0, lambda e=exception, cb=callback: cb(e))
            except Exception as e:
                print(f"Error in pool failure callback: {e}", file=sys.stderr)

    @staticmethod
    def as_completed(
            fs: Iterable["QThreadWithReturn"], timeout: float = -1
    ) -> Iterator["QThreadWithReturn"]:
        """返回一个迭代器，按完成顺序生成 Future 对象。

        Args:
            fs: QThreadWithReturn 对象的可迭代集合。
            timeout: 等待的最大秒数。<=0 表示无超时。

        Yields:
            QThreadWithReturn: 按完成顺序返回的 Future 对象。

        Raises:
            TimeoutError: 如果在 timeout 秒内没有 Future 完成。
            TypeError: 如果 timeout 不是数字类型。

        Example:
            >>> futures = [pool.submit(task, i) for i in range(5)]
            >>> for future in QThreadPoolExecutor.as_completed(futures):
            ...     result = future.result()
            ...     print(f"Task completed with result: {result}")
        """
        import time

        # 验证 timeout 参数类型
        if not isinstance(timeout, (int, float)):
            raise TypeError(f"timeout must be a number, got {type(timeout).__name__}")

        futures = set(fs)
        done = set()
        start_time = time.monotonic() if timeout > 0 else None
        while futures:
            for fut in list(futures):
                if fut.done():
                    futures.remove(fut)
                    done.add(fut)
                    yield fut
            if not futures:
                break
            if timeout > 0:
                elapsed = time.monotonic() - start_time
                if elapsed > timeout:
                    raise TimeoutError()
            # CRITICAL FIX: Must process Qt events to allow futures to complete
            # Previously just slept, preventing signal/callback execution
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.processEvents()  # Process events to allow futures to complete
            time.sleep(0.001)  # 1ms minimum delay to avoid CPU spinning


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

        # SECURITY FIX: Cleanup re-entry protection
        self._cleanup_in_progress: bool = False
        self._cleanup_lock: threading.Lock = threading.Lock()

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
                self._is_force_stopped = True
                with contextlib.suppress(AttributeError):
                    if not self._thread.wait(100):
                        self._thread.terminate()
                        self._thread.wait(1000)
                self._thread_really_finished = True
                # Fix #6: Clear callbacks in force-stop path
                self._clear_callbacks()
                self._cleanup_resources()
            else:
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

        # 转换为整数毫秒
        timeout_ms = int(timeout_ms)
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
        timeout_ms = int(timeout_ms)
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
        timeout_ms = int(timeout_ms)

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
        timeout_ms = int(timeout_ms)
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

        # STRESS TEST FIX: Set completion event FIRST before any other operations
        # This ensures concurrent result() calls can proceed immediately
        self._completion_event.set()

        self._cleanup_timeout_timer()

        # 请求线程退出
        if self._thread:
            self._thread.quit()

        # Fix #3: Fix non-Qt mode QTimer usage - check for Qt application before using QTimer
        # STRESS TEST FIX: Execute callbacks with immediate event processing
        if self._done_callback:
            try:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance()
                callback = self._done_callback
                callback_params = self._done_callback_params
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
                    # STRESS TEST FIX: Force immediate event processing for callback execution
                    # Under high load (50+ concurrent threads), callbacks may not execute
                    # unless we explicitly process events after scheduling
                    app.processEvents()
                else:
                    # Non-Qt mode: execute directly
                    self._execute_callback_safely(
                        callback, result, callback_params, "done_callback"
                    )
            except Exception as e:
                print(f"Error scheduling done callback: {e}", file=sys.stderr)

        # 发射任务完成信号
        self.finished_signal.emit()

        # STRESS TEST FIX: Process events again after signal emission
        # Ensures signals propagate to connected slots immediately
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()

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
            if (
                    self._done_callback
                    and not self._is_cancelled
                    and not self._is_force_stopped
            ):
                self._call_callback_with_result(
                    self._done_callback,
                    result,
                    self._done_callback_params,
                    "done_callback",
                )
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

        # Fix #3: Fix non-Qt mode QTimer usage - check for Qt application before using QTimer
        if self._failure_callback:
            try:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance()
                callback = self._failure_callback
                callback_params = self._failure_callback_params
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
                print(f"Error scheduling failure callback: {e}", file=sys.stderr)

        # COUNTER LOCK FIX: Emit finished_signal for failed tasks too
        # Pool completion handler needs to be called regardless of success/failure
        self.finished_signal.emit()

        # Process events to ensure signal delivery
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()

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
                else:
                    self._failure_callback(exception)
        except Exception as e:
            print(f"Error in failure callback: {e}", file=sys.stderr)

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
                with contextlib.suppress(RuntimeError, TypeError):
                    self._worker._finished_signal.disconnect()
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
                with contextlib.suppress(RuntimeError, TypeError):
                    self._thread.started.disconnect()
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
        self._done_callback = None
        self._failure_callback = None

    def _cleanup_resources(self) -> None:
        """清理资源 - SECURITY HARDENED: Thread-safe with re-entry protection"""
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

            self._cleanup_timeout_timer()

            # CRITICAL SECURITY FIX: Hardened mutex handling with timeout and logging
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

            # Fix #2: Clear callback function references to avoid circular references
            # CRITICAL: Do NOT clear callbacks here during normal completion!
            # Callbacks are captured in QTimer closures and clearing them serves no purpose.
            # Only clear in early-cancel paths (lines 499, 524) where callbacks won't execute.
            # self._clear_callbacks()  # ← DISABLED - breaks callback execution

            # Fix #5: Explicitly delete Qt synchronization objects AFTER unlocking
            # Don't delete immediately - let Python's garbage collector handle it
            # This prevents issues when cleanup is called multiple times
            try:
                # Just clear the completion event, don't delete objects
                if (
                        hasattr(self, "_completion_event")
                        and self._completion_event is not None
                ):
                    self._completion_event.clear()
            except Exception as e:
                print(
                    f"Warning: Failed to clear completion event: {e}", file=sys.stderr
                )

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

        # 调用完成回调
        if self._done_callback and not self._is_cancelled:
            try:
                self._call_callback_with_result(
                    self._done_callback,
                    result,
                    self._done_callback_params,
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

        # 调用失败回调
        if self._failure_callback and not self._is_cancelled:
            try:
                # 对于异常回调，总是传递异常对象（如果callback需要的话）
                if self._failure_callback_params == 0:
                    self._failure_callback()
                else:
                    self._failure_callback(exception)
            except Exception as e:
                print(f"Error in failure callback: {e}", file=sys.stderr)
