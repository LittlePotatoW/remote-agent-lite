from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .codex import CodexError
from .deps import AppState, current_auth, optional_auth, state
from .events import resync_event
from .images import ChatImage, ImageInputError, parse_images
from .server_info import collect_server_info
from .storage import IMAGE_MEDIA_TYPES, StorageError


router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


class ProjectCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ProjectPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None


class SessionCreateBody(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class SessionPatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None


class ImagePayload(BaseModel):
    """One inline image.

    一轮对话用的图片只活在内存里（不落盘、不入库）；定时任务要等到点才发，
    所以那些图片必须先存下来，见 `scheduled_task_images`。
    """

    name: str = Field(default="", max_length=240)
    data_url: str = Field(min_length=1)


class ScheduledTaskBody(BaseModel):
    """新建定时任务：time 按服务器本地时区解释，年月日时分拆开传。

    正文和图片至少要有一个，纯图片任务和输入框里只发图是一个意思。
    """

    prompt: str = Field(default="", max_length=200_000)
    kind: Literal["once", "daily", "weekly", "monthly"] = "once"
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    images: list[ImagePayload] = Field(default_factory=list)


class ScheduledTaskPatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None


class TurnBody(BaseModel):
    prompt: str = Field(default="", max_length=200_000)
    images: list[ImagePayload] = Field(default_factory=list)


class UploadInitBody(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(ge=0)
    sha256: str | None = None
    #: 选文件夹上传时浏览器给的相对路径（例如 `my-dir/sub/shot.png`），只取目录部分。
    relative_path: str | None = Field(default=None, max_length=400)


class UploadCompleteBody(BaseModel):
    sha256: str | None = None


def _image_payload(image: ChatImage) -> dict[str, Any]:
    """把库里存的定时任务图片还原成前端表单能直接用的形状。"""

    return {
        "name": image.name,
        "mime": image.mime,
        "data_url": image.data_url,
        "size": image.size,
    }


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


# 删除项目/会话前等待运行中任务结束的上限。超时不再「猜它结束了」，
# 而是拒绝删除，避免把还在写的目录删到一半。
DELETE_IDLE_TIMEOUT_SECONDS = 20.0


async def _wait_until_idle(app_state: AppState, where: str, value: str) -> bool:
    """等到没有运行中的任务；超时返回 False。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DELETE_IDLE_TIMEOUT_SECONDS
    while True:
        running = await app_state.db.fetchone(
            f"SELECT 1 FROM jobs WHERE {where} = ? AND status = 'running' LIMIT 1", (value,)
        )
        if not running:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.1)


async def _read_body_limited(request: Request, limit: int) -> bytes:
    """读取请求体，超过 limit 字节立刻中断，不把超大 body 读进内存。"""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="上传分片超过大小上限")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="上传分片超过大小上限")
        chunks.append(chunk)
    return b"".join(chunks)


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
        raise HTTPException(status_code=409, detail="管理员密码已经设置过了")
    try:
        await app_state.auth.set_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = await app_state.auth.login(body.password, _client_ip(request))
    if not token:
        raise HTTPException(status_code=500, detail="无法创建登录会话")
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
        raise HTTPException(status_code=401, detail="密码不正确")
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
        raise HTTPException(status_code=401, detail="当前密码不正确")
    try:
        await app_state.auth.set_password(body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/overview")
async def overview(request: Request, _: Any = Depends(current_auth)):
    app_state: AppState = state(request)
    projects = await app_state.projects.list_active()
    sessions = await app_state.sessions.list_all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        grouped.setdefault(session["project_id"], []).append(session)
    for project in projects:
        project["sessions"] = grouped.get(project["id"], [])
    return {"projects": projects}


@router.post("/projects")
async def create_project(
    body: ProjectCreateBody, request: Request, _: Any = Depends(current_auth)
):
    try:
        project = await state(request).projects.create(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project["sessions"] = []
    return {"project": project}


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectPatchBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        project = await app_state.projects.get(project_id)
        if body.name is not None:
            project = await app_state.projects.rename(project_id, body.name)
        if body.pinned is not None:
            project = await app_state.projects.set_pinned(project_id, body.pinned)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    sessions = await app_state.sessions.list_for_project(project_id)
    for session in sessions:
        try:
            await app_state.queue.cancel(session["id"])
        except Exception:
            pass
    if not await _wait_until_idle(app_state, "project_id", project_id):
        raise HTTPException(
            status_code=409,
            detail="这个项目里还有任务在运行，请先点停止，等它结束后再删除",
        )
    await app_state.projects.delete(project_id)
    return {"ok": True}


@router.get("/projects/{project_id}/sessions")
async def list_sessions(
    project_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.projects.get(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"sessions": await app_state.sessions.list_for_project(project_id)}


@router.post("/projects/{project_id}/sessions")
async def create_session(
    project_id: str,
    body: SessionCreateBody,
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
async def update_session(
    session_id: str,
    body: SessionPatchBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        session = await app_state.sessions.get(session_id)
        if body.title is not None:
            session = await app_state.sessions.rename(session_id, body.title)
        if body.pinned is not None:
            session = await app_state.sessions.set_pinned(session_id, body.pinned)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": session}


@router.post("/sessions/{session_id}/duplicate")
async def duplicate_session(
    session_id: str, request: Request, _: Any = Depends(current_auth)
):
    """复制一个对话：同一个项目里的新会话，上下文来自 codex thread 分叉。"""

    app_state: AppState = state(request)
    try:
        session = await app_state.sessions.get(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        project = await app_state.projects.get(session["project_id"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    forked_thread_id: str | None = None
    if session.get("thread_id"):
        project_dir = await app_state.projects.project_dir(project["id"])
        try:
            forked_thread_id = await app_state.codex.fork_thread(
                session["thread_id"], cwd=project_dir
            )
        except CodexError as exc:
            raise HTTPException(
                status_code=503, detail=f"复制上下文失败：{exc}"
            ) from exc

    copy = await app_state.sessions.create(project["id"], f"{session['title']} 副本")
    if forked_thread_id:
        await app_state.sessions.set_thread(copy["id"], forked_thread_id)
    await app_state.sessions.copy_messages(session_id, copy["id"])
    return {"session": await app_state.sessions.get(copy["id"])}


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
    if not await _wait_until_idle(app_state, "session_id", session_id):
        raise HTTPException(
            status_code=409,
            detail="这个对话里还有任务在运行，请先点停止，等它结束后再删除",
        )
    if session.get("thread_id"):
        try:
            await app_state.codex.delete_thread(session["thread_id"])
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
    if not body.prompt.strip() and not body.images:
        raise HTTPException(status_code=422, detail="消息内容和图片不能同时为空")
    try:
        images = parse_images([image.model_dump() for image in body.images])
    except ImageInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = await app_state.queue.enqueue(session_id, body.prompt, images)
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


@router.get("/sessions/{session_id}/scheduled-tasks")
async def list_scheduled_tasks(
    session_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.sessions.get(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"tasks": await app_state.scheduled.list_for_session(session_id)}


@router.post("/sessions/{session_id}/scheduled-tasks")
async def create_scheduled_task(
    session_id: str,
    body: ScheduledTaskBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        await app_state.sessions.get(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        images = parse_images([image.model_dump() for image in body.images])
    except ImageInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        task = await app_state.scheduled.create(
            session_id,
            body.prompt,
            kind=body.kind,
            month=body.month,
            day=body.day,
            weekday=body.weekday,
            hour=body.hour,
            minute=body.minute,
            images=images,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task": task}


@router.put("/scheduled-tasks/{task_id}")
async def replace_scheduled_task(
    task_id: str,
    body: ScheduledTaskBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    """编辑一条还没触发的定时任务：正文、图片、时间整体改掉，标题保持不变。"""

    app_state: AppState = state(request)
    try:
        images = parse_images([image.model_dump() for image in body.images])
    except ImageInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        task = await app_state.scheduled.update(
            task_id,
            body.prompt,
            kind=body.kind,
            month=body.month,
            day=body.day,
            weekday=body.weekday,
            hour=body.hour,
            minute=body.minute,
            images=images,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task": task}


@router.get("/scheduled-tasks/{task_id}/images")
async def scheduled_task_images(
    task_id: str, request: Request, _: Any = Depends(current_auth)
):
    """编辑表单要用：把这条任务已经存下来的图片连同正文一起回填。"""

    app_state: AppState = state(request)
    try:
        await app_state.scheduled.get(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    images = await app_state.scheduled.images_for(task_id)
    return {"images": [_image_payload(image) for image in images]}


@router.patch("/scheduled-tasks/{task_id}")
async def update_scheduled_task(
    task_id: str,
    body: ScheduledTaskPatchBody,
    request: Request,
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        task = await app_state.scheduled.get(task_id)
        if body.title is not None:
            task = await app_state.scheduled.rename(task_id, body.title)
        if body.pinned is not None:
            task = await app_state.scheduled.set_pinned(task_id, body.pinned)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task": task}


@router.delete("/scheduled-tasks/{task_id}")
async def delete_scheduled_task(
    task_id: str, request: Request, _: Any = Depends(current_auth)
):
    app_state: AppState = state(request)
    try:
        await app_state.scheduled.delete(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


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
        raise HTTPException(status_code=404, detail="路径不存在") from exc
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
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    return FileResponse(target, filename=target.name)


@router.get("/projects/{project_id}/files/raw")
async def raw_image(
    project_id: str,
    request: Request,
    path: str = Query(min_length=1),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        target = await app_state.files.resolve_image(project_id, path)
    except (FileNotFoundError, StorageError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="图片不存在") from exc
    return FileResponse(
        target,
        media_type=IMAGE_MEDIA_TYPES[target.suffix.lower()],
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.delete("/projects/{project_id}/files")
async def delete_file(
    project_id: str,
    request: Request,
    path: str = Query(min_length=1),
    _: Any = Depends(current_auth),
):
    app_state: AppState = state(request)
    try:
        kind = await app_state.files.delete_entry(project_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="条目不存在") from exc
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await app_state.events.publish(
        "files.changed", {"reason": "deleted", "kind": kind}, project_id=project_id
    )
    return {"ok": True, "kind": kind}


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
            project_id, body.filename, body.size, body.sha256, body.relative_path
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
    try:
        limit = await app_state.uploads.chunk_limit(upload_id)
    except (StorageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    body = await _read_body_limited(request, limit)
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
        raise HTTPException(status_code=404, detail="上传会话不存在")
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
    if upload:
        await app_state.events.publish(
            "files.changed",
            {"reason": "upload_completed"},
            project_id=upload["project_id"],
        )
    return result


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
                if await app_state.events.take_resync(queue):
                    yield resync_event(session_id=session_id).to_sse()
                if event.session_id and session_id and event.session_id != session_id:
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
