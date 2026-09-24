from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from remote_agent_lite.api import _read_body_limited
from remote_agent_lite.main import create_app


class _FakeRequest:
    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = chunks
        self.headers = headers or {}

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200


@pytest.mark.asyncio
async def test_read_body_limited_stops_oversized_stream() -> None:
    request = _FakeRequest([b"x" * 4, b"y" * 4])
    with pytest.raises(HTTPException) as excinfo:
        await _read_body_limited(request, 5)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 413


@pytest.mark.asyncio
async def test_read_body_limited_trusts_content_length_first() -> None:
    request = _FakeRequest([b"x"], {"content-length": "999"})
    with pytest.raises(HTTPException) as excinfo:
        await _read_body_limited(request, 5)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 413


def test_upload_part_rejects_oversized_chunk(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "Limits"}).json()["project"]
        init = client.post(
            f"/api/projects/{project['id']}/uploads/init",
            json={"filename": "big.txt", "size": 6},
        ).json()
        assert init["chunk_size"] == settings.upload_chunk_size

        oversized = client.put(
            f"/api/uploads/{init['upload_id']}/parts/0",
            content=b"x" * (settings.upload_chunk_size * 4),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert oversized.status_code == 413

        # 合规分片不受影响，上传仍然能继续
        ok = client.put(
            f"/api/uploads/{init['upload_id']}/parts/0",
            content=b"x" * settings.upload_chunk_size,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["received_parts"] == 1


def test_upload_part_rejects_unknown_upload(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        response = client.put("/api/uploads/no-such-upload/parts/0", content=b"x")
        assert response.status_code == 400
