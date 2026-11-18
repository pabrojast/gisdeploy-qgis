"""The commit dialog: message, per-push options, and the layer checklist."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..core.sync_state import SyncState
from .labels import VISIBILITY_LABELS, state_label, visibility_label


class CommitDialog(QDialog):
    """Choose layers + a commit message. `entries` = [(layer, SyncState)]."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Push to GISDeploy"))
        self.setMinimumWidth(460)

        self.layer_list = QListWidget()
        for layer, state in entries:
            label = f"{layer.name()}  —  {state_label(state)}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if state in (SyncState.UNTRACKED, SyncState.MODIFIED)
                else Qt.CheckState.Unchecked
            )
            self.layer_list.addItem(item)

        self.message_edit = QPlainTextEdit()
        self.message_edit.setPlaceholderText(self.tr("What changed? (commit message)"))
        self.message_edit.setFixedHeight(70)

        self.visibility_combo = QComboBox()
        for value in VISIBILITY_LABELS:
            self.visibility_combo.addItem(visibility_label(value), value)

        self.push_style_check = QCheckBox(self.tr("Also push the layer style (SLD)"))
        self.push_style_check.setChecked(True)

        form = QFormLayout()
        form.addRow(self.tr("New layers visibility"), self.visibility_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Push"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Layers to push:")))
        layout.addWidget(self.layer_list)
        layout.addWidget(self.message_edit)
        layout.addLayout(form)
        layout.addWidget(self.push_style_check)
        layout.addWidget(buttons)

    def selected_layer_ids(self):
        ids = []
        for row in range(self.layer_list.count()):
            item = self.layer_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def message(self) -> str:
        return self.message_edit.toPlainText().strip()

    def visibility(self) -> str:
        return self.visibility_combo.currentData()

    def push_style(self) -> bool:
        return self.push_style_check.isChecked()
