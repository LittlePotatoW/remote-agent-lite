from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .deps import AppState, current_auth, optional_auth, state
from .server_info import collect_server_info
from .storage import StorageError


router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SessionBody(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class SessionRenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class TurnBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=200_000)


class UploadInitBody(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(ge=0)
    sha256: str | None = None


class UploadCompleteBody(BaseModel):
    sha256: str | None = None


class GitRestoreBody(BaseModel):
    commit: str = Field(min_length=4, max_length=64)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
    else:
        candidate = request.client.host if request.client else "unknown"
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return candidate


@router.get("/auth/status")
async def auth_status(request: Request):
    app_state: AppState = state(request)
    session = await optional_auth(request)
    return {
        "authenticated": bool(session),
        "setup_required": not await app_state.auth.has_password(),
    }


@router.post("/auth/setup")
async def auth_setup(body: LoginBody, request: Request, response: Response):
    app_state: AppState = state(request)
    if await app_state.auth.has_password():
        raise HTTPException(status_code=409, detail="password is already configured")
    try:
        await app_state.auth.set_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = await app_state.auth.login(body.password, _client_ip(request))
    if not token:
        raise HTTPException(status_code=500, detail="could not create login session")
    _set_cookie(response, app_state, token)
    return {"ok": True}


@router.post("/auth/login")
async def auth_login(body: LoginBody, request: Request, response: Response):
    app_state: AppState = state(request)
    try:
        token = await app_state.auth.login(body.password, _client_ip(request))
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if not token:
        raise HTTPException(status_code=401, detail="wrong password")
    _set_cookie(response, app_state, token)
    return {"ok": True}


@router.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    app_state: AppState = state(request)
    token = request.cookies.get(app_state.settings.cookie_name)
    await app_state.auth.logout(token)
    response.delete_cookie(app_state.settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def auth_me(_: Any = Depends(current_auth)):
    return {"ok": True}


@router.post("/auth/password")
async def auth_change_password(
    body: PasswordBody, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    if not await app_state.auth.verify_password(body.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    try:
        await app_state.auth.set_password(body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/projects")
async def list_projects(request: Request, _: Any = Depends(current_auth)):
    return {"projects": await state(request).projects.list_active()}


@router.post("/projects")
async def create_project(
    body: ProjectBody, request: Request, _: Any = Depends(current_auth)
):
    try:
        project = await state(request).projects.create(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project}


@router.patch("/projects/{project_id}")
async def rename_project(
    project_id: str, body: ProjectBody, request: Request, _: Any = Depends(current_auth)
):
    try:
        project = await state(request).projects.rename(project_id, body.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project}


@router.delete("/projects/{project_id}")
async def trash_project(
    project_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
        sessions = await app_state.sessions.list_for_project(project_id)
        for session in sessions:
            try:
                await app_state.queue.cancel(session["id"])
            except Exception:
                pass
        project = await app_state.projects.trash(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"project": project}


@router.post("/projects/{project_id}/restore")
async def restore_project(
    project_id: str, request: Request, _: Any = Depends(current_auth)
):
    try:
        project = await state(request).projects.restore(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project}


@router.get("/trash")
async def list_trash(request: Request, _: Any = Depends(current_auth)):
    return {"projects": await state(request).projects.list_trash()}


@router.delete("/trash")
async def empty_trash(request: Request, _: Any = Depends(current_auth)):
    count = await state(request).projects.empty_trash()
    return {"removed": count}


@router.delete("/trash/{project_id}")
async def purge_trash_project(
    project_id: str, request: Request, _: Any = Depends(current_auth)
):
    try:
        await state(request).projects.purge(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/projects/{project_id}/sessions")
async def list_sessions(
    project_id: str, request: Request, _: Any = Depends(current_auth)
):
    try:
        await state(request).projects.get(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"sessions": await state(request).sessions.list_for_project(project_id)}


@router.post("/projects/{project_id}/sessions")
async def create_session(
    project_id: str,
    body: SessionBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = await app_state.sessions.create(project_id, body.title)
    return {"session": session}


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str, body: SessionRenameBody, request: Request, _: Any = Depends(current_auth)
):
    try:
        session = await state(request).sessions.rename(session_id, body.title)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": session}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        session = await app_state.sessions.get(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        await app_state.queue.cancel(session_id)
    except Exception:
        pass
    for _ in range(50):
        running = await app_state.db.fetchone(
            """
            SELECT 1 FROM jobs WHERE session_id = ? AND status = 'running'
            LIMIT 1
            """,
            (session_id,),
        )
        if not running:
            break
        await asyncio.sleep(0.1)
    if session.get("thread_id"):
        try:
            await app_state.codex._request(
                "thread/delete", {"threadId": session["thread_id"]}, timeout=20
            )
        except Exception:
            pass
    await app_state.sessions.delete(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str,
    request: Request,
    after_seq: int | None = Query(default=None, ge=0),
    before_seq: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=300),
    mark_read: bool = Query(default=True),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        messages = await app_state.sessions.messages(
            session_id, after_seq=after_seq, before_seq=before_seq, limit=limit
        )
        session = await app_state.sessions.get(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if mark_read and messages:
        await app_state.sessions.mark_read(session_id, messages[-1]["seq"])
    return {
        "session": session,
        "messages": messages,
        "has_more": len(messages) == limit,
    }


@router.post("/sessions/{session_id}/turns")
async def create_turn(
    session_id: str, body: TurnBody, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        result = await app_state.queue.enqueue(session_id, body.prompt)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    return result


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        result = await app_state.queue.cancel(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.get("/sessions/{session_id}/status")
async def session_status(
    session_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.sessions.get(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await app_state.queue.status(session_id)


@router.get("/projects/{project_id}/files")
async def list_files(
    project_id: str,
    request: Request,
    path: str = Query(default=""),
    _: Any = Depends(current_auth),
):
    try:
        return await state(request).files.list_entries(project_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="path not found") from exc
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/files/download")
async def download_file(
    project_id: str,
    request: Request,
    path: str = Query(min_length=1),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        target = await app_state.files.resolve_download(project_id, path)
    except (FileNotFoundError, StorageError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    return FileResponse(target, filename=target.name)


@router.delete("/projects/{project_id}/files")
async def delete_file(
    project_id: str,
    request: Request,
    path: str = Query(min_length=1),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        await app_state.files.delete_file(project_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await app_state.events.publish(
        "files.changed", {"reason": "file_deleted"}, project_id=project_id
    )
    return {"ok": True}


@router.post("/projects/{project_id}/uploads/init")
async def upload_init(
    project_id: str,
    body: UploadInitBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
        result = await app_state.uploads.init(
            project_id, body.filename, body.size, body.sha256
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.put("/uploads/{upload_id}/parts/{part_index}")
async def upload_part(
    upload_id: str,
    part_index: int,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    body = await request.body()
    try:
        return await app_state.uploads.put_part(upload_id, part_index, body)
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/uploads/{upload_id}")
async def upload_status(
    upload_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    row = await app_state.db.fetchone(
        "SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="upload session not found")
    return dict(row)


@router.post("/uploads/{upload_id}/complete")
async def upload_complete(
    upload_id: str,
    body: UploadCompleteBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    upload = await app_state.db.fetchone(
        "SELECT project_id FROM upload_sessions WHERE id = ?", (upload_id,)
    )
    try:
        result = await app_state.uploads.complete(upload_id, body.sha256)
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project_id = upload["project_id"] if upload else None
    if project_id:
        await app_state.events.publish(
            "files.changed", {"reason": "upload_completed"}, project_id=project_id
        )
    return result


@router.get("/projects/{project_id}/git/log")
async def git_log(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
        project_dir = await app_state.projects.project_dir(project_id)
        return {"commits": await app_state.git.log(project_dir, limit)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/git/commit/{commit}")
async def git_commit(
    project_id: str, commit: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
        project_dir = await app_state.projects.project_dir(project_id)
        return await app_state.git.show(project_dir, commit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/git/restore")
async def git_restore(
    project_id: str,
    body: GitRestoreBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
        project_dir = await app_state.projects.project_dir(project_id)
        commit = await app_state.git.restore(project_dir, body.commit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await app_state.events.publish(
        "files.changed", {"reason": "git_restore"}, project_id=project_id
    )
    return {"commit": commit}


@router.get("/server-info")
async def server_info(request: Request, _: Any = Depends(current_auth)):
    return collect_server_info(state(request).settings)


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/events")
async def events(
    request: Request,
    session_id: str | None = Query(default=None),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    queue = await app_state.events.subscribe()

    async def generator():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event.session_id and session_id and event.session_id != session_id:
                    if event.event_type not in {"turn.status", "session.status"}:
                        continue
                yield event.to_sse()
        finally:
            await app_state.events.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _set_cookie(response: Response, app_state: AppState, token: str) -> None:
    response.set_cookie(
        app_state.settings.cookie_name,
        token,
        max_age=app_state.settings.session_ttl_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )
