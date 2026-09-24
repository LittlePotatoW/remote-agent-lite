from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    projects_dir: Path
    uploads_dir: Path
    db_path: Path
    codex_home: Path
    frontend_dist: Path

    host: str
    port: int
    cookie_name: str
    session_ttl_days: int
    login_window_seconds: int
    login_max_failures: int
    login_lock_seconds: int

    codex_ws_url: str
    codex_token_file: Path | None
    codex_model: str
    codex_model_provider: str
    codex_web_search: str
    codex_turn_timeout_seconds: int
    codex_developer_instructions: Path | None

    upload_chunk_size: int
    max_file_size: int
    project_quota: int
    upload_ttl_hours: int

    disk_low_watermark: int
    disk_critical: int
    sse_coalesce_ms: int
    testing: bool

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path.cwd()
        data_dir = _env_path("RAL_DATA_DIR", root / "var")
        return cls(
            data_dir=data_dir,
            projects_dir=_env_path("RAL_PROJECTS_DIR", data_dir / "projects"),
            uploads_dir=_env_path("RAL_UPLOADS_DIR", data_dir / "uploads"),
            db_path=_env_path("RAL_DB_PATH", data_dir / "remote-agent-lite.db"),
            codex_home=_env_path("RAL_CODEX_HOME", data_dir / "codex-home"),
            frontend_dist=_env_path("RAL_FRONTEND_DIST", root / "frontend" / "dist"),
            host=os.environ.get("RAL_HOST", "0.0.0.0"),
            port=_env_int("RAL_PORT", 8080),
            cookie_name=os.environ.get("RAL_COOKIE_NAME", "ral_session"),
            session_ttl_days=_env_int("RAL_SESSION_TTL_DAYS", 30),
            login_window_seconds=_env_int("RAL_LOGIN_WINDOW_SECONDS", 900),
            login_max_failures=_env_int("RAL_LOGIN_MAX_FAILURES", 5),
            login_lock_seconds=_env_int("RAL_LOGIN_LOCK_SECONDS", 900),
            codex_ws_url=os.environ.get("RAL_CODEX_WS_URL", "ws://127.0.0.1:4517"),
            codex_token_file=_optional_path("RAL_CODEX_TOKEN_FILE"),
            codex_model=os.environ.get("RAL_CODEX_MODEL", ""),
            codex_model_provider=os.environ.get("RAL_CODEX_MODEL_PROVIDER", "dsapi"),
            codex_web_search=os.environ.get("RAL_CODEX_WEB_SEARCH", "live"),
            codex_turn_timeout_seconds=_env_int("RAL_CODEX_TURN_TIMEOUT_SECONDS", 7200),
            codex_developer_instructions=_optional_path("RAL_CODEX_DEVELOPER_INSTRUCTIONS"),
            upload_chunk_size=_env_int("RAL_UPLOAD_CHUNK_SIZE", 5 * 1024 * 1024),
            max_file_size=_env_int("RAL_MAX_FILE_SIZE", 200 * 1024 * 1024),
            project_quota=_env_int("RAL_PROJECT_QUOTA", 10 * 1024 * 1024 * 1024),
            upload_ttl_hours=_env_int("RAL_UPLOAD_TTL_HOURS", 24),
            disk_low_watermark=_env_int("RAL_DISK_LOW_WATERMARK", 5 * 1024 * 1024 * 1024),
            disk_critical=_env_int("RAL_DISK_CRITICAL", 1 * 1024 * 1024 * 1024),
            sse_coalesce_ms=_env_int("RAL_SSE_COALESCE_MS", 100),
            testing=_env_bool("RAL_TESTING", False),
        )

    def with_overrides(self, **kwargs: object) -> "Settings":
        return replace(self, **kwargs)

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.projects_dir,
            self.uploads_dir,
            self.codex_home,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def _optional_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
