from __future__ import annotations

from pathlib import Path

import pytest

from remote_agent_lite.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "var"
    return Settings.from_env().with_overrides(
        data_dir=data,
        projects_dir=data / "projects",
        trash_dir=data / "trash",
        uploads_dir=data / "uploads",
        db_path=data / "remote-agent-lite.db",
        codex_home=data / "codex-home",
        frontend_dist=tmp_path / "frontend-dist",
        upload_chunk_size=5,
        max_file_size=1000,
        project_quota=10 * 1024 * 1024,
        snapshot_max_file_size=128,
        codex_ws_url="ws://127.0.0.1:1",
        codex_model="test-model",
        codex_token_file=None,
        testing=True,
        login_max_failures=3,
        login_window_seconds=900,
        login_lock_seconds=60,
        disk_low_watermark=1,
        disk_critical=1,
    )
