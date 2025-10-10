import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QVBoxLayout

from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor
import random


class Bank:
    """模拟银行取款操作

    这里是耗时操作的例子，在实际的项目中逻辑应该放在其他的模块中，不应该和界面代码混在一起
    这样做只是为了演示 QThreadWithReturn 的使用
    """

    def draw(self, amount: int) -> int:
        """模拟取款操作"""
        time.sleep(2)  # 模拟耗时操作
        return amount

    def draw_lot(self) -> int:
        time.sleep(4)
        return 1000

    def draw_little(self) -> int:
        time.sleep(1)
        return 10

    def draw_error(self) -> int:
        time.sleep(1)
        raise ValueError("余额不足")


class BankLabel(QLabel):
    def __init__(self, name: str):
        super().__init__()
        self.name: str = name
        self.money: int = 0

        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setText(f"{self.name} 余额: {self.money} 元")

    def update_balance(self, amount: int):
        self.money += amount
        self.setText(f"{self.name} 余额: {self.money} 元")


class MyWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("QThreadWithReturn 示例")
        self.setGeometry(100, 100, 300, 200)

        self.bank = Bank()

        self.button = QPushButton("取款 100 元", self)

        self._xiaoming_label = BankLabel("小明")
        self._xiaohong_label = BankLabel("小红")
        self._xiaogang_label = BankLabel("小刚")
        self._xiaoqiang_label = BankLabel("小强")

        self._main_layout = QVBoxLayout(self)
        self._main_layout.addWidget(self.button)
        self._main_layout.addWidget(self._xiaoming_label)
        self._main_layout.addWidget(self._xiaohong_label)
        self._main_layout.addWidget(self._xiaogang_label)
        self._main_layout.addWidget(self._xiaoqiang_label)
        self.setLayout(self._main_layout)

        self.button.clicked.connect(self.start_draw)

    def start_draw(self):
        """开始取款操作"""
        self.button.setEnabled(False)
        self._xiaoming_label.setText("取款中...")
        self._xiaohong_label.setText("取款中...")
        self._xiaogang_label.setText("取款中...")
        self._xiaoqiang_label.setText("取款中...")

        # 使用 QThreadPoolExecutor 进行取款操作
        # 注意：不要使用 with 语句，因为 with 会在退出时调用 shutdown(wait=True)，
        # 导致阻塞主线程等待所有任务完成，界面会卡死

        def xiaoming_finished(update_value: int):
            # 成功后自动调用(传入参数为self.bank.draw的返回值)
            self._xiaoming_label.update_balance(update_value)

        def xiaohong_finished(update_value: int):
            # 成功后自动调用(传入参数为self.bank.draw的返回值)
            self._xiaohong_label.update_balance(update_value)

        def xiaogang_finished(update_value: int):
            # 成功后自动调用(传入参数为self.bank.draw的返回值)
            self._xiaogang_label.update_balance(update_value)

        def xiaoqiang_finished(update_value: int):
            # 成功后自动调用(传入参数为self.bank.draw的返回值)
            self._xiaoqiang_label.update_balance(update_value)

        def finished():
            self.button.setEnabled(True)

        def exception_handler(exc: Exception):
            print(exc)

        # 创建线程池（不使用 with 语句）
        pool = QThreadPoolExecutor(max_workers=2, thread_name_prefix="取款线程")
        pool.add_done_callback(finished)
        pool.add_failure_callback(exception_handler)

        xiaoming_future = pool.submit(self.bank.draw_lot)
        xiaoming_future.add_done_callback(xiaoming_finished)

        xiaohong_future = pool.submit(self.bank.draw, random.randint(50, 150))
        xiaohong_future.add_done_callback(xiaohong_finished)

        xiaogang_future = pool.submit(self.bank.draw_little)
        xiaogang_future.add_done_callback(xiaogang_finished)
        # xiaogang_future.add_done_callback(lambda: print('小刚取款完成'))

        xiaoqiang_future = pool.submit(self.bank.draw_error)
        xiaoqiang_future.add_done_callback(xiaoqiang_finished)

        future_list: list[QThreadWithReturn] = [
            xiaoming_future,
            xiaohong_future,
            xiaogang_future,
            xiaoqiang_future,
        ]

        # pool.shutdown(wait=False, cancel_futures=True)
        # for i in QThreadPoolExecutor.as_completed(future_list):
        #     print(i.result())


if __name__ == '__main__':
    app = QApplication([])
    window = MyWindow()
    window.show()
    app.exec()
