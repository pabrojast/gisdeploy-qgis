# GISDeploy QGIS plugin — publish QGIS layers to gisdeploy.com.
# Copyright (C) 2026 Aquedra SpA
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version. See the LICENSE file in this directory.


def classFactory(iface):  # noqa: N802 (QGIS entry point name)
    from .plugin import GisDeployPlugin

    return GisDeployPlugin(iface)
