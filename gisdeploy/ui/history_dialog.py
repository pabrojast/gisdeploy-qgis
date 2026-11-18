"""Revision history of a dataset: the 'git log' view, with rollback."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core.api_client import ApiError, TransportError
from ..translation import error_text
from .labels import status_label


class HistoryDialog(QDialog):
    def __init__(self, client, dataset, parent=None):
        super().__init__(parent)
        self.client = client
        self.dataset = dataset
        self.rolled_back = False
        self.setWindowTitle(self.tr("History — {0}").format(dataset.name))
        self.setMinimumSize(560, 360)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            [self.tr("Rev"), self.tr("Message"), self.tr("Author"), self.tr("Date"), self.tr("Status")]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(1, 200)

        self.rollback_button = QPushButton(self.tr("Rollback to selected"))
        self.rollback_button.clicked.connect(self._rollback)
        self.status_label = QLabel("")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.rollback_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)

        self._reload()

    def _reload(self):
        self.tree.clear()
        try:
            history = self.client.list_revisions(self.dataset.id)
        except (ApiError, TransportError) as error:
            self.status_label.setText(error_text(error))
            return
        for revision in history["revisions"]:
            status = status_label(revision.status)
            if revision.number == history.get("head"):
                status = self.tr("{0} (head)").format(status)
            item = QTreeWidgetItem(
                [
                    f"#{revision.number}",
                    revision.message or "",
                    revision.created_by or "",
                    revision.created_at.replace("T", " ")[:19],
                    status,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, revision.number)
            if revision.error_message:
                item.setToolTip(4, revision.error_message)
            self.tree.addTopLevelItem(item)

    def _rollback(self):
        item = self.tree.currentItem()
        if not item:
            self.status_label.setText(self.tr("Select a revision first."))
            return
        number = item.data(0, Qt.ItemDataRole.UserRole)
        confirmed = QMessageBox.question(
            self,
            self.tr("Rollback"),
            self.tr(
                "Restore revision #{0}? A new revision referencing its content "
                "will be created (nothing is deleted)."
            ).format(number),
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.client.rollback(self.dataset.id, int(number))
        except (ApiError, TransportError) as error:
            self.status_label.setText(error_text(error))
            return
        self.rolled_back = True
        if result.get("status") == "unchanged":
            self.status_label.setText(self.tr("Already at that content — nothing to do."))
        else:
            self.status_label.setText(
                self.tr("Rollback queued as revision #{0}. The layer will update shortly.").format(
                    (result.get("revision") or {}).get("number", "?")
                )
            )
        self._reload()
