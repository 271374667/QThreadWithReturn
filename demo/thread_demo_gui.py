"""PySide6 多线程功能演示界面

演示 QThreadWithReturn 和 QThreadPoolExecutor 的所有功能特性。
"""

import sys
import time
import random
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QProgressBar,
    QTabWidget,
    QGridLayout,
    QSlider,
    QSplitter,
    QLineEdit,
    QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QThread
from PySide6.QtGui import QFont, QTextCursor

from qthreadwithreturn import QThreadWithReturn, QThreadPoolExecutor


class AnimatedProgressBar(QProgressBar):
    """带动画效果的进度条"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(True)
        self.animation = QPropertyAnimation(self, b"value")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def setValue(self, value: int):
        """带动画的设置值"""
        self.animation.stop()
        self.animation.setStartValue(self.value())
        self.animation.setEndValue(value)
        self.animation.start()


class LogWidget(QPlainTextEdit):
    """日志显示组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)  # 限制日志行数

        # 设置字体
        font = QFont("Consolas", 9)
        self.setFont(font)

    def add_log(self, message: str, level: str = "INFO"):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # 根据级别设置颜色
        colors = {
            "INFO": "#4CAF50",
            "WARNING": "#FFA726",
            "ERROR": "#EF5350",
            "SUCCESS": "#66BB6A",
            "DEBUG": "#9E9E9E",
        }
        color = colors.get(level, "#FFFFFF")

        # 格式化消息
        formatted_msg = (
            f'<span style="color: {color};">[{timestamp}] [{level}]</span> {message}'
        )

        # 添加到末尾
        self.appendHtml(formatted_msg)

        # 滚动到底部
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)


class ThreadDemoWindow(QMainWindow):
    """多线程功能演示主窗口"""

    def __init__(self):
        super().__init__()
        self.threads = []  # 保存线程引用防止被垃圾回收
        self.thread_pool = None
        self.setup_ui()
        self.setup_style()

    def setup_ui(self):
        """设置UI"""
        self.setWindowTitle("PySide6 多线程功能演示")
        self.setGeometry(100, 100, 1400, 900)

        # 中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局 - 使用分割器
        main_layout = QHBoxLayout(central_widget)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧面板 - 功能区
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # 创建标签页
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)

        # 单线程演示标签页
        self.single_thread_tab = self.create_single_thread_tab()
        self.tab_widget.addTab(self.single_thread_tab, "🧵 单线程演示")

        # 线程池演示标签页
        self.thread_pool_tab = self.create_thread_pool_tab()
        self.tab_widget.addTab(self.thread_pool_tab, "🏊 线程池演示")

        # 回调机制演示标签页
        self.callback_tab = self.create_callback_tab()
        self.tab_widget.addTab(self.callback_tab, "🔔 回调机制")

        # 高级功能标签页
        self.advanced_tab = self.create_advanced_tab()
        self.tab_widget.addTab(self.advanced_tab, "⚡ 高级功能")

        left_layout.addWidget(self.tab_widget)

        # 右侧面板 - 日志和监控
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # 实时监控区
        monitor_group = QGroupBox("📊 实时监控")
        monitor_layout = QGridLayout()

        # 活跃线程数
        self.active_threads_label = QLabel("活跃线程: 0")
        monitor_layout.addWidget(self.active_threads_label, 0, 0)

        # 完成任务数
        self.completed_tasks_label = QLabel("完成任务: 0")
        monitor_layout.addWidget(self.completed_tasks_label, 0, 1)

        # CPU使用率模拟
        self.cpu_usage_bar = AnimatedProgressBar()
        self.cpu_usage_bar.setFormat("CPU: %p%")
        monitor_layout.addWidget(QLabel("CPU使用率:"), 1, 0)
        monitor_layout.addWidget(self.cpu_usage_bar, 1, 1)

        # 内存使用模拟
        self.memory_usage_bar = AnimatedProgressBar()
        self.memory_usage_bar.setFormat("内存: %p%")
        monitor_layout.addWidget(QLabel("内存使用:"), 2, 0)
        monitor_layout.addWidget(self.memory_usage_bar, 2, 1)

        monitor_group.setLayout(monitor_layout)
        right_layout.addWidget(monitor_group)

        # 日志区域
        log_group = QGroupBox("📜 运行日志")
        log_layout = QVBoxLayout()

        # 日志控制按钮
        log_controls = QHBoxLayout()
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.clicked.connect(self.clear_log)
        save_log_btn = QPushButton("保存日志")
        save_log_btn.clicked.connect(self.save_log)
        log_controls.addWidget(clear_log_btn)
        log_controls.addWidget(save_log_btn)
        log_controls.addStretch()

        self.log_widget = LogWidget()
        log_layout.addLayout(log_controls)
        log_layout.addWidget(self.log_widget)
        log_group.setLayout(log_layout)
        right_layout.addWidget(log_group)

        # 添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([800, 600])

        # 状态栏
        self.statusBar().showMessage("就绪")

        # 定时器更新监控信息
        self.monitor_timer = QTimer()
        self.monitor_timer.timeout.connect(self.update_monitor)
        self.monitor_timer.start(500)

        # 统计信息
        self.active_thread_count = 0
        self.completed_task_count = 0

    def create_single_thread_tab(self) -> QWidget:
        """创建单线程演示标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 基本线程操作
        basic_group = QGroupBox("基本操作")
        basic_layout = QGridLayout()

        # 简单任务
        self.simple_task_btn = QPushButton("运行简单任务")
        self.simple_task_btn.clicked.connect(self.run_simple_task)
        basic_layout.addWidget(self.simple_task_btn, 0, 0)

        # 带参数任务
        self.param_task_btn = QPushButton("运行带参数任务")
        self.param_task_btn.clicked.connect(self.run_param_task)
        basic_layout.addWidget(self.param_task_btn, 0, 1)

        # 长时间任务
        self.long_task_btn = QPushButton("运行长时间任务")
        self.long_task_btn.clicked.connect(self.run_long_task)
        basic_layout.addWidget(self.long_task_btn, 1, 0)

        # 错误任务
        self.error_task_btn = QPushButton("运行错误任务")
        self.error_task_btn.clicked.connect(self.run_error_task)
        basic_layout.addWidget(self.error_task_btn, 1, 1)

        basic_group.setLayout(basic_layout)
        layout.addWidget(basic_group)

        # 取消和超时
        control_group = QGroupBox("线程控制")
        control_layout = QGridLayout()

        # 可取消任务
        self.cancelable_task_btn = QPushButton("启动可取消任务")
        self.cancelable_task_btn.clicked.connect(self.run_cancelable_task)
        control_layout.addWidget(self.cancelable_task_btn, 0, 0, 1, 2)

        self.graceful_cancel_btn = QPushButton("优雅取消")
        self.graceful_cancel_btn.clicked.connect(self.graceful_cancel_task)
        self.graceful_cancel_btn.setEnabled(False)
        control_layout.addWidget(self.graceful_cancel_btn, 1, 0)

        self.force_cancel_btn = QPushButton("强制取消")
        self.force_cancel_btn.clicked.connect(self.force_cancel_task)
        self.force_cancel_btn.setEnabled(False)
        control_layout.addWidget(self.force_cancel_btn, 1, 1)

        # 超时任务
        control_layout.addWidget(QLabel("超时时间(秒):"), 2, 0)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 10)
        self.timeout_spin.setValue(3)
        control_layout.addWidget(self.timeout_spin, 2, 1)

        self.timeout_task_btn = QPushButton("运行超时任务")
        self.timeout_task_btn.clicked.connect(self.run_timeout_task)
        control_layout.addWidget(self.timeout_task_btn, 3, 0, 1, 2)

        control_group.setLayout(control_layout)
        layout.addWidget(control_group)

        # 多返回值演示
        multi_group = QGroupBox("多返回值")
        multi_layout = QVBoxLayout()

        self.multi_return_btn = QPushButton("运行多返回值任务")
        self.multi_return_btn.clicked.connect(self.run_multi_return_task)
        multi_layout.addWidget(self.multi_return_btn)

        multi_group.setLayout(multi_layout)
        layout.addWidget(multi_group)

        layout.addStretch()
        return widget

    def create_thread_pool_tab(self) -> QWidget:
        """创建线程池演示标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 线程池配置
        config_group = QGroupBox("线程池配置")
        config_layout = QGridLayout()

        config_layout.addWidget(QLabel("最大工作线程数:"), 0, 0)
        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setRange(1, 10)
        self.max_workers_spin.setValue(4)
        config_layout.addWidget(self.max_workers_spin, 0, 1)

        config_layout.addWidget(QLabel("线程名前缀:"), 1, 0)
        self.thread_prefix_edit = QLineEdit("Worker")
        config_layout.addWidget(self.thread_prefix_edit, 1, 1)

        self.create_pool_btn = QPushButton("创建线程池")
        self.create_pool_btn.clicked.connect(self.create_thread_pool)
        config_layout.addWidget(self.create_pool_btn, 2, 0, 1, 2)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # 批量任务
        batch_group = QGroupBox("批量任务")
        batch_layout = QVBoxLayout()

        # 任务数量
        task_count_layout = QHBoxLayout()
        task_count_layout.addWidget(QLabel("任务数量:"))
        self.task_count_spin = QSpinBox()
        self.task_count_spin.setRange(1, 20)
        self.task_count_spin.setValue(8)
        task_count_layout.addWidget(self.task_count_spin)
        batch_layout.addLayout(task_count_layout)

        # 提交任务
        self.submit_tasks_btn = QPushButton("提交批量任务")
        self.submit_tasks_btn.clicked.connect(self.submit_batch_tasks)
        self.submit_tasks_btn.setEnabled(False)
        batch_layout.addWidget(self.submit_tasks_btn)

        # Map操作
        self.map_tasks_btn = QPushButton("使用Map执行任务")
        self.map_tasks_btn.clicked.connect(self.run_map_tasks)
        self.map_tasks_btn.setEnabled(False)
        batch_layout.addWidget(self.map_tasks_btn)

        # as_completed演示
        self.as_completed_btn = QPushButton("按完成顺序处理")
        self.as_completed_btn.clicked.connect(self.run_as_completed)
        self.as_completed_btn.setEnabled(False)
        batch_layout.addWidget(self.as_completed_btn)

        batch_group.setLayout(batch_layout)
        layout.addWidget(batch_group)

        # 线程池控制
        pool_control_group = QGroupBox("线程池控制")
        pool_control_layout = QVBoxLayout()

        self.shutdown_pool_btn = QPushButton("关闭线程池")
        self.shutdown_pool_btn.clicked.connect(self.shutdown_thread_pool)
        self.shutdown_pool_btn.setEnabled(False)
        pool_control_layout.addWidget(self.shutdown_pool_btn)

        pool_control_group.setLayout(pool_control_layout)
        layout.addWidget(pool_control_group)

        layout.addStretch()
        return widget

    def create_callback_tab(self) -> QWidget:
        """创建回调机制演示标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 回调类型选择
        callback_group = QGroupBox("回调类型")
        callback_layout = QVBoxLayout()

        # 无参数回调
        self.no_param_callback_btn = QPushButton("无参数回调")
        self.no_param_callback_btn.clicked.connect(self.demo_no_param_callback)
        callback_layout.addWidget(self.no_param_callback_btn)

        # 单参数回调
        self.single_param_callback_btn = QPushButton("单参数回调")
        self.single_param_callback_btn.clicked.connect(self.demo_single_param_callback)
        callback_layout.addWidget(self.single_param_callback_btn)

        # 多参数回调
        self.multi_param_callback_btn = QPushButton("多参数回调（自动解包）")
        self.multi_param_callback_btn.clicked.connect(self.demo_multi_param_callback)
        callback_layout.addWidget(self.multi_param_callback_btn)

        # Lambda回调
        self.lambda_callback_btn = QPushButton("Lambda表达式回调")
        self.lambda_callback_btn.clicked.connect(self.demo_lambda_callback)
        callback_layout.addWidget(self.lambda_callback_btn)

        # 类方法回调
        self.method_callback_btn = QPushButton("类方法回调")
        self.method_callback_btn.clicked.connect(self.demo_method_callback)
        callback_layout.addWidget(self.method_callback_btn)

        callback_group.setLayout(callback_layout)
        layout.addWidget(callback_group)

        # 失败回调
        failure_group = QGroupBox("失败回调")
        failure_layout = QVBoxLayout()

        self.failure_callback_btn = QPushButton("演示失败回调")
        self.failure_callback_btn.clicked.connect(self.demo_failure_callback)
        failure_layout.addWidget(self.failure_callback_btn)

        failure_group.setLayout(failure_layout)
        layout.addWidget(failure_group)

        layout.addStretch()
        return widget

    def create_advanced_tab(self) -> QWidget:
        """创建高级功能标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 压力测试
        stress_group = QGroupBox("压力测试")
        stress_layout = QVBoxLayout()

        # 并发数
        concurrency_layout = QHBoxLayout()
        concurrency_layout.addWidget(QLabel("并发任务数:"))
        self.concurrency_slider = QSlider(Qt.Orientation.Horizontal)
        self.concurrency_slider.setRange(1, 100)
        self.concurrency_slider.setValue(10)
        self.concurrency_label = QLabel("10")
        self.concurrency_slider.valueChanged.connect(
            lambda v: self.concurrency_label.setText(str(v))
        )
        concurrency_layout.addWidget(self.concurrency_slider)
        concurrency_layout.addWidget(self.concurrency_label)
        stress_layout.addLayout(concurrency_layout)

        self.stress_test_btn = QPushButton("开始压力测试")
        self.stress_test_btn.clicked.connect(self.run_stress_test)
        stress_layout.addWidget(self.stress_test_btn)

        stress_group.setLayout(stress_layout)
        layout.addWidget(stress_group)

        # 性能测试
        perf_group = QGroupBox("性能测试")
        perf_layout = QVBoxLayout()

        self.perf_test_btn = QPushButton("运行性能测试")
        self.perf_test_btn.clicked.connect(self.run_performance_test)
        perf_layout.addWidget(self.perf_test_btn)

        self.perf_result_label = QLabel("点击按钮开始测试...")
        self.perf_result_label.setWordWrap(True)
        perf_layout.addWidget(self.perf_result_label)

        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)

        # 实际应用场景
        scenario_group = QGroupBox("实际应用场景")
        scenario_layout = QVBoxLayout()

        self.download_sim_btn = QPushButton("模拟批量下载")
        self.download_sim_btn.clicked.connect(self.simulate_downloads)
        scenario_layout.addWidget(self.download_sim_btn)

        self.calc_sim_btn = QPushButton("模拟并行计算")
        self.calc_sim_btn.clicked.connect(self.simulate_calculations)
        scenario_layout.addWidget(self.calc_sim_btn)

        scenario_group.setLayout(scenario_layout)
        layout.addWidget(scenario_group)

        layout.addStretch()
        return widget

    def setup_style(self):
        """设置界面样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #357a38;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
            QTabWidget::pane {
                border: 1px solid #cccccc;
                background-color: white;
            }
            QTabBar::tab {
                background-color: #e0e0e0;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: white;
                border-bottom: 2px solid #4CAF50;
            }
            QProgressBar {
                border: 1px solid #cccccc;
                border-radius: 3px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
        """)

    # ========== 单线程演示功能 ==========

    def run_simple_task(self):
        """运行简单任务"""
        self.log_widget.add_log("启动简单任务", "INFO")

        def task():
            time.sleep(2)
            return "简单任务完成!"

        thread = QThreadWithReturn(task)
        thread.add_done_callback(
            lambda result: self.on_task_complete(result, "简单任务")
        )
        thread.start()

        self.threads.append(thread)
        self.active_thread_count += 1

    def run_param_task(self):
        """运行带参数任务"""
        self.log_widget.add_log("启动带参数任务", "INFO")

        def task(name, count):
            result = f"处理 {name} 共 {count} 次:\n"
            for i in range(count):
                time.sleep(0.5)
                result += f"  步骤 {i + 1} 完成\n"
            return result

        thread = QThreadWithReturn(task, "测试任务", count=3)
        thread.add_done_callback(
            lambda result: self.on_task_complete(result, "带参数任务")
        )
        thread.start()

        self.threads.append(thread)
        self.active_thread_count += 1

    def run_long_task(self):
        """运行长时间任务"""
        self.log_widget.add_log("启动长时间任务", "INFO")

        def task():
            total_steps = 10
            for i in range(total_steps):
                time.sleep(1)
                progress = (i + 1) * 10
                # 这里实际应该通过信号更新进度
            return f"长时间任务完成，处理了 {total_steps} 个步骤"

        thread = QThreadWithReturn(task)
        thread.add_done_callback(
            lambda result: self.on_task_complete(result, "长时间任务")
        )
        thread.start()

        self.threads.append(thread)
        self.active_thread_count += 1

    def run_error_task(self):
        """运行错误任务"""
        self.log_widget.add_log("启动错误任务（预期会失败）", "WARNING")

        def task():
            time.sleep(1)
            raise ValueError("这是一个模拟的错误!")

        thread = QThreadWithReturn(task)
        thread.add_done_callback(
            lambda result: self.on_task_complete(result, "错误任务")
        )
        thread.add_failure_callback(lambda exc: self.on_task_error(exc, "错误任务"))
        thread.start()

        self.threads.append(thread)
        self.active_thread_count += 1

    def run_cancelable_task(self):
        """运行可取消任务"""
        self.log_widget.add_log("启动可取消任务", "INFO")

        def task():
            for i in range(20):
                if QThread.currentThread().isInterruptionRequested():
                    return "任务被取消"
                time.sleep(0.5)
            return "任务正常完成"

        self.cancelable_thread = QThreadWithReturn(task)
        self.cancelable_thread.add_done_callback(
            lambda result: self.on_task_complete(result, "可取消任务")
        )
        self.cancelable_thread.start()

        self.threads.append(self.cancelable_thread)
        self.active_thread_count += 1
        self.graceful_cancel_btn.setEnabled(True)
        self.force_cancel_btn.setEnabled(True)

    def graceful_cancel_task(self):
        """优雅取消任务"""
        if hasattr(self, "cancelable_thread") and self.cancelable_thread.running():
            success = self.cancelable_thread.cancel(force_stop=False)
            if success:
                self.log_widget.add_log(
                    "优雅取消请求已发送（等待任务自行检查中断标志）", "WARNING"
                )
            else:
                self.log_widget.add_log("优雅取消失败", "ERROR")
            self.graceful_cancel_btn.setEnabled(False)
            self.force_cancel_btn.setEnabled(False)

    def force_cancel_task(self):
        """强制取消任务"""
        if hasattr(self, "cancelable_thread") and self.cancelable_thread.running():
            success = self.cancelable_thread.cancel(force_stop=True)
            if success:
                self.log_widget.add_log("强制取消请求已发送（立即终止线程）", "ERROR")
            else:
                self.log_widget.add_log("强制取消失败", "ERROR")
            self.graceful_cancel_btn.setEnabled(False)
            self.force_cancel_btn.setEnabled(False)

    def run_timeout_task(self):
        """运行超时任务"""
        timeout_ms = self.timeout_spin.value() * 1000
        self.log_widget.add_log(f"启动超时任务（超时：{timeout_ms}ms）", "INFO")

        def task():
            time.sleep(10)  # 故意超过超时时间
            return "任务完成"

        thread = QThreadWithReturn(task)
        thread.add_done_callback(
            lambda result: self.on_task_complete(result, "超时任务")
        )
        thread.start(timeout_ms)

        self.threads.append(thread)
        self.active_thread_count += 1

    def run_multi_return_task(self):
        """运行多返回值任务"""
        self.log_widget.add_log("启动多返回值任务", "INFO")

        def task():
            time.sleep(2)
            return ("结果1", "结果2", "结果3")

        thread = QThreadWithReturn(task)
        thread.add_done_callback(
            lambda a, b, c: self.on_task_complete(
                f"多返回值: {a}, {b}, {c}", "多返回值任务"
            )
        )
        thread.start()

        self.threads.append(thread)
        self.active_thread_count += 1

    # ========== 线程池演示功能 ==========

    def create_thread_pool(self):
        """创建线程池"""
        max_workers = self.max_workers_spin.value()
        prefix = self.thread_prefix_edit.text()

        if self.thread_pool:
            self.thread_pool.shutdown(wait=False)

        self.thread_pool = QThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=prefix
        )

        self.log_widget.add_log(
            f"线程池创建成功（最大线程数: {max_workers}）", "SUCCESS"
        )

        # 启用按钮
        self.submit_tasks_btn.setEnabled(True)
        self.map_tasks_btn.setEnabled(True)
        self.as_completed_btn.setEnabled(True)
        self.shutdown_pool_btn.setEnabled(True)
        self.create_pool_btn.setText("重新创建线程池")

    def submit_batch_tasks(self):
        """提交批量任务"""
        if not self.thread_pool:
            self.log_widget.add_log("请先创建线程池", "ERROR")
            return

        task_count = self.task_count_spin.value()
        self.log_widget.add_log(f"提交 {task_count} 个任务到线程池", "INFO")

        def task(task_id):
            sleep_time = random.uniform(1, 3)
            time.sleep(sleep_time)
            return f"任务 {task_id} 完成（耗时: {sleep_time:.2f}秒）"

        futures = []
        for i in range(task_count):
            future = self.thread_pool.submit(task, i + 1)
            future.add_done_callback(
                lambda result, tid=i + 1: self.on_task_complete(
                    result, f"批量任务 {tid}"
                )
            )
            futures.append(future)
            self.active_thread_count += 1

        self.threads.extend(futures)

    def run_map_tasks(self):
        """使用map执行任务"""
        if not self.thread_pool:
            self.log_widget.add_log("请先创建线程池", "ERROR")
            return

        self.log_widget.add_log("使用map执行任务", "INFO")

        def square(x):
            time.sleep(0.5)
            return x * x

        numbers = list(range(1, 11))

        # 在另一个线程中执行批量任务以避免阻塞UI
        def run_batch():
            futures = [self.thread_pool.submit(square, x) for x in numbers]
            results = [f.result() for f in futures]
            self.log_widget.add_log(f"批量任务结果: {results}", "SUCCESS")
            self.completed_task_count += len(results)

        thread = QThreadWithReturn(run_batch)
        thread.start()
        self.threads.append(thread)

    def run_as_completed(self):
        """按完成顺序处理任务"""
        if not self.thread_pool:
            self.log_widget.add_log("请先创建线程池", "ERROR")
            return

        self.log_widget.add_log("按完成顺序处理任务", "INFO")

        def task(task_id):
            sleep_time = random.uniform(0.5, 2)
            time.sleep(sleep_time)
            return f"任务 {task_id}"

        # 提交任务
        futures = []
        for i in range(5):
            future = self.thread_pool.submit(task, i + 1)
            futures.append(future)
            self.active_thread_count += 1

        # 在另一个线程中处理as_completed
        def process_completed():
            for future in QThreadPoolExecutor.as_completed(futures):
                result = future.result()
                self.log_widget.add_log(f"完成: {result}", "SUCCESS")
                self.completed_task_count += 1
                self.active_thread_count -= 1

        thread = QThreadWithReturn(process_completed)
        thread.start()
        self.threads.append(thread)

    def shutdown_thread_pool(self):
        """关闭线程池"""
        if self.thread_pool:
            self.log_widget.add_log("正在关闭线程池...", "WARNING")
            self.thread_pool.shutdown(wait=True)
            self.thread_pool = None
            self.log_widget.add_log("线程池已关闭", "SUCCESS")

            # 禁用按钮
            self.submit_tasks_btn.setEnabled(False)
            self.map_tasks_btn.setEnabled(False)
            self.as_completed_btn.setEnabled(False)
            self.shutdown_pool_btn.setEnabled(False)
            self.create_pool_btn.setText("创建线程池")

    # ========== 回调机制演示 ==========

    def demo_no_param_callback(self):
        """演示无参数回调"""
        self.log_widget.add_log("演示无参数回调", "INFO")

        def task():
            time.sleep(1)
            return "任务结果"

        def callback():
            self.log_widget.add_log("无参数回调被调用", "SUCCESS")
            QMessageBox.information(self, "无参数回调", "回调函数被调用了！")

        thread = QThreadWithReturn(task)
        thread.add_done_callback(callback)
        thread.start()
        self.threads.append(thread)

    def demo_single_param_callback(self):
        """演示单参数回调"""
        self.log_widget.add_log("演示单参数回调", "INFO")

        def task():
            time.sleep(1)
            return "单参数结果"

        def callback(result):
            self.log_widget.add_log(f"单参数回调收到: {result}", "SUCCESS")
            QMessageBox.information(self, "单参数回调", f"收到结果: {result}")

        thread = QThreadWithReturn(task)
        thread.add_done_callback(callback)
        thread.start()
        self.threads.append(thread)

    def demo_multi_param_callback(self):
        """演示多参数回调"""
        self.log_widget.add_log("演示多参数回调（自动解包）", "INFO")

        def task():
            time.sleep(1)
            return (100, 200, 300)

        def callback(a, b, c):
            msg = f"收到三个参数: a={a}, b={b}, c={c}"
            self.log_widget.add_log(msg, "SUCCESS")
            QMessageBox.information(self, "多参数回调", msg)

        thread = QThreadWithReturn(task)
        thread.add_done_callback(callback)
        thread.start()
        self.threads.append(thread)

    def demo_lambda_callback(self):
        """演示Lambda回调"""
        self.log_widget.add_log("演示Lambda表达式回调", "INFO")

        def task():
            time.sleep(1)
            return 42

        thread = QThreadWithReturn(task)
        thread.add_done_callback(
            lambda x: self.log_widget.add_log(f"Lambda回调: 结果是 {x}", "SUCCESS")
        )
        thread.start()
        self.threads.append(thread)

    def demo_method_callback(self):
        """演示类方法回调"""
        self.log_widget.add_log("演示类方法回调", "INFO")

        def task():
            time.sleep(1)
            return "类方法回调测试"

        thread = QThreadWithReturn(task)
        thread.add_done_callback(self.class_method_callback)
        thread.start()
        self.threads.append(thread)

    def class_method_callback(self, result):
        """类方法回调"""
        self.log_widget.add_log(f"类方法回调收到: {result}", "SUCCESS")
        self.statusBar().showMessage(f"类方法回调: {result}")

    def demo_failure_callback(self):
        """演示失败回调"""
        self.log_widget.add_log("演示失败回调", "INFO")

        def task():
            time.sleep(1)
            raise RuntimeError("故意的错误!")

        def on_failure(exc):
            self.log_widget.add_log(f"失败回调: {exc}", "ERROR")
            QMessageBox.critical(self, "任务失败", f"错误: {exc}")

        thread = QThreadWithReturn(task)
        thread.add_failure_callback(on_failure)
        thread.start()
        self.threads.append(thread)

    # ========== 高级功能 ==========

    def run_stress_test(self):
        """运行压力测试"""
        concurrency = self.concurrency_slider.value()
        self.log_widget.add_log(f"开始压力测试（并发数: {concurrency}）", "WARNING")

        # 创建大线程池
        pool = QThreadPoolExecutor(max_workers=min(concurrency, 20))

        def task(task_id):
            sleep_time = random.uniform(0.1, 0.5)
            time.sleep(sleep_time)
            return f"压力测试任务 {task_id} 完成"

        futures = []
        start_time = time.time()

        for i in range(concurrency):
            future = pool.submit(task, i + 1)
            futures.append(future)

        # 等待所有任务完成
        def wait_all():
            for future in futures:
                future.result()
            elapsed = time.time() - start_time
            self.log_widget.add_log(
                f"压力测试完成！{concurrency} 个任务耗时: {elapsed:.2f}秒", "SUCCESS"
            )
            pool.shutdown()

        thread = QThreadWithReturn(wait_all)
        thread.start()
        self.threads.append(thread)

    def run_performance_test(self):
        """运行性能测试"""
        self.log_widget.add_log("开始性能测试...", "INFO")
        self.perf_result_label.setText("测试中...")

        def test():
            results = {}

            # 测试单线程性能
            start = time.time()
            for i in range(10):
                time.sleep(0.1)
            single_time = time.time() - start
            results["单线程"] = single_time

            # 测试线程池性能
            pool = QThreadPoolExecutor(max_workers=5)
            start = time.time()
            futures = [pool.submit(time.sleep, 0.1) for _ in range(10)]
            for f in futures:
                f.result()
            pool_time = time.time() - start
            results["线程池"] = pool_time
            pool.shutdown()

            return results

        def show_results(results):
            text = "性能测试结果:\n"
            text += f"单线程10个任务: {results['单线程']:.2f}秒\n"
            text += f"线程池10个任务: {results['线程池']:.2f}秒\n"
            text += f"性能提升: {results['单线程'] / results['线程池']:.2f}倍"
            self.perf_result_label.setText(text)
            self.log_widget.add_log("性能测试完成", "SUCCESS")

        thread = QThreadWithReturn(test)
        thread.add_done_callback(show_results)
        thread.start()
        self.threads.append(thread)

    def simulate_downloads(self):
        """模拟批量下载"""
        self.log_widget.add_log("开始模拟批量下载...", "INFO")

        pool = QThreadPoolExecutor(max_workers=5, thread_name_prefix="Downloader")

        def download(url):
            sleep_time = random.uniform(1, 3)
            time.sleep(sleep_time)
            size = random.randint(100, 1000)
            return f"下载 {url} 完成（{size}KB）"

        urls = [f"http://example.com/file{i}.zip" for i in range(10)]

        for url in urls:
            future = pool.submit(download, url)
            future.add_done_callback(
                lambda result: self.log_widget.add_log(result, "SUCCESS")
            )

        # 异步关闭
        def cleanup():
            time.sleep(5)
            pool.shutdown()
            self.log_widget.add_log("下载模拟完成", "SUCCESS")

        thread = QThreadWithReturn(cleanup)
        thread.start()
        self.threads.append(thread)

    def simulate_calculations(self):
        """模拟并行计算"""
        self.log_widget.add_log("开始模拟并行计算...", "INFO")

        pool = QThreadPoolExecutor(max_workers=4)

        def calculate(n):
            # 模拟复杂计算
            result = 0
            for i in range(n * 1000000):
                result += i
            return f"计算 {n} 完成，结果: {result}"

        tasks = [10, 20, 30, 40]

        for task in tasks:
            future = pool.submit(calculate, task)
            future.add_done_callback(
                lambda result: self.log_widget.add_log(result, "SUCCESS")
            )

        # 异步关闭
        def cleanup():
            time.sleep(5)
            pool.shutdown()
            self.log_widget.add_log("计算模拟完成", "SUCCESS")

        thread = QThreadWithReturn(cleanup)
        thread.start()
        self.threads.append(thread)

    # ========== 辅助功能 ==========

    def on_task_complete(self, result, task_name: str):
        """任务完成回调"""
        self.log_widget.add_log(f"{task_name} 完成: {result}", "SUCCESS")
        self.completed_task_count += 1
        self.active_thread_count = max(0, self.active_thread_count - 1)

    def on_task_error(self, exception, task_name: str):
        """任务错误回调"""
        self.log_widget.add_log(f"{task_name} 失败: {exception}", "ERROR")
        self.active_thread_count = max(0, self.active_thread_count - 1)

    def update_monitor(self):
        """更新监控信息"""
        self.active_threads_label.setText(f"活跃线程: {self.active_thread_count}")
        self.completed_tasks_label.setText(f"完成任务: {self.completed_task_count}")

        # 模拟CPU和内存使用
        cpu_usage = min(100, self.active_thread_count * 10 + random.randint(0, 20))
        memory_usage = min(
            100, 30 + self.active_thread_count * 5 + random.randint(0, 10)
        )

        self.cpu_usage_bar.setValue(cpu_usage)
        self.memory_usage_bar.setValue(memory_usage)

    def clear_log(self):
        """清空日志"""
        self.log_widget.clear()
        self.log_widget.add_log("日志已清空", "INFO")

    def save_log(self):
        """保存日志"""
        # 这里可以实现保存日志到文件的功能
        self.log_widget.add_log("日志保存功能（待实现）", "INFO")

    def closeEvent(self, event):
        """关闭事件"""
        # 清理资源
        if self.thread_pool:
            self.thread_pool.shutdown(wait=False)
        event.accept()


def main():
    """主函数"""
    app = QApplication(sys.argv)

    # 设置应用图标和名称
    app.setApplicationName("PySide6 多线程功能演示")

    # 创建并显示主窗口
    window = ThreadDemoWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
