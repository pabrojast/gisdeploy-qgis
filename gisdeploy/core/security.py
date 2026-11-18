"""Credential-free persisted URLs and safe diagnostic strings."""
from __future__ import annotations

import base64
import re
from urllib.parse import quote, urlsplit, urlunsplit


def validate_token(token: str) -> str:
    if not isinstance(token, str) or not re.fullmatch(r"agt_[A-Za-z0-9_-]+", token):
        raise ValueError("A valid GISDeploy agent token is required.")
    return token


def safe_message(value, secrets=()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            for encoded in (secret, quote(secret, safe=""), base64.b64encode(f"token:{secret}".encode()).decode()):
                text = text.replace(encoded, "[redacted]")
    text = re.sub(r"agt_[A-Za-z0-9_%-]+", "[redacted]", text)
    return re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+", r"\1 [redacted]", text)


def clean_url(value: str, service=False) -> str:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("A valid HTTPS server URL is required.")
    try:
        url = urlsplit(value)
        _port = url.port
    except ValueError:
        raise ValueError("A valid HTTPS server URL is required.") from None
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))) or not url.hostname:
        raise ValueError("Use HTTPS (HTTP is allowed only on loopback for development).")
    if url.username or url.password or url.query or url.fragment or "agt_" in value.lower():
        raise ValueError("Server URLs must not contain credentials, query parameters or fragments.")
    path = url.path.rstrip("/")
    if service:
        if not re.fullmatch(r"/ogc/[A-Za-z0-9_-]+/(wms|wfs|wmts)", path):
            raise ValueError("An OGC proxy service URL is required.")
    elif path:
        raise ValueError("Use the application origin without a path.")
    return urlunsplit((url.scheme, url.netloc, path, "", ""))
