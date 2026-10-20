import asyncio
import json

from websockets.asyncio.client import connect

from companion.socket import ALLOWED_ORIGIN, serve_companion


def test_settings_are_local_persistent_and_broadcast(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "settings.sqlite3"))

    async def check():
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as a, connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as b:
                async def send(ws, kind, payload=None):
                    await ws.send(json.dumps({"type": kind, "ts": "2026-10-15T00:00:00Z", **({"payload": payload} if payload is not None else {})}))
                    return json.loads(await asyncio.wait_for(ws.recv(), 2))

                first = await send(a, "settings_get")
                assert first == {"type": "settings_state", "ts": first["ts"], "name": None, "theme": "match", "bug_reports": 0}
                await b.send(json.dumps({"type": "settings_set", "ts": "now", "payload": {"key": "name", "value": "  Ada  "}}))
                assert json.loads(await asyncio.wait_for(a.recv(), 2))["name"] == "Ada"
                updated = json.loads(await asyncio.wait_for(b.recv(), 2))
                assert updated["name"] == "Ada" and updated["theme"] == "match"
                assert (await send(a, "settings_get"))["name"] == "Ada"
                for payload in ({"key": "unknown", "value": "x"}, {"key": "theme", "value": "blue"}, {"key": "name", "value": "x" * 41}, {"key": "name", "value": 4}):
                    assert (await send(a, "settings_set", payload))["type"] == "error"
                assert (await send(a, "settings_get"))["name"] == "Ada"
                await b.send(json.dumps({"type": "settings_set", "ts": "now", "payload": {"key": "theme", "value": "dark"}}))
                assert json.loads(await asyncio.wait_for(a.recv(), 2))["theme"] == "dark"
                assert json.loads(await asyncio.wait_for(b.recv(), 2))["theme"] == "dark"
                await b.send(json.dumps({"type": "settings_set", "ts": "now", "payload": {"key": "name", "value": "  "}}))
                assert json.loads(await asyncio.wait_for(a.recv(), 2))["name"] == ""
                assert json.loads(await asyncio.wait_for(b.recv(), 2))["name"] == ""
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as ws:
                state = await send(ws, "settings_get")
                assert state["name"] == "" and state["theme"] == "dark"

    asyncio.run(check())
