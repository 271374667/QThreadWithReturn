import sys
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp_session():
    """创建session级别的QApplication"""
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()

    # DEADLOCK FIX: Removed QTimer that called processEvents() every 10ms
    # The timer created dangerous nested event loop re-entrancy:
    # shutdown(wait=True) calls processEvents() → timer fires → nested processEvents() → deadlock
    # Instead, rely on:
    # 1. process_events fixture (function-scoped) for test boundary processing
    # 2. Explicit wait_with_events() calls in tests for mid-test event processing
    # 3. Internal processEvents() in production code (shutdown polling, result() waiting)

    yield app


@pytest.fixture(autouse=True)
def process_events():
    """每个测试前后处理Qt事件"""
    app = QApplication.instance()
    if app:
        app.processEvents()
    yield
    if app:
        app.processEvents()
