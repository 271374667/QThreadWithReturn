<div align="center">

# QThreadWithReturn

![QThreadWithReturn](https://socialify.git.ci/271374667/QThreadWithReturn/image?description=1&language=1&name=1&pattern=Plus&theme=Auto)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![PySide6](https://img.shields.io/badge/PySide6-6.4+-green.svg)](https://www.qt.io/qt-for-python)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-200%20passed-brightgreen.svg)](tests/)

基于 PySide6 的多线程高度封装库，简化 GUI 应用中的多线程编程。

简单易用，支持返回值和回调机制，避免复杂的信号槽设置、完善的内存回收、超时控制和任务取消功能、线程池支持、以及完整的类型提示。

</div>

## 简介

**该库针对需要后台耗时运行，且只有在完成后才需要更新 UI 的场景**
进行了高度封装，例如进行网络爬虫获取数据，或者进行大规模数据处理，通过回调函数的方式代替了信号与槽机制，能够在保持高封装性的同时，减少大量信号的使用，同时内部自动处理了线程的启动、结束和资源回收等问题，使得多线程编程变得更加简单和直观。

*如果是需要在线程运行过程中频繁更新 UI 的场景，或者需要线程间频繁通信的场景，建议使用传统的 `QThread` 和信号槽机制*

## 快速开始

**首先您的逻辑和界面代码应该是分离的**，不能写在同一个类里面，最好分为多个 `.py` 文件。其次是写在同一个 `.py`
文件但是不同的类里面。如果逻辑和界面的操作写在一起反而不如原本的 `QThread` 方式，会导致您的项目更加混乱。

下面是一个简单的银行取款的例子，假设取款是一个耗时操作，他是一系列复杂的逻辑操作，于是他被写在另一个类 `Bank` 里面，而界面代码写在
`MyWindow` 里面。
界面当中有一个按钮，当点击之后会调用 `Bank` 里面的 `draw` 方法进行取款操作，取款成功之后会更新界面。
其中 `draw` 方法的返回值会被 `QThreadWithReturn` 自动捕获，然后传入 `finished` 函数，而如果发生异常则会传入 `failure` 函数。

其中的 `finished` 和 `failure` 函数都是线程运行完毕之后运行的回调函数，**都是在主线程中运行的，所以可以直接操作界面控件**
，以及访问各种类的属性。

```python
"""
QThreadWithReturn的基础例子
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel

from qthreadwithreturn import QThreadWithReturn


class Bank:
    """模拟银行取款操作

    这里是耗时操作的例子，在实际的项目中逻辑应该放在其他的模块中，不应该和界面代码混在一起
    这样做只是为了演示 QThreadWithReturn 的使用
    """

    def draw(self, amount: float) -> str:
        """模拟取款操作"""
        time.sleep(2)  # 模拟耗时操作
        return f"成功取款 {amount} 元"


class MyWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("QThreadWithReturn 示例")
        self.setGeometry(100, 100, 300, 200)

        self.bank = Bank()

        self.button = QPushButton("取款 100 元", self)
        self.button.setGeometry(50, 50, 200, 40)

        self.label = QLabel("等待取款...", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.label.setGeometry(50, 100, 200, 40)

        self.button.clicked.connect(self.start_draw)

    def start_draw(self):
        """开始取款操作"""
        self.button.setEnabled(False)
        self.label.setText("取款中...")

        # 使用 QThreadWithReturn 进行取款操作
        # 通过finished和failure两个闭包函数节约了两个信号，运行完毕之后返回值会自动传入finished函数
        # 如果发生异常则会传入failure函数
        def finished(result: str):
            # 成功后自动调用(传入参数为self.bank.draw的返回值)
            self.label.setText(result)
            self.button.setEnabled(True)

        def failure(result: Exception):
            # 失败后自动调用(传入参数为self.bank.draw抛出的异常)
            self.label.setText(f"取款失败: {result}")
            self.button.setEnabled(True)

        thread = QThreadWithReturn(self.bank.draw, 100)  # 调用取款方法,传入参数100
        thread.add_done_callback(finished)
        thread.add_failure_callback(failure)
        thread.start()


if __name__ == '__main__':
    app = QApplication([])
    window = MyWindow()
    window.show()
    app.exec()

```

从上面其实就能看到 QThreadWithReturn
的优势，对于原本需要使用信号来传递的返回值和异常，现在都可以通过回调函数来处理，而且逻辑都可以通过闭包的形式写在相应的方法里面，
而不会污染其他的命名空间，即使出现了问题也能很快定位到问题所在。您也无需担心内存泄漏的问题，因为 QThreadWithReturn
会自动处理线程的结束和资源回收。
假设您的项目中有很多类似的耗时操作需要在后台运行，并且只有在完成后才需要更新 UI，那么使用 QThreadWithReturn
会让您的代码变得更加简洁、安全、易读。

### 总结

1. 逻辑代码和界面代码分离
2. 创建一个 QThreadWithReturn 对象，传入需要运行的函数和参数
3. 使用 `add_done_callback` 方法添加成功回调函数
4. 使用 `add_failure_callback` 方法添加失败回调函数(可选,如果不需要处理异常可以不添加)
5. 调用 `start` 方法启动线程

建议使用闭包函数的方式来定义回调函数，这样可以避免命名冲突，并且可以直接访问类的属性和方法。

## ✨ 特性

### 🎯 QThreadWithReturn

- `concurrent.futures.Future` 的 API，无需二次学习，快速上手
- 内置超时控制和任务取消(包括强制停止)
- 自动管理线程生命周期，防止内存泄漏
- 支持任意可调用对象（函数、方法、lambda 等）
- 完整的类型提示
- 与 Qt 事件循环无缝集成

### 🏊‍♂️ QThreadPoolExecutor

- `concurrent.futures.ThreadPoolExecutor` 的 API，无需二次学习，快速上手
- 线程池管理和任务调度
- 支持线程初始化器和命名
- 支持 `as_completed` 方法按完成顺序处理任务
- 任务取消和强制停止支持
- 完整的类型提示
- 上下文管理器支持

## 🚀 安装

```bash
# 使用 uv
uv add qthreadwithreturn

# 使用 pip  
pip install qthreadwithreturn
```

## 📖 API 参考

这里只给出公开方法的简要说明，完整的文档请参考具体的函数文档,在安装了 `qthreadwithreturn` 之后在 IDE 中悬浮就可以查看帮助

### QThreadWithReturn

带返回值的 Qt 线程类，提供类似 `concurrent.futures.Future` 的 API。

| 方法                                                     | 描述             |
|--------------------------------------------------------|----------------|
| `start(timeout_ms: int = -1)`                          | 启动线程执行任务       |
| `result(timeout_ms: int = -1)`                         | 获取任务执行结果，阻塞等待  |
| `exception(timeout_ms: int = -1)`                      | 获取任务执行时抛出的异常   |
| `cancel(force_stop: bool = False)`                     | 取消线程执行         |
| `running()`                                            | 检查任务是否正在运行     |
| `done()`                                               | 检查任务是否已完成      |
| `cancelled()`                                          | 检查任务是否被取消      |
| `wait(timeout_ms: int = -1, force_stop: bool = False)` | 等待任务完成         |
| `add_done_callback(callback: Callable)`                | 添加任务成功完成后的回调函数 |
| `add_failure_callback(callback: Callable)`             | 添加任务失败后的回调函数   |

### QThreadPoolExecutor

线程池执行器，API 兼容 `concurrent.futures.ThreadPoolExecutor`。

不建议使用 `with` 语句，因为在 GUI 应用中会导致 UI 阻塞。

#### 静态方法

| 方法                                                                     | 返回类型                            | 描述                        |
|------------------------------------------------------------------------|---------------------------------|---------------------------|
| `as_completed(fs: Iterable["QThreadWithReturn"], timeout: float = -1)` | `Iterator["QThreadWithReturn"]` | 返回一个迭代器，按完成顺序生成 Future 对象 |

#### 实例方法

| 方法                                                                                        | 描述                    |
|-------------------------------------------------------------------------------------------|-----------------------|
| `submit(fn: Callable, /, *args, **kwargs)`                                                | 提交任务到线程池执行            |
| `shutdown(wait: bool = False, *, cancel_futures: bool = False, force_stop: bool = False)` | 关闭线程池                 |
| `add_done_callback(callback: Callable)`                                                   | 添加池级别完成回调，当所有任务完成时执行  |
| `add_failure_callback(callback: Callable)`                                                | 添加任务级别失败回调，当任何任务失败时执行 |

- **add_done_callback**：当所有活跃任务完成且没有待处理任务时触发
- **add_failure_callback**：每个失败任务都会触发一次

### 🛠️ 开发环境设置

本项目使用 uv 进行配置，您可以前往 https://docs.astral.sh/uv/ 了解更多关于 uv 的相关内容。

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