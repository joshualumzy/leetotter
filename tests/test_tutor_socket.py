"""The Tutor over the wire: validation, streaming, refusals, one turn at a time, pauses, failures."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect

from companion.model import ModelClient
from companion.planner import SYSTEM_PROMPT as PLANNER_PROMPT
from companion.socket import ALLOWED_ORIGIN, serve_companion
from tests.test_session import of_type

REPLY = {"gap": "bugs_and_edge_cases", "gap_note": None, "help_kind": "question",
         "reply_text": "Try the smallest input, `[1]`. What does your loop return?", "follow_up_question": None}


def chunks(text, size=9):
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text[i:i + size]))], usage=None)
            for i in range(0, len(text), size)]


class Provider:
    """A fake provider: each reply is a list of chunks, an exception, or a threading.Event to wait on first."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def create(self, **kwargs):
        if kwargs["messages"][0]["content"] == PLANNER_PROMPT:   # starting a session asks the Planner; these tests script the Tutor only
            raise RuntimeError("Planner not scripted here")
        reply = self.replies.pop(0)
        if isinstance(reply, tuple):
            gate, reply = reply
            assert gate.wait(5)
        if isinstance(reply, Exception):
            raise reply
        return iter(reply)


def models(monkeypatch, path, *replies):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    fake = SimpleNamespace(chat=SimpleNamespace(completions=Provider(*replies)))
    return ModelClient(client=fake, db_path=path)


def wire(kind, payload=None):
    message = {"type": kind, "ts": "2026-10-15T00:00:00Z"}
    if payload is not None:
        message["payload"] = payload
    return json.dumps(message)


async def until(ws, kind, **fields):
    """Read messages until one of this type (and these fields) arrives; return it with everything before it."""
    seen = []
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        seen.append(message)
        if message["type"] == kind and all(message.get(k) == v for k, v in fields.items()):
            return message, seen


async def in_session(ws):
    await ws.send(wire("session_start"))
    await until(ws, "session_state", state="active")
    await ws.send(wire("problem_opened", {"site": "neetcode", "slug": "duplicate-integer", "title": "Duplicate Integer"}))
    await until(ws, "session_state")


def run(path, monkeypatch, client, body):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    async def check():
        async with serve_companion(port=0, models=client) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await body(ws, port)

    asyncio.run(check())


@pytest.mark.parametrize("kind, payload", [
    ("help_request", {"text": "x"}),
    ("chat_message", None),
    ("chat_message", {"text": ""}),
    ("chat_message", {"text": "   "}),
    ("chat_message", {"text": 5}),
    ("chat_message", {"text": "x" * 4001}),
    ("problem_statement", {"slug": "duplicate-integer", "statement": ""}),
    ("problem_statement", {"statement": "text"}),
])
def test_malformed_tutor_messages_get_an_error_naming_the_type(tmp_path, monkeypatch, kind, payload):
    async def body(ws, _):
        await ws.send(wire(kind, payload))
        reply, _ = await until(ws, "error")
        assert kind in reply["message"]

    run(tmp_path / "db.sqlite3", monkeypatch, None, body)


def test_a_press_streams_its_reply_then_sends_the_labels_and_logs_the_turn(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"

    async def body(ws, _):
        await in_session(ws)
        await ws.send(wire("problem_statement", {"slug": "duplicate-integer", "statement": "Return true if any value repeats."}))
        await until(ws, "session_state")
        await ws.send(wire("help_request"))
        start, _ = await until(ws, "tutor_start")
        assert (start["request"], start["press_index"]) == ("help", 1)
        done, seen = await until(ws, "tutor_done")
        pieces = [m for m in seen if m["type"] == "tutor_chunk"]
        assert len(pieces) > 1 and all(m["turn_id"] == start["turn_id"] and m["attempt"] == 0 for m in pieces)
        assert "".join(m["text"] for m in pieces) == REPLY["reply_text"]
        assert (done["turn_id"], done["reply_text"], done["gap"], done["help_kind"]) == (
            start["turn_id"], REPLY["reply_text"], "bugs_and_edge_cases", "question")

    run(path, monkeypatch, models(monkeypatch, path, chunks(json.dumps(REPLY))), body)
    turn, = of_type(path, "help_turn")
    assert turn[1]["gap"] == "bugs_and_edge_cases" and turn[1]["model"] == "x-test-2"


def test_a_chat_message_is_answered_and_logged(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"

    async def body(ws, _):
        await in_session(ws)
        await ws.send(wire("chat_message", {"text": "  why does [1] fail?  "}))
        start, _ = await until(ws, "tutor_start")
        assert start["request"] == "chat"
        await until(ws, "tutor_done")

    run(path, monkeypatch, models(monkeypatch, path, chunks(json.dumps(REPLY))), body)
    assert of_type(path, "chat_message")[0][1]["text"] == "why does [1] fail?"


def test_help_is_refused_while_paused_only_to_the_sender(tmp_path, monkeypatch):
    async def body(ws, port):
        await in_session(ws)
        await ws.send(wire("session_pause"))
        await until(ws, "session_state", state="paused")
        await ws.send(wire("help_request"))
        error, _ = await until(ws, "tutor_error")
        assert error["turn_id"] is None and "paused" in error["message"]

    run(tmp_path / "db.sqlite3", monkeypatch, None, body)


def test_while_a_reply_streams_the_session_keeps_answering_a_second_ask_is_refused_and_a_pause_lets_it_finish(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    gate = threading.Event()

    async def body(ws, port):
        await in_session(ws)
        async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as other:
            await ws.send(wire("help_request"))
            start, _ = await until(ws, "tutor_start")
            await until(other, "tutor_start", turn_id=start["turn_id"])
            await ws.send(wire("chat_message", {"text": "hello?"}))
            refused, _ = await until(ws, "tutor_error")
            assert refused["turn_id"] is None and "still answering" in refused["message"]
            await ws.send(wire("session_pause"))
            await until(ws, "session_state", state="paused")
            gate.set()
            done, _ = await until(ws, "tutor_done")
            assert done["turn_id"] == start["turn_id"]
            await until(other, "tutor_done", turn_id=start["turn_id"])

    run(path, monkeypatch, models(monkeypatch, path, (gate, chunks(json.dumps(REPLY)))), body)
    assert len(of_type(path, "help_turn")) == 1 and of_type(path, "chat_message") == []


def test_a_failed_call_tells_the_panel_and_logs_the_failure(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    bad = json.dumps({**REPLY, "gap": "not_a_gap"})

    async def body(ws, _):
        await in_session(ws)
        await ws.send(wire("help_request"))
        start, _ = await until(ws, "tutor_start")
        error, seen = await until(ws, "tutor_error")
        assert error["turn_id"] == start["turn_id"] and "isn't shown" in error["message"]
        assert {m["attempt"] for m in seen if m["type"] == "tutor_chunk"} == {0, 1}

    run(path, monkeypatch, models(monkeypatch, path, chunks(bad), chunks(bad)), body)
    failed, = of_type(path, "help_failed")
    assert failed[1]["reason"] == "invalid_output"
    assert of_type(path, "help_turn") == []


def test_stopping_the_companion_mid_reply_logs_the_turn_failed(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    gate = threading.Event()
    client = models(monkeypatch, path, (gate, chunks(json.dumps(REPLY))))

    async def check():
        async with serve_companion(port=0, models=client) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await in_session(ws)
                await ws.send(wire("help_request"))
                await until(ws, "tutor_start")
        gate.set()

    asyncio.run(check())
    failed, = of_type(path, "help_failed")
    assert failed[1]["reason"] == "shutdown"
    assert of_type(path, "help_turn") == []


def test_a_failure_before_the_call_starts_closes_the_turn_and_later_requests_work(tmp_path, monkeypatch):
    import companion.socket as socket_module
    path = tmp_path / "db.sqlite3"
    real = socket_module._broadcast
    failed_once = []

    async def flaky(outgoing, sender, broadcast=True):
        if any(m["type"] == "tutor_start" for m in outgoing) and not failed_once:
            failed_once.append(True)
            raise RuntimeError("send failed")
        await real(outgoing, sender, broadcast)

    monkeypatch.setattr(socket_module, "_broadcast", flaky)

    async def body(ws, _):
        await in_session(ws)
        await ws.send(wire("help_request"))
        error, _ = await until(ws, "tutor_error")
        assert error["turn_id"] is not None and "send failed" in error["message"]
        await ws.send(wire("help_request"))
        start, _ = await until(ws, "tutor_start")
        assert start["press_index"] == 1
        await until(ws, "tutor_done")

    run(path, monkeypatch, models(monkeypatch, path, chunks(json.dumps(REPLY))), body)
    failed, = of_type(path, "help_failed")
    assert failed[1]["reason"] == "error"


def test_stopping_the_companion_after_the_reply_was_stored_logs_no_failure(tmp_path, monkeypatch):
    import companion.socket as socket_module
    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    real = socket_module._broadcast

    async def stuck(outgoing, sender, broadcast=True):
        if any(m["type"] == "tutor_done" for m in outgoing):
            await asyncio.Event().wait()
        await real(outgoing, sender, broadcast)

    monkeypatch.setattr(socket_module, "_broadcast", stuck)
    client = models(monkeypatch, path, chunks(json.dumps(REPLY)))

    async def check():
        async with serve_companion(port=0, models=client) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await in_session(ws)
                await ws.send(wire("help_request"))
                await until(ws, "tutor_start")
                for _ in range(100):
                    if of_type(path, "help_turn"):
                        break
                    await asyncio.sleep(0.02)

    asyncio.run(check())
    assert len(of_type(path, "help_turn")) == 1
    assert of_type(path, "help_failed") == []
