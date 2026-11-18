#!/usr/bin/env python3
"""Headless load test of the plugin inside a real QGIS (3.22+ or 4.x).

Loads the plugin through classFactory with a minimal iface, opens the dock and
every dialog, exports a vector and a raster layer, checks the Spanish
translation and unloads. No network and no credentials: the GISDeploy client is
never contacted.

  QT_QPA_PLATFORM=offscreen python3 qgis-plugin/scripts/smoke_headless.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # the folder that contains gisdeploy/

import time  # noqa: E402

from qgis.core import (  # noqa: E402
    Qgis,
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.gui import QgsMessageBar  # noqa: E402
from qgis.PyQt.QtCore import QCoreApplication  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402
from qgis.testing import start_app  # noqa: E402  (uses a temporary QGIS profile)


class FakeIface:
    """The QgisInterface calls the plugin makes, recorded."""

    def __init__(self):
        self.window = QMainWindow()
        self.bar = QgsMessageBar()
        self.calls = []

    def mainWindow(self):  # noqa: N802
        return self.window

    def messageBar(self):  # noqa: N802
        return self.bar

    def __getattr__(self, name):
        if name in ("addToolBarIcon", "removeToolBarIcon", "addPluginToWebMenu",
                    "removePluginWebMenu", "addDockWidget", "removeDockWidget"):
            return lambda *args: self.calls.append(name)
        raise AttributeError(name)


class FakeClient:
    def list_revisions(self, _dataset_id):
        return {"revisions": [], "head": None}


class FakeDataset:
    id = "fixture"
    name = "fixture"


def check(condition, label):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        check.failed = True


check.failed = False


def vector_layer():
    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string(20)", "puntos", "memory")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(-70.6, -33.4)))
    feature.setAttributes(["Santiago"])
    layer.dataProvider().addFeatures([feature])
    return layer


def raster_layer(workdir):
    path = os.path.join(workdir, "grid.asc")  # not a GeoTIFF: exercises QgsRasterFileWriter
    with open(path, "w", encoding="ascii") as handle:
        handle.write("ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 1\nNODATA_value -9999\n1 2\n3 4\n")
    return QgsRasterLayer(path, "grilla")


def main() -> int:
    app = start_app()
    print(f"QGIS {Qgis.version()}")
    QgsSettings().setValue("locale/userLocale", "es")

    import gisdeploy
    from gisdeploy.core.sync_state import SyncState
    from gisdeploy.qgis_io.exporter import export_layer, project_layers
    from gisdeploy.qgis_io.layer_state import layer_sync_state
    from gisdeploy.qgis_io.tasks import start_task
    from gisdeploy.ui.labels import status_label
    from gisdeploy.ui.commit_dialog import CommitDialog
    from gisdeploy.ui.connect_dialog import ConnectDialog
    from gisdeploy.ui.history_dialog import HistoryDialog

    workdir = tempfile.mkdtemp(prefix="gisdeploy-smoke-")
    points, grid = vector_layer(), raster_layer(workdir)
    check(points.isValid() and grid.isValid(), "fixture layers are valid")
    QgsProject.instance().addMapLayers([points, grid])

    iface = FakeIface()
    plugin = gisdeploy.classFactory(iface)
    check(plugin.translator is not None, "Spanish translator installed for locale es")
    plugin.initGui()
    plugin.action.trigger()
    dock = plugin.dock
    # The fake main window is never shown, so check the dock's own visibility flag.
    check(dock is not None and not dock.isHidden() and "addDockWidget" in iface.calls,
          "dock opens from the toolbar action")
    check(dock.tabs.tabText(1) == "En GISDeploy", "dock tabs are translated")
    check(dock.connection_label.text() == "Sin conexión", "connection label is translated")
    check([status_label(s) for s in ("READY", "ACTIVE", "SUPERSEDED")] == ["Lista", "Activa", "Reemplazada"],
          "server statuses are translated")
    states = [dock.local_tree.topLevelItem(i).text(1) for i in range(dock.local_tree.topLevelItemCount())]
    check(states == ["● sin subir", "● sin subir"], f"local layers listed as untracked: {states}")

    entries = [(layer, layer_sync_state(layer)) for layer in project_layers()]
    check([state for _layer, state in entries] == [SyncState.UNTRACKED] * 2, "both layers are pushable")
    commit = CommitDialog(entries, dock)
    check(commit.windowTitle() == "Subir a GISDeploy", "push dialog is translated")
    check(sorted(commit.selected_layer_ids()) == sorted(layer.id() for layer in (points, grid)),
          "untracked layers are preselected")
    check(commit.visibility() == "PRIVATE", "new layers default to private (API value kept)")
    connect = ConnectDialog(dock)
    check(connect.windowTitle() == "Conectar con GISDeploy", "connect dialog is translated")
    history = HistoryDialog(FakeClient(), FakeDataset(), dock)
    check(history.windowTitle() == "Historial — fixture", "history dialog is translated")

    # Push, refresh and publish all run through start_task; the caller drops the
    # returned task, which must still run and report back.
    delivered = []
    start_task("smoke", lambda report: "worked", lambda result, error: delivered.append((result, error)))
    deadline = time.monotonic() + 20
    while not delivered and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    check(delivered == [("worked", None)] and QgsApplication.taskManager().countActiveTasks() == 0,
          f"background task runs without a caller reference: {delivered}")

    zip_path, vector_hash = export_layer(points, workdir)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    check(names == ["puntos.gpkg"] and bool(vector_hash), "vector exports to a zipped GeoPackage")
    tif_path, raster_hash = export_layer(grid, workdir)
    check(tif_path.endswith(".tif") and os.path.getsize(tif_path) > 0 and bool(raster_hash),
          "raster exports to GeoTIFF")

    plugin.unload()
    check(plugin.dock is None and plugin.action is None, "unload removes the dock and the action")
    check(QCoreApplication.translate("GisDeploy", "● untracked") == "● untracked",
          "unload removes the translator")
    check(iface.calls.count("addToolBarIcon") == iface.calls.count("removeToolBarIcon") == 1,
          "toolbar icon added once and removed once")

    QgsProject.instance().removeAllMapLayers()
    app.processEvents()
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
