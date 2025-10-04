#!/usr/bin/env python3
"""GUI 对比演示：展示 QThreadWithReturn 相比传统方法的优势

运行这个程序可以直观地看到：
1. 传统方法如何导致界面卡死
2. QThreadWithReturn 如何保持界面响应
"""

import sys
import time
import random
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QProgressBar,
    QTextEdit,
    QGroupBox,
    QTabWidget,
    QSpinBox,
    QSlider,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor


class BadExampleWidget(QWidget):
    """❌ 错误示例：界面会卡死"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 标题
        title = QLabel("❌ 传统做法 - 界面会卡死")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: red; margin: 10px;")
        layout.addWidget(title)

        # 控制按钮
        self.bad_button = QPushButton("开始耗时任务 (界面将卡死5秒)")
        self.bad_button.setStyleSheet(
            "QPushButton { background-color: #ffcccc; padding: 10px; }"
        )
        self.bad_button.clicked.connect(self.start_bad_task)
        layout.addWidget(self.bad_button)

        # 状态显示
        self.bad_status = QLabel("准备就绪")
        layout.addWidget(self.bad_status)

        # 进度条
        self.bad_progress = QProgressBar()
        layout.addWidget(self.bad_progress)

        # 测试响应性的滑块
        layout.addWidget(QLabel("测试界面响应性 - 拖动滑块:"))
        self.test_slider1 = QSlider(Qt.Orientation.Horizontal)
        self.test_slider1.setRange(0, 100)
        self.test_slider1.setValue(50)
        self.slider_label1 = QLabel("50")
        self.test_slider1.valueChanged.connect(
            lambda v: self.slider_label1.setText(str(v))
        )

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.test_slider1)
        slider_layout.addWidget(self.slider_label1)
        layout.addLayout(slider_layout)

        # 说明文字
        warning = QLabel("⚠️ 点击按钮后，整个界面将冻结5秒，滑块无法拖动！")
        warning.setStyleSheet("color: orange; font-weight: bold; margin: 5px;")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.setLayout(layout)

    def start_bad_task(self):
        """❌ 错误的做法：在主线程中执行耗时任务"""
        self.bad_button.setEnabled(False)
        self.bad_status.setText("处理中... (界面已冻结)")
        self.bad_progress.setRange(0, 0)  # 不确定进度

        # 强制刷新UI
        QApplication.processEvents()

        # 模拟耗时任务 - 这会阻塞主线程！
        time.sleep(5)  # 界面卡死5秒！

        # 恢复界面
        self.bad_button.setEnabled(True)
        self.bad_status.setText("✅ 任务完成 (但用户体验很差)")
        self.bad_progress.setRange(0, 1)
        self.bad_progress.setValue(1)


class GoodExampleWidget(QWidget):
    """✅ 正确示例：界面始终响应"""

    def __init__(self):
        super().__init__()
        self.thread = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 标题
        title = QLabel("✅ QThreadWithReturn - 界面始终响应")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: green; margin: 10px;")
        layout.addWidget(title)

        # 控制按钮
        self.good_button = QPushButton("开始耗时任务 (界面保持响应)")
        self.good_button.setStyleSheet(
            "QPushButton { background-color: #ccffcc; padding: 10px; }"
        )
        self.good_button.clicked.connect(self.start_good_task)
        layout.addWidget(self.good_button)

        # 取消按钮
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_task)
        layout.addWidget(self.cancel_button)

        # 状态显示
        self.good_status = QLabel("准备就绪")
        layout.addWidget(self.good_status)

        # 进度条
        self.good_progress = QProgressBar()
        layout.addWidget(self.good_progress)

        # 测试响应性的滑块
        layout.addWidget(QLabel("测试界面响应性 - 拖动滑块:"))
        self.test_slider2 = QSlider(Qt.Orientation.Horizontal)
        self.test_slider2.setRange(0, 100)
        self.test_slider2.setValue(50)
        self.slider_label2 = QLabel("50")
        self.test_slider2.valueChanged.connect(
            lambda v: self.slider_label2.setText(str(v))
        )

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.test_slider2)
        slider_layout.addWidget(self.slider_label2)
        layout.addLayout(slider_layout)

        # 计数器（证明界面在响应）
        self.counter = 0
        self.counter_label = QLabel("界面响应计数器: 0")
        layout.addWidget(self.counter_label)

        # 定时器更新计数器
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_counter)
        self.timer.start(100)  # 每100ms更新一次

        # 说明文字
        info = QLabel("✅ 点击按钮后，界面依然流畅，可以拖动滑块，计数器持续更新！")
        info.setStyleSheet("color: green; font-weight: bold; margin: 5px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.setLayout(layout)

    def update_counter(self):
        """更新计数器，证明界面在响应"""
        self.counter += 1
        self.counter_label.setText(f"界面响应计数器: {self.counter}")

    def start_good_task(self):
        """✅ 正确的做法：使用 QThreadWithReturn"""

        def heavy_computation():
            # 模拟耗时计算：数据分析、文件处理、网络请求等
            total_steps = 50
            for i in range(total_steps):
                time.sleep(0.1)  # 总共5秒
                # 可以在这里检查取消请求
                if hasattr(heavy_computation, "_should_cancel"):
                    if heavy_computation._should_cancel:
                        return "任务被取消"
                # 模拟进度更新（实际应用中可以用信号）
            return "计算完成！"

        # 创建线程
        self.thread = QThreadWithReturn(heavy_computation)

        # 设置开始状态
        self.good_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.good_status.setText("后台处理中...")
        self.good_progress.setRange(0, 0)  # 不确定进度

        # 添加完成回调（自动在主线程执行，安全更新UI）
        self.thread.add_done_callback(self.on_task_completed)

        # 添加错误处理
        self.thread.add_failure_callback(self.on_task_failed)

        # 启动线程
        self.thread.start()
        # 界面立即响应，用户可以继续操作其他控件！

    def cancel_task(self):
        """取消当前任务"""
        if self.thread:
            self.thread.cancel()
            self.on_task_completed("任务已取消")

    def on_task_completed(self, result):
        """任务完成回调 - 在主线程中执行，安全更新UI"""
        self.good_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.good_status.setText(f"✅ {result}")
        self.good_progress.setRange(0, 1)
        self.good_progress.setValue(1)

    def on_task_failed(self, exception):
        """任务失败回调"""
        self.good_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.good_status.setText(f"❌ 出错了: {exception}")
        self.good_progress.setRange(0, 1)


class ThreadPoolExampleWidget(QWidget):
    """🏊‍♂️ 线程池示例：并行处理多个任务"""

    def __init__(self):
        super().__init__()
        self.pool = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 标题
        title = QLabel("🏊‍♂️ 线程池 - 并行处理多个任务")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: blue; margin: 10px;")
        layout.addWidget(title)

        # 任务数量选择
        task_layout = QHBoxLayout()
        task_layout.addWidget(QLabel("任务数量:"))
        self.task_count = QSpinBox()
        self.task_count.setRange(1, 20)
        self.task_count.setValue(8)
        task_layout.addWidget(self.task_count)

        task_layout.addWidget(QLabel("线程数:"))
        self.worker_count = QSpinBox()
        self.worker_count.setRange(1, 8)
        self.worker_count.setValue(3)
        task_layout.addWidget(self.worker_count)

        layout.addLayout(task_layout)

        # 控制按钮
        self.pool_button = QPushButton("开始批量处理")
        self.pool_button.setStyleSheet(
            "QPushButton { background-color: #ccccff; padding: 10px; }"
        )
        self.pool_button.clicked.connect(self.start_batch_processing)
        layout.addWidget(self.pool_button)

        # 进度显示
        self.pool_progress = QProgressBar()
        layout.addWidget(self.pool_progress)

        # 状态显示
        self.pool_status = QLabel("准备就绪")
        layout.addWidget(self.pool_status)

        # 结果显示
        self.result_text = QTextEdit()
        self.result_text.setMaximumHeight(150)
        self.result_text.setPlaceholderText("任务执行结果将在这里显示...")
        layout.addWidget(self.result_text)

        self.setLayout(layout)

    def start_batch_processing(self):
        """开始批量处理"""
        task_count = self.task_count.value()
        worker_count = self.worker_count.value()

        def process_single_task(task_id):
            # 模拟处理任务：文件处理、数据转换、网络请求等
            processing_time = random.uniform(1.0, 3.0)
            time.sleep(processing_time)
            return f"任务 {task_id} 处理完成 (耗时 {processing_time:.1f}s)"

        # 创建线程池
        self.pool = QThreadPoolExecutor(max_workers=worker_count)
        self.completed_count = 0
        self.total_tasks = task_count

        # 清空结果显示
        self.result_text.clear()

        # UI状态
        self.pool_button.setEnabled(False)
        self.pool_progress.setMaximum(self.total_tasks)
        self.pool_progress.setValue(0)
        self.pool_status.setText(
            f"并行处理 {task_count} 个任务 (使用 {worker_count} 个线程)"
        )

        # 提交所有任务
        for task_id in range(1, task_count + 1):
            future = self.pool.submit(process_single_task, task_id)
            future.add_done_callback(self.on_task_completed)

    def on_task_completed(self, result):
        """单个任务完成回调"""
        self.completed_count += 1
        self.pool_progress.setValue(self.completed_count)

        # 显示结果
        self.result_text.append(f"[{self.completed_count}/{self.total_tasks}] {result}")

        # 自动滚动到最新内容
        cursor = self.result_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.result_text.setTextCursor(cursor)

        if self.completed_count == self.total_tasks:
            self.pool_status.setText("✅ 所有任务处理完成！")
            self.pool_button.setEnabled(True)
            if self.pool:
                self.pool.shutdown()


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QThreadWithReturn GUI 对比演示")
        self.setGeometry(100, 100, 800, 700)

        # 创建标签页
        tab_widget = QTabWidget()

        # 错误示例标签页
        bad_tab = BadExampleWidget()
        tab_widget.addTab(bad_tab, "❌ 传统做法")

        # 正确示例标签页
        good_tab = GoodExampleWidget()
        tab_widget.addTab(good_tab, "✅ QThreadWithReturn")

        # 线程池标签页
        pool_tab = ThreadPoolExampleWidget()
        tab_widget.addTab(pool_tab, "🏊‍♂️ 线程池")

        self.setCentralWidget(tab_widget)

        # 设置样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
            QTabWidget::pane {
                border: 1px solid #cccccc;
                background-color: white;
            }
            QTabWidget::tab-bar {
                alignment: center;
            }
            QTabWidget::tab {
                background-color: #e0e0e0;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabWidget::tab:selected {
                background-color: white;
                border-bottom: 2px solid #3498db;
            }
        """)


def main():
    """主函数"""
    app = QApplication(sys.argv)

    # 设置应用信息
    app.setApplicationName("QThreadWithReturn GUI Demo")
    app.setApplicationVersion("1.0")

    # 创建主窗口
    window = MainWindow()
    window.show()

    print("🚀 QThreadWithReturn GUI 对比演示已启动!")
    print("👆 请切换不同标签页体验各种功能")
    print("📝 特别注意观察界面响应性的差异")

    return app.exec()


if __name__ == "__main__":
    main()
