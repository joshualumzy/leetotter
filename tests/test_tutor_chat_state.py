"""chat_state: the conversation on the current attempt, rebuilt from stored events."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from companion.session import SessionCoordinator
from companion.socket import ALLOWED_ORIGIN, serve_companion
from tests.test_session import SLUG_B, TITLE_B, make, msg, opened, start_msg
from tests.test_tutor_session import active, begin, reply


def turns(coord):
    out = coord.chat_state()
    assert out["type"] == "chat_state" and out["ts"]
    return out["turns"], out["streaming"]


def test_no_session_or_no_problem_is_an_empty_conversation(tmp_path):
    coord, _, _ = make(tmp_path)
    assert turns(coord) == ([], None)
    coord.handle(start_msg())
    assert turns(coord) == ([], None)


def test_the_conversation_lists_presses_messages_replies_and_failures_in_order(tmp_path):
    coord, _, _ = active(tmp_path)
    _, press = begin(coord)
    coord.finish_turn(press, reply(), "x-test-1")
    _, chat = begin(coord, "chat", "why?")
    coord.fail_turn(chat, "timeout", "The Tutor didn't answer in time. Ask again.")
    _, streaming = begin(coord, "chat", "again?")
    assert turns(coord) == ([
        {"turn_id": press.turn_id, "kind": "press", "press_index": 1},
        {"turn_id": press.turn_id, "kind": "reply", "text": "What could you remember as you go?",
         "gap": "stuck_optimising", "gap_note": None, "help_kind": "question", "linked": []},
        {"turn_id": chat.turn_id, "kind": "chat", "text": "why?"},
        {"turn_id": chat.turn_id, "kind": "failed", "reason": "timeout", "message": "The Tutor didn't answer in time. Ask again."},
        {"turn_id": streaming.turn_id, "kind": "chat", "text": "again?"},
    ], streaming.turn_id)


def test_streaming_is_reported_only_on_the_problem_that_asked(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord, "chat", "on A")
    coord.handle(opened(SLUG_B, TITLE_B))
    assert turns(coord) == ([], None)
    assert turn.turn_id > 0


def test_streaming_from_a_previous_session_is_not_reported_on_the_same_problem(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord, "chat", "in the old session")
    coord.handle(msg("session_end"))
    coord.handle(start_msg())
    coord.handle(opened())
    assert turns(coord) == ([], None)
    assert turn.turn_id > 0


def test_only_the_current_attempt_is_shown_and_nothing_after_the_session_ends(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord, "chat", "on A")
    coord.finish_turn(turn, reply(), "x-test-1")
    coord.handle(opened(SLUG_B, TITLE_B))
    assert turns(coord) == ([], None)
    _, turn = begin(coord, "chat", "on B")
    coord.finish_turn(turn, reply(), "x-test-1")
    assert [t.get("text") for t in turns(coord)[0]][0] == "on B"
    coord.handle(msg("session_end"))
    assert turns(coord) == ([], None)


def test_the_conversation_survives_a_restart_with_the_open_turn_shown_failed(tmp_path):
    coord, clock, path = active(tmp_path)
    _, turn = begin(coord, "chat", "hello")
    del coord
    fresh = SessionCoordinator(path, clock)
    listed, streaming = turns(fresh)
    assert streaming is None
    assert [t["kind"] for t in listed] == ["chat", "failed"] and listed[1]["reason"] == "restart"


def test_chat_get_answers_only_the_sender_and_rejects_a_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "db.sqlite3"))

    async def check():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with connect(url, origin=ALLOWED_ORIGIN, proxy=None) as ws, connect(url, origin=ALLOWED_ORIGIN, proxy=None) as other:
                await ws.send(json.dumps({"type": "chat_get", "ts": "2026-10-15T00:00:00Z"}))
                answer = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert (answer["type"], answer["turns"], answer["streaming"]) == ("chat_state", [], None)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(other.recv(), 0.3)
                await ws.send(json.dumps({"type": "chat_get", "ts": "2026-10-15T00:00:00Z", "payload": {}}))
                error = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert error["type"] == "error" and "chat_get" in error["message"]

    asyncio.run(check())
