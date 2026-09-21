from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Cookie, HTTPException, Request, status

from .codex import CodexClient
from .config import Settings
from .db import Database
from .events import EventBus
from .projects import ProjectService
from .queueing import JobQueue
from .scheduled import ScheduledTaskService
from .security import AuthService
from .sessions import SessionService
from .storage import FileService, UploadService


@dataclass
class AppState:
    settings: Settings
    db: Database
    auth: AuthService
    projects: ProjectService
    sessions: SessionService
    files: FileService
    uploads: UploadService
    events: EventBus
    codex: CodexClient
    queue: JobQueue
    scheduled: ScheduledTaskService


def state(request: Request) -> AppState:
    return request.app.state.ral


async def current_auth(request: Request):
    app_state: AppState = state(request)
    token = request.cookies.get(app_state.settings.cookie_name)
    session = await app_state.auth.validate(token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return session


async def optional_auth(request: Request):
    app_state: AppState = state(request)
    token = request.cookies.get(app_state.settings.cookie_name)
    return await app_state.auth.validate(token)
