"""Client-visible grading failures and scheduling at the session boundary."""

import asyncio
from datetime import datetime, timedelta, timezone
import json

import companion.socket as socket
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_session import msg, opened, submit


class Connection:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    def __aiter__(self):
        self.remaining = iter(self.messages)
        return self

    async def __anext__(self):
        try:
            return json.dumps(next(self.remaining))
        except StopIteration:
            await asyncio.gather(*socket._grading_tasks)
            raise StopAsyncIteration

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def test_permanent_grading_failure_is_visible_with_specific_message(tmp_path, monkeypatch):
    path = tmp_path / "grading.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    async def scenario():
        socket._coordinator = SessionCoordinator(path, lambda: datetime.now(timezone.utc))
        socket._grading_lock = asyncio.Lock()
        connection = Connection([msg("session_start", {"budget_minutes": 10}),
                                 opened("unknown-for-grading", "Unknown"),
                                 submit("unknown-for-grading", True), msg("session_end")])
        await socket._handle(connection)
        await asyncio.gather(*socket._grading_tasks)
        errors = [item["message"] for item in connection.sent if item["type"] == "error"]
        assert len(errors) == 1
        assert "Unknown problem: unknown-for-grading" in errors[0]
        assert "delayed" not in errors[0]

    asyncio.run(scenario())
    with open_store(path) as db:
        assert db.execute("SELECT count(*) FROM grading_failures").fetchone()[0] == 1


def test_overrun_stop_schedules_grading(tmp_path, monkeypatch):
    path = tmp_path / "grading.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    triggers = []
    monkeypatch.setattr(socket, "_schedule_grading", lambda trigger="initial": triggers.append(trigger))

    async def scenario():
        now = datetime.now(timezone.utc)
        socket._coordinator = SessionCoordinator(path, lambda: now)
        socket._coordinator.handle(msg("session_start", {"budget_minutes": 1}))
        socket._coordinator.clock = lambda: now + timedelta(minutes=2)
        socket._coordinator.tick()
        connection = Connection([msg("session_overrun", {"choice": "stop"})])
        await socket._handle(connection)
        assert any(item["type"] == "session_state" and item["state"] == "ended" for item in connection.sent)

    asyncio.run(scenario())
    assert triggers == ["initial"]
