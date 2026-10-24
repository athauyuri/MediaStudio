from dataclasses import dataclass
import logging
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class Cancelled(Exception):
    """Cooperative cancellation checkpoint reached."""


@dataclass(frozen=True)
class WorkerError:
    error_code: str
    category: str
    user_message: str
    technical_message: str
    exception_type: str
    source_path: str | None = None
    destination_path: str | None = None
    retryable: bool = False
    suggested_actions: tuple[str, ...] = ("Stop",)


class WorkerSignals(QObject):
    update = Signal(object)
    progress = Signal(int, str)
    result = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    finished = Signal()


class WorkerContext:
    def __init__(self, stop: Event, signals: WorkerSignals):
        self._stop = stop
        self._signals = signals

    def checkpoint(self) -> None:
        if self._stop.is_set():
            raise Cancelled()

    @property
    def is_cancelled(self) -> bool:
        return self._stop.is_set()

    def notify(self, value: object) -> None:
        self._signals.update.emit(value)

    def report(self, percent: int, message: str = "") -> None:
        self.checkpoint()
        self._signals.progress.emit(max(0, min(100, percent)), message)


class Task(QRunnable):
    def __init__(self, work: Callable[[WorkerContext], object]):
        super().__init__()
        self.signals = WorkerSignals()
        self.stop = Event()
        self.work = work

    @Slot()
    def run(self) -> None:
        context = WorkerContext(self.stop, self.signals)
        try:
            context.checkpoint()
            result = self.work(context)
            context.checkpoint()
            self.signals.result.emit(result)
        except Cancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            logging.getLogger("media_studio.workers").exception("Background task failed")
            self.signals.failed.emit(WorkerError(
                "UNKNOWN", "UNKNOWN", "Background task failed. See the log for details.",
                str(exc), type(exc).__name__))
        finally:
            self.signals.finished.emit()


class WorkerManager(QObject):
    idle = Signal()

    def __init__(self, concurrency: int = 2, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(concurrency)
        self._tasks: set[Task] = set()
        self._closing = False

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def submit(self, work: Callable[[WorkerContext], object], *, configure=None) -> Task:
        if self._closing:
            raise RuntimeError("Worker manager is shutting down")
        # Bound the in-memory queue; persistent lazy batches belong to M49+.
        if len(self._tasks) >= self.pool.maxThreadCount() * 2:
            raise RuntimeError("Worker capacity reached")
        task = Task(work)
        task.signals.finished.connect(self._finished)
        if configure is not None:
            configure(task.signals)
        self._tasks.add(task)
        self.pool.start(task)
        return task

    @Slot()
    def _finished(self) -> None:
        signals = self.sender()
        self._tasks = {task for task in self._tasks if task.signals is not signals}
        if not self._tasks:
            self.idle.emit()

    def shutdown(self) -> None:
        """Request cancellation without blocking the Qt event loop."""
        self._closing = True
        for task in self._tasks:
            task.stop.set()
