<div align="center">

# QThreadWithReturn

![QThreadWithReturn](https://socialify.git.ci/271374667/QThreadWithReturn/image?description=1&language=1&name=1&pattern=Plus&theme=Auto)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![PySide6](https://img.shields.io/badge/PySide6-6.4+-green.svg)](https://www.qt.io/qt-for-python)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-73%20passed-brightgreen.svg)](tests/)

一个基于 PySide6 的线程工具库，简化 GUI 应用中的多线程编程。

QThreadWithReturn 为传统 QThread 提供了更直观的 API，支持返回值和回调机制，避免复杂的信号槽设置。

</div>

## ✨ 特性

### 🎯 QThreadWithReturn
- 支持获取线程执行结果，提供类似 `concurrent.futures.Future` 的 API
- 灵活的回调机制，支持多种回调函数签名
- 内置超时控制和任务取消
- 线程安全的状态管理
- 与 Qt 事件循环无缝集成

### 🏊‍♂️ QThreadPoolExecutor
- 完全兼容 `concurrent.futures.ThreadPoolExecutor` API
- 线程池管理和任务调度
- 支持线程初始化器和命名，便于调试
- 支持 `as_completed` 等标准接口
- 上下文管理器支持

## 🚀 安装

```bash
# 使用 uv
uv add qthreadwithreturn

# 使用 pip  
pip install qthreadwithreturn
```

## 💡 使用示例

### 问题场景

在 GUI 应用中执行耗时操作时，传统做法会阻塞主线程，导致界面无响应。

#### ❌ 传统做法的问题

```python
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QVBoxLayout, QWidget
import time
import sys

class BadExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("传统做法示例")
        
        # 创建中心组件和布局
        central_widget = QWidget()
        layout = QVBoxLayout()
        
        self.button = QPushButton("执行耗时任务")
        self.label = QLabel("就绪")
        
        layout.addWidget(self.label)
        layout.addWidget(self.button)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        
        self.button.clicked.connect(self.blocking_task)
    
    def blocking_task(self):
        """在主线程执行，会阻塞界面"""
        self.label.setText("处理中...")
        time.sleep(5)  # 主线程被阻塞
        self.label.setText("完成")

# 运行示例
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BadExample()
    window.show()
    app.exec()
```

#### ✅ 使用 QThreadWithReturn 的解决方案

```python
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QProgressBar, QVBoxLayout, QWidget
from qthreadwithreturn import QThreadWithReturn
import time
import sys

class GoodExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QThreadWithReturn 示例")
        
        # 创建中心组件和布局
        central_widget = QWidget()
        layout = QVBoxLayout()
        
        self.button = QPushButton("执行耗时任务")
        self.label = QLabel("就绪")
        self.progress = QProgressBar()
        
        layout.addWidget(self.label)
        layout.addWidget(self.progress)
        layout.addWidget(self.button)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        
        self.button.clicked.connect(self.start_task)
    
    def start_task(self):
        """使用 QThreadWithReturn 在后台执行任务"""
        def heavy_computation():
            # 模拟耗时计算
            for i in range(100):
                time.sleep(0.05)
            return "处理完成"
        
        # 创建线程
        self.thread = QThreadWithReturn(heavy_computation)
        
        # 更新UI状态
        self.button.setEnabled(False)
        self.label.setText("后台处理中...")
        self.progress.setRange(0, 0)
        
        # 设置回调
        self.thread.add_done_callback(self.on_task_completed)
        self.thread.add_failure_callback(self.on_task_failed)
        
        # 启动线程，界面保持响应
        self.thread.start()
    
    def on_task_completed(self, result):
        """任务完成回调"""
        self.button.setEnabled(True)
        self.label.setText(result)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
    
    def on_task_failed(self, exception):
        """任务失败回调"""
        self.button.setEnabled(True)
        self.label.setText(f"处理失败: {exception}")
        self.progress.setRange(0, 1)

# 运行示例
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GoodExample()
    window.show()
    app.exec()
```

#### 🏊‍♂️ 线程池使用示例

```python
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QProgressBar, QVBoxLayout, QWidget
from qthreadwithreturn import QThreadPoolExecutor
import time
import sys

class BatchProcessingExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("线程池批处理示例")
        
        # 创建中心组件和布局
        central_widget = QWidget()
        layout = QVBoxLayout()
        
        self.start_btn = QPushButton("开始批处理")
        self.progress = QProgressBar()
        self.status = QLabel("就绪")
        
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.start_btn)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        
        self.start_btn.clicked.connect(self.process_files)
    
    def process_files(self):
        file_list = ["file1.txt", "file2.txt", "file3.txt", "file4.txt"]
        
        def process_single_file(filename):
            # 模拟文件处理
            time.sleep(2)
            return f"{filename} 完成"
        
        # 创建线程池
        self.pool = QThreadPoolExecutor(max_workers=2)
        self.completed_count = 0
        self.total_files = len(file_list)
        
        # 更新UI
        self.progress.setMaximum(self.total_files)
        self.progress.setValue(0)
        self.status.setText("处理中...")
        self.start_btn.setEnabled(False)
        
        # 提交任务
        for filename in file_list:
            future = self.pool.submit(process_single_file, filename)
            future.add_done_callback(self.on_file_completed)
    
    def on_file_completed(self, result):
        """任务完成回调"""
        self.completed_count += 1
        self.progress.setValue(self.completed_count)
        self.status.setText(f"完成 {self.completed_count}/{self.total_files}: {result}")
        
        if self.completed_count == self.total_files:
            self.status.setText("所有任务完成")
            self.start_btn.setEnabled(True)
            self.pool.shutdown()

# 运行示例
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BatchProcessingExample()
    window.show()
    app.exec()
```

## 🆚 与传统 QThread 对比

### 传统 QThread 实现

```python
from PySide6.QtCore import QThread, QObject, Signal

# 传统方式需要较多样板代码
class Worker(QObject):
    finished = Signal(object)
    error = Signal(Exception)
    
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
    
    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(e)

# 使用时的设置
def traditional_approach():
    thread = QThread()
    worker = Worker(my_function, arg1, arg2)
    worker.moveToThread(thread)
    
    # 信号连接
    thread.started.connect(worker.run)
    worker.finished.connect(lambda result: print(f"结果: {result}"))
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    
    thread.start()
    # 获取返回值需要通过信号处理
```

### QThreadWithReturn 实现

```python
# 简化的使用方式
thread = QThreadWithReturn(my_function, arg1, arg2)
thread.add_done_callback(lambda result: print(f"结果: {result}"))
thread.start()

# 直接获取返回值
result = thread.result()
```

### 对比总结

| 特性 | 传统 QThread | QThreadWithReturn |
|------|-------------|------------------|
| **代码量** | 较多样板代码 | 简化的接口 |
| **返回值** | 信号传递 | 直接 `result()` 获取 |
| **错误处理** | 手动信号连接 | 自动异常传播 |
| **资源清理** | 手动管理 | 自动清理 |
| **超时控制** | 需额外实现 | 内置支持 |
| **任务取消** | 需自行处理 | 内置 `cancel()` |
| **线程池** | 需自己实现 | 提供现成实现 |
| **学习成本** | 需理解信号槽 | 接近标准库 API |

## 📚 高级功能

### 🎨 回调机制

```python
# 无参数回调
thread.add_done_callback(lambda: print("任务完成"))

# 单参数回调
thread.add_done_callback(lambda result: print(f"结果: {result}"))

# 多参数回调 - 自动解包
def multi_return_task():
    return 1, 2, 3

thread = QThreadWithReturn(multi_return_task)
thread.add_done_callback(lambda a, b, c: print(f"{a}, {b}, {c}"))

# 类方法回调
class ResultHandler:
    def handle_result(self, result):
        self.result = result

handler = ResultHandler()
thread.add_done_callback(handler.handle_result)
```

### ⏰ 超时控制

```python
# 设置5秒超时
thread.start(timeout_ms=5000)

try:
    result = thread.result(timeout=5.0)
except TimeoutError:
    print("任务超时")
except Exception as e:
    print(f"任务失败: {e}")
```

### 🛑 任务取消

```python
# 优雅取消
success = thread.cancel()

# 强制终止（需谨慎使用）
success = thread.cancel(force_stop=True)

# 检查状态
if thread.cancelled():
    print("任务已取消")
```

### 🔄 错误处理

```python
def failing_task():
    raise ValueError("模拟错误")

thread = QThreadWithReturn(failing_task)

# 添加失败回调
thread.add_failure_callback(lambda exc: print(f"任务失败: {exc}"))

thread.start()

try:
    result = thread.result()
except ValueError as e:
    print(f"捕获异常: {e}")
```

### 🏊‍♂️ 线程池高级用法

```python
def init_worker(worker_name):
    """工作线程初始化"""
    print(f"初始化工作线程: {worker_name}")

def compute_task(x):
    return x ** 2

with QThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="计算线程",
    initializer=init_worker,
    initargs=("数据处理器",)
) as pool:
    # 提交任务并添加回调
    future = pool.submit(compute_task, 10)
    future.add_done_callback(lambda result: print(f"计算完成: {result}"))
    
    # 等待结果
    print(future.result())  # 输出: 100
```

## 🎮 演示程序

### 🆚 GUI 对比演示
运行对比程序，体验不同实现方式的差异：

```bash
# 对比演示
python examples/gui_demo_comparison.py
```

演示内容：
- 传统做法：界面阻塞演示
- QThreadWithReturn：响应式界面演示
- 线程池：并行任务处理演示

### 📱 完整功能演示
```bash
# 完整演示程序
python -m demo.thread_demo_gui
```

### 💻 命令行示例
```bash
# 基本用法示例
python examples/basic_usage.py
```

## 🎯 应用场景

QThreadWithReturn 适合以下 GUI 应用场景：

### 📊 数据处理应用
```python
# 数据分析、文件处理
thread = QThreadWithReturn(pandas.read_csv, "large_file.csv")
thread.add_done_callback(lambda df: self.update_table_view(df))
```

### 🌐 网络应用
```python  
# HTTP请求、API调用
thread = QThreadWithReturn(requests.get, "https://api.example.com/data")
thread.add_done_callback(lambda resp: self.display_data(resp.json()))
```

### 🎨 图像处理工具
```python
# 图像处理、批量转换
with QThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(process_image, img) for img in images]
    for future in pool.as_completed(futures):
        self.update_progress()
```

### 📁 文件管理器
```python
# 文件操作、批量处理
thread = QThreadWithReturn(shutil.copy2, source, destination)  
thread.add_done_callback(lambda: self.refresh_file_list())
```

### 🤖 机器学习工具
```python
# 模型推理、数据处理
thread = QThreadWithReturn(model.predict, input_data)
thread.add_done_callback(lambda result: self.show_predictions(result))
```

## 🔧 兼容性

- **Python**: 3.10+
- **Qt 版本**: PySide6 6.4+  
- **操作系统**: Windows, macOS, Linux
- **运行环境**: 
  - 有 Qt 应用环境：使用 Qt 信号机制
  - 无 Qt 应用环境：自动切换到标准线程模式
  - 已在 Python 3.10、3.11、3.13 中测试

## 🧪 测试

项目包含 73 个测试用例：

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_thread_utils.py -v

# 生成覆盖率报告
pytest tests/ --cov=qthreadwithreturn
```

## 📖 API 参考

### QThreadWithReturn

| 方法 | 描述 |
|------|------|
| `start(timeout_ms=-1)` | 启动线程，可选超时设置 |
| `result(timeout=None)` | 获取执行结果，阻塞等待 |
| `exception(timeout=None)` | 获取异常信息 |
| `cancel(force_stop=False)` | 取消线程执行 |
| `running()` | 检查是否正在运行 |
| `done()` | 检查是否已完成 |
| `cancelled()` | 检查是否已取消 |
| `add_done_callback(callback)` | 添加成功完成回调 |
| `add_failure_callback(callback)` | 添加失败回调 |

### QThreadPoolExecutor  

| 方法 | 描述 |
|------|------|
| `submit(fn, *args, **kwargs)` | 提交任务到线程池 |
| `shutdown(wait=True, cancel_futures=False, force_stop=False)` | 关闭线程池 |
| `as_completed(futures, timeout=None)` | 按完成顺序迭代 Future 对象 |

## 💡 最佳实践

### ✅ 推荐做法

```python
# 1. 使用上下文管理器自动资源清理
with QThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(task, i) for i in range(10)]
    results = [f.result() for f in futures]

# 2. 在回调中更新 UI（回调在主线程执行）
def update_progress(result):
    progress_bar.setValue(result)
    
thread.add_done_callback(update_progress)

# 3. 合理设置超时时间
thread.start(timeout_ms=30000)  # 30秒超时

# 4. 异常处理
try:
    result = thread.result()
except Exception as e:
    logger.error(f"任务失败: {e}")
```

### ⚠️ 注意事项

```python
# 避免在工作线程中直接更新 UI
def bad_worker():
    label.setText("更新")  # 错误：跨线程UI更新

# 记得资源清理  
pool = QThreadPoolExecutor()
# 使用完后记得调用 shutdown()

# 谨慎使用强制终止
thread.cancel(force_stop=True)  # 可能导致资源泄漏
```

## 🤝 贡献

欢迎贡献代码，请遵循以下步骤：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/new-feature`)
3. 提交更改 (`git commit -m 'Add new feature'`)
4. 推送到分支 (`git push origin feature/new-feature`)
5. 开启 Pull Request

### 🛠️ 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/271374667/QThreadWithReturn.git
cd QThreadWithReturn

# 使用 uv 安装依赖
uv sync

# 运行测试
uv run pytest

# 运行演示
uv run python -m demo.thread_demo_gui
```

## 📄 许可证

本项目使用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 📞 支持

- **问题报告**: [GitHub Issues](https://github.com/271374667/QThreadWithReturn/issues)
- **讨论**: [GitHub Discussions](https://github.com/271374667/QThreadWithReturn/discussions)
- **邮件**: 271374667@qq.com