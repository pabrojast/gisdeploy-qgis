"""SLD clean-up before upload (stdlib only).

QGIS writes an ``xsi:schemaLocation`` hint pointing at schemas.opengis.net in
every SLD it exports. GISDeploy refuses any SLD attribute that references an
external resource, so the hint alone gets the whole style rejected. Nothing
reads it — GeoServer does not fetch schemas — so it is dropped.
"""

from __future__ import annotations

import re

_SCHEMA_LOCATION = re.compile(
    r"""\s(?:[A-Za-z_][\w.-]*:)?(?:schemaLocation|noNamespaceSchemaLocation)\s*=\s*(?:"[^"]*"|'[^']*')"""
)


def strip_schema_locations(sld: str) -> str:
    """Return `sld` without ``schemaLocation`` / ``noNamespaceSchemaLocation`` attributes."""
    return _SCHEMA_LOCATION.sub("", sld)
