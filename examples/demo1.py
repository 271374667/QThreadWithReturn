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