"""GISDeploy agent-API client — pure Python, stdlib only.

Speaks the `/api/agent/*` surface: Bearer `agt_` token, the
``{ok, data, error, operation}`` envelope, an ``Idempotency-Key`` header on
every write, and the chunked upload/push flow for files above the chunk size.
File bytes travel as multipart/form-data (the server also accepts raw bodies,
but multipart carries UTF-8 commit messages safely — HTTP headers cannot).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Callable, List, Optional

from .security import clean_url, safe_message, validate_token
from .models import Context, Dataset, DatasetStatus, Revision
from .transport import Transport, TransportError, TransportResponse, UrllibTransport

CHUNK_THRESHOLD = 48 * 1024 * 1024  # mirror of the server's UPLOAD_CHUNK_SIZE

ProgressCallback = Callable[[float], None]


class ApiError(Exception):
    """An error envelope (or unexpected response) from the API."""

    def __init__(self, code: str, message: str, suggested_actions: Optional[List[str]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggested_actions = suggested_actions or []


def _new_idempotency_key() -> str:
    return f"qgis-{uuid.uuid4()}"


def _encode_multipart(fields: dict, file_field: Optional[tuple] = None) -> tuple:
    """Build a multipart/form-data body. file_field = (name, filename, bytes)."""
    boundary = f"----gisdeploy{uuid.uuid4().hex}"
    parts: List[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    if file_field is not None:
        name, filename, payload = file_field
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(payload)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class GisDeployClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        transport: Optional[Transport] = None,
        timeout: float = 120.0,
    ):
        self.base_url = clean_url(base_url)
        self._token = validate_token(token)
        self.transport = transport or UrllibTransport()
        self.timeout = timeout

    # ── plumbing ────────────────────────────────────────────────────────────

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"}
        headers.update(extra or {})
        return headers

    def _unwrap(self, response: TransportResponse):
        envelope = response.json()
        if not isinstance(envelope, dict):
            raise ApiError("internal", f"Unexpected response (HTTP {response.status}).")
        if envelope.get("ok") is True:
            return envelope.get("data")
        error = envelope.get("error") or {}
        raise ApiError(
            safe_message(error.get("code", "internal"), (self._token,)),
            safe_message(error.get("message", f"Request failed (HTTP {response.status})."), (self._token,)),
            [safe_message(action, (self._token,)) for action in error.get("suggestedActions") or []],
        )

    def _get(self, path: str):
        response = self.transport.request(
            "GET", f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout
        )
        return self._unwrap(response)

    def _send_json(self, method: str, path: str, payload: dict, idempotent: bool = True):
        headers = {"Content-Type": "application/json"}
        if idempotent:
            headers["Idempotency-Key"] = _new_idempotency_key()
        response = self.transport.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(headers),
            body=json.dumps(payload).encode("utf-8"),
            timeout=self.timeout,
        )
        return self._unwrap(response)

    def _post_multipart(self, path: str, fields: dict, file_field: tuple):
        body, content_type = _encode_multipart(fields, file_field)
        headers = {"Content-Type": content_type, "Idempotency-Key": _new_idempotency_key()}
        response = self.transport.request(
            "POST",
            f"{self.base_url}{path}",
            headers=self._headers(headers),
            body=body,
            timeout=max(self.timeout, 600.0),
        )
        return self._unwrap(response)

    # ── discovery ───────────────────────────────────────────────────────────

    def manifest(self) -> dict:
        return self._get("/api/agent/manifest") or {}

    def context(self) -> Context:
        return Context.from_dict(self._get("/api/agent/context") or {})

    # ── projects / datasets ─────────────────────────────────────────────────

    def create_project(self, name: str) -> dict:
        return self._send_json("POST", "/api/agent/projects", {"name": name})

    def list_datasets(self, project_id: Optional[str] = None) -> List[Dataset]:
        datasets: List[Dataset] = []
        cursor: Optional[str] = None
        while True:
            query = "?take=100"
            if project_id:
                query += f"&projectId={project_id}"
            if cursor:
                query += f"&cursor={cursor}"
            page = self._get(f"/api/agent/datasets{query}") or {}
            datasets.extend(Dataset.from_dict(d) for d in page.get("datasets") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                return datasets

    def dataset_status(self, dataset_id: str) -> DatasetStatus:
        return DatasetStatus.from_dict(self._get(f"/api/agent/datasets/{dataset_id}") or {})

    def update_dataset(self, dataset_id: str, **fields) -> dict:
        return self._send_json("PATCH", f"/api/agent/datasets/{dataset_id}", fields)

    def delete_dataset(self, dataset_id: str) -> dict:
        return self._send_json("DELETE", f"/api/agent/datasets/{dataset_id}", {})

    # ── uploads (new datasets) ──────────────────────────────────────────────

    def upload_file(
        self,
        filepath: str,
        name: Optional[str] = None,
        visibility: str = "PRIVATE",
        project_id: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> dict:
        """Upload a new dataset; switches to the chunked flow for large files."""
        size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        if size > CHUNK_THRESHOLD:
            return self._upload_chunked(
                filepath, filename, size, name, visibility, project_id, progress
            )
        with open(filepath, "rb") as handle:
            payload = handle.read()
        if progress:
            progress(0.5)
        result = self._post_multipart(
            "/api/agent/uploads",
            {
                "name": name,
                "visibility": visibility,
                **({"projectId": project_id} if project_id else {}),
            },
            ("file", filename, payload),
        )
        if progress:
            progress(1.0)
        return result

    def _upload_chunked(
        self, filepath, filename, size, name, visibility, project_id, progress
    ):
        init = self._send_json(
            "POST",
            "/api/agent/uploads/init",
            {
                "filename": filename,
                "size": size,
                **({"name": name} if name else {}),
                **({"projectId": project_id} if project_id else {}),
            },
            idempotent=False,
        )
        self._send_chunks(init, filepath, size, progress)
        return self._send_json(
            "POST",
            "/api/agent/uploads/complete",
            {"uploadId": init["uploadId"], "visibility": visibility},
        )

    def _send_chunks(self, init: dict, filepath: str, size: int, progress):
        upload_id = init["uploadId"]
        chunk_size = int(init["chunkSize"])
        total = int(init["totalChunks"])
        with open(filepath, "rb") as handle:
            for index in range(total):
                chunk = handle.read(chunk_size)
                response = self.transport.request(
                    "POST",
                    f"{self.base_url}/api/agent/uploads/chunk?uploadId={upload_id}&index={index}",
                    headers=self._headers({"Content-Type": "application/octet-stream"}),
                    body=chunk,
                    timeout=max(self.timeout, 600.0),
                )
                self._unwrap(response)
                if progress:
                    progress((index + 1) / (total + 1))

    # ── revisions (git for maps) ────────────────────────────────────────────

    def push_revision(
        self,
        dataset_id: str,
        filepath: str,
        message: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> dict:
        size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        if size > CHUNK_THRESHOLD:
            init = self._send_json(
                "POST",
                f"/api/agent/datasets/{dataset_id}/revisions/init",
                {"filename": filename, "size": size, **({"message": message} if message else {})},
                idempotent=False,
            )
            self._send_chunks(init, filepath, size, progress)
            return self._send_json(
                "POST",
                f"/api/agent/datasets/{dataset_id}/revisions/complete",
                {"uploadId": init["uploadId"]},
            )
        with open(filepath, "rb") as handle:
            payload = handle.read()
        if progress:
            progress(0.5)
        result = self._post_multipart(
            f"/api/agent/datasets/{dataset_id}/revisions",
            {"message": message},
            ("file", filename, payload),
        )
        if progress:
            progress(1.0)
        return result

    def list_revisions(self, dataset_id: str) -> dict:
        data = self._get(f"/api/agent/datasets/{dataset_id}/revisions") or {}
        return {
            "head": data.get("head"),
            "revisions": [Revision.from_dict(r) for r in data.get("revisions") or []],
        }

    def rollback(self, dataset_id: str, number: int, message: Optional[str] = None) -> dict:
        payload = {"message": message} if message else {}
        return self._send_json(
            "POST", f"/api/agent/datasets/{dataset_id}/revisions/{number}/rollback", payload
        )

    # ── styling / maps ──────────────────────────────────────────────────────

    def apply_sld(self, dataset_id: str, sld: str) -> dict:
        return self._send_json("POST", f"/api/agent/datasets/{dataset_id}/sld", {"sld": sld})

    def create_map(self, title: str, dataset_ids: List[str], visibility: str = "PRIVATE") -> dict:
        return self._send_json(
            "POST",
            "/api/agent/maps",
            {"title": title, "datasetIds": dataset_ids, "visibility": visibility},
        )

    def publish_map(self, map_id: str) -> dict:
        return self._send_json("POST", f"/api/agent/maps/{map_id}/publish", {})


__all__ = [
    "ApiError",
    "GisDeployClient",
    "TransportError",
    "CHUNK_THRESHOLD",
]
