from __future__ import annotations

from fastapi.testclient import TestClient

from remote_agent_lite.main import create_app


def test_api_main_flow(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is True
        assert client.post("/api/auth/setup", json={"password": "password123"}).status_code == 200
        project_response = client.post("/api/projects", json={"name": "API Project"})
        assert project_response.status_code == 200, project_response.text
        project = project_response.json()["project"]
        session_response = client.post(f"/api/projects/{project['id']}/sessions", json={})
        assert session_response.status_code == 200
        session = session_response.json()["session"]
        messages = client.get(f"/api/sessions/{session['id']}/messages")
        assert messages.status_code == 200
        files = client.get(f"/api/projects/{project['id']}/files")
        assert files.status_code == 200
        assert any(entry["name"] == "uploads" for entry in files.json()["entries"])
        server_info = client.get("/api/server-info")
        assert server_info.status_code == 200


def test_api_upload_and_auth_guard(settings) -> None:
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 401
        client.post("/api/auth/setup", json={"password": "password123"})
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
        complete = client.post(
            f"/api/uploads/{init['upload_id']}/complete", json={}
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["path"] == "uploads/hello.txt"
        download = client.get(
            f"/api/projects/{project['id']}/files/download?path=uploads/hello.txt"
        )
        assert download.status_code == 200
        assert download.content == payload
