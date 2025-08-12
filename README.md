# QThreadWithReturn

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![PySide6](https://img.shields.io/badge/PySide6-6.4+-green.svg)](https://www.qt.io/qt-for-python)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-73%20passed-brightgreen.svg)](tests/)

**PySide6 高级线程工具库** - 专为 GUI 应用设计的线程解决方案，让界面永不卡顿，多线程编程变得简单直观。

> 🎯 **核心使命**: 代替 QThread 原本繁杂的信号与槽机制，为简单的耗时任务提供更简单、更直观的线程编程体验。

## ✨ 核心特性

### 🎯 QThreadWithReturn - 带返回值的线程类
- **🔄 支持获取线程执行结果** - 类似 `concurrent.futures.Future` 的 API
- **🎨 灵活的回调机制** - 支持无参数、单参数、多参数回调，自动解包返回值
- **⏰ 超时控制** - 支持线程超时自动取消
- **🛑 优雅取消与强制终止** - 支持优雅取消和强制终止
- **🔄 自动处理 Qt 事件循环** - 避免界面冻结
- **🔒 线程安全的状态管理** - 安全的状态同步机制

### 🏊‍♂️ QThreadPoolExecutor - 线程池执行器  
- **🔗 API 兼容** - 完全兼容 `concurrent.futures.ThreadPoolExecutor`
- **⚡ 自动管理线程生命周期** - 智能任务调度和线程复用
- **🎛️ 支持线程初始化器和命名** - 便于调试和监控
- **🔄 支持 `as_completed` 等标准接口** - 熟悉的异步编程模式
- **📦 支持上下文管理器** - 自动资源清理

## 🚀 快速开始

### 安装

```bash
# 使用 uv (推荐)
uv add qthreadwithreturn

# 使用 pip  
pip install qthreadwithreturn
```

### 🖥️ GUI 应用中的优势 - 界面永不卡顿

QThreadWithReturn 的最大优势是在 GUI 应用中执行耗时任务时，**界面保持响应，用户体验流畅**。

#### ❌ 传统做法：界面卡死

```python
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel
import time

class BadExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.button = QPushButton("开始耗时任务 (界面会卡死)")
        self.label = QLabel("准备就绪")
        self.button.clicked.connect(self.bad_long_task)
    
    def bad_long_task(self):
        """❌ 错误：在主线程中执行耗时任务"""
        self.label.setText("处理中...")
        time.sleep(5)  # 界面卡死5秒！
        self.label.setText("完成")
```

#### ✅ QThreadWithReturn：界面始终响应

```python
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QProgressBar
from qthreadwithreturn import QThreadWithReturn
import time

class GoodExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.button = QPushButton("开始耗时任务 (界面保持响应)")
        self.label = QLabel("准备就绪")
        self.progress = QProgressBar()
        self.button.clicked.connect(self.good_long_task)
    
    def good_long_task(self):
        """✅ 正确：使用 QThreadWithReturn"""
        def heavy_computation():
            # 模拟耗时计算：数据分析、文件处理、网络请求等
            for i in range(100):
                time.sleep(0.05)  # 总共5秒
                # 可以在这里检查取消请求
            return "计算完成！"
        
        # 创建线程
        self.thread = QThreadWithReturn(heavy_computation)
        
        # 设置开始状态
        self.button.setEnabled(False)
        self.label.setText("后台处理中...")
        self.progress.setRange(0, 0)  # 不确定进度
        
        # 添加完成回调（自动在主线程执行，安全更新UI）
        self.thread.add_done_callback(self.on_task_completed)
        
        # 添加错误处理
        self.thread.add_failure_callback(self.on_task_failed)
        
        # 启动线程
        self.thread.start()
        # 界面立即响应，用户可以继续操作其他控件！
    
    def on_task_completed(self, result):
        """任务完成回调 - 在主线程中执行，安全更新UI"""
        self.button.setEnabled(True)
        self.label.setText(f"✅ {result}")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
    
    def on_task_failed(self, exception):
        """任务失败回调"""
        self.button.setEnabled(True)
        self.label.setText(f"❌ 出错了: {exception}")
        self.progress.setRange(0, 1)
```

#### 🏊‍♂️ 线程池：并行处理多个任务

```python
class BatchProcessingExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.start_btn = QPushButton("批量处理文件")
        self.progress = QProgressBar()
        self.status = QLabel("就绪")
        self.start_btn.clicked.connect(self.process_files)
    
    def process_files(self):
        file_list = ["file1.txt", "file2.txt", "file3.txt", "file4.txt"]
        
        def process_single_file(filename):
            # 模拟文件处理：解析、转换、上传等
            time.sleep(2)
            return f"{filename} 处理完成"
        
        # 创建线程池
        self.pool = QThreadPoolExecutor(max_workers=2)
        self.completed_count = 0
        self.total_files = len(file_list)
        
        # UI状态
        self.progress.setMaximum(self.total_files)
        self.progress.setValue(0)
        self.status.setText("批量处理中...")
        
        # 提交所有任务
        for filename in file_list:
            future = self.pool.submit(process_single_file, filename)
            future.add_done_callback(self.on_file_completed)
    
    def on_file_completed(self, result):
        """单个文件完成回调"""
        self.completed_count += 1
        self.progress.setValue(self.completed_count)
        self.status.setText(f"已完成 {self.completed_count}/{self.total_files}: {result}")
        
        if self.completed_count == self.total_files:
            self.status.setText("✅ 所有文件处理完成！")
            self.pool.shutdown()
```

## 🆚 与传统 QThread 对比

### ❌ 传统 QThread 的复杂性

```python
from PySide6.QtCore import QThread, QObject, pyqtSignal

# 传统方式：需要大量样板代码
class Worker(QObject):
    finished = pyqtSignal(object)  # 需要手动定义信号
    error = pyqtSignal(Exception)
    
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
    
    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)  # 手动发射信号
        except Exception as e:
            self.error.emit(e)

# 使用时需要更多代码
def traditional_approach():
    thread = QThread()
    worker = Worker(my_function, arg1, arg2)
    worker.moveToThread(thread)
    
    # 手动连接信号
    thread.started.connect(worker.run)
    worker.finished.connect(lambda result: print(f"结果: {result}"))
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    
    thread.start()
    # 无法直接获取返回值！需要额外处理
```

### ✅ QThreadWithReturn 的简洁性

```python
# 一行创建，自动处理所有细节！
thread = QThreadWithReturn(my_function, arg1, arg2)
thread.add_done_callback(lambda result: print(f"结果: {result}"))
thread.start()

# 直接获取返回值
result = thread.result()  # 就这么简单！
```

### 🔥 核心优势对比

| 特性 | 传统 QThread | QThreadWithReturn |
|------|-------------|------------------|
| **代码量** | 20+ 行样板代码 | 3 行核心代码 |
| **返回值** | ❌ 需要信号传递 | ✅ 直接 `result()` 获取 |
| **错误处理** | ❌ 手动信号连接 | ✅ 自动异常传播 + 回调 |
| **资源清理** | ❌ 手动管理生命周期 | ✅ 自动清理资源 |
| **超时控制** | ❌ 需要额外实现 | ✅ 内置超时机制 |
| **取消任务** | ❌ 复杂的中断处理 | ✅ 一键取消 `cancel()` |
| **线程池** | ❌ 需要自己实现 | ✅ 完整的线程池支持 |
| **学习成本** | ❌ 需要深入理解Qt信号 | ✅ 符合直觉的API |

## 📚 高级功能

### 🎨 灵活的回调机制

```python
# 无参数回调
thread.add_done_callback(lambda: print("任务完成!"))

# 单参数回调
thread.add_done_callback(lambda result: print(f"结果: {result}"))

# 多参数回调 - 支持自动解包
def multi_return_task():
    return 1, 2, 3

thread = QThreadWithReturn(multi_return_task)
thread.add_done_callback(lambda a, b, c: print(f"{a}, {b}, {c}"))  # 自动解包

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
# 优雅取消（推荐）
success = thread.cancel()

# 强制终止（谨慎使用）
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

## 🎮 在线演示程序

### 🆚 GUI 对比演示 - 直观感受差异
运行交互式对比程序，直观体验界面响应性的巨大差异：

```bash
# 对比演示：传统方法 vs QThreadWithReturn
python examples/gui_demo_comparison.py
```

**演示内容**:
- **❌ 传统做法标签页**: 点击按钮后界面卡死5秒，滑块无法拖动
- **✅ QThreadWithReturn标签页**: 界面始终响应，所有控件正常工作
- **🏊‍♂️ 线程池标签页**: 并行处理多个任务，实时显示进度

### 📱 完整功能演示
项目还包含功能完整的 GUI 演示程序：

```bash
# 完整功能演示
python -m demo.thread_demo_gui
```

### 💻 命令行示例
基础功能快速体验：

```bash
# 基本用法示例
python examples/basic_usage.py
```

![QThreadWithReturn Demo](https://via.placeholder.com/700x450?text=QThreadWithReturn+GUI+Demo+%E2%80%93+%E7%95%8C%E9%9D%A2%E6%B0%B8%E4%B8%8D%E5%8D%A1%E9%A1%BF)

## 🎯 实际应用场景

QThreadWithReturn 特别适合以下 GUI 应用场景：

### 📊 **数据处理应用**
```python
# 大数据分析、Excel处理、CSV导入导出
thread = QThreadWithReturn(pandas.read_csv, "large_file.csv")
thread.add_done_callback(lambda df: self.update_table_view(df))
```

### 🌐 **网络应用**
```python  
# HTTP请求、文件下载、API调用
thread = QThreadWithReturn(requests.get, "https://api.example.com/data")
thread.add_done_callback(lambda resp: self.display_data(resp.json()))
```

### 🎨 **图像处理工具**
```python
# 图像滤镜、格式转换、批量处理
with QThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(process_image, img) for img in images]
    for future in pool.as_completed(futures):
        self.update_progress()
```

### 📁 **文件管理器**
```python
# 文件搜索、批量重命名、大文件复制
thread = QThreadWithReturn(shutil.copy2, source, destination)  
thread.add_done_callback(lambda: self.refresh_file_list())
```

### 🤖 **AI/机器学习工具**
```python
# 模型训练、图像识别、自然语言处理
thread = QThreadWithReturn(model.predict, input_data)
thread.add_done_callback(lambda result: self.show_predictions(result))
```

## 🔧 兼容性

- **Python**: 3.10+ (完整支持 3.11+)
- **Qt 版本**: PySide6 6.4+  
- **操作系统**: Windows, macOS, Linux
- **特殊支持**: 
  - ✅ 有 Qt 应用环境：使用 Qt 信号机制，完整功能
  - ✅ 无 Qt 应用环境：自动切换到标准线程模式
  - ✅ 多Python版本：在 3.10、3.11、3.13 中测试通过

## 🧪 测试

项目具有完整的测试覆盖率，包含 73 个测试用例：

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_thread_utils.py -v

# 生成覆盖率报告
pytest tests/ --cov=qthreadwithreturn
```

## 📖 API 文档

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

### ❌ 避免的做法

```python
# ❌ 不要在工作线程中直接更新 UI
def bad_worker():
    label.setText("更新")  # 错误！跨线程UI更新

# ❌ 不要忘记资源清理  
pool = QThreadPoolExecutor()
# ... 使用pool但忘记调用shutdown()

# ❌ 不要过度使用强制终止
thread.cancel(force_stop=True)  # 可能导致资源泄漏
```

## 🤝 贡献

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 开启 Pull Request

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/your-username/qthreadwithreturn.git
cd qthreadwithreturn

# 使用 uv 安装依赖
uv sync

# 运行测试
uv run pytest

# 运行演示
uv run python -m demo.thread_demo_gui
```

## 📄 许可证

本项目使用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- 感谢 Qt 团队提供强大的跨平台框架
- 感谢 Python 社区的 `concurrent.futures` 设计灵感
- 感谢所有贡献者和用户的支持

## 📞 支持

- 🐛 **问题报告**: [GitHub Issues](https://github.com/your-username/qthreadwithreturn/issues)
- 💬 **讨论**: [GitHub Discussions](https://github.com/your-username/qthreadwithreturn/discussions)
- 📧 **邮件**: your-email@example.com

---

<div align="center">

**让多线程编程变得简单直观** ✨

[⭐ 给个星标](https://github.com/your-username/qthreadwithreturn) | [📖 查看文档](https://qthreadwithreturn.readthedocs.io) | [🚀 快速开始](#-快速开始)

</div>