#!/usr/bin/env python3
"""QThreadWithReturn 基本使用示例

演示 QThreadWithReturn 和 QThreadPoolExecutor 的核心功能。
"""

import sys
import time
import random
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication
from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor


def demo_basic_usage():
    """基本使用演示"""
    print("=== 基本使用演示 ===")
    
    def calculate(x, y):
        """模拟耗时计算"""
        print(f"开始计算 {x} + {y}")
        time.sleep(1)
        result = x + y
        print(f"计算完成: {x} + {y} = {result}")
        return result
    
    # 创建线程
    thread = QThreadWithReturn(calculate, 10, y=20)
    
    # 添加回调
    thread.add_done_callback(lambda result: print(f"✅ 回调收到结果: {result}"))
    
    # 启动线程
    thread.start()
    
    # 获取结果
    result = thread.result()
    print(f"🎯 最终结果: {result}")
    print()


def demo_callback_features():
    """回调功能演示"""
    print("=== 回调功能演示 ===")
    
    # 1. 无参数回调
    def no_param_task():
        time.sleep(0.5)
        return "任务完成"
    
    thread1 = QThreadWithReturn(no_param_task)
    thread1.add_done_callback(lambda: print("✅ 无参数回调: 任务已完成"))
    thread1.start()
    thread1.result()
    
    # 2. 多返回值自动解包
    def multi_return_task():
        time.sleep(0.5)
        return "张三", 25, "工程师"
    
    thread2 = QThreadWithReturn(multi_return_task)
    thread2.add_done_callback(lambda name, age, job: 
                             print(f"✅ 多参数回调: 姓名={name}, 年龄={age}, 职业={job}"))
    thread2.start()
    thread2.result()
    
    # 3. 类方法回调
    class ResultHandler:
        def __init__(self):
            self.results = []
        
        def handle_result(self, result):
            self.results.append(result)
            print(f"✅ 类方法回调: 收到结果 {result}")
    
    handler = ResultHandler()
    thread3 = QThreadWithReturn(lambda: "来自类方法的结果")
    thread3.add_done_callback(handler.handle_result)
    thread3.start()
    thread3.result()
    print(f"处理器收集的结果: {handler.results}")
    print()


def demo_error_handling():
    """错误处理演示"""
    print("=== 错误处理演示 ===")
    
    def failing_task():
        time.sleep(0.5)
        raise ValueError("模拟错误")
    
    thread = QThreadWithReturn(failing_task)
    
    # 添加失败回调
    thread.add_failure_callback(lambda exc: print(f"❌ 失败回调: {type(exc).__name__}: {exc}"))
    
    thread.start()
    
    try:
        thread.result()
    except ValueError as e:
        print(f"❌ 捕获异常: {e}")
    
    print()


def demo_timeout_and_cancellation():
    """超时和取消演示"""
    print("=== 超时和取消演示 ===")
    
    def long_running_task():
        for i in range(10):
            time.sleep(0.5)
            print(f"  长时间任务进度: {i+1}/10")
        return "长时间任务完成"
    
    # 超时演示
    print("1. 超时演示:")
    thread1 = QThreadWithReturn(long_running_task)
    thread1.start()
    
    try:
        result = thread1.result(timeout=2.0)  # 2秒超时
        print(f"✅ 任务完成: {result}")
    except TimeoutError:
        print("⏰ 任务超时")
        thread1.cancel(force_stop=True)
    
    # 取消演示
    print("\n2. 取消演示:")
    thread2 = QThreadWithReturn(long_running_task)
    thread2.start()
    
    # 等待1秒后取消
    time.sleep(1)
    if thread2.cancel():
        print("✅ 任务已取消")
    else:
        print("❌ 任务取消失败")
    
    print()


def demo_thread_pool():
    """线程池演示"""
    print("=== 线程池演示 ===")
    
    def worker_task(task_id):
        """工作任务"""
        processing_time = random.uniform(0.5, 2.0)
        print(f"  任务 {task_id} 开始处理 (预计 {processing_time:.1f}s)")
        time.sleep(processing_time)
        result = f"任务 {task_id} 完成"
        print(f"  ✅ {result}")
        return result
    
    # 使用上下文管理器
    with QThreadPoolExecutor(max_workers=3, thread_name_prefix="工作线程") as pool:
        # 批量提交任务
        print("提交 8 个任务到线程池:")
        futures = [pool.submit(worker_task, i) for i in range(1, 9)]
        
        # 按完成顺序获取结果
        print("\n按完成顺序获取结果:")
        for i, future in enumerate(QThreadPoolExecutor.as_completed(futures), 1):
            result = future.result()
            print(f"第 {i} 个完成: {result}")
    
    print()


def demo_advanced_thread_pool():
    """高级线程池功能演示"""
    print("=== 高级线程池功能演示 ===")
    
    def init_worker(worker_id):
        """工作线程初始化"""
        print(f"  🔧 初始化工作线程: {worker_id}")
    
    def compute_task(x):
        """计算任务"""
        result = x ** 2
        print(f"  计算: {x}² = {result}")
        time.sleep(0.3)
        return result
    
    with QThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="计算线程",
        initializer=init_worker,
        initargs=("数据处理器",)
    ) as pool:
        print("提交计算任务:")
        
        # 提交任务并添加回调
        futures = []
        for x in [2, 3, 4, 5]:
            future = pool.submit(compute_task, x)
            future.add_done_callback(
                lambda result, x=x: print(f"  ✅ 回调: {x}的平方是 {result}")
            )
            futures.append(future)
        
        # 等待所有任务完成
        results = [f.result() for f in futures]
        print(f"\n所有计算结果: {results}")
    
    print()


def main():
    """主函数"""
    print("🚀 QThreadWithReturn 使用示例\n")
    
    # 创建 Qt 应用（如果需要）
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    try:
        # 运行所有演示
        demo_basic_usage()
        demo_callback_features()
        demo_error_handling()
        demo_timeout_and_cancellation()
        demo_thread_pool()
        demo_advanced_thread_pool()
        
        print("🎉 所有演示完成!")
        
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")


if __name__ == "__main__":
    main()