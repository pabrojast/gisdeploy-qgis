import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gisdeploy.core.api_client import ApiError, GisDeployClient  # noqa: E402
from gisdeploy.core.transport import Transport, TransportResponse  # noqa: E402


class FakeTransport(Transport):
    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, status, payload):
        self.responses.append(TransportResponse(status=status, body=json.dumps(payload).encode()))

    def request(self, method, url, headers=None, body=None, timeout=60.0):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        return self.responses.pop(0)


@pytest.fixture()
def client_and_transport():
    transport = FakeTransport()
    client = GisDeployClient("https://gisdeploy.test", "agt_token", transport=transport)
    return client, transport


def test_ok_envelope_unwraps_data(client_and_transport):
    client, transport = client_and_transport
    transport.queue(200, {"ok": True, "data": {"organization": {"name": "Acme", "slug": "acme", "plan": "PRO"}}})
    context = client.context()
    assert context.organization_name == "Acme"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer agt_token"


def test_error_envelope_raises_api_error(client_and_transport):
    client, transport = client_and_transport
    transport.queue(
        403,
        {"ok": False, "error": {"code": "forbidden", "message": "Quota.", "suggestedActions": ["Upgrade"]}},
    )
    with pytest.raises(ApiError) as excinfo:
        client.create_project("X")
    assert excinfo.value.code == "forbidden"
    assert excinfo.value.suggested_actions == ["Upgrade"]


def test_writes_carry_idempotency_key(client_and_transport):
    client, transport = client_and_transport
    transport.queue(200, {"ok": True, "data": {"projectId": "p1", "name": "X", "slug": "x"}})
    client.create_project("X")
    key = transport.calls[0]["headers"].get("Idempotency-Key", "")
    assert key.startswith("qgis-")


def test_push_revision_small_file_uses_multipart_with_message(tmp_path, client_and_transport):
    client, transport = client_and_transport
    payload = tmp_path / "layer.zip"
    payload.write_bytes(b"zip-bytes")
    transport.queue(200, {"ok": True, "data": {"status": "queued", "datasetId": "ds1", "revision": {"number": 2}}})

    result = client.push_revision("ds1", str(payload), message="cambió el río Ñuble")

    call = transport.calls[0]
    assert call["url"].endswith("/api/agent/datasets/ds1/revisions")
    assert b"cambi\xc3\xb3" in call["body"]  # UTF-8 survives multipart
    assert b"zip-bytes" in call["body"]
    assert result["status"] == "queued"


def test_large_push_switches_to_chunked_flow(tmp_path, client_and_transport, monkeypatch):
    client, transport = client_and_transport
    monkeypatch.setattr("gisdeploy.core.api_client.CHUNK_THRESHOLD", 4)
    payload = tmp_path / "big.zip"
    payload.write_bytes(b"0123456789")

    transport.queue(200, {"ok": True, "data": {"uploadId": "u1", "chunkSize": 4, "totalChunks": 3}})
    for _ in range(3):
        transport.queue(200, {"ok": True, "data": {"index": 0, "received": 1, "totalChunks": 3}})
    transport.queue(200, {"ok": True, "data": {"status": "queued", "datasetId": "ds1", "revision": {"number": 2}}})

    progress = []
    client.push_revision("ds1", str(payload), message="big", progress=progress.append)

    urls = [call["url"] for call in transport.calls]
    assert urls[0].endswith("/revisions/init")
    assert all("/uploads/chunk?uploadId=u1&index=" in u for u in urls[1:4])
    assert urls[4].endswith("/revisions/complete")
    # init carries the message as JSON (UTF-8 safe), chunks carry raw bytes
    assert json.loads(transport.calls[0]["body"])["message"] == "big"
    assert transport.calls[1]["body"] == b"0123"
    assert progress  # progress callback was driven


def test_new_upload_carries_selected_project_in_direct_and_chunked_flows(
    tmp_path, client_and_transport, monkeypatch
):
    client, transport = client_and_transport
    payload = tmp_path / "layer.zip"
    payload.write_bytes(b"zip-bytes")
    transport.queue(200, {"ok": True, "data": {"datasetId": "ds1"}})

    client.upload_file(str(payload), project_id="proj_2")

    assert b'name="projectId"\r\n\r\nproj_2' in transport.calls[0]["body"]

    monkeypatch.setattr("gisdeploy.core.api_client.CHUNK_THRESHOLD", 4)
    transport.queue(200, {"ok": True, "data": {"uploadId": "u1", "chunkSize": 16, "totalChunks": 1}})
    transport.queue(200, {"ok": True, "data": {"index": 0, "received": 1, "totalChunks": 1}})
    transport.queue(200, {"ok": True, "data": {"datasetId": "ds2"}})

    client.upload_file(str(payload), project_id="proj_2")

    assert json.loads(transport.calls[1]["body"])["projectId"] == "proj_2"


def test_list_datasets_follows_cursor(client_and_transport):
    client, transport = client_and_transport
    transport.queue(200, {"ok": True, "data": {"datasets": [{"id": "a"}], "nextCursor": "a"}})
    transport.queue(200, {"ok": True, "data": {"datasets": [{"id": "b"}], "nextCursor": None}})
    datasets = client.list_datasets("proj1")
    assert [d.id for d in datasets] == ["a", "b"]
    assert "cursor=a" in transport.calls[1]["url"]


def test_non_json_response_raises_internal(client_and_transport):
    client, transport = client_and_transport
    transport.responses.append(TransportResponse(status=502, body=b"<html>bad gateway</html>"))
    with pytest.raises(ApiError) as excinfo:
        client.context()
    assert excinfo.value.code == "internal"
