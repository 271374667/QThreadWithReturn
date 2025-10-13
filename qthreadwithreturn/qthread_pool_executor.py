import contextlib
import inspect
import sys
import threading
from concurrent.futures import CancelledError, TimeoutError
from typing import Callable, Optional, Iterable, Iterator, Set, Tuple

from PySide6.QtCore import QTimer

from qthreadwithreturn.qthread_with_return import QThreadWithReturn


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
        self._waiting_for_shutdown = False  # Track if shutdown(wait=True) is in progress

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

        # DEADLOCK FIX: Don't start new tasks during shutdown(wait=True)
        # If shutdown(wait=True) is waiting for tasks to complete, starting new tasks
        # creates an infinite loop where the wait never completes
        if self._waiting_for_shutdown:
            return

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
            force_stop: bool = False,
            *,
            cancel_futures: bool = False,
            wait: bool = False,
    ) -> None:
        """关闭线程池。

        Args:
            force_stop: 如果为 True，立即强制终止所有任务（最高优先级），忽略其他参数。
            cancel_futures: 如果为 True，取消所有待处理的任务。
            wait: 如果为 True，阻塞直到任务完成。会发出警告，因为可能导致UI冻结。

        优先级说明:
            1. force_stop=True: 最高优先级，立即强制停止所有任务并触发回调，忽略其他参数
            2. cancel_futures=True: 取消待处理任务，等待活跃任务完成（如果wait=True）
            3. wait=True: 阻塞直到所有任务完成（会发出警告）

        Note:
            - shutdown 后不能再提交新任务
            - force_stop=True 会立即标记所有任务为完成并触发池级别回调
            - wait=True 会发出警告，因为可能导致UI无响应
            - 池级别完成回调会在所有任务完成后自动触发

        Example:
            >>> pool = QThreadPoolExecutor(max_workers=2)
            >>> # 提交一些任务...
            >>> pool.shutdown(force_stop=True)  # 立即强制停止所有任务
            ...
            >>> # 取消待处理任务，等待活跃任务
            >>> pool.shutdown(cancel_futures=True, wait=True)
            ...
            >>> # UI应用中推荐使用异步关闭
            >>> pool.shutdown()  # 不阻塞主线程
        """
        import warnings
        import time
        from PySide6.QtWidgets import QApplication

        # PRIORITY 1: force_stop 最高优先级路径 - 改进版
        if force_stop:
            # 发出 wait 警告（如果设置了 wait=True）
            if wait:
                warnings.warn(
                    "Setting wait=True will block until all tasks complete, "
                    "which may cause UI freezing and application unresponsiveness.",
                    UserWarning,
                    stacklevel=2
                )

            # 标记池为已关闭
            with self._shutdown_lock:
                self._shutdown = True
                # 收集所有任务（pending + active）
                all_tasks = list(self._pending_tasks) + list(self._active_futures)
                # 立即清空任务列表
                self._pending_tasks.clear()
                self._active_futures.clear()

            # 对每个任务进行分阶段强制停止处理
            for future in all_tasks:
                try:
                    # 第一阶段：请求中断（给线程优雅退出的机会）
                    if future._thread and future._thread.isRunning():
                        future._thread.requestInterruption()
                        if hasattr(future, '_worker') and future._worker:
                            future._worker._should_stop = True

                        # 短暂等待（100ms）
                        if future._thread.wait(100):
                            # 优雅退出成功
                            with contextlib.suppress(Exception):
                                future._mutex.lock()
                                try:
                                    if not future._is_finished:
                                        future._is_finished = True
                                        future._is_force_stopped = True
                                        future._wait_condition.wakeAll()
                                finally:
                                    future._mutex.unlock()
                            continue

                        # 第二阶段：quit（半优雅退出）
                        future._thread.quit()
                        if future._thread.wait(200):
                            # 半优雅退出成功
                            with contextlib.suppress(Exception):
                                future._mutex.lock()
                                try:
                                    if not future._is_finished:
                                        future._is_finished = True
                                        future._is_force_stopped = True
                                        future._wait_condition.wakeAll()
                                finally:
                                    future._mutex.unlock()
                            continue

                        # 第三阶段：terminate（最后手段）
                        with contextlib.suppress(Exception):
                            future._thread.terminate()
                            # 关键：等待足够长时间确保线程真正终止
                            future._thread.wait(2000)

                    # 安全地标记状态（所有退出路径都执行）
                    with contextlib.suppress(Exception):
                        future._mutex.lock()
                        try:
                            if not future._is_finished:
                                future._is_finished = True
                                future._is_force_stopped = True
                                future._wait_condition.wakeAll()
                        finally:
                            future._mutex.unlock()

                    # 断开池管理的信号连接（防止访问已销毁对象）
                    with contextlib.suppress(Exception):
                        if hasattr(future, "_pool_connection"):
                            future.finished_signal.disconnect(future._pool_connection)
                            del future._pool_connection
                        if hasattr(future, "_pool_managed"):
                            del future._pool_managed

                    # 等待 Qt 事件循环处理信号断开
                    app = QApplication.instance()
                    if app is not None:
                        app.processEvents()

                except Exception as e:
                    print(f"Error force-stopping task: {e}", file=sys.stderr)

            # 统一处理 Qt 事件和回调
            app = QApplication.instance()
            if app is not None:
                # 多次处理事件，确保所有 deleteLater 和信号完成
                for _ in range(10):
                    app.processEvents()
                    time.sleep(0.01)  # 10ms

                # 最终处理确保回调完成
                time.sleep(0.05)  # 50ms
                app.processEvents()

            # 检查并调用池级别完成回调
            if self._is_pool_complete():
                self._execute_done_callbacks()

            # force_stop 立即返回，不管其他参数
            return

        # PRIORITY 2: 标准路径 - cancel_futures 和 wait
        # 发出 wait 警告
        if wait:
            warnings.warn(
                "Setting wait=True will block until all tasks complete, "
                "which may cause UI freezing and application unresponsiveness.",
                UserWarning,
                stacklevel=2
            )

        # 标准关闭逻辑
        already_shutdown = False
        with self._shutdown_lock:
            if self._shutdown:
                already_shutdown = True
            else:
                self._shutdown = True

            # 取消待处理任务（如果 cancel_futures=True）
            if not already_shutdown and cancel_futures:
                pending_copy = list(self._pending_tasks)
                for future in pending_copy:
                    with contextlib.suppress(Exception):
                        future.cancel(force_stop=False)
                self._pending_tasks.clear()

            # 复制活跃任务列表
            active_copy = list(self._active_futures)

        # 如果 wait=True，等待所有任务完成（包括新启动的任务）
        if wait:
            # DEADLOCK FIX: Set flag to prevent _try_start_tasks from starting cancelled tasks
            # Only set this when cancel_futures=True, otherwise pending tasks should be allowed to start
            if cancel_futures:
                self._waiting_for_shutdown = True
            try:
                start_time = time.time()
                max_wait_time = 60.0  # 增加到 60 秒以支持长时间运行的任务

                # 轮询等待所有任务完成
                # 注意：不能只等待 active_copy，因为 pending_tasks 会被动态启动
                while (time.time() - start_time) < max_wait_time:
                    # 获取当前所有活跃和待处理的任务（动态检查）
                    with self._counter_lock:
                        current_active = list(self._active_futures)
                        current_pending = list(self._pending_tasks)

                    # 如果没有未完成的任务，退出
                    # HANG FIX: Check if futures are done, not just if set is empty
                    # Cancelled futures may still be in _active_futures but are done()
                    active_not_done = [f for f in current_active if not f.done()]
                    if not active_not_done and not current_pending:
                        break

                    # 检查并等待活跃任务
                    for future in current_active:
                        try:
                            if not future.done():
                                future.wait(50, force_stop=False)  # 50ms 非阻塞检查
                        except Exception:
                            pass  # 忽略异常，继续检查其他任务

                    # 处理 Qt 事件（允许新任务启动和回调执行）
                    app = QApplication.instance()
                    if app is not None:
                        app.processEvents()

                    time.sleep(0.01)

                # 清理引用
                self._active_futures.clear()
                self._pending_tasks.clear()

                # 处理剩余的 Qt 事件
                app = QApplication.instance()
                if app is not None:
                    app.processEvents()
                    time.sleep(0.05)
                    app.processEvents()

                # 断开信号连接
                for future in active_copy:
                    with contextlib.suppress(RuntimeError, TypeError):
                        if hasattr(future, "_pool_connection"):
                            future.finished_signal.disconnect(future._pool_connection)
                            del future._pool_connection
                        if hasattr(future, "_pool_managed"):
                            del future._pool_managed
            finally:
                # DEADLOCK FIX: Always clear the flag after wait completes
                self._waiting_for_shutdown = False

        # 检查并调用池级别完成回调
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
                    # CALLBACK EXCEPTION FIX: Wrap callback to catch exceptions
                    # Callbacks executed via QTimer.singleShot() can raise exceptions during processEvents()
                    # which would propagate to the caller (e.g., shutdown()). We must catch and log them.
                    def safe_callback(cb=callback):
                        try:
                            cb()
                        except Exception as e:
                            print(f"Error in pool done callback: {e}", file=sys.stderr)

                    QTimer.singleShot(0, safe_callback)
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
                    # CALLBACK EXCEPTION FIX: Wrap callback to catch exceptions
                    def safe_callback(cb=callback):
                        try:
                            cb()
                        except Exception as e:
                            print(f"Error in pool failure callback: {e}", file=sys.stderr)

                    QTimer.singleShot(0, safe_callback)
                else:
                    # CALLBACK EXCEPTION FIX: Wrap callback to catch exceptions
                    # 使用lambda捕获exception，避免闭包问题
                    def safe_callback_with_exc(e=exception, cb=callback):
                        try:
                            cb(e)
                        except Exception as err:
                            print(f"Error in pool failure callback: {err}", file=sys.stderr)

                    QTimer.singleShot(0, safe_callback_with_exc)
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
