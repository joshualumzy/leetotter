"""Startup recovery at the persisted session boundary."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from companion.session import SessionCoordinator
from companion.store import open_store


START = datetime(2026, 10, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("event_type,payload,event_time", [
    ("code_snapshot", "{broken json", START + timedelta(seconds=10)),
    ("problem_opened", json.dumps({"slug": "two-sum"}), START + timedelta(seconds=10)),
    ("session_started", json.dumps({"budget_minutes": True}), START),
    ("code_snapshot", json.dumps({"code": "x"}), "bad timestamp"),
    ("code_changed", json.dumps({"diff": {"start_line": "two", "removed": [], "added": []}}), START + timedelta(seconds=10)),
])
def test_corrupt_unfinished_session_is_logged_and_interrupted(tmp_path, caplog, event_type, payload, event_time):
    path = tmp_path / "store.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO sessions (started_at, budget_minutes) VALUES (?, 1)", (START.isoformat(),))
        if event_type != "session_started":
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (1, ?, 'session_started', ?)",
                       (START.isoformat(), json.dumps({"budget_minutes": 1})))
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (1, ?, ?, ?)",
                   (event_time.isoformat() if isinstance(event_time, datetime) else event_time, event_type, payload))

    coordinator = SessionCoordinator(path, lambda: START + timedelta(hours=1))

    assert coordinator.handle({"type": "session_get_state"})[0]["state"] == "idle"
    assert "Cannot restore session 1" in caplog.text
    with open_store(path) as db:
        ended_at, = db.execute("SELECT ended_at FROM sessions WHERE id = 1").fetchone()
        interrupted = db.execute("SELECT ts, payload FROM events WHERE type = 'session_interrupted'").fetchall()
        restored = db.execute("SELECT id FROM events WHERE type = 'session_restored'").fetchall()
    expected_end = event_time if isinstance(event_time, datetime) else START
    assert ended_at == expected_end.isoformat()
    assert interrupted == [(ended_at, "{}")]
    assert restored == []
