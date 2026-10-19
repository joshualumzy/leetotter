"""Reset all my progress: one transaction, personal tables only (SPEC §2, §11)."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

import companion.socket as socket
from companion.history import import_history, import_status
from companion.planner import Planner
from companion.session import SessionCoordinator
from companion.store import open_store, reset_progress
from tests.test_planner_socket import Connection
from tests.test_session import msg, opened


PERSONAL = ("events", "sessions", "reviews", "hypotheses", "teaching", "notes", "model_calls",
            "settings", "handled_attempts", "grader_retries", "grading_failures", "reflector_failures",
            "recap_feedback_failures")
KEPT = ("references", "fsrs")


def seed(path):
    with open_store(path) as db:
        session_id = db.execute(
            "INSERT INTO sessions (started_at, ended_at, summary, recap_bullets) VALUES (?, ?, ?, ?)",
            ("2026-10-17T10:00:00+00:00", "2026-10-17T10:30:00+00:00", "Done.", '["A bullet"]'),
        ).lastrowid
        event_id = db.execute(
            "INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
            (session_id, "2026-10-17T10:20:00+00:00", json.dumps({"slug": "two-sum", "accepted": True})),
        ).lastrowid
        db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, reviewed_at) "
                   "VALUES (?, ?, 'problem', 'two-sum', 'good', 1, 1, '2026-10-17T10:20:00+00:00')",
                   (session_id, event_id))
        note_id = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, 'preference', 'quiet', ?)",
                             ("2026-10-17T10:00:00+00:00", session_id)).lastrowid
        db.execute("INSERT INTO hypotheses (text, category, skill, status, cited_event_ids, recap_session_id) "
                   "VALUES ('Visited marking', 'Graphs', 'solve', 'open', ?, ?)",
                   (json.dumps([event_id]), session_id))
        db.execute("INSERT INTO teaching (category, framing, help_kind) VALUES ('Graphs', 'walk through', 'question')")
        db.execute("INSERT INTO settings (key, value) VALUES ('name', 'Ada')")
        db.execute("INSERT INTO settings (key, value) VALUES ('neetcode_history_imported', '2026-10-17T10:00:00+00:00')")
        db.execute("INSERT INTO model_calls (role, model, latency_ms, validation_result, created_at) "
                   "VALUES ('reflector', 'test-model', 10, 'valid', '2026-10-17T10:30:00+00:00')")
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)',
                   ("two-sum", json.dumps({"patterns": ["hash-map"]}), "checked"))
        db.execute("INSERT INTO fsrs (library_version, parameters) VALUES ('5.0', '{}')")
        db.execute("INSERT INTO reflector_failures (session_id, message) VALUES (?, 'offline')", (session_id,))
        db.execute("INSERT INTO recap_feedback_failures (session_id, message) VALUES (?, 'offline')", (session_id,))
        db.execute("INSERT INTO handled_attempts (start_event_id) VALUES (?)", (event_id,))
        db.execute("INSERT INTO grader_retries (start_event_id, failed_tries) VALUES (?, 1)", (event_id,))
        db.execute("INSERT INTO grading_failures (start_event_id, message) VALUES (?, 'offline')", (event_id,))
        return session_id, note_id


def snapshot(path):
    with open_store(path) as db:
        return {table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                for table in PERSONAL + KEPT}


def test_reset_erases_every_personal_table_in_one_transaction_and_keeps_the_rest(tmp_path):
    path = tmp_path / "reset.sqlite3"
    seed(path)
    before = snapshot(path)

    reset_progress(path)

    after = snapshot(path)
    assert all(after[table] == [] for table in PERSONAL)
    assert after["references"] == before["references"]
    assert after["fsrs"] == before["fsrs"]
    assert import_status(path) is False
    result = import_history(["https://leetcode.com/problems/two-sum/"], path=path)
    assert result == {"already_imported": False, "matched": 1, "unmatched": []}
    assert import_status(path) is True
    with open_store(path) as db:
        assert db.execute("SELECT count(*) FROM events WHERE type = 'prior_solve'").fetchone()[0] == 1


def test_a_failure_partway_through_the_erase_leaves_every_table_intact(tmp_path):
    path = tmp_path / "reset.sqlite3"
    seed(path)
    before = snapshot(path)
    with open_store(path) as db:
        db.execute("CREATE TRIGGER stop_notes BEFORE DELETE ON notes BEGIN SELECT RAISE(ABORT, 'forced failure'); END")

    with pytest.raises(Exception, match="forced failure"):
        reset_progress(path)

    assert snapshot(path) == before


def test_socket_reset_ends_a_running_session_and_clears_cached_state(tmp_path, monkeypatch):
    path = tmp_path / "reset.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    socket._clients.clear()
    now = lambda: datetime(2026, 10, 18, tzinfo=timezone.utc)
    socket._coordinator = SessionCoordinator(path, now)
    socket._planner = Planner(path, now)
    socket._grading_lock = asyncio.Lock()
    socket._reflection_lock = asyncio.Lock()
    socket._coordinator.handle(msg("session_start"))
    socket._coordinator.handle(opened())
    socket._planner.card = {"type": "suggestion", "slug": "two-sum", "title": "Two Sum"}
    socket._planner._card_session_id = socket._coordinator.session_id
    seed(path)

    async def scenario():
        connection = Connection([msg("reset_progress", {"confirm": "RESET"})])
        await socket._handle(connection)
        assert [item["type"] for item in connection.sent] == ["reset_done", "settings_state"]
        assert connection.sent[1]["name"] is None and connection.sent[1]["theme"] == "match"

    asyncio.run(scenario())
    assert socket._coordinator.state == "idle"
    assert socket._coordinator.session_id is None
    assert socket._coordinator.problem is None
    assert socket._planner.card is None and socket._planner._waiting is None
    assert all(snapshot(path)[table] == [] for table in PERSONAL)
    assert len(snapshot(path)["references"]) == 1


@pytest.mark.parametrize("payload", [None, {}, {"confirm": "reset"}, {"confirm": "RESET "}, {"confirm": 1}])
def test_reset_needs_both_steps_and_changes_nothing_without_the_word(payload, tmp_path, monkeypatch):
    path = tmp_path / "reset.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    socket._clients.clear()
    now = lambda: datetime(2026, 10, 18, tzinfo=timezone.utc)
    socket._coordinator = SessionCoordinator(path, now)
    socket._planner = Planner(path, now)
    socket._coordinator.handle(msg("session_start"))
    seed(path)
    before = snapshot(path)
    socket._clients.add(object())  # keep _handle from pausing the running session on disconnect

    async def scenario():
        connection = Connection([msg("reset_progress", payload)])
        await socket._handle(connection)
        assert [item["type"] for item in connection.sent] == ["error"]
        assert "reset_progress" in connection.sent[0]["message"]

    asyncio.run(scenario())
    assert snapshot(path) == before
    assert socket._coordinator.state == "active"
    socket._clients.clear()
