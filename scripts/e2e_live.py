#!/usr/bin/env python3
"""End-to-end test of the plugin inside a real QGIS against a live GISDeploy server.

Drives the plugin's own dock and dialogs (modal dialogs are auto-accepted) in a
temporary QGIS profile with the Spanish interface:

  connect (encrypted authcfg) → push a private layer → edit → push revision 2 →
  history → rollback → add it back as WMS and render it → [push a public layer →
  publish map → open the public link anonymously] → check that no token leaked
  into settings, project files or messages → delete the test layers.

It creates real data in the token's workspace (test layers are deleted at the end;
published maps cannot be deleted through the API and are reported for manual
removal). The token is read from a file and never printed.

  GISDEPLOY_E2E_TOKEN_FILE=~/.config/gisdeploy-qgis-test.token \
  QT_QPA_PLATFORM=offscreen python3 qgis-plugin/scripts/e2e_live.py

Environment: GISDEPLOY_E2E_URL (default https://gisdeploy.com),
GISDEPLOY_E2E_PROJECT (project name; default: the first project of the token),
GISDEPLOY_E2E_PUBLISH=0 to skip the public layer and map,
GISDEPLOY_E2E_OUT (folder for screenshots; default a temporary folder).
"""

from __future__ import annotations

import collections
import os
import re
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from qgis.core import (  # noqa: E402
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize  # noqa: E402
from qgis.PyQt.QtGui import QColor, QDesktopServices  # noqa: E402
from qgis.PyQt.QtWidgets import QMessageBox  # noqa: E402
from qgis.testing import start_app  # noqa: E402

from smoke_headless import FakeIface  # noqa: E402

STAMP = time.strftime("%Y%m%d-%H%M%S")
OUT = os.environ.get("GISDEPLOY_E2E_OUT") or tempfile.mkdtemp(prefix="gisdeploy-e2e-")
os.makedirs(OUT, exist_ok=True)
FAILED = []


def check(condition, label):
    print(("ok   " if condition else "FAIL ") + label, flush=True)
    if not condition:
        FAILED.append(label)
    return condition


def wait_until(app, condition, timeout=300.0, label="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            for _ in range(20):  # let QgsTask.finished callbacks run
                app.processEvents()
                time.sleep(0.02)
            return True
        time.sleep(0.2)
    print(f"     timed out waiting for {label}", flush=True)
    return False


def tasks_idle():
    return QgsApplication.taskManager().countActiveTasks() == 0


def read_token() -> str:
    path = os.path.expanduser(os.environ.get("GISDEPLOY_E2E_TOKEN_FILE", "~/.config/gisdeploy-qgis-test.token"))
    with open(path, encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token.startswith("agt_"):
        raise SystemExit(f"{path} does not contain a GISDeploy token (agt_…)")
    return token


SYMBOL_COLOR = "#d7191c"  # GeoServer's fallback when it drops a style's parameters is #808080 grey


def near(rgb, hex_color, tolerance=24):
    wanted = QColor(hex_color)
    return rgb is not None and all(abs(a - b) <= tolerance for a, b in zip(rgb, (wanted.red(), wanted.green(), wanted.blue())))


def points_layer(name, coords):
    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=nombre:string(40)", name, "memory")
    features = []
    for label, (x, y) in coords:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        feature.setAttributes([label])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    symbol = layer.renderer().symbol()
    symbol.setColor(QColor(SYMBOL_COLOR))
    symbol.setSize(6)
    return layer


def render(layer, extent_4326, path, probe_4326=None):
    """Render over opaque blue: (painted px, dominant colour, leftmost painted x, probe pixel x)."""
    settings = QgsMapSettings()
    settings.setLayers([layer])
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    settings.setDestinationCrs(crs)
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, QgsProject.instance())
    settings.setExtent(transform.transformBoundingBox(extent_4326))
    settings.setOutputSize(QSize(512, 512))
    settings.setBackgroundColor(QColor(0, 0, 255))  # opaque blue: anything else was drawn by the layer
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    image.save(path)
    colors = collections.Counter()
    leftmost = None
    for x in range(image.width()):
        for y in range(image.height()):
            color = QColor(image.pixel(x, y))  # QColor(QRgb) drops alpha: compare RGB only
            rgb = (color.red(), color.green(), color.blue())
            if rgb != (0, 0, 255):
                colors[rgb] += 1
                leftmost = x if leftmost is None else min(leftmost, x)
    dominant = colors.most_common(1)[0][0] if colors else None  # the symbol fill, not its outline or edges
    probe_x = None
    if probe_4326 is not None:
        probe_x = settings.mapToPixel().transform(transform.transform(QgsPointXY(*probe_4326))).x()
    return sum(colors.values()), dominant, leftmost, probe_x


def anonymous_status(url):
    request = urllib.request.Request(url, headers={"User-Agent": "gisdeploy-qgis-e2e"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main() -> int:
    app = start_app()  # temporary profile: settings and auth DB are discarded
    print(f"QGIS {Qgis.version()}  ·  screenshots in {OUT}", flush=True)
    QgsSettings().setValue("locale/userLocale", "es")
    url = os.environ.get("GISDEPLOY_E2E_URL", "https://gisdeploy.com")
    token = read_token()

    import gisdeploy
    from gisdeploy.qgis_io import layer_state
    from gisdeploy.qgis_io.auth import save_connection, saved_connection
    from gisdeploy.ui import dock_widget, history_dialog
    from gisdeploy.ui.commit_dialog import CommitDialog
    from gisdeploy.ui.history_dialog import HistoryDialog

    manager = QgsApplication.authManager()
    check(manager.setMasterPassword("gisdeploy-e2e-master", True), "temporary QGIS auth database unlocked")

    # ── connect, as the Connect… dialog does after its live test ────────────────
    authcfg = save_connection(url, token)
    check(saved_connection() == (url, authcfg), "connection stored as an authcfg reference")
    token = None  # from here on only QGIS's encrypted store holds it

    iface = FakeIface()
    messages = []
    for level in ("pushSuccess", "pushWarning", "pushInfo", "pushCritical"):
        original = getattr(iface.bar, level)

        def record(title, text, _original=original, _level=level):
            messages.append((_level, text))
            return _original(title, text)

        setattr(iface.bar, level, record)
    original_push_message = iface.bar.pushMessage

    def record_message(title, text, *args, **kwargs):
        messages.append(("pushMessage", text))
        return original_push_message(title, text, *args, **kwargs)

    iface.bar.pushMessage = record_message

    plugin = gisdeploy.classFactory(iface)
    plugin.initGui()
    plugin.action.trigger()
    dock = plugin.dock
    dock.resize(520, 640)
    connected = check(dock.client is not None, f"dock connected on open: {dock.connection_label.text()}")
    if not connected:
        print("     last messages: " + "; ".join(text for _level, text in messages[-3:]))
        return 1
    project_names = [dock.project_combo.itemText(i) for i in range(dock.project_combo.count())]
    wanted = os.environ.get("GISDEPLOY_E2E_PROJECT")
    if wanted:
        if not check(wanted in project_names, f"project {wanted!r} available ({project_names})"):
            return 1
        dock.project_combo.setCurrentIndex(project_names.index(wanted))
    check(dock.project_combo.count() > 0, f"projects listed: {project_names}")
    print(f"     using project {dock.project_combo.currentText()!r}", flush=True)
    wait_until(app, tasks_idle, 60, "remote refresh")

    dataset_id = public_id = public_map = None
    try:
        # ── 1. push a new private layer ─────────────────────────────────────────────
        santiago = [("Plaza de Armas", (-70.6506, -33.4378)), ("Cerro Santa Lucía", (-70.6435, -33.4405)),
                    ("Parque Forestal", (-70.6420, -33.4359))]
        private = points_layer(f"prueba-plugin-privada-{STAMP}", santiago)
        QgsProject.instance().addMapLayer(private)
        dock.refresh_local()
        check(dock.local_tree.topLevelItem(0).text(1) == "● sin subir", "new layer shows as untracked")

        commit_plan = {"message": "", "visibility": "PRIVATE", "layer_ids": set(), "preselected": None, "shots": []}

        def fake_exec(dialog):
            commit_plan["preselected"] = set(dialog.selected_layer_ids())
            for row in range(dialog.layer_list.count()):
                item = dialog.layer_list.item(row)
                wanted = item.data(dock_widget.Qt.ItemDataRole.UserRole) in commit_plan["layer_ids"]
                item.setCheckState(dock_widget.Qt.CheckState.Checked if wanted else dock_widget.Qt.CheckState.Unchecked)
            dialog.message_edit.setPlainText(commit_plan["message"])
            index = dialog.visibility_combo.findData(commit_plan["visibility"])
            dialog.visibility_combo.setCurrentIndex(index)
            dialog.resize(480, 360)
            shot = os.path.join(OUT, f"push-dialog-{len(commit_plan['shots']) + 1}.png")
            dialog.grab().save(shot)
            commit_plan["shots"].append(shot)
            return 1

        CommitDialog.exec = fake_exec

        def push(layers, message, visibility="PRIVATE"):
            commit_plan.update(message=message, visibility=visibility, layer_ids={layer.id() for layer in layers})
            before = len(messages)
            dock._push()
            check(commit_plan["preselected"] == commit_plan["layer_ids"],
                  f"push dialog preselects only the new/modified layers for '{message}'")
            done = wait_until(
                app, lambda: tasks_idle() and any(level in ("pushSuccess", "pushMessage") for level, _t in messages[before:]),
                600, f"push '{message}'",
            )
            return done, messages[before:]

        done, events = push([private], "Primera versión desde QGIS (prueba)")
        dataset_id = layer_state.linked_dataset_id(private)
        if not check(done and dataset_id is not None, f"push 1 linked the layer to dataset {dataset_id}"):
            raise RuntimeError(f"first push failed: {[text for _l, text in messages[-2:]]}")
        check(not any(level in ("pushWarning", "pushMessage") for level, _t in events),
              f"push 1 without warnings: {[text for _l, text in events]}")
        dock.refresh_local()
        check(dock.local_tree.topLevelItem(0).text(1) == "✔ sincronizada", "layer shows as synced after push")
        wait_until(app, tasks_idle, 60, "remote refresh")

        # ── 2. edit and push revision 2 ─────────────────────────────────────────────
        private.dataProvider().addFeatures([points_layer("tmp", [("Bellas Artes", (-70.6440, -33.4345))]).getFeature(1)])
        dock.refresh_local()
        check(dock.local_tree.topLevelItem(0).text(1) == "✚ modificada", "edited layer shows as modified")
        done, events = push([private], "Agrega Bellas Artes (prueba)")
        check(done and not any(level in ("pushWarning", "pushMessage") for level, _t in events),
              f"push 2 completed: {[text for _l, text in events]}")
        history = dock.client.list_revisions(dataset_id)
        numbers = sorted(revision.number for revision in history["revisions"])
        check(history["head"] == 2 and 2 in numbers, f"server head is revision 2 (revisions {numbers})")

        # ── 3. history dialog and rollback ──────────────────────────────────────────
        dataset = next(d for d in dock.client.list_datasets(dock._current_project_id()) if d.id == dataset_id)
        dialog = HistoryDialog(dock.client, dataset, dock)
        dialog.resize(640, 360)
        rows = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
        check("#1" in rows and "#2" in rows, f"history dialog lists the revisions: {rows}")
        dialog.grab().save(os.path.join(OUT, "history-dialog.png"))

        class YesBox:
            StandardButton = QMessageBox.StandardButton

            @staticmethod
            def question(*_args, **_kwargs):
                return QMessageBox.StandardButton.Yes

        history_dialog.QMessageBox = YesBox
        first = next(dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())
                     if dialog.tree.topLevelItem(i).text(0) == "#1")
        dialog.tree.setCurrentItem(first)
        dialog._rollback()
        check(dialog.rolled_back, f"rollback accepted: {dialog.status_label.text()}")
        # The dataset stays READY while a revision processes: wait for the head itself.
        landed = wait_until(app, lambda: time.sleep(2) or dock.client.list_revisions(dataset_id)["head"] == 3,
                            300, "rollback revision #3")
        check(landed, f"rollback revision #3 is serving (head {dock.client.list_revisions(dataset_id)['head']})")

        # ── 4. add it back to QGIS as WMS and render it ─────────────────────────────
        dock._refresh_remote()
        wait_until(app, tasks_idle, 60, "remote refresh")
        item = next((dock.remote_tree.topLevelItem(i) for i in range(dock.remote_tree.topLevelItemCount())
                     if dock.remote_tree.topLevelItem(i).data(0, dock_widget.Qt.ItemDataRole.UserRole) == dataset_id), None)
        check(item is not None, "dataset listed in the En GISDeploy tab")
        dock.remote_tree.setCurrentItem(item)
        dock.tabs.setCurrentIndex(1)
        dock.grab().save(os.path.join(OUT, "dock-remote.png"))
        before = set(QgsProject.instance().mapLayers())
        dock._add_remote_layer()
        added = [QgsProject.instance().mapLayer(i) for i in set(QgsProject.instance().mapLayers()) - before]
        wms = added[0] if added else None
        check(wms is not None and wms.isValid(), "Add to QGIS loaded a valid WMS layer")
        if wms is not None:
            source = wms.source()
            check("authcfg=" in source and "agt_" not in source and "password" not in source.lower(),
                  "WMS source uses authcfg only")
            extent = QgsRectangle(-70.6560, -33.4440, -70.6380, -33.4320)
            west_point = min(coords for _label, coords in santiago)  # the layer's west edge runs through its centre
            painted, dominant, leftmost, west_x = render(wms, extent, os.path.join(OUT, "wms-render.png"), west_point)
            check(0 < painted < 512 * 512 // 4, f"private WMS draws the points over a transparent background ({painted} px)")
            check(leftmost is not None and leftmost < west_x - 2,
                  f"edge symbols are drawn whole, not cropped at the layer extent "
                  f"(leftmost painted x {leftmost}, west point centre x {west_x:.0f})")
            check(near(dominant, SYMBOL_COLOR),
                  f"private WMS draws the QGIS symbol colour {SYMBOL_COLOR}, not GeoServer's default grey "
                  f"(dominant {'#%02x%02x%02x' % dominant if dominant else None})")

        # Plugins up to 0.1.1 send QGIS's SLD untouched, schemaLocation hint included.
        raw_sld = os.path.join(OUT, "raw.sld")
        private.saveSldStyle(raw_sld)
        with open(raw_sld, encoding="utf-8") as handle:
            raw = handle.read()
        if check("schemaLocation" in raw, "QGIS export carries a schemaLocation hint"):
            try:
                status = dock.client.apply_sld(dataset_id, raw).get("status")
            except Exception as error:  # noqa: BLE001 — the check reports it
                status = type(error).__name__
            check(status in ("applied", "unchanged"), f"server accepts the raw QGIS SLD ({status})")
        dock.tabs.setCurrentIndex(0)
        dock.grab().save(os.path.join(OUT, "dock-local.png"))

        # ── 5. public layer, publish map, open the link anonymously ─────────────────
        if os.environ.get("GISDEPLOY_E2E_PUBLISH", "1") != "0":
            public = points_layer(f"prueba-plugin-publica-{STAMP}", santiago)
            QgsProject.instance().addMapLayer(public)
            done, events = push([public], "Capa pública de prueba", visibility="PUBLIC")
            public_id = layer_state.linked_dataset_id(public)
            check(done and public_id is not None, f"public layer pushed as dataset {public_id}")
            dock._refresh_remote()
            wait_until(app, tasks_idle, 60, "remote refresh")
            item = next((dock.remote_tree.topLevelItem(i) for i in range(dock.remote_tree.topLevelItemCount())
                         if dock.remote_tree.topLevelItem(i).data(0, dock_widget.Qt.ItemDataRole.UserRole) == public_id),
                        None)
            check(item is not None and item.text(2) == "Pública", "public layer listed as Pública")
            dock.remote_tree.setCurrentItem(item)
            opened = []

            class TitleDialog:
                @staticmethod
                def getText(*_args, **_kwargs):
                    return f"Mapa de prueba plugin QGIS {STAMP}", True

            dock_widget.QInputDialog = TitleDialog
            QDesktopServices.openUrl = staticmethod(lambda qurl: opened.append(qurl.toString()) or True)
            before = len(messages)
            dock._publish_map()
            wait_until(app, lambda: tasks_idle() and len(messages) > before, 180, "publish map")
            public_map = opened[0] if opened else None
            check(public_map is not None, f"Publish map… opened the link: {public_map}")
            if public_map:
                status = anonymous_status(public_map)
                check(status == 200, f"public map opens without login (HTTP {status})")

        # ── 6. no credential leaks ───────────────────────────────────────────────────
        project_path = os.path.join(OUT, "prueba.qgz")
        QgsProject.instance().write(project_path)
        with zipfile.ZipFile(project_path) as archive:
            project_text = "".join(archive.read(name).decode("utf-8", "ignore") for name in archive.namelist())
        settings_file = QgsSettings().fileName()
        with open(settings_file, encoding="utf-8", errors="ignore") as handle:
            settings_text = handle.read()
        check("agt_" not in project_text and "password=" not in project_text, "saved .qgz contains no token")
        check("agt_" not in settings_text, "QGIS settings contain no token")
        check(not any("agt_" in text for _level, text in messages), "messages show no token")

    except Exception:  # noqa: BLE001 — record, then always clean up
        FAILED.append("aborted")
        print(re.sub(r"agt_[A-Za-z0-9_-]+", "[redacted]", traceback.format_exc()), flush=True)
    finally:
        # ── 7. cleanup ───────────────────────────────────────────────────────────────
        # Maps this run published come down through the agent API: unpublish (the
        # public link must stop working), then delete (it must leave the listing).
        try:
            test_maps = [m for m in dock.client._get("/api/agent/maps?take=200").get("maps", [])
                         if STAMP in (m.get("title") or "")]
        except Exception as error:  # noqa: BLE001
            test_maps = []
            check(public_map is None, f"maps listed for cleanup ({type(error).__name__})")
        for item in test_maps:
            link = item.get("publicUrl")
            try:
                dock.client._send_json("POST", f"/api/agent/maps/{item['id']}/unpublish", {})
                if link:
                    status = anonymous_status(link)
                    check(status == 404, f"unpublished map link stops working (HTTP {status})")
                dock.client._send_json("DELETE", f"/api/agent/maps/{item['id']}", {})
                remaining = {m["id"] for m in dock.client._get("/api/agent/maps?take=200").get("maps", [])}
                check(item["id"] not in remaining, f"deleted test map {item['id']} is gone from the listing")
                public_map = None
            except Exception as error:  # noqa: BLE001 — report, keep going
                check(False, f"test map {item['id']} cleaned up ({error})")
        leftovers = set(filter(None, (dataset_id, public_id)))
        try:  # also sweep layers a half-finished push may have created
            leftovers |= {d.id for d in dock.client.list_datasets(dock._current_project_id()) if STAMP in (d.name or "")}
        except Exception as error:  # noqa: BLE001
            print(f"     could not list leftovers: {type(error).__name__}", flush=True)
        for identifier in sorted(leftovers):
            try:
                dock.client.delete_dataset(identifier)
                print(f"     deleted test dataset {identifier}", flush=True)
            except Exception as error:  # noqa: BLE001 — report, keep going
                print(f"     could not delete {identifier}: {error}", flush=True)
                FAILED.append(f"cleanup {identifier}")
        if public_map:
            print(f"     delete the test map manually in the dashboard: {public_map}", flush=True)
    plugin.unload()
    QgsProject.instance().removeAllMapLayers()
    print(f"\n{'ALL OK' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}", flush=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
