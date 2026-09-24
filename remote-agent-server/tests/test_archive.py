from __future__ import annotations

import os
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remote_agent_lite.main import create_app
from remote_agent_lite.projects import ProjectService
from remote_agent_lite.db import Database
from remote_agent_lite.storage import FileService


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200


def _project_with_files(client: TestClient, settings) -> tuple[dict, Path]:
    project = client.post("/api/projects", json={"name": "Pack"}).json()["project"]
    root = settings.projects_dir / project["slug"]
    notes = root / "notes"
    (notes / "sub").mkdir(parents=True)
    (notes / "empty").mkdir()
    (notes / "a.txt").write_text("hello " * 200, encoding="utf-8")
    (notes / "shot.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 500)
    (notes / "sub" / "b.txt").write_text("world", encoding="utf-8")
    (notes / ".git").mkdir()
    (notes / ".git" / "config").write_text("secret", encoding="utf-8")
    (root / "outside.txt").write_text("outside", encoding="utf-8")
    os.symlink(root / "outside.txt", notes / "link.txt")
    return project, root


def test_archive_folder_streams_a_zip(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project, _ = _project_with_files(client, settings)

        response = client.get(f"/api/projects/{project['id']}/files/archive?path=notes")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert "notes.zip" in response.headers["content-disposition"]

        bundle = zipfile.ZipFile(BytesIO(response.content))
        # 顶层目录名保留；受管目录、符号链接都不进包；空目录也要在
        assert sorted(bundle.namelist()) == [
            "notes/",
            "notes/a.txt",
            "notes/empty/",
            "notes/shot.jpg",
            "notes/sub/",
            "notes/sub/b.txt",
        ]
        assert bundle.read("notes/sub/b.txt") == b"world"
        # 已经压过的格式直接 store，文本走 deflate
        assert bundle.getinfo("notes/shot.jpg").compress_type == zipfile.ZIP_STORED
        assert bundle.getinfo("notes/a.txt").compress_type == zipfile.ZIP_DEFLATED
        # 下载完服务器上不留临时 zip
        assert list(settings.uploads_dir.glob("archive-*.zip")) == []


def test_archive_rejects_unsafe_paths(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project, _ = _project_with_files(client, settings)
        base = f"/api/projects/{project['id']}/files/archive"

        assert client.get(f"{base}?path=../outside.txt").status_code == 400
        assert client.get(f"{base}?path=.git").status_code == 400
        assert client.get(f"{base}?path=missing").status_code == 404


@pytest.mark.asyncio
async def test_stale_archives_are_swept(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    projects = ProjectService(db, settings)
    files = FileService(projects, settings)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    stale = settings.uploads_dir / "archive-stale.zip"
    fresh = settings.uploads_dir / "archive-fresh.zip"
    stale.write_bytes(b"PK")
    fresh.write_bytes(b"PK")
    old = 1700000000
    os.utime(stale, (old, old))

    assert await files.cleanup_stale_archives() == 1
    assert not stale.exists()
    assert fresh.exists()
