from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remote_agent_lite.db import Database
from remote_agent_lite.main import create_app
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.storage import StorageError, UploadService


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200


@pytest.mark.asyncio
async def test_chunked_upload_and_resume(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    project = await projects.create("Uploads")
    uploads = UploadService(db, projects, settings)
    payload = b"hello world!"
    upload = await uploads.init(project["id"], "hello.txt", len(payload))
    assert upload["total_parts"] == 3
    await uploads.put_part(upload["upload_id"], 0, payload[:5])
    await uploads.put_part(upload["upload_id"], 1, payload[5:10])
    status = await db.fetchone(
        "SELECT received_parts FROM upload_sessions WHERE id = ?", (upload["upload_id"],)
    )
    assert status["received_parts"] == 2
    await uploads.put_part(upload["upload_id"], 2, payload[10:])
    result = await uploads.complete(upload["upload_id"])
    assert result["path"] == "uploads/hello.txt"
    project_dir = await projects.project_dir(project["id"])
    assert (project_dir / "uploads" / "hello.txt").read_bytes() == payload


@pytest.mark.asyncio
async def test_folder_upload_keeps_the_relative_path(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    project = await projects.create("Folders")
    uploads = UploadService(db, projects, settings)

    payload = b"abc"  # 夹具里的分片上限是 5 字节
    upload = await uploads.init(
        project["id"], "shot.txt", len(payload), None, "my-dir/sub/shot.txt"
    )
    assert upload["relative_path"] == "uploads/my-dir/sub/shot.txt"
    await uploads.put_part(upload["upload_id"], 0, payload)
    result = await uploads.complete(upload["upload_id"])

    assert result["path"] == "uploads/my-dir/sub/shot.txt"
    project_dir = await projects.project_dir(project["id"])
    assert (project_dir / "uploads" / "my-dir" / "sub" / "shot.txt").read_bytes() == payload


@pytest.mark.asyncio
async def test_folder_upload_rejects_unsafe_paths(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    project = await projects.create("Unsafe")
    uploads = UploadService(db, projects, settings)

    for bad in ("../evil/x.txt", "/absolute/x.txt", ".git/x.txt", "node_modules/x.txt"):
        with pytest.raises(StorageError):
            await uploads.init(project["id"], "x.txt", 4, None, bad)

    # 不带 relative_path 的普通上传照旧落在 uploads/ 下
    plain = await uploads.init(project["id"], "plain.txt", 4)
    assert plain["relative_path"] == "uploads/plain.txt"


def test_folder_upload_through_the_api(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "Api"}).json()["project"]
        payload = b"abcd"
        init = client.post(
            f"/api/projects/{project['id']}/uploads/init",
            json={"filename": "note.txt", "size": len(payload), "relative_path": "docs/a/note.txt"},
        )
        assert init.status_code == 200, init.text
        handle = init.json()
        assert handle["relative_path"] == "uploads/docs/a/note.txt"
        part = client.put(
            f"/api/uploads/{handle['upload_id']}/parts/0",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert part.status_code == 200, part.text
        done = client.post(f"/api/uploads/{handle['upload_id']}/complete", json={})
        assert done.status_code == 200, done.text
        assert done.json()["path"] == "uploads/docs/a/note.txt"

        landing = client.get(
            f"/api/projects/{project['id']}/files?path=uploads/docs/a"
        ).json()["entries"]
        assert [entry["name"] for entry in landing] == ["note.txt"]

        # 目录穿越在接口层就被挡住
        bad = client.post(
            f"/api/projects/{project['id']}/uploads/init",
            json={"filename": "x.txt", "size": 1, "relative_path": "../x.txt"},
        )
        assert bad.status_code == 400
