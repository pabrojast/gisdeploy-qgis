"""Credential-free provider URIs: QGIS resolves authcfg at request time."""

from __future__ import annotations

from typing import Optional
import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlsplit, urlunsplit
from ..core.security import clean_url


def geoserver_layer_name(dataset_id: str) -> str:
    return "ds_" + "".join(ch if ch.isalnum() else "_" for ch in dataset_id)


def workspace_from_wms_url(wms_url: str) -> Optional[str]:
    # e.g. https://ogc.gisdeploy.com/ogc/org_acme/wms → org_acme
    parts = urlparse(wms_url).path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "ogc":
        return parts[1]
    return None


def wms_uri(wms_url: str, dataset_id: str, authcfg: Optional[str] = None) -> str:
    wms_url = clean_url(wms_url, service=True)
    if authcfg and not re.fullmatch(r"[A-Za-z0-9]{7}", authcfg):
        raise ValueError("A QGIS authentication configuration ID is required.")
    layer = geoserver_layer_name(dataset_id)
    # Anonymous discovery now requires a named public layer. With authcfg, the
    # proxy supplies project-filtered capabilities automatically.
    if not authcfg:
        wms_url += "?" + urlencode({"LAYERS": layer})
    parts = [
        "contextualWMSLegend=0",
        "crs=EPSG:3857",
        "dpiMode=7",
        "featureCount=10",
        "format=image/png",
        f"layers={quote(layer)}",
        "styles=",
        f"url={quote(wms_url, safe='')}",
    ]
    if authcfg:
        parts.append(f"authcfg={authcfg}")
    return "&".join(parts)


def scrub_legacy_uri(source: str) -> Optional[str]:
    """Remove legacy plaintext credentials from GISDeploy WMS/WMTS sources.

    Returns None for unrelated layers; credentials are never copied into a new
    auth config implicitly. Users re-add private layers using their saved authcfg.
    """
    pairs = dict(parse_qsl(source, keep_blank_values=True))
    url = urlsplit(pairs.get("url", ""))
    if not re.fullmatch(r"/ogc/[A-Za-z0-9_-]+/(wms|wfs|wmts)", url.path):
        return None
    hostname = url.hostname or ""
    authority = f"[{hostname}]" if ":" in hostname else hostname
    try:
        if url.port:
            authority += f":{url.port}"
        public_url = clean_url(urlunsplit((url.scheme, authority, url.path, "", "")), service=True)
    except ValueError:
        return ""  # unsafe HTTP/credential-bearing source must not remain saved
    permitted = {"contextualWMSLegend", "crs", "dpiMode", "featureCount", "format", "layers", "styles", "tileMatrixSet", "authcfg"}
    clean = {key: val for key, val in pairs.items() if key in permitted and "agt_" not in val.lower()}
    if "authcfg" in clean and not re.fullmatch(r"[A-Za-z0-9]{7}", clean["authcfg"]):
        clean.pop("authcfg")
    clean["url"] = public_url
    return urlencode(clean, quote_via=quote)
