from threading import Event, get_ident

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from app.ui.source_page import SourcePage
from app.workers.manager import WorkerManager


def ready(qtbot, page):
    qtbot.waitUntil(lambda: not page._busy and page._loaded)


def test_source_list_and_ui_actions(qtbot, source_setup, monkeypatch):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    manager = WorkerManager()
    page = SourcePage(service, manager)
    qtbot.addWidget(page)
    page.show()
    ready(qtbot, page)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "E:\\Media")
    qtbot.mouseClick(page.add_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.model.rowCount() == 1 and not page._busy)
    assert page.model.data(page.model.index(0, 0)) == "Media"
    assert page.model.data(page.model.index(0, 1)) == "E:\\Media"
    assert page.model.data(page.model.index(0, 2)) == "Online"
    assert page.model.data(page.model.index(0, 6)) == "Not yet scanned"
    page.table.selectRow(0)
    qtbot.mouseClick(page.enable_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert page.model.data(page.model.index(0, 2)) == "Disabled"
    page.table.selectRow(0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Archive", True))
    qtbot.mouseClick(page.rename_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert page.model.data(page.model.index(0, 0)) == "Archive"
    page.table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert page.model.rowCount() == 0
    manager.shutdown()


def test_refresh_runs_off_ui_thread_and_does_not_freeze(qtbot, source_setup, monkeypatch):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    service.register("E:\\Media")
    manager = WorkerManager()
    page = SourcePage(service, manager)
    qtbot.addWidget(page)
    page.show()
    ready(qtbot, page)
    started, release = Event(), Event()
    original = resolver.inspect
    threads = []
    def slow(path):
        threads.append(get_ident())
        started.set()
        release.wait(3)
        return original(path)
    monkeypatch.setattr(resolver, "inspect", slow)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    try:
        page.refresh_status()
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        assert page._busy and not page.add_button.isEnabled()
        assert threads[0] != get_ident()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not page._busy)
    timer.stop()
    assert page.model.data(page.model.index(0, 2)) == "Online"
    manager.shutdown()


def test_duplicate_error_and_nested_confirmation_ui(qtbot, source_setup, monkeypatch):
    service, resolver, _ = source_setup
    resolver.add("E:\\Media")
    resolver.add("E:\\Media\\Projects")
    service.register("E:\\Media")
    manager = WorkerManager()
    page = SourcePage(service, manager)
    qtbot.addWidget(page)
    page.show()
    ready(qtbot, page)
    page.register_path("e:/MEDIA")
    qtbot.waitUntil(lambda: not page._busy)
    assert "already registered" in page.message.text()
    prompts = []
    def yes(*args):
        prompts.append(args[-1])
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", yes)
    page.register_path("E:\\Media\\Projects")
    qtbot.waitUntil(lambda: page.model.rowCount() == 2 and not page._busy)
    assert len(prompts) == 1 and "overlapping" in prompts[0]
    manager.shutdown()


def test_sources_use_bounded_pages(qtbot, source_setup):
    service, resolver, _ = source_setup
    for index in range(3):
        path = f"E:\\Root{index}"
        resolver.add(path)
        service.register(path)
    manager = WorkerManager()
    page = SourcePage(service, manager)
    page.PAGE_SIZE = 2
    qtbot.addWidget(page)
    page.show()
    ready(qtbot, page)
    assert page.model.rowCount() == 2
    qtbot.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert page.model.rowCount() == 1
    qtbot.mouseClick(page.previous_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert page.model.rowCount() == 2
    manager.shutdown()


def test_declined_nested_registration_and_referenced_removal(qtbot, source_setup, monkeypatch):
    from app.database.repositories import AssetRepository
    service, resolver, database = source_setup
    resolver.add("E:\\Media")
    resolver.add("E:\\Media\\Child")
    row = service.register("E:\\Media")
    AssetRepository(database).create(source_id=row["source_id"],
        absolute_path="E:\\Media\\a.png", relative_path="a.png", filename="a.png",
        extension=".png", media_type="image")
    manager = WorkerManager()
    page = SourcePage(service, manager)
    qtbot.addWidget(page)
    page.show()
    ready(qtbot, page)
    prompts = []
    def no(*args):
        prompts.append(True)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", no)
    page.register_path("E:\\Media\\Child")
    qtbot.waitUntil(lambda: bool(prompts) and not page._busy)
    assert page.model.rowCount() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page.table.selectRow(0)
    qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._busy)
    assert "indexed assets" in page.message.text()
    assert page.model.rowCount() == 1
    manager.shutdown()
