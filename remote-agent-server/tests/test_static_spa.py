from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remote_agent_lite.main import create_app, resolve_static_file


def _write_dist(dist: Path) -> Path:
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("INDEX", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("app", encoding="utf-8")
    return dist


def test_resolve_static_file_rejects_sibling_with_same_prefix(tmp_path: Path) -> None:
    """dist 与 dist-old 共享字符串前缀，越界请求必须被挡住（回归用例）。"""
    dist = _write_dist(tmp_path / "dist")
    sibling = tmp_path / "dist-old"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    root = dist.resolve()

    assert resolve_static_file(root, "assets/app.js") == root / "assets" / "app.js"
    assert resolve_static_file(root, "index.html") == root / "index.html"
    assert resolve_static_file(root, "../dist-old/secret.txt") is None
    assert resolve_static_file(root, "../dist-old") is None
    assert resolve_static_file(root, "missing.html") is None


def test_spa_serves_assets_and_falls_back_to_index(settings) -> None:
    settings.ensure_dirs()
    _write_dist(settings.frontend_dist)
    sibling = settings.frontend_dist.parent / f"{settings.frontend_dist.name}-old"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/assets/app.js").text == "app"
        assert client.get("/some/deep/route").text == "INDEX"
        assert client.get("/api/does-not-exist").status_code == 404
        # %2e%2e 会被解码成 ..；这里必须回退到 index.html，
        # 修复前同一个请求会返回兄弟目录里的 TOP SECRET（见报告 P1-1）
        leaked = client.get("/%2e%2e/frontend-dist-old/secret.txt")
        assert leaked.status_code == 200
        assert "TOP SECRET" not in leaked.text
