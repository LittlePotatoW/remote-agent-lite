from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence

import websockets

from .config import Settings
from .context import render_global_guidance
from .images import ChatImage
from .utils import new_id


logger = logging.getLogger(__name__)


class CodexError(RuntimeError):
    pass


class CodexUnavailable(CodexError):
    pass


@dataclass
class TurnResult:
    status: str
    text: str
    error: str | None = None
    turn_id: str | None = None
    completed_turn: dict[str, Any] | None = None


@dataclass
class TurnStream:
    thread_id: str
    turn_id: str | None = None
    final_text: str = ""
    error: str | None = None
    _deltas: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)
    done: asyncio.Future[TurnResult] = field(default_factory=asyncio.Future)
    _completed: bool = False

    async def push_delta(self, delta: str) -> None:
        if not delta:
            return
        await self._deltas.put(delta)

    async def set_final(self, text: str) -> None:
        if text:
            self.final_text = text

    async def complete(self, turn: dict[str, Any]) -> None:
        if self._completed:
            return
        self._completed = True
        status_raw = turn.get("status") or "completed"
        status = {
            "completed": "succeeded",
            "failed": "failed",
            "interrupted": "interrupted",
            "inProgress": "running",
        }.get(status_raw, status_raw)
        error = None
        if status == "failed":
            error_obj = turn.get("error") or {}
            error = error_obj.get("message") or str(error_obj)
        await self._deltas.put(None)
        if not self.done.done():
            self.done.set_result(
                TurnResult(
                    status=status,
                    text=self.final_text,
                    error=error,
                    turn_id=self.turn_id,
                    completed_turn=turn,
                )
            )

    async def fail(self, error: str) -> None:
        if self._completed:
            return
        self._completed = True
        self.error = error
        await self._deltas.put(None)
        if not self.done.done():
            self.done.set_result(
                TurnResult(
                    status="failed",
                    text=self.final_text,
                    error=error,
                    turn_id=self.turn_id,
                )
            )

    async def deltas(self) -> AsyncIterator[str]:
        while True:
            item = await self._deltas.get()
            if item is None:
                break
            yield item


class CodexClient:
    """JSON-RPC client for a local `codex app-server` WebSocket."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._ws: Any = None
        self._ws_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._streams: dict[str, TurnStream] = {}
        self._receiver_task: asyncio.Task[Any] | None = None
        self._connected = False
        self._stopping = False
        self._developer_instructions: Callable[[], str] | None = None

    def set_developer_instructions(self, provider: Callable[[], str]) -> None:
        self._developer_instructions = provider

    async def close(self) -> None:
        self._stopping = True
        if self._receiver_task:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._connected = False
        self._fail_all(CodexUnavailable("Codex client stopped"))

    async def ping(self) -> bool:
        try:
            await self._ensure_connected()
            return True
        except CodexError:
            return False

    async def ensure_thread(self, session: dict[str, Any], cwd: Path) -> tuple[str, bool]:
        thread_id = session.get("thread_id")
        if thread_id:
            try:
                await self._request(
                    "thread/resume",
                    {
                        "threadId": thread_id,
                        "cwd": str(cwd),
                        "model": self.settings.codex_model or None,
                        "modelProvider": self.settings.codex_model_provider,
                        "sandbox": "danger-full-access",
                        "approvalPolicy": "never",
                        "developerInstructions": self._instructions(),
                        "config": {"web_search": self.settings.codex_web_search},
                    },
                    timeout=45,
                )
                return thread_id, False
            except CodexError:
                logger.warning("thread %s could not be resumed; starting a new thread", thread_id)
        response = await self._request(
            "thread/start",
            {
                "cwd": str(cwd),
                "model": self.settings.codex_model or None,
                "modelProvider": self.settings.codex_model_provider,
                "sandbox": "danger-full-access",
                "approvalPolicy": "never",
                "developerInstructions": self._instructions(),
                "config": {"web_search": self.settings.codex_web_search},
            },
            timeout=60,
        )
        result = response or {}
        thread = result.get("thread") or {}
        new_thread_id = thread.get("id") or result.get("threadId")
        if not new_thread_id:
            raise CodexError("thread/start did not return a thread id")
        return str(new_thread_id), True

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        cwd: Path,
        images: Sequence[ChatImage] = (),
        client_user_message_id: str | None = None,
    ) -> TurnStream:
        stream = TurnStream(thread_id=thread_id)
        self._streams[thread_id] = stream
        try:
            response = await self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "cwd": str(cwd),
                    "model": self.settings.codex_model or None,
                    "sandboxPolicy": {"type": "dangerFullAccess"},
                    "approvalPolicy": "never",
                    "input": [
                        *(
                            {"type": "image", "url": image.data_url, "detail": "auto"}
                            for image in images
                        ),
                        {"type": "text", "text": prompt},
                    ],
                    "clientUserMessageId": client_user_message_id or new_id(),
                },
                timeout=60,
            )
            turn = (response or {}).get("turn") or {}
            stream.turn_id = turn.get("id")
            return stream
        except Exception:
            self._streams.pop(thread_id, None)
            raise

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        await self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=20)

    async def thread_read(self, thread_id: str, *, include_turns: bool = False) -> dict[str, Any]:
        response = await self._request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
            timeout=45,
        )
        return response or {}

    async def delete_thread(self, thread_id: str) -> None:
        await self._request("thread/delete", {"threadId": thread_id}, timeout=20)

    async def recover_turn(self, thread_id: str, cwd: Path) -> TurnStream | None:
        try:
            response = await self.thread_read(thread_id, include_turns=True)
        except CodexError:
            return None
        thread = response.get("thread") or {}
        turns = thread.get("turns") or []
        active = next(
            (turn for turn in reversed(turns) if turn.get("status") == "inProgress"),
            None,
        )
        if not active:
            return None
        stream = TurnStream(thread_id=thread_id, turn_id=active.get("id"))
        self._streams[thread_id] = stream
        try:
            await self._request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "cwd": str(cwd),
                    "model": self.settings.codex_model or None,
                    "modelProvider": self.settings.codex_model_provider,
                    "sandbox": "danger-full-access",
                    "approvalPolicy": "never",
                    "developerInstructions": self._instructions(),
                    "config": {"web_search": self.settings.codex_web_search},
                },
                timeout=45,
            )
        except CodexError:
            self._streams.pop(thread_id, None)
            return None
        return stream

    def _instructions(self) -> str:
        if self._developer_instructions:
            return self._developer_instructions()
        if self.settings.codex_developer_instructions:
            return self.settings.codex_developer_instructions.read_text(encoding="utf-8")
        return render_global_guidance(self.settings)

    async def _ensure_connected(self) -> None:
        if self._connected and self._ws is not None:
            return
        async with self._ws_lock:
            if self._connected and self._ws is not None:
                return
            await self._connect_once()

    async def _connect_once(self) -> None:
        token = ""
        if self.settings.codex_token_file:
            token = self.settings.codex_token_file.read_text(encoding="utf-8").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            self._ws = await self._open_ws(headers)
        except Exception as exc:
            raise CodexUnavailable(f"could not connect to Codex app-server: {exc}") from exc
        self._connected = True
        self._receiver_task = asyncio.create_task(self._receiver())
        try:
            await self._request(
                "initialize",
                {
                    "clientInfo": {"name": "remote-agent-lite", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
                timeout=30,
            )
            await self._send({"method": "initialized"})
        except Exception:
            await self._drop_connection()
            raise

    async def _open_ws(self, headers: dict[str, str]) -> Any:
        kwargs: dict[str, Any] = {
            "max_size": None,
            "ping_interval": 20,
            "ping_timeout": 20,
            "close_timeout": 5,
        }
        try:
            return await websockets.connect(
                self.settings.codex_ws_url,
                additional_headers=headers,
                **kwargs,
            )
        except TypeError:
            return await websockets.connect(
                self.settings.codex_ws_url,
                extra_headers=headers,
                **kwargs,
            )

    async def _request(self, method: str, params: dict[str, Any], *, timeout: float = 60) -> Any:
        await self._ensure_connected()
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CodexError(f"{method} timed out") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            if self._ws is None:
                raise CodexUnavailable("Codex websocket is not connected")
            await self._ws.send(json.dumps(message, ensure_ascii=False))

    async def _receiver(self) -> None:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if "id" in message and ("result" in message or "error" in message):
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(CodexError(str(message["error"])))
                        else:
                            future.set_result(message.get("result"))
                    continue
                if "id" in message and "method" in message:
                    await self._reply_unsupported(message["id"], message.get("method", ""))
                    continue
                if "method" in message:
                    await self._handle_notification(message["method"], message.get("params") or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Codex receiver stopped: %s", exc)
        finally:
            await self._drop_connection()

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        thread_id = params.get("threadId")
        if method == "item/agentMessage/delta":
            stream = self._streams.get(thread_id or "")
            if stream:
                await stream.push_delta(params.get("delta") or "")
            return
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                stream = self._streams.get(thread_id or "")
                if stream:
                    await stream.set_final(item.get("text") or "")
            return
        if method == "turn/completed":
            stream = self._streams.get(thread_id or "")
            if stream:
                turn = params.get("turn") or {}
                if turn.get("id") and stream.turn_id and turn.get("id") != stream.turn_id:
                    return
                if not stream.final_text:
                    for item in reversed(turn.get("items") or []):
                        if item.get("type") == "agentMessage" and item.get("text"):
                            stream.final_text = item["text"]
                            break
                self._streams.pop(thread_id or "", None)
                await stream.complete(turn)
            return
        if method == "error":
            stream = self._streams.get(thread_id or "")
            if stream:
                error = params.get("error") or params.get("message") or "Codex error"
                self._streams.pop(thread_id or "", None)
                await stream.fail(str(error))
            return
        if method in {"thread/statusChanged", "warning", "deprecationNotice"}:
            return

    async def _reply_unsupported(self, request_id: Any, method: str) -> None:
        with contextlib.suppress(Exception):
            await self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"remote-agent-lite does not handle interactive request: {method}",
                    },
                }
            )

    async def _drop_connection(self) -> None:
        self._connected = False
        self._ws = None
        self._fail_all(CodexUnavailable("Codex app-server connection closed"))

    def _fail_all(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        for stream in list(self._streams.values()):
            if not stream.done.done():
                asyncio.create_task(stream.fail(str(error)))
        self._streams.clear()
