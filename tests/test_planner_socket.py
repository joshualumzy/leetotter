"""The Planner's in-process socket seam; no loopback server or provider is used."""

import asyncio
import json
from datetime import datetime, timezone

import companion.socket as socket
from companion.planner import Planner
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_session import msg, opened, submit


class Connection:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.gather(*tuple(socket._planner_tasks))
        try:
            return json.dumps(next(self.messages))
        except StopIteration:
            await asyncio.gather(*tuple(socket._planner_tasks))
            raise StopAsyncIteration

    async def send(self, raw):
        self.sent.append(json.loads(raw))


class RecordedModels:
    def __init__(self, replies):
        self.replies = iter(replies)

    def call_role(self, role, messages, schema, **kwargs):
        assert role == "planner"
        json.loads(messages[-1]["content"])
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            reply = {"slug": reply, "reading": "none", "ask": None, "steer": None,
                     "understood": None, "named_by_you": False}
        return schema.model_validate({"reason": "internal category reason", **reply})


def setup(tmp_path, monkeypatch, slugs):
    path = tmp_path / "planner_socket.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    now = lambda: datetime(2026, 10, 18, tzinfo=timezone.utc)
    socket._coordinator = SessionCoordinator(path, now)
    socket._planner = Planner(path, now)
    socket._models = RecordedModels(slugs)
    socket._grading_lock = asyncio.Lock()
    monkeypatch.setattr(socket, "_schedule_grading", lambda *args: None)
    monkeypatch.setattr(socket, "_schedule_reference", lambda *args: None)


def test_panel_open_sends_title_only_and_go_opens_it(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["house-robber"])
        connection = Connection([msg("hello"), msg("planner_go")])
        await socket._handle(connection)
        suggestions = [m for m in connection.sent if m["type"] == "suggestion"]
        assert len(suggestions) == 1
        assert suggestions[0]["title"] == "House Robber"
        assert not {"reason", "category", "patterns"} & suggestions[0].keys()
        assert any(m["type"] == "planner_open" and m["slug"] == "house-robber" for m in connection.sent)
    asyncio.run(scenario())


def test_leaving_an_accepted_problem_starts_planner_before_grading(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["coin-change", "house-robber"])
        connection = Connection([msg("session_start"),
                                 opened("climbing-stairs", "Climbing Stairs"),
                                 submit("climbing-stairs", True), msg("session_done")])
        await socket._handle(connection)
        assert socket._coordinator.problem is None
        suggestions = [m["slug"] for m in connection.sent if m["type"] == "suggestion"]
        assert suggestions == ["coin-change", "house-robber"]   # the Start's, then the one asked on leaving the problem
    asyncio.run(scenario())


def test_swap_and_clear_are_routed_to_planner(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["house-robber", "coin-change", "climbing-stairs"])
        connection = Connection([msg("hello"), msg("session_start"),
                                 msg("planner_swap"), msg("planner_clear")])
        await socket._handle(connection)
        suggestions = [m["slug"] for m in connection.sent if m["type"] == "suggestion"]
        assert suggestions == ["house-robber", "coin-change", "climbing-stairs"]
    asyncio.run(scenario())


def test_card_chat_stores_steer_and_clear_replaces_without_swap(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["house-robber", {
            "slug": "coin-change", "reading": "steer", "ask": None,
            "steer": {"label": "more dynamic programming", "horizon": "session", "focus": [{
                "categories": ["1-D Dynamic Programming"], "patterns": [], "direction": "more"}]},
            "understood": "More dynamic programming this session.", "named_by_you": False}, "climbing-stairs"])
        connection = Connection([msg("hello"), msg("session_start"),
                                 msg("planner_chat", {"text": "more DP"}), msg("planner_clear")])
        await socket._handle(connection)
        cards = [m for m in connection.sent if m["type"] == "suggestion"]
        assert [m["steer"] for m in cards] == [None, "more dynamic programming", None]
        assert cards[1]["message"] == "More dynamic programming this session."
        with open_store(socket._coordinator.db_path) as db:
            assert db.execute("SELECT count(*) FROM notes WHERE kind = 'steer'").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM notes WHERE kind = 'preference'").fetchone()[0] == 0
    asyncio.run(scenario())


def test_planner_failure_sends_fallback_card(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, [RuntimeError("offline")])
        connection = Connection([msg("hello")])
        await socket._handle(connection)
        cards = [m for m in connection.sent if m["type"] == "suggestion"]
        assert len(cards) == 1 and cards[0]["source"] == "fallback" and cards[0]["message"]
    asyncio.run(scenario())
