"""Background tasks stay referenced until QGIS delivers their result."""

import gc
import importlib
import os
import sys
import types
import weakref
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tasks_module():
    class Task:
        Flag = types.SimpleNamespace(CanCancel=1)

        def __init__(self, *args):
            pass

    manager = types.SimpleNamespace(accept=True, added=[])

    def add_task(task):
        manager.added.append(weakref.ref(task))  # QGIS keeps only the C++ side
        return 1 if manager.accept else 0

    manager.addTask = add_task
    application = types.SimpleNamespace(taskManager=lambda: manager)
    qt_core = types.SimpleNamespace(
        QCoreApplication=types.SimpleNamespace(translate=lambda _context, text: text),
        QLocale=None, QTranslator=None,
    )
    fakes = {
        "qgis": types.ModuleType("qgis"),
        "qgis.core": types.SimpleNamespace(QgsApplication=application, QgsTask=Task),
        "qgis.PyQt": types.ModuleType("qgis.PyQt"),
        "qgis.PyQt.QtCore": qt_core,
    }
    with patch.dict(sys.modules, fakes):
        yield importlib.import_module("gisdeploy.qgis_io.tasks"), manager
    sys.modules.pop("gisdeploy.qgis_io.tasks", None)
    sys.modules.pop("gisdeploy.translation", None)


def test_started_task_survives_without_a_caller_reference_until_finished(tasks_module):
    tasks, manager = tasks_module
    delivered = []
    tasks.start_task("fixture", lambda report: "done", lambda result, error: delivered.append((result, error)))
    gc.collect()
    task = manager.added[0]()
    assert task is not None, "an unreferenced task must not be garbage-collected before it runs"
    assert task.run() is True
    task.finished(True)
    assert delivered == [("done", None)]
    del task
    gc.collect()
    assert manager.added[0]() is None, "finished tasks are released"


def test_task_is_released_even_if_the_callback_fails(tasks_module):
    tasks, manager = tasks_module

    def broken(_result, _error):
        raise ValueError("callback bug")

    tasks.start_task("fixture", lambda report: None, broken)
    task = manager.added[0]()
    with pytest.raises(ValueError):
        task.finished(True)
    assert task not in tasks._ACTIVE_TASKS


def test_rejected_task_is_reported_and_not_kept(tasks_module):
    tasks, manager = tasks_module
    manager.accept = False
    with pytest.raises(RuntimeError):
        tasks.start_task("fixture", lambda report: None, lambda result, error: None)
    assert not tasks._ACTIVE_TASKS


class _Revisions:
    """A fake client whose revision `number` walks through `states`."""

    def __init__(self, number, states, error=None):
        self.number, self.states, self.error, self.calls = number, list(states), error, 0

    def list_revisions(self, _dataset_id):
        self.calls += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        revision = types.SimpleNamespace(number=self.number, status=state, error_message=self.error)
        older = types.SimpleNamespace(number=self.number - 1, status="ACTIVE", error_message=None)
        return {"head": self.number - 1, "revisions": [revision, older]}


def test_revision_push_waits_for_the_new_revision_not_the_dataset(tasks_module, monkeypatch):
    tasks, _manager = tasks_module
    monkeypatch.setattr(tasks.time, "sleep", lambda _s: None)
    client = _Revisions(2, ["UPLOADED", "PROCESSING", "ACTIVE"])
    notes = []
    revision = tasks.poll_revision(client, "ds", 2, lambda fraction, note="": notes.append(note))
    assert revision.status == "ACTIVE" and client.calls == 3
    assert "Processing (UPLOADED)…" in notes and "Processing (PROCESSING)…" in notes


def test_superseded_revision_counts_as_landed(tasks_module, monkeypatch):
    tasks, _manager = tasks_module
    monkeypatch.setattr(tasks.time, "sleep", lambda _s: None)
    assert tasks.poll_revision(_Revisions(2, ["SUPERSEDED"]), "ds", 2, lambda *a: None).status == "SUPERSEDED"


def test_failed_revision_is_an_error_even_though_the_dataset_stays_ready(tasks_module, monkeypatch):
    tasks, _manager = tasks_module
    monkeypatch.setattr(tasks.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="previous revision keeps serving"):
        tasks.poll_revision(_Revisions(2, ["PROCESSING", "FAILED"]), "ds", 2, lambda *a: None)
    with pytest.raises(RuntimeError, match="bad geometry"):
        tasks.poll_revision(_Revisions(2, ["FAILED"], error="bad geometry"), "ds", 2, lambda *a: None)
