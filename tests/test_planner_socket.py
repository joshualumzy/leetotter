"""The Planner's in-process socket seam; no loopback server or provider is used."""

import asyncio
import json
from datetime import datetime, timezone

import companion.socket as socket
from companion.catalog import load_catalog
from companion.planner import Pick, Planner
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_planner_flow import RecordedPlanner, reply
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


class RecordedModels(RecordedPlanner):
    """Scripted Planner decisions: a slug string is a plain pick, a dict a reply() description."""

    def __init__(self, replies):
        super().__init__(*[reply(r) if isinstance(r, str) else r for r in replies])

    def call_role(self, role, messages, schema, **kwargs):
        assert role == "planner"
        content = json.loads(messages[-1]["content"])
        offered = [c["slug"] for c in content.get("allowed", [])]
        if schema is Pick and offered and self.replies and isinstance(self.replies[0], dict):
            picks = self.replies[0].get("picks") or []
            if picks and picks[0] not in offered:  # a real Picker chooses within the category code offered
                self.replies[0] = {**self.replies[0], "picks": [offered[0]]}
        return super().call_role(role, messages, schema, **kwargs)


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
        shown = suggestions[0]
        assert shown["title"] == load_catalog().by_slug(shown["slug"])["title"]
        assert not {"reason", "category", "patterns"} & shown.keys()
        assert any(m["type"] == "planner_open" and m["slug"] == shown["slug"] for m in connection.sent)
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
        assert len(suggestions) == 2   # the Start's, then the one asked on leaving the problem
    asyncio.run(scenario())


def test_swap_and_clear_are_routed_to_planner(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["house-robber", "coin-change", "climbing-stairs"])
        connection = Connection([msg("hello"), msg("session_start"),
                                 msg("planner_swap"), msg("planner_clear")])
        await socket._handle(connection)
        suggestions = [m["slug"] for m in connection.sent if m["type"] == "suggestion"]
        assert len(suggestions) == 3
    asyncio.run(scenario())


def test_card_chat_stores_steer_and_clear_replaces_without_swap(tmp_path, monkeypatch):
    async def scenario():
        dp = {"categories": ["1-D Dynamic Programming"], "patterns": [], "difficulties": [], "direction": "more"}
        setup(tmp_path, monkeypatch, ["house-robber", reply(
            "coin-change", "steer", understood="More dynamic programming this session.",
            steer={"label": "more dynamic programming", "horizon": "session", "focus": [dp], "note": ""}), "climbing-stairs"])
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


def test_resuming_between_problems_asks_the_planner(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["house-robber"])
        socket._coordinator.handle(msg("session_start"))
        socket._coordinator.handle(msg("session_pause"))
        connection = Connection([msg("hello"), msg("session_resume")])
        await socket._handle(connection)
        assert any(m["type"] == "session_state" and m["state"] == "active" and m["problem"] is None
                   for m in connection.sent)
        suggestions = [m for m in connection.sent if m["type"] == "suggestion"]
        assert len(suggestions) == 1
    asyncio.run(scenario())
