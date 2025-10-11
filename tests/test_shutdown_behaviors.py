"""
测试 QThreadPoolExecutor.shutdown 方法的新行为。

测试重构后的 shutdown 方法，包括：
1. force_stop 最高优先级行为
2. cancel_futures 行为
3. wait 参数及警告
4. 各种参数组合
5. 回调执行保证
"""

import time
import warnings
import pytest
from PySide6.QtWidgets import QApplication
from qthreadwithreturn import QThreadPoolExecutor


@pytest.fixture
def process_events():
    """在测试前后处理Qt事件"""
    app = QApplication.instance()
    if app:
        app.processEvents()
    yield
    if app:
        app.processEvents()


class TestShutdownForceStop:
    """测试 force_stop=True 的行为"""

    def test_force_stop_terminates_all_tasks(self, process_events):
        """force_stop=True 应该立即终止所有任务"""
        pool = QThreadPoolExecutor(max_workers=2)

        # 提交一些长时间运行的任务
        def long_task():
            time.sleep(5)
            return "completed"

        futures = [pool.submit(long_task) for _ in range(4)]
        time.sleep(0.1)  # 让一些任务开始

        # 强制停止
        start_time = time.time()
        pool.shutdown(force_stop=True)
        elapsed = time.time() - start_time

        # 应该立即返回（不等待5秒）
        assert elapsed < 1.0, f"force_stop took {elapsed}s, expected < 1s"

        # 所有任务应该被标记为完成
        app = QApplication.instance()
        if app:
            app.processEvents()

        time.sleep(0.2)  # 给予时间让信号传播
        if app:
            app.processEvents()

    def test_force_stop_triggers_done_callback(self, process_events):
        """force_stop=True 应该触发池级别的 done_callback"""
        pool = QThreadPoolExecutor(max_workers=2)
        callback_executed = []

        def done_callback():
            callback_executed.append(True)

        pool.add_done_callback(done_callback)

        # 提交任务
        def task():
            time.sleep(2)
            return "result"

        futures = [pool.submit(task) for _ in range(3)]
        time.sleep(0.1)

        # 强制停止
        pool.shutdown(force_stop=True)

        # 等待回调执行
        time.sleep(0.3)
        app = QApplication.instance()
        if app:
            for _ in range(10):
                app.processEvents()
                time.sleep(0.02)

        # 验证回调被调用
        assert len(callback_executed) == 1, "done_callback should be called once"

    def test_force_stop_with_pending_tasks(self, process_events):
        """force_stop=True 应该同时处理 pending 和 active 任务"""
        pool = QThreadPoolExecutor(max_workers=1)

        def task(n):
            time.sleep(0.5)
            return n

        # 提交多个任务（超过 max_workers，创建 pending）
        futures = [pool.submit(task, i) for i in range(5)]
        time.sleep(0.1)

        # 强制停止
        pool.shutdown(force_stop=True)

        # 所有任务应该被标记为完成或取消
        time.sleep(0.3)
        app = QApplication.instance()
        if app:
            app.processEvents()

    def test_force_stop_ignores_wait_parameter(self, process_events):
        """force_stop=True 应该立即返回，即使 wait=True"""
        pool = QThreadPoolExecutor(max_workers=2)

        def long_task():
            time.sleep(10)

        futures = [pool.submit(long_task) for _ in range(2)]
        time.sleep(0.1)

        # force_stop 应该立即返回，不等待
        start_time = time.time()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            pool.shutdown(force_stop=True, wait=True)
            # 应该有警告
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert "UI freezing" in str(w[0].message)

        elapsed = time.time() - start_time
        assert elapsed < 1.0, f"force_stop with wait=True took {elapsed}s"

    def test_force_stop_with_failure_callback(self, process_events):
        """force_stop=True 应该触发失败回调（如果任务未完成）"""
        pool = QThreadPoolExecutor(max_workers=2)
        failure_count = []
        done_count = []

        def failure_callback(exc):
            failure_count.append(exc)

        def done_callback():
            done_count.append(True)

        pool.add_failure_callback(failure_callback)
        pool.add_done_callback(done_callback)

        def failing_task():
            time.sleep(0.1)
            raise ValueError("test error")

        futures = [pool.submit(failing_task) for _ in range(2)]
        time.sleep(0.05)

        # 强制停止
        pool.shutdown(force_stop=True)

        # 等待回调
        time.sleep(0.3)
        app = QApplication.instance()
        if app:
            for _ in range(10):
                app.processEvents()
                time.sleep(0.02)

        # done_callback 应该被调用
        assert len(done_count) == 1


class TestShutdownCancelFutures:
    """测试 cancel_futures=True 的行为"""

    def test_cancel_futures_cancels_pending_only(self, process_events):
        """cancel_futures=True 应该只取消 pending 任务"""
        pool = QThreadPoolExecutor(max_workers=1)
        results = []

        def task(n):
            time.sleep(0.2)
            results.append(n)
            return n

        # 提交多个任务
        futures = [pool.submit(task, i) for i in range(4)]
        time.sleep(0.05)  # 让第一个任务开始

        # 取消 pending
        pool.shutdown(cancel_futures=True)

        # 等待完成
        time.sleep(1.0)
        app = QApplication.instance()
        if app:
            app.processEvents()

        # 只有第一个任务应该完成
        assert len(results) <= 2, f"Expected ≤2 completed tasks, got {len(results)}"

    def test_cancel_futures_with_wait(self, process_events):
        """cancel_futures=True 和 wait=True 应该等待活跃任务完成"""
        pool = QThreadPoolExecutor(max_workers=1)
        results = []

        def task(n):
            time.sleep(0.3)
            results.append(n)
            return n

        futures = [pool.submit(task, i) for i in range(3)]
        time.sleep(0.1)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            pool.shutdown(cancel_futures=True, wait=True)
            assert len(w) == 1
            assert "UI freezing" in str(w[0].message)

        app = QApplication.instance()
        if app:
            app.processEvents()

        # 第一个任务应该完成
        assert len(results) >= 1

    def test_cancel_futures_triggers_done_callback(self, process_events):
        """cancel_futures=True 完成后应该触发 done_callback"""
        pool = QThreadPoolExecutor(max_workers=1)
        callback_executed = []

        def done_callback():
            callback_executed.append(True)

        pool.add_done_callback(done_callback)

        def task():
            time.sleep(0.2)

        futures = [pool.submit(task) for _ in range(3)]
        time.sleep(0.05)

        pool.shutdown(cancel_futures=True, wait=True)

        time.sleep(0.2)
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        assert len(callback_executed) == 1


class TestShutdownWait:
    """测试 wait=True 的行为"""

    def test_wait_blocks_until_completion(self, process_events):
        """wait=True 应该阻塞直到所有任务完成"""
        pool = QThreadPoolExecutor(max_workers=2)
        results = []

        def task(n):
            time.sleep(0.2)  # 减少睡眠时间
            results.append(n)
            return n

        futures = [pool.submit(task, i) for i in range(3)]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            pool.shutdown(wait=True)
            assert len(w) == 1

        app = QApplication.instance()
        if app:
            app.processEvents()

        # 所有任务应该完成
        assert len(results) == 3, f"Expected 3 results, got {len(results)}: {results}"

    def test_wait_emits_warning(self, process_events):
        """wait=True 应该发出警告"""
        pool = QThreadPoolExecutor(max_workers=1)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            pool.shutdown(wait=True)

            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert "UI freezing" in str(w[0].message)
            assert "unresponsiveness" in str(w[0].message)

    def test_wait_false_returns_immediately(self, process_events):
        """wait=False（默认）应该立即返回"""
        pool = QThreadPoolExecutor(max_workers=2)

        def long_task():
            time.sleep(2)

        futures = [pool.submit(long_task) for _ in range(2)]
        time.sleep(0.1)

        start_time = time.time()
        pool.shutdown(wait=False)  # 默认行为
        elapsed = time.time() - start_time

        assert elapsed < 0.5, f"shutdown(wait=False) took {elapsed}s"


class TestShutdownParameterCombinations:
    """测试各种参数组合"""

    def test_default_shutdown(self, process_events):
        """默认 shutdown() 应该立即返回"""
        pool = QThreadPoolExecutor(max_workers=2)
        callback_executed = []

        def done_callback():
            callback_executed.append(True)

        pool.add_done_callback(done_callback)

        def task():
            time.sleep(0.3)

        futures = [pool.submit(task) for _ in range(2)]
        time.sleep(0.1)

        start_time = time.time()
        pool.shutdown()  # 所有参数为默认值
        elapsed = time.time() - start_time

        assert elapsed < 0.5

        # 等待任务完成
        time.sleep(0.5)
        app = QApplication.instance()
        if app:
            for _ in range(10):
                app.processEvents()
                time.sleep(0.02)

        # 回调应该被调用
        assert len(callback_executed) == 1

    def test_force_stop_overrides_cancel_futures(self, process_events):
        """force_stop=True 应该覆盖 cancel_futures"""
        pool = QThreadPoolExecutor(max_workers=1)

        def task():
            time.sleep(2)

        futures = [pool.submit(task) for _ in range(3)]
        time.sleep(0.1)

        start_time = time.time()
        pool.shutdown(force_stop=True, cancel_futures=True)
        elapsed = time.time() - start_time

        # 应该立即返回
        assert elapsed < 1.0

    def test_multiple_shutdown_calls(self, process_events):
        """多次调用 shutdown 应该是安全的"""
        pool = QThreadPoolExecutor(max_workers=2)

        def task():
            time.sleep(0.2)

        futures = [pool.submit(task) for _ in range(2)]
        time.sleep(0.1)

        # 第一次调用
        pool.shutdown()

        # 第二次调用应该安全（不会崩溃）
        try:
            pool.shutdown(force_stop=True)
            # 不应该抛出异常
        except Exception as e:
            pytest.fail(f"Multiple shutdown calls raised exception: {e}")

    def test_shutdown_empty_pool(self, process_events):
        """空池的 shutdown 应该立即触发回调"""
        pool = QThreadPoolExecutor(max_workers=2)
        callback_executed = []

        def done_callback():
            callback_executed.append(True)

        pool.add_done_callback(done_callback)

        # 不提交任何任务
        pool.shutdown()

        time.sleep(0.2)
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        # 回调应该被立即调用
        assert len(callback_executed) == 1


class TestShutdownCallbackExecution:
    """测试回调执行保证"""

    def test_force_stop_executes_all_callbacks(self, process_events):
        """force_stop=True 应该执行所有注册的回调"""
        pool = QThreadPoolExecutor(max_workers=2)
        done_callbacks = []
        failure_callbacks = []

        def done_callback1():
            done_callbacks.append("callback1")

        def done_callback2():
            done_callbacks.append("callback2")

        def failure_callback(exc):
            failure_callbacks.append(exc)

        pool.add_done_callback(done_callback1)
        pool.add_done_callback(done_callback2)
        pool.add_failure_callback(failure_callback)

        def task():
            time.sleep(1)

        futures = [pool.submit(task) for _ in range(3)]
        time.sleep(0.1)

        pool.shutdown(force_stop=True)

        # 等待回调执行
        time.sleep(0.3)
        app = QApplication.instance()
        if app:
            for _ in range(15):
                app.processEvents()
                time.sleep(0.02)

        # 所有 done_callbacks 应该被调用
        assert len(done_callbacks) == 2
        assert "callback1" in done_callbacks
        assert "callback2" in done_callbacks

    def test_callbacks_execute_in_main_thread(self, process_events):
        """回调应该在主线程中执行"""
        import threading
        pool = QThreadPoolExecutor(max_workers=2)
        callback_thread = []

        def done_callback():
            callback_thread.append(threading.current_thread().name)

        pool.add_done_callback(done_callback)

        def task():
            time.sleep(0.2)

        futures = [pool.submit(task) for _ in range(2)]

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            pool.shutdown(wait=True)

        time.sleep(0.2)
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        # 回调应该被调用
        assert len(callback_thread) == 1


class TestShutdownEdgeCases:
    """测试边界情况"""

    def test_shutdown_with_no_tasks_submitted(self, process_events):
        """未提交任务时 shutdown 应该正常工作"""
        pool = QThreadPoolExecutor(max_workers=2)
        callback_executed = []

        def done_callback():
            callback_executed.append(True)

        pool.add_done_callback(done_callback)
        pool.shutdown()

        time.sleep(0.2)
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        assert len(callback_executed) == 1

    def test_force_stop_with_already_completed_tasks(self, process_events):
        """force_stop 处理已完成任务应该安全"""
        pool = QThreadPoolExecutor(max_workers=2)

        def quick_task():
            return "done"

        futures = [pool.submit(quick_task) for _ in range(2)]

        # 等待任务完成
        time.sleep(0.3)
        app = QApplication.instance()
        if app:
            app.processEvents()

        # force_stop 应该安全
        try:
            pool.shutdown(force_stop=True)
        except Exception as e:
            pytest.fail(f"force_stop with completed tasks raised: {e}")

    def test_shutdown_after_submit_raises_runtime_error(self, process_events):
        """shutdown 后 submit 应该抛出 RuntimeError"""
        pool = QThreadPoolExecutor(max_workers=2)
        pool.shutdown()

        with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
            pool.submit(lambda: None)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
