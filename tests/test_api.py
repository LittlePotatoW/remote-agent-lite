from __future__ import annotations

from fastapi.testclient import TestClient

from remote_agent_lite.main import create_app


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200


def test_overview_and_project_lifecycle(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is True
        _login(client)

        project = client.post("/api/projects", json={"name": "API Project"}).json()["project"]
        assert project["pinned"] is False

        session = client.post(
            f"/api/projects/{project['id']}/sessions", json={}
        ).json()["session"]

        overview = client.get("/api/overview").json()["projects"]
        assert len(overview) == 1
        assert overview[0]["session_count"] == 1
        assert overview[0]["sessions"][0]["id"] == session["id"]
        assert overview[0]["sessions"][0]["job_status"] is None

        pinned = client.patch(
            f"/api/sessions/{session['id']}", json={"pinned": True}
        ).json()["session"]
        assert pinned["pinned"] is True

        renamed = client.patch(
            f"/api/projects/{project['id']}", json={"name": "Renamed"}
        ).json()["project"]
        assert renamed["name"] == "Renamed"

        assert client.get("/api/server-info").status_code == 200

        assert client.delete(f"/api/projects/{project['id']}").status_code == 200
        assert client.get("/api/overview").json()["projects"] == []
        assert client.delete(f"/api/projects/{project['id']}").status_code == 404
        directory = settings.projects_dir / project["slug"]
        assert not directory.exists()


def test_auth_guard_and_folder_delete(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/overview").status_code == 401
        _login(client)

        project = client.post("/api/projects", json={"name": "Files"}).json()["project"]
        listing = client.get(f"/api/projects/{project['id']}/files").json()
        assert any(entry["name"] == "uploads" for entry in listing["entries"])

        response = client.delete(
            f"/api/projects/{project['id']}/files", params={"path": "uploads"}
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "directory"
        remaining = client.get(f"/api/projects/{project['id']}/files").json()["entries"]
        assert all(entry["name"] != "uploads" for entry in remaining)

        assert (
            client.delete(
                f"/api/projects/{project['id']}/files", params={"path": "../escape"}
            ).status_code
            == 400
        )


def test_upload_and_download(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "Upload API"}).json()["project"]
        payload = b"hello world!"
        init = client.post(
            f"/api/projects/{project['id']}/uploads/init",
            json={"filename": "hello.txt", "size": len(payload)},
        ).json()
        assert init["total_parts"] == 3
        for index, start in enumerate((0, 5, 10)):
            part = payload[start : start + 5]
            response = client.put(
                f"/api/uploads/{init['upload_id']}/parts/{index}",
                content=part,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert response.status_code == 200, response.text
        complete = client.post(f"/api/uploads/{init['upload_id']}/complete", json={})
        assert complete.status_code == 200, complete.text
        assert complete.json()["path"] == "uploads/hello.txt"
        download = client.get(
            f"/api/projects/{project['id']}/files/download?path=uploads/hello.txt"
        )
        assert download.status_code == 200
        assert download.content == payload


def test_image_raw_endpoint(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "Images"}).json()["project"]
        directory = settings.projects_dir / project["slug"]
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        (directory / "chart.png").write_bytes(png)
        (directory / "note.svg").write_text("<svg/>", encoding="utf-8")
        (directory / "readme.txt").write_text("hi", encoding="utf-8")

        url = f"/api/projects/{project['id']}/files/raw"
        response = client.get(url, params={"path": "chart.png"})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/png"
        assert response.headers["content-disposition"] == "inline"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == "default-src 'none'"
        assert response.content == png

        assert client.get(url, params={"path": "note.svg"}).status_code == 404
        assert client.get(url, params={"path": "readme.txt"}).status_code == 404
        assert client.get(url, params={"path": "missing.png"}).status_code == 404
        assert client.get(url, params={"path": "../escape.png"}).status_code == 404
        assert client.get(f"/api/projects/{project['id']}/files/raw").status_code == 422
