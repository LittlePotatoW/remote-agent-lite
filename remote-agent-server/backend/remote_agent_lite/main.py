from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .codex import CodexClient
from .config import Settings, get_settings, set_settings
from .context import sync_codex_home
from .db import Database
from .deps import AppState, state
from .events import EventBus
from .projects import ProjectService
from .queueing import JobQueue
from .scheduled import ScheduledRunner, ScheduledTaskService
from .security import AuthService
from .sessions import SessionService
from .storage import FileService, UploadService


logger = logging.getLogger(__name__)


def resolve_static_file(dist_root: Path, full_path: str) -> Path | None:
    """把请求路径解析为 dist 内的文件；越出 dist（含同名前缀的兄弟目录）返回 None。"""
    try:
        # 必须按「是不是 dist 的子路径」判断：字符串前缀比较会把 dist-old、
        # dist.bak 这类兄弟目录也算成 dist 内部，造成未登录越权读文件
        relative = (dist_root / full_path).resolve().relative_to(dist_root)
    except (ValueError, OSError):
        return None
    candidate = dist_root / relative
    return candidate if candidate.is_file() else None


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    set_settings(app_settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.ensure_dirs()
        if app_settings.testing:
            sync_codex_home(app_settings)
        db = Database(app_settings.db_path)
        await db.init()
        auth = AuthService(
            db,
            ttl_days=app_settings.session_ttl_days,
            login_window_seconds=app_settings.login_window_seconds,
            login_max_failures=app_settings.login_max_failures,
            login_lock_seconds=app_settings.login_lock_seconds,
        )
        await auth.cleanup_expired()
        projects = ProjectService(db, app_settings)
        sessions = SessionService(db)
        files = FileService(projects, app_settings)
        uploads = UploadService(db, projects, app_settings)
        events = EventBus()
        codex = CodexClient(app_settings)
        queue = JobQueue(db, app_settings, projects, sessions, codex, events)
        scheduled = ScheduledTaskService(db)
        runner = ScheduledRunner(scheduled, queue)
        app.state.ral = AppState(
            settings=app_settings,
            db=db,
            auth=auth,
            projects=projects,
            sessions=sessions,
            files=files,
            uploads=uploads,
            events=events,
            codex=codex,
            queue=queue,
            scheduled=scheduled,
        )
        await queue.start()
        await runner.start()
        maintenance = asyncio.create_task(_maintenance_loop(app.state.ral))
        try:
            yield
        finally:
            maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintenance
            await runner.stop()
            await queue.stop()
            await codex.close()

    app = FastAPI(
        title="remote-agent-lite",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host", "")
                allowed = {
                    f"http://{host}",
                    f"https://{host}",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                }
                if origin not in allowed:
                    return JSONResponse(status_code=403, content={"detail": "origin rejected"})
        return await call_next(request)

    app.include_router(router)

    dist = app_settings.frontend_dist
    if dist.exists():
        dist_root = dist.resolve()
        index_file = dist_root / "index.html"
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "not found"})
            candidate = resolve_static_file(dist_root, full_path)
            if candidate is not None:
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app


async def _maintenance_loop(app_state: AppState) -> None:
    while True:
        try:
            await app_state.uploads.cleanup_expired()
            await app_state.files.cleanup_stale_archives()
            await app_state.auth.cleanup_expired()
        except Exception:
            logger.exception("maintenance task failed")
        await asyncio.sleep(300)


app = create_app()
