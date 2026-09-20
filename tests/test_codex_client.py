from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from remote_agent_lite.codex import CodexClient


@pytest.mark.asyncio
async def test_codex_client_jsonrpc_flow(settings, tmp_path) -> None:
    seen: list[str] = []

    async def handler(connection) -> None:
        first = json.loads(await connection.recv())
        seen.append(first["method"])
        await connection.send(
            json.dumps(
                {
                    "id": first["id"],
                    "result": {
                        "codexHome": str(tmp_path),
                        "platformFamily": "unix",
                        "platformOs": "linux",
                        "userAgent": "fake",
                    },
                }
            )
        )
        initialized = json.loads(await connection.recv())
        seen.append(initialized["method"])
        start = json.loads(await connection.recv())
        seen.append(start["method"])
        assert start["params"]["sandbox"] == "danger-full-access"
        assert start["params"]["approvalPolicy"] == "never"
        await connection.send(
            json.dumps({"id": start["id"], "result": {"thread": {"id": "thread-1"}}})
        )
        turn = json.loads(await connection.recv())
        seen.append(turn["method"])
        await connection.send(
            json.dumps({"id": turn["id"], "result": {"turn": {"id": "turn-1"}}})
        )
        await connection.send(
            json.dumps(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        "delta": "hello",
                    },
                }
            )
        )
        await connection.send(
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"id": "item-1", "type": "agentMessage", "text": "hello"},
                    },
                }
            )
        )
        await connection.send(
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                }
            )
        )
        await asyncio.sleep(0.2)

    server = await websockets.serve(handler, "127.0.0.1", 0, max_size=None)
    port = server.sockets[0].getsockname()[1]
    local_settings = settings.with_overrides(codex_ws_url=f"ws://127.0.0.1:{port}")
    client = CodexClient(local_settings)
    try:
        thread_id, recreated = await client.ensure_thread({"thread_id": None}, tmp_path)
        assert thread_id == "thread-1"
        assert recreated is True
        stream = await client.start_turn("thread-1", "hi", cwd=tmp_path)
        deltas = [delta async for delta in stream.deltas()]
        result = await asyncio.wait_for(stream.done, timeout=2)
        assert deltas == ["hello"]
        assert result.status == "succeeded"
        assert result.text == "hello"
        assert seen == ["initialize", "initialized", "thread/start", "turn/start"]
    finally:
        await client.close()
        server.close()
        await server.wait_closed()



@pytest.mark.asyncio
async def test_fork_thread_reuses_connection_and_returns_new_thread(settings, tmp_path) -> None:
    forked: list[dict] = []

    async def handler(connection) -> None:
        await connection.send(json.dumps({"id": json.loads(await connection.recv())["id"], "result": {}}))
        await connection.recv()  # initialized
        request = json.loads(await connection.recv())
        forked.append(request)
        await connection.send(
            json.dumps({"id": request["id"], "result": {"thread": {"id": "thread-forked"}}})
        )
        await asyncio.sleep(0.2)

    server = await websockets.serve(handler, "127.0.0.1", 0, max_size=None)
    port = server.sockets[0].getsockname()[1]
    client = CodexClient(settings.with_overrides(codex_ws_url=f"ws://127.0.0.1:{port}"))
    try:
        new_thread_id = await client.fork_thread("thread-1", cwd=tmp_path)
        assert new_thread_id == "thread-forked"
        assert forked[0]["method"] == "thread/fork"
        assert forked[0]["params"]["threadId"] == "thread-1"
        assert forked[0]["params"]["cwd"] == str(tmp_path)
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
