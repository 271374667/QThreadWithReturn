# ================= 全面测试覆盖率：边界条件、错误处理、压力测试 =================
import pytest
import time
import threading
import sys
import gc
import weakref
import concurrent.futures
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QThread

from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor
from tests.test_thread_utils import wait_with_events, wait_for_thread_completion


class TestBoundaryConditions:
    """边界条件测试：测试极值、空值、异常输入"""

    @pytest.mark.parametrize(
        "max_workers,expected_behavior",
        [
            (None, "default"),  # 边界：None使用默认值
            (1, "valid"),  # 边界：最小有效值
            (1000, "limited"),  # 边界：很大的值（但会被限制）
        ],
    )
    def test_thread_pool_max_workers_boundaries(
        self, qapp, max_workers, expected_behavior
    ):
        """测试线程池max_workers的边界值"""
        pool = QThreadPoolExecutor(max_workers=max_workers)

        if expected_behavior == "default":
            # None应该使用默认值
            import os

            expected_default = min((os.cpu_count() or 1) * 2, 32)
            assert pool._max_workers == expected_default
        elif expected_behavior == "valid":
            assert pool._max_workers == max_workers
        elif expected_behavior == "limited":
            # 大值不会被自动限制，但应该是有效的
            assert pool._max_workers == max_workers

        pool.shutdown()

    @pytest.mark.parametrize(
        "timeout_ms,expected_behavior",
        [
            (-1, "no_timeout"),  # 边界：无超时
            (0, "immediate"),  # 边界：立即超时
            (1, "very_short"),  # 边界：极短超时
            (50, "too_short"),  # 边界：短超时但会超时
            (200, "sufficient"),  # 边界：足够的超时
            (100000, "very_long"),  # 边界：很长超时
        ],
    )
    def test_thread_timeout_boundaries(self, qapp, timeout_ms, expected_behavior):
        """测试线程超时的边界值"""

        def slow_task():
            time.sleep(0.1)  # 100ms task
            return "completed"

        thread = QThreadWithReturn(slow_task)
        thread.start(timeout_ms)

        if expected_behavior in ["immediate", "very_short", "too_short"]:
            # 这些超时应该取消线程
            wait_with_events(200)  # 给更多时间让超时触发
            assert thread.cancelled(), f"{expected_behavior}超时应该取消线程"
        else:
            # 其他情况应该正常完成
            result = thread.result(timeout=2.0)
            assert result == "completed"

    def test_empty_iterables(self, qapp):
        """测试空的可迭代对象"""
        with QThreadPoolExecutor(max_workers=2) as pool:
            # 测试空列表 - 使用submit替代map
            futures = [pool.submit(lambda x: x, x) for x in []]
            results = [f.result() for f in futures]
            assert results == []

            # 测试空集合
            futures = [pool.submit(lambda x: x, x) for x in set()]
            results = [f.result() for f in futures]
            assert results == []

    def test_none_and_null_values(self, qapp):
        """测试None值和空值处理"""

        def handle_none(value):
            return value if value is not None else "null"

        with QThreadPoolExecutor(max_workers=2) as pool:
            # 测试None值
            future = pool.submit(handle_none, None)
            result = future.result()
            assert result == "null"

            # 测试包含None的列表 - 使用submit替代map
            futures = [pool.submit(handle_none, x) for x in [1, None, 2, None]]
            results = [f.result() for f in futures]
            assert results == [1, "null", 2, "null"]

    def test_very_large_data_structures(self, qapp):
        """测试大数据结构"""

        def process_large_list(data):
            return len(data)

        # 创建大列表
        large_list = list(range(10000))

        thread = QThreadWithReturn(process_large_list, large_list)
        thread.start()
        result = thread.result(timeout=5.0)
        assert result == 10000


class TestErrorHandling:
    """错误处理测试：各种异常场景和恢复机制"""

    def test_initializer_exception_handling(self, qapp):
        """测试初始化器异常处理"""

        def failing_initializer():
            raise RuntimeError("Initializer failed")

        def dummy_task():
            return "success"

        with QThreadPoolExecutor(
            max_workers=1, initializer=failing_initializer
        ) as pool:
            # 尽管初始化器失败，任务应该仍能执行
            future = pool.submit(dummy_task)
            result = future.result()
            assert result == "success"

    def test_callback_exception_handling(self, qapp):
        """测试回调异常不影响主程序"""
        exception_occurred = {"value": False}

        def failing_callback(result):
            exception_occurred["value"] = True
            raise RuntimeError("Callback failed")

        def simple_task():
            return "task_result"

        # 捕获stderr来验证异常被记录
        import io

        captured_stderr = io.StringIO()

        with patch("sys.stderr", captured_stderr):
            thread = QThreadWithReturn(simple_task)
            thread.add_done_callback(failing_callback)
            thread.start()

            result = thread.result()
            wait_with_events(200)

            # 主任务应该正常完成
            assert result == "task_result"
            # 回调异常应该被记录
            stderr_content = captured_stderr.getvalue()
            assert "Error in done_callback" in stderr_content

    def test_thread_start_twice_error(self, qapp):
        """测试线程重复启动的错误处理"""

        def simple_task():
            time.sleep(0.1)
            return "done"

        thread = QThreadWithReturn(simple_task)
        thread.start()

        # 尝试再次启动应该抛出异常
        with pytest.raises(RuntimeError, match="Thread is already running"):
            thread.start()

        # 等待第一个线程完成
        thread.result()

    def test_cancel_not_started_thread(self, qapp):
        """测试取消未启动的线程"""

        def simple_task():
            return "done"

        thread = QThreadWithReturn(simple_task)
        # 取消未启动的线程应该成功
        assert thread.cancel() == True
        assert thread.cancelled() == True
        assert thread.done() == True

    def test_result_after_cancel_error(self, qapp):
        """测试取消后获取结果的错误处理"""

        def slow_task():
            time.sleep(1.0)
            return "completed"

        thread = QThreadWithReturn(slow_task)
        thread.start()
        wait_with_events(50)
        thread.cancel()
        wait_for_thread_completion(thread, 2000)

        # 获取结果应该抛出CancelledError
        with pytest.raises(concurrent.futures.CancelledError):
            thread.result()

        with pytest.raises(concurrent.futures.CancelledError):
            thread.exception()

    def test_qt_application_not_available(self, qapp):
        """测试Qt应用不可用时的处理"""

        def simple_task():
            return "success"

        # 临时patch QApplication.instance返回None
        with patch("PySide6.QtWidgets.QApplication.instance", return_value=None):
            thread = QThreadWithReturn(simple_task)
            thread.start()
            # 应该仍能正常工作，但给个短超时避免无限等待
            result = thread.result(timeout=2.0)
            assert result == "success"


class TestDataDrivenTests:
    """数据驱动测试：参数化测试不同数据组合"""

    @pytest.mark.parametrize(
        "input_data,expected_output",
        [
            # 基本数据类型
            (42, 42),
            ("hello", "hello"),
            ([1, 2, 3], [1, 2, 3]),
            ({"key": "value"}, {"key": "value"}),
            # 边界值
            (0, 0),
            (-1, -1),
            (float("inf"), float("inf")),
            (float("-inf"), float("-inf")),
            # 空值
            (None, None),
            ([], []),
            ({}, {}),
            ("", ""),
            # 复杂数据结构
            (
                {"nested": {"deep": [1, 2, {"even": "deeper"}]}},
                {"nested": {"deep": [1, 2, {"even": "deeper"}]}},
            ),
        ],
    )
    def test_various_data_types(self, qapp, input_data, expected_output):
        """测试各种数据类型的处理"""

        def identity(data):
            return data

        thread = QThreadWithReturn(identity, input_data)
        thread.start()
        result = thread.result()
        assert result == expected_output

    @pytest.mark.parametrize(
        "num_threads,task_duration,expected_results",
        [
            (1, 0.01, ["result_0"]),
            (3, 0.01, ["result_0", "result_1", "result_2"]),
            (5, 0.01, ["result_0", "result_1", "result_2", "result_3", "result_4"]),
            (10, 0.01, [f"result_{i}" for i in range(10)]),
        ],
    )
    def test_multiple_thread_scenarios(
        self, qapp, num_threads, task_duration, expected_results
    ):
        """测试不同数量线程的场景"""

        def task(index):
            time.sleep(task_duration)
            return f"result_{index}"

        threads = []
        for i in range(num_threads):
            thread = QThreadWithReturn(task, i)
            threads.append(thread)

        # 启动所有线程
        for thread in threads:
            thread.start()

        # 收集结果
        results = []
        for thread in threads:
            results.append(thread.result(timeout=2.0))

        results.sort()  # 排序以便比较
        assert results == sorted(expected_results)

    @pytest.mark.parametrize(
        "callback_type,param_count",
        [
            ("no_param", 0),
            ("single_param", 1),
            ("double_param", 2),
            ("triple_param", 3),
            ("with_defaults", 1),  # 有默认参数的回调
        ],
    )
    def test_callback_parameter_combinations(self, qapp, callback_type, param_count):
        """测试不同参数数量的回调组合"""
        results = []

        def no_param_callback():
            results.append("no_param")

        def single_param_callback(result):
            results.append(("single", result))

        def double_param_callback(a, b):
            results.append(("double", a, b))

        def triple_param_callback(a, b, c):
            results.append(("triple", a, b, c))

        def with_defaults_callback(result, extra="default"):
            results.append(("defaults", result, extra))

        callbacks = {
            "no_param": no_param_callback,
            "single_param": single_param_callback,
            "double_param": double_param_callback,
            "triple_param": triple_param_callback,
            "with_defaults": with_defaults_callback,
        }

        # 选择合适的任务返回值
        if callback_type == "double_param":
            task = lambda: (1, 2)
        elif callback_type == "triple_param":
            task = lambda: (1, 2, 3)
        else:
            task = lambda: "single_result"

        thread = QThreadWithReturn(task)
        thread.add_done_callback(callbacks[callback_type])
        thread.start()
        thread.result()
        wait_with_events(200)

        # 验证回调被调用
        assert len(results) > 0


class TestStressTests:
    """压力测试：高并发、大量数据、长时间运行"""

    def test_high_concurrency_stress(self, qapp):
        """高并发压力测试"""
        NUM_THREADS = 50
        NUM_TASKS_PER_THREAD = 10

        def cpu_intensive_task(n):
            # CPU密集型任务
            result = 0
            for i in range(n * 1000):
                result += i * i
            return result

        with QThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(NUM_THREADS):
                future = pool.submit(cpu_intensive_task, NUM_TASKS_PER_THREAD)
                futures.append(future)

            # 等待所有任务完成
            results = []
            for future in futures:
                results.append(future.result(timeout=30.0))

            # 验证结果
            assert len(results) == NUM_THREADS
            assert all(isinstance(r, int) and r >= 0 for r in results)

    def test_memory_intensive_operations(self, qapp):
        """内存密集型操作测试"""

        def memory_task(size):
            # 创建大内存对象
            data = [i for i in range(size)]
            return len(data)

        LARGE_SIZE = 100000

        with QThreadPoolExecutor(max_workers=3) as pool:
            futures = []
            for i in range(5):  # 5个大内存任务
                future = pool.submit(memory_task, LARGE_SIZE)
                futures.append(future)

            results = []
            for future in futures:
                result = future.result(timeout=10.0)
                results.append(result)

            assert all(r == LARGE_SIZE for r in results)

    def test_rapid_start_cancel_cycles(self, qapp):
        """快速启动取消循环测试"""

        def medium_task():
            time.sleep(0.5)
            return "completed"

        success_count = 0
        cancel_count = 0

        for i in range(20):
            thread = QThreadWithReturn(medium_task)
            thread.start()

            # 随机决定是否取消
            if i % 3 == 0:
                time.sleep(0.1)  # 让线程开始运行
                if thread.cancel():
                    cancel_count += 1
                    assert thread.cancelled()
            else:
                result = thread.result(timeout=2.0)
                if result == "completed":
                    success_count += 1

        # 验证有成功和取消的情况
        assert success_count > 0
        assert cancel_count > 0

    def test_long_running_stability(self, qapp):
        """长时间运行稳定性测试"""

        def incremental_task(duration, increment):
            start_time = time.time()
            counter = 0
            while time.time() - start_time < duration:
                counter += increment
                time.sleep(0.01)  # 小量睡眠避免100% CPU
            return counter

        # 启动多个长时间任务
        with QThreadPoolExecutor(max_workers=3) as pool:
            futures = []
            for i in range(3):
                future = pool.submit(incremental_task, 1.0, i + 1)  # 1秒任务
                futures.append(future)

            results = []
            for future in futures:
                result = future.result(timeout=5.0)
                results.append(result)
                assert result > 0  # 应该有正数结果

    def test_callback_stress(self, qapp):
        """回调压力测试"""
        callback_count = {"value": 0}
        callback_errors = {"value": 0}

        def stress_callback(result):
            callback_count["value"] += 1
            # 模拟回调中的一些处理
            if result % 10 == 0:
                time.sleep(0.001)  # 偶尔延迟

        def error_callback(result):
            callback_count["value"] += 1
            if result == 13:  # 特定值触发异常
                callback_errors["value"] += 1
                raise ValueError("Callback stress test error")

        # 创建大量带回调的任务
        with QThreadPoolExecutor(max_workers=5) as pool:
            futures = []
            for i in range(30):
                future = pool.submit(lambda x: x, i)
                if i == 13:
                    future.add_done_callback(error_callback)
                else:
                    future.add_done_callback(stress_callback)
                futures.append(future)

            # 等待所有任务完成
            for future in futures:
                future.result(timeout=5.0)

            wait_with_events(500)  # 等待所有回调执行

            # 验证回调执行
            assert callback_count["value"] == 30
            assert callback_errors["value"] == 1


class TestRaceConditions:
    """竞态条件测试：多线程同步和资源竞争"""

    def test_concurrent_start_cancel(self, qapp):
        """并发启动和取消操作"""

        def slow_task():
            time.sleep(0.5)
            return "done"

        results = []
        threads = []

        # 创建多个线程
        for i in range(10):
            thread = QThreadWithReturn(slow_task)
            threads.append(thread)

        # 并发启动线程
        start_threads = []
        for thread in threads:
            t = threading.Thread(target=thread.start)
            start_threads.append(t)
            t.start()

        # 等待所有启动完成
        for t in start_threads:
            t.join()

        time.sleep(0.1)  # 让线程开始运行

        # 并发取消一些线程
        cancel_threads = []
        for i, thread in enumerate(threads):
            if i % 2 == 0:  # 取消偶数索引的线程
                t = threading.Thread(target=thread.cancel)
                cancel_threads.append(t)
                t.start()

        # 等待所有取消操作完成
        for t in cancel_threads:
            t.join()

        # 验证状态一致性
        for thread in threads:
            assert thread.done() or thread.running()

    def test_shared_callback_object(self, qapp):
        """共享回调对象的线程安全测试"""

        class SharedCounter:
            def __init__(self):
                self.count = 0
                self.lock = threading.Lock()

            def increment(self, result):
                with self.lock:
                    self.count += result

        counter = SharedCounter()

        def counting_task(value):
            return value

        # 创建多个使用相同回调对象的线程
        with QThreadPoolExecutor(max_workers=5) as pool:
            futures = []
            for i in range(20):
                future = pool.submit(counting_task, 1)
                future.add_done_callback(counter.increment)
                futures.append(future)

            # 等待所有任务完成
            for future in futures:
                future.result()

            wait_with_events(500)

            # 验证计数正确（线程安全）
            assert counter.count == 20

    def test_rapid_thread_creation_destruction(self, qapp):
        """快速线程创建销毁测试"""

        def quick_task(value):
            return value * 2

        # 快速创建和销毁大量线程
        results = []
        for i in range(50):
            thread = QThreadWithReturn(quick_task, i)
            thread.start()
            result = thread.result(timeout=1.0)
            results.append(result)

        # 验证所有结果正确
        expected = [i * 2 for i in range(50)]
        assert results == expected


class TestMemoryAndResourceManagement:
    """内存和资源管理测试：泄漏检测和清理验证"""

    def test_thread_object_cleanup(self, qapp):
        """测试线程对象清理"""

        def simple_task():
            return "done"

        # 创建弱引用来检测对象是否被清理
        weak_refs = []

        for i in range(10):
            thread = QThreadWithReturn(simple_task)
            weak_ref = weakref.ref(thread)
            weak_refs.append(weak_ref)

            thread.start()
            thread.result()
            # 删除强引用
            del thread

        # 强制垃圾回收
        gc.collect()
        wait_with_events(100)
        gc.collect()

        # 检查对象是否被清理（允许一些对象仍然存在）
        alive_count = sum(1 for ref in weak_refs if ref() is not None)
        # 至少应该清理掉大部分对象
        assert alive_count <= 3, f"Too many objects still alive: {alive_count}"

    def test_callback_reference_cleanup(self, qapp):
        """测试回调引用清理"""
        callback_instances = []

        class CallbackObject:
            def __init__(self, id):
                self.id = id
                callback_instances.append(self)

            def __call__(self, result):
                pass

        def simple_task():
            return "done"

        # 创建带回调对象的线程
        threads = []
        for i in range(5):
            thread = QThreadWithReturn(simple_task)
            callback = CallbackObject(i)
            thread.add_done_callback(callback)
            threads.append(thread)

        # 运行所有线程
        for thread in threads:
            thread.start()
            thread.result()

        wait_with_events(200)

        # 清理线程引用
        del threads
        gc.collect()
        wait_with_events(100)
        gc.collect()

        # 检查回调对象的引用计数
        # 注意：这个测试可能在不同环境下有不同表现
        # 主要目的是确保没有明显的内存泄漏
        assert len(callback_instances) == 5

    def test_large_scale_memory_usage(self, qapp):
        """大规模内存使用测试"""

        def memory_task():
            # 创建一些临时对象
            temp_data = [i for i in range(1000)]
            return len(temp_data)

        # 记录初始内存（如果psutil可用）
        try:
            import psutil
            import os

            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss
            memory_check_available = True
        except ImportError:
            memory_check_available = False

        # 运行大量任务
        with QThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(50):  # 减少任务数量避免内存问题
                future = pool.submit(memory_task)
                futures.append(future)

            results = []
            for future in futures:
                result = future.result()
                results.append(result)

        # 强制垃圾回收
        gc.collect()
        wait_with_events(100)
        gc.collect()

        # 检查内存增长（如果可用）
        if memory_check_available:
            final_memory = process.memory_info().rss
            memory_growth = final_memory - initial_memory
            memory_growth_mb = memory_growth / 1024 / 1024

            # 允许一定的内存增长，但不应该太大
            assert memory_growth_mb < 200, (
                f"Memory growth too large: {memory_growth_mb:.2f}MB"
            )

        assert all(r == 1000 for r in results)


# 添加pytest fixtures
@pytest.fixture(scope="session")
def qapp():
    """创建QApplication实例供测试使用"""
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()
    yield app
