import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

import companion.socket as socket
from companion.session import SessionCoordinator
from companion.socket import ALLOWED_ORIGIN, serve_companion
from companion.store import open_store
from tests.test_planner_socket import Connection
from tests.test_session import msg


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
def test_valid_reader_events_outside_a_session_return_idle_state(message, tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    async def check():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"ts": "2026-10-15T00:00:00Z", **message}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert reply["type"] == "session_state" and reply["state"] == "idle"

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


def recap_store(tmp_path, monkeypatch):
    path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    socket._clients.clear()
    socket._coordinator = SessionCoordinator(path, lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    socket._planner = None
    socket._grading_lock = asyncio.Lock()
    socket._reflection_lock = asyncio.Lock()
    return path


def add_session(path, *, ended=True, recap=None, seen=0):
    with open_store(path) as db:
        return db.execute(
            "INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) VALUES (?, ?, ?, ?, ?)",
            ("2026-10-17T10:00:00+00:00", "2026-10-17T10:30:00+00:00" if ended else None,
             "Done." if recap is not None else None, json.dumps(recap) if recap is not None else None, seen),
        ).lastrowid


def test_hello_sends_pending_recaps_and_only_unseen_ready_recaps(tmp_path, monkeypatch):
    async def scenario():
        path = recap_store(tmp_path, monkeypatch)
        unseen = add_session(path, recap=["Kept: two pointers."])
        add_session(path, recap=["Dropped: visited marking."], seen=1)
        pending = add_session(path)
        connection = Connection([msg("hello")])
        await socket._handle(connection)
        recaps = [state for state in connection.sent if state["type"] == "recap_state"]
        assert [(state["session_id"], state["status"]) for state in recaps] == [(pending, "preparing"), (unseen, "ready")]

    asyncio.run(scenario())


def test_recap_seen_marks_an_ended_session_without_a_reply(tmp_path, monkeypatch):
    async def scenario():
        path = recap_store(tmp_path, monkeypatch)
        session_id = add_session(path, recap=["Kept: two pointers."])
        connection = Connection([msg("recap_seen", {"session_id": session_id})])
        await socket._handle(connection)
        assert connection.sent == []
        with open_store(path) as db:
            assert db.execute("SELECT recap_seen FROM sessions WHERE id = ?", (session_id,)).fetchone() == (1,)

    asyncio.run(scenario())


def test_recap_seen_rejects_an_unknown_or_unended_session(tmp_path, monkeypatch):
    async def scenario():
        path = recap_store(tmp_path, monkeypatch)
        ended = add_session(path, recap=["Kept: two pointers."])
        unended = add_session(path, ended=False)
        connection = Connection([msg("recap_seen", {"session_id": 999}),
                                 msg("recap_seen", {"session_id": unended})])
        await socket._handle(connection)
        errors = [state for state in connection.sent if state["type"] == "error"]
        assert len(errors) == 2
        assert all("No ended session for recap" in error["message"] for error in errors)
        with open_store(path) as db:
            assert db.execute("SELECT recap_seen FROM sessions WHERE id = ?", (ended,)).fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [None, {}, {"session_id": "1"}, {"session_id": 0}, {"session_id": -1}, {"session_id": True}])
def test_malformed_recap_seen_gets_an_error(payload, tmp_path, monkeypatch):
    async def scenario():
        recap_store(tmp_path, monkeypatch)
        connection = Connection([msg("recap_seen", payload)])
        await socket._handle(connection)
        errors = [state for state in connection.sent if state["type"] == "error"]
        assert len(errors) == 1
        assert "recap_seen" in errors[0]["message"]

    asyncio.run(scenario())
