import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from companion.socket import ALLOWED_ORIGIN, serve_companion


def test_allowed_origin_matches_the_manifest_public_key():
    manifest = json.loads((Path(__file__).parents[1] / "extension" / "manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).digest()[:16]
    extension_id = "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))
    assert ALLOWED_ORIGIN == f"chrome-extension://{extension_id}"


def test_only_extension_origin_can_connect_and_server_uses_loopback():
    async def check():
        async with serve_companion(port=0) as server:
            address = server.sockets[0].getsockname()
            assert address[0] == "127.0.0.1"
            uri = f"ws://127.0.0.1:{address[1]}"
            with pytest.raises(InvalidStatus):
                async with connect(uri, origin="https://neetcode.io", proxy=None):
                    pass
            with pytest.raises(InvalidStatus):
                async with connect(uri, proxy=None):
                    pass
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"type": "hello", "ts": "2026-10-15T00:00:00Z"}))
                assert json.loads(await ws.recv())["type"] == "status"

    asyncio.run(check())


def test_unknown_type_gets_visible_protocol_error():
    async def check():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"type": "mystery", "ts": "2026-10-15T00:00:00Z"}))
                reply = json.loads(await ws.recv())
                assert reply["type"] == "error"
                assert "mystery" in reply["message"]
                assert "ts" in reply

    asyncio.run(check())


@pytest.mark.parametrize("message", [
    {"type": "problem_opened", "payload": {"site": "neetcode", "slug": "duplicate-integer", "title": "Duplicate Integer"}},
    {"type": "code_changed", "payload": {"diff": {"start_line": 2, "removed": ["a"], "added": ["b"]}}},
    {"type": "code_snapshot", "payload": {"code": "print(1)"}},
    {"type": "run_result", "payload": {"slug": "duplicate-integer", "cases": [{"passed": False, "error_type": "wrong_answer", "error_message": "", "runtime": "0.027", "failing_case": {"input": "x", "expected": "true", "actual": "false"}}]}},
    {"type": "submit_result", "payload": {"slug": "duplicate-integer", "accepted": True, "error_type": None, "error_message": "", "runtime": "0.032", "memory": 14184, "passed": 34, "total": 34}},
    {"type": "focus_changed", "payload": {"tab_active": True, "window_focused": False}},
])
def test_valid_reader_events_are_accepted_without_storage_or_reply(message):
    async def check():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"ts": "2026-10-15T00:00:00Z", **message}))
                await ws.send(json.dumps({"type": "ping", "ts": "2026-10-15T00:00:01Z"}))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.05)

    asyncio.run(check())


@pytest.mark.parametrize("message", [
    {"type": "problem_opened", "payload": {"site": "neetcode", "slug": "", "title": "Duplicate Integer"}},
    {"type": "code_changed", "payload": {"diff": {"start_line": 0, "removed": [], "added": []}}},
    {"type": "code_snapshot", "payload": {}},
    {"type": "run_result", "payload": {"slug": "duplicate-integer", "cases": []}},
    {"type": "submit_result", "payload": {"accepted": True}},
    {"type": "focus_changed", "payload": {"tab_active": "yes", "window_focused": True}},
])
def test_malformed_reader_events_get_an_error(message):
    async def check():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"ts": "2026-10-15T00:00:00Z", **message}))
                reply = json.loads(await ws.recv())
                assert reply["type"] == "error"
                assert message["type"] in reply["message"]

    asyncio.run(check())
