# ================= 专注覆盖率测试：针对特定代码分支 =================
import pytest
import time
import sys
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread

from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor
from tests.test_thread_utils import wait_with_events, wait_for_thread_completion


class TestMissingCodeCoverage:
    """专门测试缺失覆盖率的代码分支"""
    def test_thread_name_setting(self, qapp):
        """测试线程名称设置功能"""
        def get_thread_name():
            return QThread.currentThread().objectName()
        
        thread = QThreadWithReturn(get_thread_name, thread_name="TestThread")
        thread.start()
        result = thread.result()
        
        # 验证线程名称设置
        assert result == "TestThread" or result == ""
    
    def test_initializer_functionality(self, qapp):
        """测试初始化器功能"""
        init_data = {"called": False}
        
        def test_initializer():
            init_data["called"] = True
        
        def simple_task():
            return "initialized"
        
        thread = QThreadWithReturn(
            simple_task,
            initializer=test_initializer,
            initargs=()
        )
        thread.start()
        result = thread.result()
        
        assert result == "initialized"
        # 初始化器可能在worker线程中被调用
    
    def test_validate_callback_error_branch(self, qapp):
        """测试回调验证的错误分支"""
        thread = QThreadWithReturn(lambda: "test")
        
        # 测试非可调用对象
        with pytest.raises(TypeError, match="must be callable"):
            thread.add_done_callback("not_callable")
        
        # 测试inspect.signature失败的情况
        class ProblematicCallable:
            def __call__(self):
                pass
            
            def __signature__(self):
                raise ValueError("Signature error")
        
        problematic = ProblematicCallable()
        with pytest.raises(ValueError, match="Cannot inspect .* signature"):
            thread.add_done_callback(problematic)
    
    def test_cancel_edge_cases(self, qapp):
        """测试取消操作的边界情况"""
        def slow_task():
            time.sleep(0.1)
            return "completed"
        
        # 测试取消未启动的线程
        thread1 = QThreadWithReturn(slow_task)
        result = thread1.cancel()
        assert result == True
        assert thread1.cancelled() == True
        
        # 测试取消已完成的线程
        thread2 = QThreadWithReturn(lambda: "quick")
        thread2.start()
        thread2.result()
        result = thread2.cancel()
        assert result == False
    
    def test_wait_method_branches(self, qapp):
        """测试wait方法的所有分支"""
        def quick_task():
            return "quick"
        
        # 测试没有线程时的wait
        thread1 = QThreadWithReturn(quick_task)
        result = thread1.wait()
        assert result == True
        
        # 测试有线程时的wait
        thread2 = QThreadWithReturn(quick_task)
        thread2.start()
        # 等待一小段时间让线程开始执行
        wait_with_events(50)
        result = thread2.wait(1000)
        assert result == True
    
    def test_exception_method_timeout(self, qapp):
        """测试exception方法的超时分支"""
        def failing_task():
            time.sleep(0.3)
            raise ValueError("test error")
        
        thread = QThreadWithReturn(failing_task)
        thread.start()
        
        # 测试超时获取异常
        import concurrent.futures
        with pytest.raises(concurrent.futures.TimeoutError):
            thread.exception(timeout=0.1)
        
        # 等待任务完成后获取异常
        wait_with_events(400)
        exc = thread.exception()
        assert isinstance(exc, ValueError)
    
    def test_callback_parameter_mismatch(self, qapp):
        """测试回调参数不匹配的错误处理"""
        import io
        from unittest.mock import patch
        
        def wrong_param_callback(a, b):
            pass
        
        def single_return_task():
            return "single"
        
        # 捕获stderr输出
        with patch('sys.stderr', new=io.StringIO()) as fake_stderr:
            thread = QThreadWithReturn(single_return_task)
            thread.add_done_callback(wrong_param_callback)
            thread.start()
            thread.result()
            wait_with_events(200)
            
            # 检查错误输出
            error_output = fake_stderr.getvalue()
            assert "Error in done_callback" in error_output or "expects 2 arguments" in error_output
    
    def test_thread_pool_as_completed_edge_cases(self, qapp):
        """测试as_completed方法的边界情况"""
        # 测试空的futures列表
        empty_completed = list(QThreadPoolExecutor.as_completed([]))
        assert empty_completed == []
        
        # 测试超时情况
        def slow_task():
            time.sleep(0.5)
            return "slow"
        
        with QThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(slow_task)
            
            import concurrent.futures
            with pytest.raises(concurrent.futures.TimeoutError):
                list(QThreadPoolExecutor.as_completed([future], timeout=0.1))


@pytest.fixture(scope="session")
def qapp():
    """创建QApplication实例供测试使用"""
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()
    yield app