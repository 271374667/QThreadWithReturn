import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer


@pytest.fixture(scope="session", autouse=True)
def qapp_session():
    """创建session级别的QApplication"""
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()

    # 设置定时器处理事件循环
    timer = QTimer()
    timer.timeout.connect(app.processEvents)
    timer.start(10)  # 每10ms处理一次事件

    yield app

    timer.stop()


@pytest.fixture(autouse=True)
def process_events():
    """每个测试前后处理Qt事件"""
    app = QApplication.instance()
    if app:
        app.processEvents()
    yield
    if app:
        app.processEvents()
