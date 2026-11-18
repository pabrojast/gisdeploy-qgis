"""QGIS SLD exports must pass GISDeploy's no-external-resources check."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gisdeploy.core.sld import strip_schema_locations  # noqa: E402

QGIS_EXPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" version="1.1.0" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.1.0/StyledLayerDescriptor.xsd" '
    'xmlns:se="http://www.opengis.net/se">\n'
    "  <NamedLayer><se:Name>puntos</se:Name></NamedLayer>\n"
    "</StyledLayerDescriptor>\n"
)


def test_schema_location_hint_is_removed_and_everything_else_kept():
    cleaned = strip_schema_locations(QGIS_EXPORT)
    assert "schemaLocation" not in cleaned and "schemas.opengis.net" not in cleaned
    assert cleaned == QGIS_EXPORT.replace(
        ' xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.1.0/StyledLayerDescriptor.xsd"', ""
    )
    assert 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' in cleaned  # namespace declarations stay


def test_other_schema_location_spellings_are_removed():
    for attribute in ("schemaLocation='a b'", 'xsi:noNamespaceSchemaLocation="x.xsd"', 'q:schemaLocation = "a b"'):
        assert strip_schema_locations(f"<StyledLayerDescriptor {attribute}/>") == "<StyledLayerDescriptor/>"


def test_sld_without_hints_is_unchanged():
    plain = '<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" version="1.0.0"/>'
    assert strip_schema_locations(plain) == plain
