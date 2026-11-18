"""HTTP transport abstraction.

The API client speaks to a tiny ``Transport`` interface so the core stays pure
Python (stdlib only, no QGIS imports) and tests can drive it with a fake. The
default implementation uses ``urllib`` from the standard library — QGIS ships
it everywhere and no third-party dependency is needed.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class TransportResponse:
    status: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


class TransportError(Exception):
    """Network-level failure (DNS, refused, timeout) — no HTTP response."""


class Transport:
    """Interface: implementations return a TransportResponse for any status."""

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        timeout: float = 60.0,
    ) -> TransportResponse:
        raise NotImplementedError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization to a redirected origin (or downgrade).
        return None


class UrllibTransport(Transport):
    def __init__(self, verify_tls: bool = True):
        self._context = None if verify_tls else ssl._create_unverified_context()

    def request(self, method, url, headers=None, body=None, timeout=60.0):
        request = urllib.request.Request(url, data=body, method=method.upper())
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=self._context))
            with opener.open(request, timeout=timeout) as response:
                return TransportResponse(
                    status=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            # Non-2xx still carries the API's JSON envelope — surface it.
            return TransportResponse(
                status=error.code,
                body=error.read(),
                headers=dict(error.headers.items()) if error.headers else {},
            )
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TransportError("Network request failed. Check the server URL and connection.") from None
