"""Connection settings: server URL + agent token, validated live."""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..core.api_client import ApiError, GisDeployClient, TransportError

from ..qgis_io.auth import AuthenticationError, connection_token, save_connection, saved_connection
from ..core.security import clean_url
from ..translation import error_text


class ConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Connect to GISDeploy"))
        self.setMinimumWidth(420)

        url, self.authcfg = saved_connection()
        self.original_url = url
        self.url_edit = QLineEdit(url)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("agt_…")

        form = QFormLayout()
        form.addRow(self.tr("Server URL"), self.url_edit)
        form.addRow(self.tr("API token"), self.token_edit)

        hint = QLabel(
            self.tr(
                "Create an access token in the GISDeploy dashboard under QGIS & API "
                "(preset Full). Credentials are saved in the QGIS encrypted "
                "authentication database. Leave the token blank to reuse your saved connection."
            )
        )
        hint.setWordWrap(True)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Test && Save"))
        buttons.accepted.connect(self._test_and_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)

        self.context = None

    def _test_and_save(self):
        url = self.url_edit.text().strip().rstrip("/")
        token = self.token_edit.text().strip()

        self.status_label.setText(self.tr("Testing connection…"))
        self.repaint()
        try:
            url = clean_url(url)
            if not token:
                if url != self.original_url or not self.authcfg:
                    raise AuthenticationError(self.tr("Enter a token for this server."))
                token = connection_token(self.authcfg)
            client = GisDeployClient(url, token, timeout=30.0)
            self.context = client.context()
        except (AuthenticationError, ValueError) as error:
            self.status_label.setText(error_text(error))
            return
        except ApiError as error:
            self.status_label.setText(self.tr("Rejected: {0}").format(error_text(error.message)))
            return
        except TransportError as error:
            self.status_label.setText(self.tr("Network error: {0}").format(error_text(error)))
            return

        try:
            self.authcfg = save_connection(url, token)
        except AuthenticationError as error:
            self.status_label.setText(error_text(error))
            return
        self.token_edit.clear()
        self.accept()
