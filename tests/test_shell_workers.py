from threading import Event, get_ident

import pytest
from PySide6.QtCore import QTimer

from app.config import AppConfig
from app.ui.main_window import MainWindow, NAVIGATION
from app.workers.manager import WorkerManager


def test_navigation_and_read_only_shell(qtbot, tmp_path):
    original = tmp_path / "original.png"
    original.write_bytes(b"untouched")
    manager = WorkerManager()
    window = MainWindow(AppConfig(), manager)
    qtbot.addWidget(window)
    window.show()
    for index in range(len(NAVIGATION)):
        window.navigation.setCurrentRow(index)
        assert window.pages.currentIndex() == index
    assert original.read_bytes() == b"untouched"
    assert list(tmp_path.iterdir()) == [original]
    window.close()
    assert not window.isVisible()


def test_worker_result_and_progress_off_ui_thread(qtbot):
    manager = WorkerManager()
    gate = Event()
    thread = get_ident()
    def work(context):
        gate.wait(2)
        context.report(50, "Halfway")
        return get_ident()
    task = manager.submit(work)
    progress = []
    task.signals.progress.connect(lambda *args: progress.append(args))
    with qtbot.waitSignal(task.signals.result, timeout=3000) as signal:
        gate.set()
    assert signal.args[0] != thread
    assert progress == [(50, "Halfway")]
    qtbot.waitUntil(lambda: manager.active_count == 0)
    manager.shutdown()


def test_error_is_structured_and_other_tasks_work(qtbot):
    manager = WorkerManager()
    gate = Event()
    def fail(context):
        gate.wait(2)
        raise ValueError("test failure")
    task = manager.submit(fail)
    with qtbot.waitSignal(task.signals.failed) as signal:
        gate.set()
    assert signal.args[0].exception_type == "ValueError"
    qtbot.waitUntil(lambda: manager.active_count == 0)
    gate.clear()
    task = manager.submit(lambda context: gate.wait(2))
    with qtbot.waitSignal(task.signals.result):
        gate.set()
    qtbot.waitUntil(lambda: manager.active_count == 0)
    manager.shutdown()


def test_responsive_cooperative_shutdown(qtbot):
    manager = WorkerManager(1)
    entered = Event()
    def work(context):
        entered.set()
        while True:
            Event().wait(0.01)
            context.checkpoint()
    window = MainWindow(AppConfig(), manager)
    qtbot.addWidget(window)
    window.show()
    task = manager.submit(work)
    qtbot.waitUntil(entered.is_set)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    with qtbot.waitSignal(task.signals.cancelled):
        window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    timer.stop()
    assert ticks
    with pytest.raises(RuntimeError, match="shutting down"):
        manager.submit(work)


def test_queue_is_bounded_and_queued_tasks_cancel(qtbot):
    manager = WorkerManager(1)
    gate = Event()
    first = manager.submit(lambda context: gate.wait(2))
    called = []
    second = manager.submit(lambda context: called.append(True))
    with pytest.raises(RuntimeError, match="capacity"):
        manager.submit(lambda context: None)
    with qtbot.waitSignal(second.signals.cancelled):
        manager.shutdown()
        gate.set()
    qtbot.waitUntil(lambda: manager.active_count == 0)
    assert not called
