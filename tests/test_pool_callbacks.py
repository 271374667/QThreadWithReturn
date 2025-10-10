"""Test suite for QThreadPoolExecutor callback methods.

This module tests the pool-level callback functionality:
- add_done_callback(): Called when ALL tasks complete
- add_failure_callback(): Called for EACH failed task
- Context manager removal validation
"""

import pytest
import time
import threading
from PySide6.QtWidgets import QApplication
from qthreadwithreturn import QThreadPoolExecutor


def wait_with_events(ms):
    """Wait specified time while processing Qt events to allow callbacks to execute."""
    app = QApplication.instance()
    if app is None:
        time.sleep(max(0.001, ms / 1000.0))
        return

    deadline = time.monotonic() + (ms / 1000.0)
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.010)  # 10ms intervals


@pytest.mark.usefixtures("qapp_session")
class TestQThreadPoolExecutorCallbacks:
    """Test QThreadPoolExecutor callback methods."""

    # ========== Context Manager Removal Tests ==========

    @pytest.mark.unit
    def test_context_manager_removed(self):
        """Verify context manager methods no longer exist."""
        pool = QThreadPoolExecutor(max_workers=2)
        try:
            assert not hasattr(pool, '__enter__'), "Pool should not have __enter__ method"
            assert not hasattr(pool, '__exit__'), "Pool should not have __exit__ method"

            # Verify attempting to use with statement raises TypeError
            with pytest.raises(TypeError, match="does not support the context manager protocol"):
                with pool:
                    pass
        finally:
            pool.shutdown(wait=False, force_stop=True)

    # ========== add_done_callback Tests ==========

    @pytest.mark.unit
    def test_pool_done_callback_basic(self):
        """Test done callback fires when all tasks complete."""
        pool = QThreadPoolExecutor(max_workers=2)
        completed = {"called": False}

        def done_callback():
            completed["called"] = True

        try:
            pool.add_done_callback(done_callback)

            # Submit 3 tasks
            futures = [pool.submit(time.sleep, 0.05) for _ in range(3)]

            # Shutdown and wait
            pool.shutdown(wait=True)

            # Process Qt events
            wait_with_events(100)

            assert completed["called"] is True, "Done callback should be called after all tasks complete"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_done_callback_multiple(self):
        """Test multiple done callbacks execute in order."""
        pool = QThreadPoolExecutor(max_workers=2)
        results = []

        try:
            pool.add_done_callback(lambda: results.append(1))
            pool.add_done_callback(lambda: results.append(2))
            pool.add_done_callback(lambda: results.append(3))

            futures = [pool.submit(time.sleep, 0.05) for _ in range(2)]
            pool.shutdown(wait=True)
            wait_with_events(100)

            assert results == [1, 2, 3], f"Callbacks should execute in order, got {results}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_done_callback_added_late(self):
        """Test callback added after tasks are submitted."""
        pool = QThreadPoolExecutor(max_workers=2)
        completed = {"called": False}

        try:
            # Submit tasks first
            futures = [pool.submit(time.sleep, 0.05) for _ in range(3)]

            # Add callback after submission
            pool.add_done_callback(lambda: completed.update(called=True))

            pool.shutdown(wait=True)
            wait_with_events(100)

            assert completed["called"] is True, "Late-added callback should still be called"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_done_callback_empty_pool(self):
        """Test done callback on pool with no tasks."""
        pool = QThreadPoolExecutor(max_workers=2)
        completed = {"called": False}

        try:
            pool.add_done_callback(lambda: completed.update(called=True))
            pool.shutdown(wait=True)
            wait_with_events(100)

            assert completed["called"] is True, "Callback should fire even with no tasks"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_done_callback_without_shutdown(self):
        """Test done callback fires when all tasks complete, even without shutdown."""
        pool = QThreadPoolExecutor(max_workers=2)
        completed = {"called": False}

        try:
            pool.add_done_callback(lambda: completed.update(called=True))

            future = pool.submit(time.sleep, 0.05)
            future.result()  # Wait for task
            wait_with_events(200)  # Extra time for callback to execute

            # NEW BEHAVIOR: Callback SHOULD fire when all tasks complete, even without shutdown
            assert completed["called"] is True, "Callback should fire when all tasks complete"

            # Calling shutdown again shouldn't cause issues
            pool.shutdown(wait=True)
            wait_with_events(100)
            # Callback should still have been called exactly once
            assert completed["called"] is True, "Callback should remain fired after shutdown"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.thread_safety
    def test_pool_done_callback_thread_safety(self):
        """Test adding callbacks from multiple threads."""
        pool = QThreadPoolExecutor(max_workers=4)
        results = []
        lock = threading.Lock()

        def add_callback(n):
            def callback():
                with lock:
                    results.append(n)
            pool.add_done_callback(callback)

        try:
            # Add callbacks from multiple threads
            threads = [threading.Thread(target=add_callback, args=(i,))
                       for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Submit tasks
            futures = [pool.submit(time.sleep, 0.01) for _ in range(5)]
            pool.shutdown(wait=True)
            wait_with_events(200)

            assert len(results) == 10, f"All 10 callbacks should fire, got {len(results)}"
            assert set(results) == set(range(10)), f"All callbacks should execute, got {results}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_done_callback_fires_once(self):
        """Test done callback only fires once even if shutdown called multiple times."""
        pool = QThreadPoolExecutor(max_workers=2)
        call_count = {"count": 0}

        try:
            pool.add_done_callback(lambda: call_count.update(count=call_count["count"]+1))

            future = pool.submit(time.sleep, 0.05)
            pool.shutdown(wait=True)
            wait_with_events(100)

            # Try shutdown again
            pool.shutdown(wait=True)
            wait_with_events(100)

            assert call_count["count"] == 1, f"Callback should only fire once, fired {call_count['count']} times"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    # ========== add_failure_callback Tests ==========

    @pytest.mark.unit
    def test_pool_failure_callback_basic(self):
        """Test failure callback fires when task raises exception."""
        pool = QThreadPoolExecutor(max_workers=2)
        failures = []

        def failure_callback(exc):
            failures.append(exc)

        try:
            pool.add_failure_callback(failure_callback)

            # Submit task that fails
            future = pool.submit(lambda: 1/0)

            # Wait for completion
            try:
                future.result()
            except ZeroDivisionError:
                pass

            wait_with_events(100)
            pool.shutdown(wait=True)

            assert len(failures) == 1, f"Should have 1 failure, got {len(failures)}"
            assert isinstance(failures[0], ZeroDivisionError), f"Should be ZeroDivisionError, got {type(failures[0])}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_failure_callback_multiple_failures(self):
        """Test failure callback fires for each failed task."""
        pool = QThreadPoolExecutor(max_workers=2)
        failures = []

        try:
            pool.add_failure_callback(lambda exc: failures.append(type(exc).__name__))

            # Submit 3 different failing tasks
            pool.submit(lambda: 1/0)  # ZeroDivisionError
            pool.submit(lambda: [][1])  # IndexError
            pool.submit(lambda: {}['key'])  # KeyError

            pool.shutdown(wait=True)
            wait_with_events(200)

            assert len(failures) == 3, f"Should have 3 failures, got {len(failures)}"
            assert 'ZeroDivisionError' in failures, "Should have ZeroDivisionError"
            assert 'IndexError' in failures, "Should have IndexError"
            assert 'KeyError' in failures, "Should have KeyError"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_failure_callback_multiple_handlers(self):
        """Test multiple failure callbacks all execute."""
        pool = QThreadPoolExecutor(max_workers=2)
        results = []

        try:
            pool.add_failure_callback(lambda exc: results.append('callback1'))
            pool.add_failure_callback(lambda exc: results.append('callback2'))

            future = pool.submit(lambda: 1/0)

            try:
                future.result()
            except:
                pass

            wait_with_events(100)
            pool.shutdown(wait=True)

            assert 'callback1' in results, "callback1 should be called"
            assert 'callback2' in results, "callback2 should be called"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_failure_callback_no_params(self):
        """Test failure callback with no parameters."""
        pool = QThreadPoolExecutor(max_workers=2)
        called = {"value": False}

        try:
            pool.add_failure_callback(lambda: called.update(value=True))

            future = pool.submit(lambda: 1/0)

            try:
                future.result()
            except:
                pass

            wait_with_events(100)
            pool.shutdown(wait=True)

            assert called["value"] is True, "No-param failure callback should be called"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.integration
    def test_pool_failure_callback_mixed(self):
        """Test failure callback only fires for failed tasks."""
        pool = QThreadPoolExecutor(max_workers=2)
        failures = []

        try:
            pool.add_failure_callback(lambda exc: failures.append(exc))

            # Submit 5 tasks: 3 succeed, 2 fail
            pool.submit(lambda: 42)  # Success
            pool.submit(lambda: 1/0)  # Fail
            pool.submit(lambda: 100)  # Success
            pool.submit(lambda: [][1])  # Fail
            pool.submit(lambda: 200)  # Success

            pool.shutdown(wait=True)
            wait_with_events(200)

            assert len(failures) == 2, f"Should have 2 failures, got {len(failures)}"
            assert isinstance(failures[0], ZeroDivisionError), "First failure should be ZeroDivisionError"
            assert isinstance(failures[1], IndexError), "Second failure should be IndexError"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.thread_safety
    def test_pool_failure_callback_thread_safety(self):
        """Test adding failure callbacks from multiple threads."""
        pool = QThreadPoolExecutor(max_workers=4)
        failures = []
        lock = threading.Lock()

        def add_failure_callback(n):
            def callback(exc):
                with lock:
                    failures.append(n)
            pool.add_failure_callback(callback)

        try:
            # Add callbacks from multiple threads
            threads = [threading.Thread(target=add_failure_callback, args=(i,))
                       for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Submit one failing task
            future = pool.submit(lambda: 1/0)

            try:
                future.result()
            except:
                pass

            wait_with_events(200)
            pool.shutdown(wait=True)

            # All 5 callbacks should fire for the one failure
            assert len(failures) == 5, f"All 5 callbacks should fire, got {len(failures)}"
            assert set(failures) == set(range(5)), f"All callbacks should execute, got {failures}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.integration
    def test_pool_callbacks_independent(self):
        """Test failure and done callbacks work independently."""
        pool = QThreadPoolExecutor(max_workers=2)
        done_called = {"value": False}
        failures = []

        try:
            pool.add_done_callback(lambda: done_called.update(value=True))
            pool.add_failure_callback(lambda exc: failures.append(exc))

            # Submit mixed tasks
            pool.submit(lambda: 42)
            pool.submit(lambda: 1/0)
            pool.submit(lambda: 100)

            pool.shutdown(wait=True)
            wait_with_events(200)

            # Done callback should fire (all tasks completed)
            assert done_called["value"] is True, "Done callback should fire"
            # Failure callback should fire once
            assert len(failures) == 1, f"Should have 1 failure, got {len(failures)}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    # ========== Integration Tests ==========

    @pytest.mark.integration
    def test_pool_callbacks_in_gui_context(self):
        """Test callbacks work correctly in GUI application context."""
        # This test simulates demo2.py usage
        pool = QThreadPoolExecutor(max_workers=2)

        completed_tasks = {"count": 0}
        all_done = {"called": False}
        failures = []

        def task(n):
            if n == 2:
                raise ValueError(f"Task {n} failed")
            time.sleep(0.1)
            return n * 10

        def task_finished(result):
            completed_tasks["count"] += 1

        try:
            pool.add_done_callback(lambda: all_done.update(called=True))
            pool.add_failure_callback(lambda exc: failures.append(exc))

            # Submit 3 tasks (one will fail)
            f1 = pool.submit(task, 1)
            f1.add_done_callback(task_finished)

            f2 = pool.submit(task, 2)  # This will fail
            f2.add_done_callback(task_finished)

            f3 = pool.submit(task, 3)
            f3.add_done_callback(task_finished)

            pool.shutdown(wait=True)
            wait_with_events(500)

            # All individual task callbacks should fire (2 success + 1 failure)
            assert completed_tasks["count"] == 2, f"Should have 2 successful task callbacks, got {completed_tasks['count']}"

            # Pool done callback should fire
            assert all_done["called"] is True, "Pool done callback should fire"

            # Pool failure callback should fire for the one failure
            assert len(failures) == 1, f"Should have 1 failure, got {len(failures)}"
            assert isinstance(failures[0], ValueError), f"Should be ValueError, got {type(failures[0])}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    # ========== Edge Case Tests ==========

    @pytest.mark.unit
    def test_pool_done_callback_with_cancelled_tasks(self):
        """Test done callback fires even if some tasks are cancelled."""
        pool = QThreadPoolExecutor(max_workers=1)
        completed = {"called": False}

        try:
            pool.add_done_callback(lambda: completed.update(called=True))

            # Submit multiple tasks
            futures = [pool.submit(time.sleep, 0.5) for _ in range(3)]

            # Cancel pending tasks
            pool.shutdown(wait=False, cancel_futures=True)
            wait_with_events(100)

            # Force shutdown to ensure completion
            pool.shutdown(wait=True, force_stop=True)
            wait_with_events(100)

            # Callback should still fire
            assert completed["called"] is True, "Callback should fire even with cancelled tasks"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_callback_exception_handling(self):
        """Test that exceptions in callbacks don't break the pool."""
        pool = QThreadPoolExecutor(max_workers=2)
        good_callback_called = {"value": False}

        def bad_callback():
            raise RuntimeError("Callback error")

        def good_callback():
            good_callback_called["value"] = True

        try:
            pool.add_done_callback(bad_callback)
            pool.add_done_callback(good_callback)

            future = pool.submit(time.sleep, 0.05)
            pool.shutdown(wait=True)
            wait_with_events(100)

            # Good callback should still execute despite bad callback error
            assert good_callback_called["value"] is True, "Good callback should execute even if bad callback fails"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_callback_with_lambda_closures(self):
        """Test callbacks work with lambda closures."""
        pool = QThreadPoolExecutor(max_workers=2)
        results = []

        try:
            # Test closure capture
            for i in range(3):
                pool.add_done_callback(lambda idx=i: results.append(idx))

            pool.submit(time.sleep, 0.05)
            pool.shutdown(wait=True)
            wait_with_events(100)

            assert results == [0, 1, 2], f"Lambda closures should work, got {results}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_failure_callback_with_cancelled_error(self):
        """Test that CancelledError doesn't trigger failure callback."""
        pool = QThreadPoolExecutor(max_workers=1)
        failures = []

        try:
            pool.add_failure_callback(lambda exc: failures.append(exc))

            # Submit task and cancel
            future = pool.submit(time.sleep, 1.0)
            future.cancel(force_stop=True)

            pool.shutdown(wait=True)
            wait_with_events(100)

            # CancelledError should not trigger failure callback
            assert len(failures) == 0, f"CancelledError should not trigger failure callback, got {len(failures)} failures"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.unit
    def test_pool_callbacks_with_immediate_shutdown(self):
        """Test callbacks work when shutdown is called immediately."""
        pool = QThreadPoolExecutor(max_workers=2)
        done_called = {"value": False}

        try:
            pool.add_done_callback(lambda: done_called.update(value=True))

            # Shutdown immediately without submitting tasks
            pool.shutdown(wait=True)
            wait_with_events(100)

            assert done_called["value"] is True, "Callback should fire on immediate shutdown"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)

    @pytest.mark.integration
    def test_pool_callbacks_stress(self):
        """Stress test: Many tasks, many callbacks."""
        pool = QThreadPoolExecutor(max_workers=4)
        done_count = {"value": 0}
        failure_count = {"value": 0}
        lock = threading.Lock()

        def done_callback():
            with lock:
                done_count["value"] += 1

        def failure_callback(exc):
            with lock:
                failure_count["value"] += 1

        try:
            # Add 10 done callbacks
            for _ in range(10):
                pool.add_done_callback(done_callback)

            # Add 5 failure callbacks
            for _ in range(5):
                pool.add_failure_callback(failure_callback)

            # Submit 20 tasks: 15 succeed, 5 fail
            for i in range(20):
                if i % 4 == 0:  # Every 4th task fails
                    pool.submit(lambda: 1/0)
                else:
                    pool.submit(lambda: 42)

            pool.shutdown(wait=True)
            wait_with_events(500)

            # All 10 done callbacks should fire once
            assert done_count["value"] == 10, f"Should have 10 done callback calls, got {done_count['value']}"

            # 5 failure callbacks * 5 failed tasks = 25 total calls
            expected_failures = 5 * 5  # 5 callbacks * 5 failed tasks
            assert failure_count["value"] == expected_failures, \
                f"Should have {expected_failures} failure callback calls, got {failure_count['value']}"
        finally:
            if not pool._shutdown:
                pool.shutdown(wait=False, force_stop=True)


# Session fixture for QApplication
@pytest.fixture(scope="session")
def qapp_session():
    """Create QApplication instance for testing session."""
    import sys
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    yield app
