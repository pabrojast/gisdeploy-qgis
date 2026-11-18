"""The GISDeploy dock: connection, project, local git-status, remote catalog."""

from __future__ import annotations

import os
import tempfile

from qgis.core import Qgis, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.api_client import ApiError, GisDeployClient, TransportError
from ..qgis_io import layer_state
from ..qgis_io.exporter import ExportError, export_layer, project_layers
from ..qgis_io.tasks import poll_until_ready, start_task
from ..qgis_io.wms import scrub_legacy_uri, wms_uri
from ..qgis_io.auth import AuthenticationError, connection_token
from ..core.security import clean_url, safe_message
from ..translation import error_text
from .commit_dialog import CommitDialog
from .connect_dialog import ConnectDialog, saved_connection
from .history_dialog import HistoryDialog
from .labels import state_label, visibility_label


class GisDeployDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("GISDeploy", parent)
        self.iface = iface
        self.setObjectName("GisDeployDock")
        self.client: GisDeployClient | None = None
        self.context = None
        self.authcfg = None
        self.remote_datasets = []
        self._busy = False

        self._build_ui()
        self._try_autoconnect()

    # ── UI scaffolding ──────────────────────────────────────────────────────

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        # Connection row
        connection_row = QHBoxLayout()
        self.connection_label = QLabel(self.tr("Not connected"))
        self.connection_label.setWordWrap(True)
        configure_button = QPushButton(self.tr("Connect…"))
        configure_button.clicked.connect(self._configure)
        connection_row.addWidget(self.connection_label, 1)
        connection_row.addWidget(configure_button)
        layout.addLayout(connection_row)

        # Project row
        project_row = QHBoxLayout()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(lambda _i: self._refresh_remote())
        new_project_button = QPushButton("+")
        new_project_button.setFixedWidth(28)
        new_project_button.setToolTip(self.tr("New project"))
        new_project_button.clicked.connect(self._create_project)
        project_row.addWidget(QLabel(self.tr("Project")))
        project_row.addWidget(self.project_combo, 1)
        project_row.addWidget(new_project_button)
        layout.addLayout(project_row)

        self.tabs = QTabWidget()

        # Local tab
        local_tab = QWidget()
        local_layout = QVBoxLayout(local_tab)
        self.local_tree = QTreeWidget()
        self.local_tree.setHeaderLabels([self.tr("Layer"), self.tr("State")])
        self.local_tree.setRootIsDecorated(False)
        local_layout.addWidget(self.local_tree)
        local_buttons = QHBoxLayout()
        push_button = QPushButton(self.tr("Push…"))
        push_button.clicked.connect(self._push)
        refresh_local_button = QPushButton(self.tr("Refresh"))
        refresh_local_button.clicked.connect(self.refresh_local)
        local_buttons.addWidget(push_button)
        local_buttons.addWidget(refresh_local_button)
        local_buttons.addStretch(1)
        local_layout.addLayout(local_buttons)
        self.tabs.addTab(local_tab, self.tr("Local"))

        # Remote tab
        remote_tab = QWidget()
        remote_layout = QVBoxLayout(remote_tab)
        self.remote_tree = QTreeWidget()
        self.remote_tree.setHeaderLabels(
            [self.tr("Dataset"), self.tr("Status"), self.tr("Visibility"), self.tr("Last commit")]
        )
        self.remote_tree.setRootIsDecorated(False)
        remote_layout.addWidget(self.remote_tree)
        remote_buttons = QHBoxLayout()
        add_button = QPushButton(self.tr("Add to QGIS"))
        add_button.clicked.connect(self._add_remote_layer)
        history_button = QPushButton(self.tr("History…"))
        history_button.clicked.connect(self._show_history)
        publish_button = QPushButton(self.tr("Publish map…"))
        publish_button.clicked.connect(self._publish_map)
        refresh_remote_button = QPushButton(self.tr("Refresh"))
        refresh_remote_button.clicked.connect(self._refresh_remote)
        for button in (add_button, history_button, publish_button, refresh_remote_button):
            remote_buttons.addWidget(button)
        remote_buttons.addStretch(1)
        remote_layout.addLayout(remote_buttons)
        self.tabs.addTab(remote_tab, self.tr("Remote"))

        layout.addWidget(self.tabs)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.setWidget(container)

        QgsProject.instance().layersAdded.connect(self._scrub_legacy_layers)
        self._scrub_legacy_layers(list(QgsProject.instance().mapLayers().values()))
        QgsProject.instance().layersAdded.connect(lambda _l: self.refresh_local())
        QgsProject.instance().layersRemoved.connect(lambda _l: self.refresh_local())

    # ── connection ──────────────────────────────────────────────────────────

    def _try_autoconnect(self):
        url, authcfg = saved_connection()
        if url and authcfg:
            self._connect(url, authcfg, silent=True)
        self.refresh_local()

    def _configure(self):
        dialog = ConnectDialog(self)
        if dialog.exec():
            url, authcfg = saved_connection()
            self._connect(url, authcfg)

    def _connect(self, url: str, authcfg: str, silent: bool = False):
        try:
            self.client = GisDeployClient(url, connection_token(authcfg))
            self.authcfg = authcfg
            self.context = self.client.context()
        except (ApiError, TransportError, AuthenticationError, ValueError) as error:
            self.client = None
            self.context = None
            self.connection_label.setText(self.tr("Not connected"))
            if not silent:
                self._error(error_text(error))
            return
        usage = self.context.usage or {}
        self.connection_label.setText(
            self.tr("{0} — plan {1} · {2} layers").format(
                self.context.organization_name, self.context.plan, usage.get("layers", "?")
            )
        )
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self.context.projects:
            self.project_combo.addItem(project.name, project.id)
        self.project_combo.blockSignals(False)
        self._refresh_remote()

    def _require_client(self) -> bool:
        if self.client is None:
            self._error(self.tr("Connect to GISDeploy first (Connect… button)."))
            return False
        return True

    # ── local tab ───────────────────────────────────────────────────────────

    def refresh_local(self):
        self.local_tree.clear()
        for layer in project_layers():
            state = layer_state.layer_sync_state(layer)
            item = QTreeWidgetItem([layer.name(), state_label(state)])
            item.setData(0, Qt.ItemDataRole.UserRole, layer.id())
            self.local_tree.addTopLevelItem(item)

    def _push(self):
        if not self._require_client() or self._busy:
            return
        entries = [
            (layer, layer_state.layer_sync_state(layer)) for layer in project_layers()
        ]
        if not entries:
            self._error(self.tr("No pushable layers in this project."))
            return
        dialog = CommitDialog(entries, self)
        if not dialog.exec():
            return
        selected_ids = set(dialog.selected_layer_ids())
        if not selected_ids:
            return

        message = dialog.message()
        visibility = dialog.visibility()
        include_style = dialog.push_style()

        # Exports (and SLD writes) happen on the main thread; only network work
        # is handed to the background task.
        workdir = tempfile.mkdtemp(prefix="gisdeploy-push-")
        jobs = []
        for layer, _state in entries:
            if layer.id() not in selected_ids:
                continue
            try:
                upload_path, content_hash = export_layer(layer, workdir)
            except ExportError as error:
                self._error(self.tr("{0}: {1}").format(layer.name(), error_text(error)))
                continue
            sld_path = None
            if include_style:
                sld_path = os.path.join(workdir, f"{layer.id()}.sld")
                _message, ok = layer.saveSldStyle(sld_path), True
                if not os.path.isfile(sld_path):
                    sld_path = None
            jobs.append(
                {
                    "layer_id": layer.id(),
                    "layer_name": layer.name(),
                    "dataset_id": layer_state.linked_dataset_id(layer),
                    "upload_path": upload_path,
                    "content_hash": content_hash,
                    "sld_path": sld_path,
                }
            )
        if not jobs:
            return

        client = self.client
        base_url = client.base_url
        project_id = self._current_project_id()

        def work(report):
            results = []
            total = len(jobs)
            for index, job in enumerate(jobs):
                offset = index / total

                def job_report(fraction, note="", _offset=offset):
                    report(_offset + fraction / total, note)

                job_report(0.05, self.tr("Uploading {0}…").format(job["layer_name"]))
                if job["dataset_id"]:
                    outcome = client.push_revision(
                        job["dataset_id"],
                        job["upload_path"],
                        message or None,
                        progress=lambda f: job_report(0.05 + f * 0.4),
                    )
                    dataset_id = outcome.get("datasetId", job["dataset_id"])
                    unchanged = outcome.get("status") == "unchanged"
                else:
                    outcome = client.upload_file(
                        job["upload_path"],
                        name=job["layer_name"],
                        visibility=visibility,
                        project_id=project_id,
                        progress=lambda f: job_report(0.05 + f * 0.4),
                    )
                    dataset_id = outcome["datasetId"]
                    unchanged = False

                if not unchanged:
                    job_report(0.5, self.tr("Processing {0}…").format(job["layer_name"]))
                    poll_until_ready(client, dataset_id, job_report, base_fraction=0.5)

                sld_error = None
                if job["sld_path"]:
                    try:
                        with open(job["sld_path"], "r", encoding="utf-8") as handle:
                            client.apply_sld(dataset_id, handle.read())
                    except (ApiError, TransportError, OSError) as error:
                        sld_error = str(error)

                results.append(
                    {
                        "layer_id": job["layer_id"],
                        "layer_name": job["layer_name"],
                        "dataset_id": dataset_id,
                        "content_hash": job["content_hash"],
                        "unchanged": unchanged,
                        "sld_error": sld_error,
                    }
                )
            return results

        def on_done(results, error):
            self._busy = False
            if error is not None:
                self._error(self.tr("Push failed: {0}").format(error_text(error)))
                self.refresh_local()
                return
            pushed = 0
            for result in results or []:
                layer = QgsProject.instance().mapLayer(result["layer_id"])
                if layer is not None:
                    layer_state.link_layer(
                        layer, result["dataset_id"], result["content_hash"], base_url
                    )
                if not result["unchanged"]:
                    pushed += 1
                if result["sld_error"]:
                    self.iface.messageBar().pushWarning(
                        "GISDeploy",
                        self.tr("Style push failed for {0}: {1}").format(
                            result["layer_name"], result["sld_error"]
                        ),
                    )
            self.iface.messageBar().pushSuccess(
                "GISDeploy",
                self.tr("Push complete: {0} layer(s) updated.").format(pushed),
            )
            self.refresh_local()
            self._refresh_remote()

        self._busy = True
        self.status_label.setText(self.tr("Pushing {0} layer(s)…").format(len(jobs)))
        start_task(self.tr("GISDeploy push"), work, on_done)

    # ── remote tab ──────────────────────────────────────────────────────────

    def _current_project_id(self):
        return self.project_combo.currentData()

    def _refresh_remote(self):
        if self.client is None:
            return
        client = self.client
        project_id = self._current_project_id()

        def work(_report):
            return client.list_datasets(project_id)

        def on_done(datasets, error):
            if error is not None:
                self._error(error_text(error))
                return
            self.remote_datasets = datasets or []
            self.remote_tree.clear()
            for dataset in self.remote_datasets:
                head = dataset.head
                last_commit = ""
                if head:
                    last_commit = f"#{head.number}"
                    if head.message:
                        last_commit += f" {head.message}"
                item = QTreeWidgetItem(
                    [dataset.name, dataset.status, visibility_label(dataset.visibility), last_commit]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, dataset.id)
                self.remote_tree.addTopLevelItem(item)
            self.status_label.setText("")

        start_task(self.tr("GISDeploy refresh"), work, on_done)

    def _selected_remote_dataset(self):
        item = self.remote_tree.currentItem()
        if not item:
            self._error(self.tr("Select a remote dataset first."))
            return None
        dataset_id = item.data(0, Qt.ItemDataRole.UserRole)
        for dataset in self.remote_datasets:
            if dataset.id == dataset_id:
                return dataset
        return None

    def _add_remote_layer(self):
        if not self._require_client():
            return
        dataset = self._selected_remote_dataset()
        if dataset is None:
            return
        if not dataset.wms_url:
            self._error(self.tr("This dataset has no WMS endpoint yet (still processing?)."))
            return

        try:
            uri = wms_uri(dataset.wms_url, dataset.id, self.authcfg)
        except ValueError as error:
            self._error(error_text(error))
            return
        layer = QgsRasterLayer(uri, dataset.name, "wms")
        if not layer.isValid():
            self._error(self.tr("Could not load the WMS layer."))
            return
        QgsProject.instance().addMapLayer(layer)
        self.iface.messageBar().pushSuccess(
            "GISDeploy", self.tr("Added {0} as WMS.").format(dataset.name)
        )

    def _show_history(self):
        if not self._require_client():
            return
        dataset = self._selected_remote_dataset()
        if dataset is None:
            return
        dialog = HistoryDialog(self.client, dataset, self)
        dialog.exec()
        if dialog.rolled_back:
            self._refresh_remote()

    def _publish_map(self):
        if not self._require_client():
            return
        dataset = self._selected_remote_dataset()
        if dataset is None:
            return
        title, ok = QInputDialog.getText(
            self, self.tr("Publish map"), self.tr("Map title:"), text=dataset.name
        )
        if not ok or not title.strip():
            return

        client = self.client
        visibility = "PUBLIC" if dataset.visibility == "PUBLIC" else "PRIVATE"

        def work(_report):
            created = client.create_map(title.strip(), [dataset.id], visibility)
            return client.publish_map(created["storyId"])

        def on_done(result, error):
            if error is not None:
                self._error(self.tr("Publish failed: {0}").format(error_text(error)))
                return
            url = (result or {}).get("publicUrl") or (result or {}).get("embedUrl") or ""
            self.iface.messageBar().pushSuccess(
                "GISDeploy", self.tr("Map published: {0}").format(url)
            )
            if url:
                from qgis.PyQt.QtCore import QUrl
                from qgis.PyQt.QtGui import QDesktopServices

                QDesktopServices.openUrl(QUrl(url))

        start_task(self.tr("GISDeploy publish"), work, on_done)

    def _create_project(self):
        if not self._require_client():
            return
        name, ok = QInputDialog.getText(self, self.tr("New project"), self.tr("Project name:"))
        if not ok or not name.strip():
            return
        try:
            result = self.client.create_project(name.strip())
        except (ApiError, TransportError, AuthenticationError, ValueError) as error:
            self._error(error_text(error))
            return
        self.project_combo.addItem(name.strip(), result["projectId"])
        self.project_combo.setCurrentIndex(self.project_combo.count() - 1)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _scrub_legacy_layers(self, layers):
        changed = False
        for layer in layers:
            base_url = layer.customProperty(layer_state.PROP_BASE_URL)
            if base_url:
                try:
                    clean_url(str(base_url))
                except ValueError:
                    layer.removeCustomProperty(layer_state.PROP_BASE_URL)
                    changed = True
            if layer.providerType() != "wms":
                continue
            source = layer.source()
            if not any(marker in source.lower() for marker in ("password", "username", "authorization", "agt_")):
                continue
            cleaned = scrub_legacy_uri(source)
            if cleaned is not None:
                layer.setDataSource(cleaned, layer.name(), "wms")
                changed = True
        if changed:
            QgsProject.instance().setDirty(True)
            self.iface.messageBar().pushWarning("GISDeploy", self.tr("Legacy layer credentials were removed. Re-add private layers, save this project, and revoke the old token in GISDeploy."))

    def _error(self, message: str):
        message = safe_message(message)
        self.status_label.setText(message)
        self.iface.messageBar().pushMessage("GISDeploy", message, level=Qgis.MessageLevel.Warning)
