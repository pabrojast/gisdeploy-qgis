"""Turn a QGIS layer into an artifact GISDeploy accepts.

The server takes .tif/.tiff for rasters and a .zip for vectors (GPKG inside a
zip is supported and preserves attribute types + CRS, so that's what we ship —
memory layers included). Everything is written into a caller-owned temp dir.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from typing import Optional, Tuple

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsProject,
    QgsRasterFileWriter,
    QgsRasterLayer,
    QgsRasterPipe,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from ..core.hashing import sha256_file


class ExportError(Exception):
    pass


def _safe_name(layer) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in layer.name().strip().lower()
    ).strip("-")
    return cleaned or "layer"


def export_vector(layer, workdir: str) -> Tuple[str, str]:
    """Vector layer → zipped single-layer GPKG. Returns (zip_path, layer_hash_source)."""
    base = _safe_name(layer)
    gpkg_path = os.path.join(workdir, f"{base}.gpkg")

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = base
    error = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, gpkg_path, QgsCoordinateTransformContext(), options
    )
    code = error[0] if isinstance(error, tuple) else error
    if code != QgsVectorFileWriter.WriterError.NoError:
        detail = error[1] if isinstance(error, tuple) and len(error) > 1 else code
        raise ExportError(f"GPKG export failed: {detail}")

    zip_path = os.path.join(workdir, f"{base}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(gpkg_path, arcname=f"{base}.gpkg")
    return zip_path, gpkg_path


def export_raster(layer, workdir: str) -> Tuple[str, str]:
    """Raster layer → GeoTIFF. Reuses the source file when it already is one."""
    source = layer.source().split("|")[0]
    base = _safe_name(layer)
    if os.path.isfile(source) and source.lower().endswith((".tif", ".tiff")):
        target = os.path.join(workdir, f"{base}{os.path.splitext(source)[1].lower()}")
        shutil.copyfile(source, target)
        return target, target

    target = os.path.join(workdir, f"{base}.tif")
    provider = layer.dataProvider()
    pipe = QgsRasterPipe()
    if not pipe.set(provider.clone()):
        raise ExportError("Could not prepare the raster pipe for export.")
    writer = QgsRasterFileWriter(target)
    result = writer.writeRaster(
        pipe, provider.xSize(), provider.ySize(), provider.extent(), provider.crs()
    )
    if result != QgsRasterFileWriter.WriterError.NoError:
        raise ExportError(f"GeoTIFF export failed (code {result}).")
    return target, target


def export_layer(layer, workdir: str) -> Tuple[str, Optional[str]]:
    """Export any supported layer. Returns (upload_path, content_hash).

    The hash is the plugin's dirty-tracking baseline: feature-level for vectors
    (byte-stable across exports), file bytes for rasters.
    """
    if isinstance(layer, QgsVectorLayer):
        from .layer_state import layer_content_hash

        zip_path, _ = export_vector(layer, workdir)
        return zip_path, layer_content_hash(layer)
    if isinstance(layer, QgsRasterLayer):
        tif_path, hash_source = export_raster(layer, workdir)
        try:
            return tif_path, sha256_file(hash_source)
        except OSError:
            return tif_path, None
    raise ExportError("Only vector and raster layers can be pushed.")


def project_layers():
    """The pushable layers of the current QGIS project, in layer-tree order."""
    layers = []
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, (QgsVectorLayer, QgsRasterLayer)):
            if (layer.dataProvider().name() or "") in ("wms", "wcs", "arcgisrest", "xyz"):
                continue
            layers.append(layer)
    return layers
