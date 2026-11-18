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
        self._on_done(self._result if ok else None, self._error)


def start_task(description: str, work: Callable, on_done: Callable) -> WorkerTask:
    task = WorkerTask(description, work, on_done)
    QgsApplication.taskManager().addTask(task)
    return task


def poll_until_ready(client, dataset_id: str, report, base_fraction: float = 0.6):
    """Poll dataset.status until READY/FAILED, mapping time onto the tail of the bar."""
    started = time.monotonic()
    while True:
        status = client.dataset_status(dataset_id)
        if status.status == "READY":
            report(1.0, "")
            return status
        if status.status == "FAILED":
            raise RuntimeError(status.error_message or "Processing failed on the server.")
        elapsed = time.monotonic() - started
        if elapsed > POLL_TIMEOUT_SECONDS:
            raise RuntimeError("Timed out waiting for the server to process the dataset.")
        share = min(elapsed / 120.0, 0.95)
        report(
            base_fraction + (1.0 - base_fraction) * share,
            tr("Processing ({0})…").format(status.stage or status.status),
        )
        time.sleep(POLL_INTERVAL_SECONDS)
