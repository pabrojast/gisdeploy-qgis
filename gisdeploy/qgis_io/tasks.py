"""Background execution for network work.

Layer exports run on the main thread (QGIS layer access is not thread-safe);
everything network-bound — uploads, pushes, status polling, publishing — runs
inside a QgsTask so the UI never blocks. The worker function receives a
``report(fraction, note)`` callback wired to the task progress bar.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from qgis.core import QgsApplication, QgsTask
from ..core.security import safe_message
from ..translation import tr

POLL_INTERVAL_SECONDS = 4.0  # the manifest's recommended dataset.status cadence
POLL_TIMEOUT_SECONDS = 600.0

# The task manager does not keep the Python side of a QgsTask subclass alive: a
# task nobody references is garbage-collected and silently never runs. Running
# tasks are held here until their `finished` callback has been delivered.
_ACTIVE_TASKS = set()


class WorkerTask(QgsTask):
    """Run `work(report)` off the UI thread, then `on_done(result, error)` on it."""

    def __init__(self, description: str, work: Callable, on_done: Callable):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self._work = work
        self._on_done = on_done
        self._result = None
        self._error: Optional[BaseException] = None

    def run(self):  # noqa: D102 — QgsTask API
        def report(fraction: float, note: str = ""):
            self.setProgress(max(0.0, min(1.0, fraction)) * 100)
            if note:
                self.setDescription(note)

        try:
            self._result = self._work(report)
            return True
        except BaseException as error:  # noqa: BLE001 — surfaced via on_done
            self._error = RuntimeError(safe_message(error))
            return False

    def finished(self, ok):  # noqa: D102 — QgsTask API (main thread)
        try:
            self._on_done(self._result if ok else None, self._error)
        finally:
            _ACTIVE_TASKS.discard(self)


def start_task(description: str, work: Callable, on_done: Callable) -> WorkerTask:
    task = WorkerTask(description, work, on_done)
    _ACTIVE_TASKS.add(task)
    if not QgsApplication.taskManager().addTask(task):
        _ACTIVE_TASKS.discard(task)
        raise RuntimeError("Could not start the background task.")
    return task


def _poll(probe, report, base_fraction: float):
    """Call `probe()` → (done, result, stage) until done, mapping time onto the bar's tail."""
    started = time.monotonic()
    while True:
        done, result, stage = probe()
        if done:
            report(1.0, "")
            return result
        elapsed = time.monotonic() - started
        if elapsed > POLL_TIMEOUT_SECONDS:
            raise RuntimeError("Timed out waiting for the server to process the dataset.")
        share = min(elapsed / 120.0, 0.95)
        report(base_fraction + (1.0 - base_fraction) * share, tr("Processing ({0})…").format(stage))
        time.sleep(POLL_INTERVAL_SECONDS)


def poll_until_ready(client, dataset_id: str, report, base_fraction: float = 0.6):
    """Poll a NEW dataset's status until READY or FAILED."""

    def probe():
        status = client.dataset_status(dataset_id)
        if status.status == "FAILED":
            raise RuntimeError(status.error_message or "Processing failed on the server.")
        return status.status == "READY", status, status.stage or status.status

    return _poll(probe, report, base_fraction)


def poll_revision(client, dataset_id: str, number: int, report, base_fraction: float = 0.6):
    """Poll a pushed revision until it serves (ACTIVE, or already SUPERSEDED) or FAILED.

    dataset.status cannot tell: the dataset stays READY while a new revision is
    processed, because the previous revision keeps serving (and is restored if
    the new one fails).
    """

    def probe():
        history = client.list_revisions(dataset_id)
        revision = next((r for r in history["revisions"] if r.number == number), None)
        state = revision.status if revision else "UPLOADED"
        if state == "FAILED":
            raise RuntimeError(
                revision.error_message or "The new revision failed processing; the previous revision keeps serving."
            )
        return state in ("ACTIVE", "SUPERSEDED"), revision, state

    return _poll(probe, report, base_fraction)
