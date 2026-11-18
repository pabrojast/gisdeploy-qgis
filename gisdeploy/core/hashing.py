"""Deterministic content hashing.

Vector exports (GPKG inside a zip) are not byte-stable across exports, so the
"did the layer change since the last push" question is answered by a
feature-level hash: sha256 over every feature's geometry WKB and attribute
values in a stable order. Rasters hash their source file bytes.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Sequence


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _attr_bytes(value) -> bytes:
    if value is None:
        return b"\x00"
    if isinstance(value, bytes):
        return value
    if isinstance(value, float):
        # repr keeps full precision and is stable per Python version.
        return repr(value).encode("utf-8")
    return str(value).encode("utf-8")


def feature_hash(features: Iterable[Sequence]) -> str:
    """Hash an iterable of (wkb_bytes_or_None, attribute_sequence) pairs.

    The caller supplies features in a stable order (QGIS iterates by feature
    id). Field separators prevent ambiguity between adjacent values.
    """
    digest = hashlib.sha256()
    for wkb, attributes in features:
        digest.update(b"\x01F")
        digest.update(wkb or b"\x00")
        for value in attributes:
            digest.update(b"\x02A")
            digest.update(_attr_bytes(value))
    return digest.hexdigest()


def raster_hash(source_path: str) -> Optional[str]:
    try:
        return sha256_file(source_path)
    except OSError:
        return None
