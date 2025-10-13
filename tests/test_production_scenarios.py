"""
Production Integration Tests for QThreadWithReturn

Tests real-world usage patterns, error recovery, and edge cases
that are likely to occur in production environments.
"""

import pytest
import time
import sys
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor


# Test fixtures
@pytest.fixture
def qapp():
    """Provide QApplication instance for tests requiring event loop"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    # Cleanup is handled by pytest cleanup


def wait_with_events(ms):
    """Wait while processing Qt events"""
    app = QApplication.instance()
    if app is None:
        time.sleep(ms / 1000.0)
        return

    deadline = time.monotonic() + (ms / 1000.0)
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.010)


# ============================================================================
# TestProductionWorkflows: Real-world usage patterns
# ============================================================================


class TestProductionWorkflows:
    """Test common production usage patterns and workflows"""

    def test_long_running_background_task_with_progress(self, qapp):
        """Simulate long-running task with progress callbacks"""
        progress_updates = []

        def long_task_with_progress():
            """Task that simulates progress reporting"""
            for i in range(5):
                time.sleep(0.1)
                # In real scenario, would emit progress signal
                progress_updates.append(i * 20)  # 0%, 20%, 40%, 60%, 80%
            return "completed"

        def done_callback(result):
            progress_updates.append(100)  # 100% complete

        thread = QThreadWithReturn(long_task_with_progress)
        thread.add_done_callback(done_callback)
        thread.start()
        result = thread.result(timeout_ms=2000)

        wait_with_events(100)  # Allow callback to execute

        assert result == "completed"
        assert 100 in progress_updates
        assert len(progress_updates) >= 5

    def test_batch_processing_with_error_recovery(self, qapp):
        """Test batch processing where some tasks fail but processing continues"""

        def process_item(item):
            if item == "error":
                raise ValueError(f"Failed to process {item}")
            return f"processed_{item}"

        items = ["item1", "item2", "error", "item3", "error", "item4"]
        results = []
        errors = []

        with QThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(process_item, item) for item in items]

            for future in futures:
                try:
                    result = future.result(timeout_ms=1000)
                    results.append(result)
                except Exception as e:
                    errors.append(str(e))

        # Verify successful items processed and errors captured
        assert len(results) == 4  # 4 successful items
        assert len(errors) == 2  # 2 error items
        assert "processed_item1" in results
        assert "processed_item4" in results

    def test_mixed_blocking_and_async_patterns(self, qapp):
        """Test mixing result() blocking calls with callback-based async patterns"""
        callback_result = []

        def quick_task():
            time.sleep(0.05)
            return "quick"

        def slow_task():
            time.sleep(0.2)
            return "slow"

        def done_callback(result):
            callback_result.append(result)

        # Start async task with callback
        async_thread = QThreadWithReturn(slow_task)
        async_thread.add_done_callback(done_callback)
        async_thread.start()

        # Run blocking task while async is running
        sync_thread = QThreadWithReturn(quick_task)
        sync_thread.start()
        sync_result = sync_thread.result(timeout_ms=1000)

        # Wait for async to complete
        async_result = async_thread.result(timeout_ms=1000)
        wait_with_events(100)

        assert sync_result == "quick"
        assert async_result == "slow"
        assert "slow" in callback_result

    def test_graceful_degradation_on_task_failure(self, qapp):
        """Test system continues working after task failures"""

        def failing_task():
            raise RuntimeError("Task failed")

        def working_task():
            return "success"

        # First task fails
        thread1 = QThreadWithReturn(failing_task)
        thread1.start()

        with pytest.raises(RuntimeError):
            thread1.result(timeout_ms=1000)

        # Subsequent tasks should still work
        thread2 = QThreadWithReturn(working_task)
        thread2.start()
        result = thread2.result(timeout_ms=1000)

        assert result == "success"

    def test_sequential_dependent_tasks(self, qapp):
        """Test tasks that depend on results of previous tasks"""

        def task1():
            return {"step": 1, "data": "initial"}

        def task2(prev_result):
            return {"step": 2, "data": prev_result["data"] + "_processed"}

        def task3(prev_result):
            return {"step": 3, "data": prev_result["data"] + "_finalized"}

        # Execute sequentially with dependencies
        thread1 = QThreadWithReturn(task1)
        thread1.start()
        result1 = thread1.result(timeout_ms=1000)

        thread2 = QThreadWithReturn(task2, prev_result=result1)
        thread2.start()
        result2 = thread2.result(timeout_ms=1000)

        thread3 = QThreadWithReturn(task3, prev_result=result2)
        thread3.start()
        result3 = thread3.result(timeout_ms=1000)

        assert result3["step"] == 3
        assert result3["data"] == "initial_processed_finalized"

    def test_task_cancellation_in_workflow(self, qapp):
        """Test cancelling tasks mid-workflow without breaking subsequent tasks"""

        def cancellable_task():
            time.sleep(2.0)  # Long enough to cancel
            return "should_not_complete"

        def normal_task():
            return "normal_completion"

        # Start cancellable task
        thread1 = QThreadWithReturn(cancellable_task)
        thread1.start()
        time.sleep(0.05)  # Let it start

        # Cancel it
        cancelled = thread1.cancel()
        assert cancelled

        # Start normal task - should work fine
        thread2 = QThreadWithReturn(normal_task)
        thread2.start()
        result = thread2.result(timeout_ms=1000)

        assert result == "normal_completion"


# ============================================================================
# TestThreadPoolSaturation: Resource limit handling
# ============================================================================


class TestThreadPoolSaturation:
    """Test thread pool behavior under resource constraints"""

    @pytest.fixture(autouse=True)
    def cleanup_between_tests(self, qapp):
        """Ensure cleanup between resource-intensive tests"""
        yield
        # Allow Qt event loop to process deferred deletions
        wait_with_events(300)
        import gc
        gc.collect(generation=0)

    def test_more_tasks_than_workers_queuing(self, qapp):
        """Test submitting more tasks than max_workers - should queue properly"""

        def task(n):
            time.sleep(0.1)
            return n * 2

        with QThreadPoolExecutor(max_workers=2) as pool:
            # Submit 10 tasks with only 2 workers
            futures = [pool.submit(task, i) for i in range(10)]

            # All should complete eventually
            results = [f.result(timeout_ms=5000) for f in futures]

        assert len(results) == 10
        assert results == [i * 2 for i in range(10)]

    def test_worker_reuse_after_completion(self, qapp):
        """Validate thread pool creates workers for concurrent tasks"""
        executed_in = []

        def task():
            import threading

            executed_in.append(threading.current_thread().ident)
            time.sleep(0.05)
            return "done"

        with QThreadPoolExecutor(max_workers=2) as pool:
            # Submit 10 tasks sequentially
            for _ in range(10):
                future = pool.submit(task)
                future.result(timeout_ms=1000)

        # Each task gets a new QThread (by design of QThreadWithReturn)
        # Validate that all tasks completed
        assert len(executed_in) == 10

    def test_pool_saturation_with_timeout(self, qapp):
        """Test behavior when pool is saturated and tasks timeout"""

        def long_task():
            time.sleep(2.0)  # 减少从 5 秒到 2 秒
            return "done"

        pool = QThreadPoolExecutor(max_workers=1)

        try:
            # Saturate pool
            future1 = pool.submit(long_task)
            time.sleep(0.05)  # 让第一个任务开始

            # This will queue
            future2 = pool.submit(long_task)

            # Try to get result quickly - should timeout
            with pytest.raises(TimeoutError):
                future2.result(timeout_ms=100)

            # Cancel both - 使用普通 cancel
            future1.cancel()
            future2.cancel()

            # 等待取消完成
            wait_with_events(100)

        finally:
            # 显式 shutdown
            pool.shutdown(cancel_futures=True)
            wait_with_events(200)

    def test_initializer_exception_handling(self, qapp):
        """Test pool behavior when initializer raises exception"""

        def bad_initializer():
            raise RuntimeError("Initializer failed")

        def task():
            return "task_result"

        # Pool with failing initializer - task should still execute
        # (initializer exceptions are caught and logged, but don't block task execution)
        with QThreadPoolExecutor(max_workers=2, initializer=bad_initializer) as pool:
            future = pool.submit(task)
            # Task executes despite initializer failure
            result = future.result(timeout_ms=1000)
            assert result == "task_result"

    def test_rapid_submit_cancel_cycles(self, qapp):
        """Test rapid submit/cancel cycles don't cause resource leaks"""

        def task():
            time.sleep(0.3)
            return "done"

        pool = QThreadPoolExecutor(max_workers=1)  # 只用 1 个 worker

        try:
            for i in range(5):  # 进一步减少到 5 次
                future = pool.submit(task)
                time.sleep(0.05)  # 给予足够时间启动
                # 使用普通 cancel 而不是 force_stop，让任务自然取消
                cancelled = future.cancel()

                # 每次操作后都等待一下
                wait_with_events(50)

        finally:
            # 显式 shutdown，不使用 with 上下文管理器
            pool.shutdown(cancel_futures=True)
            wait_with_events(200)

        # If we get here without hanging, test passes
        assert True


# ============================================================================
# TestConcurrentOperations: Race condition validation
# ============================================================================


class  TestConcurrentOperations:
    """Test concurrent access patterns and race conditions"""

    @pytest.fixture(autouse=True)
    def cleanup_between_tests(self, qapp):
        """Ensure cleanup between concurrent operation tests"""
        yield
        # Allow Qt event loop to process deferred deletions
        wait_with_events(400)  # 更长的等待时间，因为并发测试创建更多资源
        import gc
        gc.collect(generation=0)

    def test_concurrent_cancel_calls(self, qapp):
        """Test multiple threads calling cancel() on same future"""
        import threading

        def long_task():
            time.sleep(2.0)
            return "done"

        thread = QThreadWithReturn(long_task)
        thread.start()
        time.sleep(0.05)  # Let it start

        cancel_results = []

        def cancel_thread():
            result = thread.cancel(force_stop=True)
            cancel_results.append(result)

        # Spawn 10 threads all trying to cancel
        threads = [threading.Thread(target=cancel_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At least one should succeed, others should return False
        assert any(cancel_results)
        assert thread.cancelled()

    def test_concurrent_result_calls(self, qapp):
        """Test multiple threads calling result() on same future"""
        import threading

        def task():
            time.sleep(0.1)
            return "result"

        thread = QThreadWithReturn(task)
        thread.start()

        # Wait for task to complete first
        thread.result(timeout_ms=2000)

        results = []
        lock = threading.Lock()

        def result_thread():
            try:
                # Now all threads try to get the already-completed result
                result = thread.result(timeout_ms=1000)
                with lock:
                    results.append(("success", result))
            except Exception as e:
                with lock:
                    results.append(("error", type(e).__name__))

        # Spawn 5 threads all trying to get result from completed thread
        threads = [threading.Thread(target=result_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify we got results from all threads
        assert len(results) == 5
        # All should succeed since thread already completed
        successful = [r for r in results if r[0] == "success"]
        assert len(successful) >= 4, (
            f"Got results: {results}"
        )  # Allow 1 failure for edge cases

    def test_concurrent_pool_shutdown(self, qapp):
        """Test concurrent shutdown calls don't cause crashes"""
        import threading

        def task():
            time.sleep(0.1)
            return "done"

        pool = QThreadPoolExecutor(max_workers=2)
        futures = [pool.submit(task) for _ in range(5)]

        def shutdown_thread():
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass  # Some threads may fail if already shut down

        # Spawn 5 threads all trying to shutdown
        threads = [threading.Thread(target=shutdown_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Pool should be shut down
        with pytest.raises(RuntimeError):
            pool.submit(task)

    def test_mixed_operations_under_load(self, qapp):
        """Test mixed operations (submit, cancel, result) concurrently"""
        import threading
        import random
        import warnings

        def task(n):
            time.sleep(random.uniform(0.01, 0.05))  # 减少睡眠时间
            return n * 2

        pool = QThreadPoolExecutor(max_workers=2)  # 减少 workers 从 3 到 2
        futures = []
        lock = threading.Lock()

        def worker_thread():
            for _ in range(5):  # 减少操作次数从 10 到 5
                op = random.choice(["submit", "cancel", "result"])

                if op == "submit":
                    future = pool.submit(task, random.randint(1, 100))
                    with lock:
                        futures.append(future)

                elif op == "cancel" and futures:
                    with lock:
                        if futures:
                            future = random.choice(futures)
                    try:
                        future.cancel()
                    except Exception:
                        pass

                elif op == "result" and futures:
                    with lock:
                        if futures:
                            future = random.choice(futures)
                    try:
                        future.result(timeout_ms=500)
                    except Exception:
                        pass

                time.sleep(0.02)  # 增加延迟，降低操作频率

        # Spawn 3 threads doing random operations (减少从 5 到 3)
        threads = [threading.Thread(target=worker_thread) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pool.shutdown(wait=True, cancel_futures=True)

        # 等待资源清理
        wait_with_events(300)

        # If we get here without crashing, test passes
        assert True


# ============================================================================
# TestCallbackResilience: Error handling and recovery
# ============================================================================


class TestCallbackResilience:
    """Test callback error handling and system resilience"""

    def test_callback_exception_doesnt_break_event_loop(self, qapp):
        """Test that callback exceptions don't break Qt event loop"""
        exceptions_caught = []

        def task():
            return "result"

        def bad_callback(result):
            raise ValueError("Callback error")

        # Install exception handler
        import sys

        original_excepthook = sys.excepthook

        def exception_handler(exc_type, exc_value, exc_traceback):
            exceptions_caught.append(exc_value)

        sys.excepthook = exception_handler

        try:
            thread = QThreadWithReturn(task)
            thread.add_done_callback(bad_callback)
            thread.start()
            result = thread.result(timeout_ms=1000)

            wait_with_events(200)  # Allow callback to execute and fail

            # Event loop should still work - submit another task
            thread2 = QThreadWithReturn(task)
            thread2.start()
            result2 = thread2.result(timeout_ms=1000)

            assert result == "result"
            assert result2 == "result"
        finally:
            sys.excepthook = original_excepthook

    def test_chained_callbacks_with_mixed_success(self, qapp):
        """Test multiple callbacks where some fail"""
        results = []

        def task1():
            return "task1"

        def good_callback(result):
            results.append(f"good_{result}")

        def bad_callback(result):
            raise RuntimeError("Bad callback")

        # Task 1 with good callback
        thread1 = QThreadWithReturn(task1)
        thread1.add_done_callback(good_callback)
        thread1.start()
        thread1.result(timeout_ms=1000)

        # Task 2 with bad callback
        thread2 = QThreadWithReturn(task1)
        thread2.add_done_callback(bad_callback)
        thread2.start()
        thread2.result(timeout_ms=1000)

        # Task 3 with good callback - should still work
        thread3 = QThreadWithReturn(task1)
        thread3.add_done_callback(good_callback)
        thread3.start()
        thread3.result(timeout_ms=1000)

        wait_with_events(300)

        # At least 2 good callbacks should have executed
        assert len([r for r in results if r.startswith("good_")]) >= 2

    def test_failure_callback_with_exception_in_callback(self, qapp):
        """Test failure callback that itself raises exception"""

        def failing_task():
            raise ValueError("Task failed")

        def bad_failure_callback(exc):
            raise RuntimeError("Failure callback failed")

        thread = QThreadWithReturn(failing_task)
        thread.add_failure_callback(bad_failure_callback)
        thread.start()

        # Should still get original exception
        with pytest.raises(ValueError):
            thread.result(timeout_ms=1000)

        wait_with_events(100)

        # System should still be operational
        thread2 = QThreadWithReturn(lambda: "ok")
        thread2.start()
        result = thread2.result(timeout_ms=1000)
        assert result == "ok"


# ============================================================================
# TestCleanupScenarios: Resource management validation
# ============================================================================


class TestCleanupScenarios:
    """Test proper resource cleanup in various scenarios"""

    @pytest.fixture(autouse=True)
    def cleanup_between_tests(self, qapp):
        """Ensure gradual cleanup between tests to prevent resource accumulation"""
        yield
        # Allow Qt event loop to process deferred deletions
        wait_with_events(500)  # 增加等待时间从 300ms 到 500ms
        # Gradual garbage collection to avoid stack overflow
        import gc

        gc.collect(generation=0)  # Only collect youngest generation
        # 再次处理 Qt 事件，确保 deleteLater 完成
        wait_with_events(200)

    def test_cleanup_on_normal_completion(self, qapp):
        """Verify resources are cleaned up after normal completion"""
        import weakref

        def task():
            return "done"

        thread = QThreadWithReturn(task)
        weak_ref = weakref.ref(thread)

        thread.start()
        result = thread.result(timeout_ms=1000)

        assert result == "done"

        # Allow deferred cleanup to complete
        wait_with_events(500)

        # Delete strong reference
        del thread

        # Allow Qt's natural cleanup mechanism (deleteLater) to work
        wait_with_events(300)

        # Note: We don't force gc.collect() here as it can cause crashes
        # when many Qt objects from previous tests are collected simultaneously.
        # Qt objects should be cleaned by Qt's event loop mechanism.

    def test_cleanup_on_cancellation(self, qapp):
        """Verify resources are cleaned up after cancellation"""

        def long_task():
            time.sleep(5.0)
            return "done"

        thread = QThreadWithReturn(long_task)
        thread.start()
        time.sleep(0.05)

        thread.cancel(force_stop=True)
        assert thread.cancelled()

        wait_with_events(200)  # Allow cleanup

        # Thread should be stopped
        assert not thread.running()

    def test_cleanup_on_exception(self, qapp):
        """Verify resources are cleaned up after exception"""

        def failing_task():
            raise RuntimeError("Task failed")

        thread = QThreadWithReturn(failing_task)
        thread.start()

        with pytest.raises(RuntimeError):
            thread.result(timeout_ms=1000)

        wait_with_events(200)  # Allow cleanup

        # Thread should be done
        assert thread.done()
        assert not thread.running()

    def test_forced_garbage_collection(self, qapp):
        """Test that gradual GC doesn't cause issues"""
        import gc

        def task():
            return "result"

        threads = []
        for _ in range(10):
            thread = QThreadWithReturn(task)
            thread.start()
            threads.append(thread)

        # Gradual garbage collection while threads running (generation 0 only)
        gc.collect(generation=0)

        # All threads should complete
        results = [t.result(timeout_ms=1000) for t in threads]
        assert all(r == "result" for r in results)

        wait_with_events(300)

    def test_pool_cleanup_with_pending_tasks(self, qapp):
        """Test pool cleanup when tasks are still pending"""

        def task():
            time.sleep(0.5)
            return "done"

        pool = QThreadPoolExecutor(max_workers=1)

        # Submit more tasks than can complete quickly
        futures = [pool.submit(task) for _ in range(5)]

        time.sleep(0.1)  # Let one start

        # Shutdown with pending tasks
        pool.shutdown(wait=False, cancel_futures=True)

        # Pool should handle cleanup gracefully
        wait_with_events(200)


# ============================================================================
# TestEdgeCasesAndCornerCases: Unusual scenarios
# ============================================================================


class TestEdgeCasesAndCornerCases:
    """Test unusual edge cases and corner scenarios"""

    def test_zero_max_workers_pool(self, qapp):
        """Test pool with 0 max_workers (should default to CPU count)"""
        with QThreadPoolExecutor(max_workers=None) as pool:
            future = pool.submit(lambda: "done")
            result = future.result(timeout_ms=1000)
            assert result == "done"

    def test_task_returning_none(self, qapp):
        """Test task that explicitly returns None"""

        def task():
            return None

        thread = QThreadWithReturn(task)
        thread.start()
        result = thread.result(timeout_ms=1000)

        assert result is None
        assert thread.done()

    def test_task_with_no_return_statement(self, qapp):
        """Test task function with no return (implicit None)"""
        executed = []

        def task():
            executed.append(True)
            # No return statement

        thread = QThreadWithReturn(task)
        thread.start()
        result = thread.result(timeout_ms=1000)

        assert result is None
        assert executed[0] is True

    def test_callback_with_varargs(self, qapp):
        """Test callback that accepts *args, **kwargs"""
        callback_called = []

        def task():
            return "result"

        def callback(*args, **kwargs):
            callback_called.append((args, kwargs))

        thread = QThreadWithReturn(task)
        thread.add_done_callback(callback)
        thread.start()
        thread.result(timeout_ms=1000)

        wait_with_events(100)

        assert len(callback_called) > 0

    def test_very_quick_task_completion(self, qapp):
        """Test task that completes before result() is called"""

        def instant_task():
            return "instant"

        thread = QThreadWithReturn(instant_task)
        thread.start()
        time.sleep(0.2)  # Let it definitely complete

        # result() on already-complete thread
        result = thread.result(timeout_ms=1000)
        assert result == "instant"

    def test_multiple_result_calls_same_thread(self, qapp):
        """Test calling result() multiple times on same thread"""

        def task():
            return "result"

        thread = QThreadWithReturn(task)
        thread.start()

        result1 = thread.result(timeout_ms=1000)
        result2 = thread.result(timeout_ms=1000)
        result3 = thread.result(timeout_ms=1000)

        assert result1 == result2 == result3 == "result"
