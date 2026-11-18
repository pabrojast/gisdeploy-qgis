"""Plugin entry: registers the dock and the toolbar action."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:  # Qt6 (QGIS 4) moved QAction to QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 (QGIS 3)
    from qgis.PyQt.QtWidgets import QAction

from .translation import install_translator, remove_translator
from .ui.dock_widget import GisDeployDock

ICON_PATH = os.path.join(os.path.dirname(__file__), "ui", "icons", "gisdeploy.svg")


class GisDeployPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None
        self.translator = install_translator()

    def initGui(self):  # noqa: N802 — QGIS API
        self.action = QAction(QIcon(ICON_PATH), "GISDeploy", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToWebMenu("GISDeploy", self.action)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginWebMenu("GISDeploy", self.action)
            self.action = None
        remove_translator(self.translator)
        self.translator = None

    def _toggle_dock(self, checked: bool):
        if self.dock is None:
            self.dock = GisDeployDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self.action.setChecked)
        self.dock.setVisible(checked)
