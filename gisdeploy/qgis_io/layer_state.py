"""Persisted link between a QGIS layer and its GISDeploy dataset.

Stored in the layer's custom properties (saved inside the .qgz project), so a
reopened project remembers which layers are tracked and what content was last
pushed.
"""

from __future__ import annotations

from typing import Optional

from ..core.security import clean_url
from ..core.hashing import feature_hash, raster_hash
from ..core.sync_state import SyncState, compute_sync_state

PROP_DATASET_ID = "gisdeploy/datasetId"
PROP_PUSHED_HASH = "gisdeploy/pushedHash"
PROP_BASE_URL = "gisdeploy/baseUrl"


def linked_dataset_id(layer) -> Optional[str]:
    value = layer.customProperty(PROP_DATASET_ID)
    return value if isinstance(value, str) and value else None


def pushed_hash(layer) -> Optional[str]:
    value = layer.customProperty(PROP_PUSHED_HASH)
    return value if isinstance(value, str) and value else None


def link_layer(layer, dataset_id: str, content_hash: Optional[str], base_url: str) -> None:
    base_url = clean_url(base_url)
    layer.setCustomProperty(PROP_DATASET_ID, dataset_id)
    layer.setCustomProperty(PROP_PUSHED_HASH, content_hash or "")
    layer.setCustomProperty(PROP_BASE_URL, base_url)


def unlink_layer(layer) -> None:
    layer.removeCustomProperty(PROP_DATASET_ID)
    layer.removeCustomProperty(PROP_PUSHED_HASH)
    layer.removeCustomProperty(PROP_BASE_URL)


def layer_content_hash(layer) -> Optional[str]:
    """Feature-hash a vector layer, file-hash a raster layer."""
    from qgis.core import QgsRasterLayer, QgsVectorLayer

    try:
        if isinstance(layer, QgsVectorLayer):
            def pairs():
                for feature in layer.getFeatures():
                    geometry = feature.geometry()
                    wkb = bytes(geometry.asWkb()) if geometry and not geometry.isNull() else None
                    yield (wkb, list(feature.attributes()))

            return feature_hash(pairs())
        if isinstance(layer, QgsRasterLayer):
            source = layer.source().split("|")[0]
            return raster_hash(source)
    except Exception:  # noqa: BLE001 — a hash failure must never break the UI
        return None
    return None


def layer_sync_state(layer) -> SyncState:
    return compute_sync_state(
        linked_dataset_id(layer),
        pushed_hash(layer),
        layer_content_hash(layer),
    )
