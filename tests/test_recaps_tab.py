"""The Recaps tab's read state and the Planner's recap gate (SPEC §5, §8)."""

import asyncio
import json
from datetime import datetime, timezone

import companion.socket as socket
from companion.planner import Planner
from companion.reflector import recaps_state
from companion.store import open_store
from tests.test_planner_socket import Connection
from tests.test_socket import add_session, recap_store
from tests.test_session import msg


def test_recaps_state_lists_ended_recaps_newest_first_with_status_and_unread(tmp_path):
    path = tmp_path / "recaps.sqlite3"
    with open_store(path) as db:
        seen = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                          "VALUES ('2026-10-15T09:00:00+00:00', '2026-10-15T09:30:00+00:00', 'Done.', ?, 1)",
                          (json.dumps(["Kept: two pointers."]),)).lastrowid
        unseen = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                            "VALUES ('2026-10-16T09:00:00+00:00', '2026-10-16T09:30:00+00:00', 'Smooth.', ?, 0)",
                            (json.dumps([]),)).lastrowid
        failed = db.execute("INSERT INTO sessions (started_at, ended_at) "
                            "VALUES ('2026-10-17T09:00:00+00:00', '2026-10-17T09:30:00+00:00')").lastrowid
        db.execute("INSERT INTO reflector_failures (session_id, message) VALUES (?, 'Session review is missing: offline')", (failed,))
        feedback = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                              "VALUES ('2026-10-18T09:00:00+00:00', '2026-10-18T09:30:00+00:00', 'Done.', ?, 1)",
                              (json.dumps(["Dropped: visited marking."]),)).lastrowid
        db.execute("INSERT INTO recap_feedback_failures (session_id, message) VALUES (?, 'Feedback offline')", (feedback,))
        preparing = db.execute("INSERT INTO sessions (started_at, ended_at) "
                               "VALUES ('2026-10-19T09:00:00+00:00', '2026-10-19T09:30:00+00:00')").lastrowid
        db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-19T10:00:00+00:00')")

    state = recaps_state(path)

    assert state["type"] == "recaps_state"
    assert [item["session_id"] for item in state["recaps"]] == [preparing, feedback, failed, unseen, seen]
    by_id = {item["session_id"]: item for item in state["recaps"]}
    assert [by_id[item]["status"] for item in (preparing, feedback, failed, unseen, seen)] == [
        "preparing", "feedback_failed", "failed", "ready", "ready"]
    assert by_id[unseen]["seen"] is False and by_id[unseen]["has_bullets"] is False
    assert by_id[seen]["seen"] is True and by_id[seen]["bullets"] == ["Kept: two pointers."] and by_id[seen]["has_bullets"] is True
    assert by_id[failed]["message"] == "Session review is missing: offline"
    assert by_id[feedback]["message"] == "Feedback offline"
    assert by_id[seen]["date"] == "2026-10-15T09:00:00+00:00"
    assert state["unread"] is False  # an unseen smooth recap has nothing to read
    with open_store(path) as db:
        db.execute("UPDATE sessions SET recap_bullets = ? WHERE id = ?", (json.dumps(["New: edge cases."]), unseen))
    assert recaps_state(path)["unread"] is True
    with open_store(path) as db:
        db.execute("UPDATE sessions SET recap_seen = 1 WHERE id = ?", (unseen,))
    assert recaps_state(path)["unread"] is False


def test_recaps_get_returns_the_list_and_recap_seen_broadcasts_the_new_state(tmp_path, monkeypatch):
    async def scenario():
        path = recap_store(tmp_path, monkeypatch)
        session_id = add_session(path, recap=["Kept: two pointers."])
        connection = Connection([msg("recaps_get")])
        await socket._handle(connection)
        assert [item["type"] for item in connection.sent] == ["recaps_state"]
        state = connection.sent[0]
        assert state["ts"] and state["unread"] is True
        assert state["recaps"][0]["session_id"] == session_id

        socket._clients.clear()
        seen = Connection([msg("recap_seen", {"session_id": session_id})])
        await socket._handle(seen)
        states = [item for item in seen.sent if item["type"] == "recaps_state"]
        assert len(states) == 1
        assert states[0]["unread"] is False and states[0]["recaps"][0]["seen"] is True

    asyncio.run(scenario())


def test_planner_hides_only_hypotheses_changed_by_an_unseen_recap(tmp_path):
    path = tmp_path / "planner.sqlite3"
    with open_store(path) as db:
        seen = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                          "VALUES ('2026-10-17T09:00:00+00:00', '2026-10-17T09:30:00+00:00', 'Done.', '[]', 1)").lastrowid
        unseen = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                            "VALUES ('2026-10-18T09:00:00+00:00', '2026-10-18T09:30:00+00:00', 'Done.', '[]', 0)").lastrowid
        for text, changed_in in (("Predates the recaps", None), ("Changed in an opened recap", seen),
                                 ("Changed in a recap never opened", unseen)):
            db.execute("INSERT INTO hypotheses (text, category, skill, status, cited_event_ids, recap_session_id) "
                       "VALUES (?, 'Graphs', 'solve', 'open', '[]', ?)", (text, changed_in))

    request = Planner(path, lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)).panel_opened("idle")

    assert {item["text"] for item in request.context["hypotheses"]} == {
        "Predates the recaps", "Changed in an opened recap"}


def test_recaps_state_opens_each_recap_with_the_grades_from_that_session(tmp_path):
    from tests.test_progress import add_attempt, add_review

    path = tmp_path / "grades.sqlite3"
    good, hard, pending, other = "duplicate-integer", "is-anagram", "two-integer-sum", "anagram-groups"
    when = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    with open_store(path) as db:
        session = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                             "VALUES ('2026-10-17T09:00:00+00:00', '2026-10-17T09:30:00+00:00', 'Done.', '[\"A lesson.\"]', 1)").lastrowid
        good_left = add_attempt(db, session, good, accepted=True)
        good_id = add_review(db, good, "good", when, session=session, event_id=good_left)
        hard_left = add_attempt(db, session, hard, accepted=True, help=("question",))
        hard_id = add_review(db, hard, "hard", when, session=session, event_id=hard_left)
        pending_left = add_attempt(db, session, pending, accepted=True)
        pending_id = add_review(db, pending, None, when, session=session, event_id=pending_left)
        earlier = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets) "
                             "VALUES ('2026-10-16T09:00:00+00:00', '2026-10-16T09:30:00+00:00', 'Done.', '[]')").lastrowid
        other_left = add_attempt(db, earlier, other, accepted=True)
        add_review(db, other, "again", when, session=earlier, event_id=other_left)
        titles = {slug: db_title(slug) for slug in (good, hard, pending, other)}

    item = next(item for item in recaps_state(path)["recaps"] if item["session_id"] == session)
    assert item["grades"] == [
        {"review_id": good_id, "problem": good, "title": titles[good], "word": "solid",
         "reason": "solved on your own", "pending": False},
        {"review_id": hard_id, "problem": hard, "title": titles[hard], "word": "shaky",
         "reason": "needed help, with a question", "pending": False},
        {"review_id": pending_id, "problem": pending, "title": titles[pending], "word": "grading…",
         "reason": "", "pending": True},
    ]


def db_title(slug):
    from companion.catalog import load_catalog
    return load_catalog().by_slug(slug)["title"]


def test_recaps_state_reports_a_feedback_review_as_updating_while_it_runs(tmp_path):
    path = tmp_path / "feedback.sqlite3"
    with open_store(path) as db:
        session = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                             "VALUES ('2026-10-17T09:00:00+00:00', '2026-10-17T09:30:00+00:00', 'Done.', '[\"A lesson.\"]', 1)").lastrowid
        note = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES ('2026-10-17T09:30:00+00:00', "
                          "'recap_feedback', 'That drop was wrong.', ?)", (session,)).lastrowid
        db.execute("INSERT INTO recap_feedback_pending (session_id, note_id) VALUES (?, ?)", (session, note))

    item = next(item for item in recaps_state(path)["recaps"] if item["session_id"] == session)
    assert item["status"] == "updating"
    assert item["message"] is None and item["has_bullets"] is True


def test_recap_feedback_is_noted_and_broadcasts_updating(tmp_path, monkeypatch):
    async def scenario():
        path = recap_store(tmp_path, monkeypatch)
        session_id = add_session(path, recap=["Dropped: visited marking."])
        scheduled = []
        monkeypatch.setattr(socket, "_schedule_feedback", lambda session, note: scheduled.append((session, note)))
        connection = Connection([msg("recap_feedback", {"session_id": session_id, "text": "That drop was wrong."})])
        await socket._handle(connection)
        noted = [item for item in connection.sent if item["type"] == "recap_noted"]
        assert noted and noted[0]["message"] == "Noted."
        states = [item for item in connection.sent if item["type"] == "recaps_state"]
        assert states and states[-1]["recaps"][0]["status"] == "updating"
        assert scheduled == [(session_id, noted[0]["note_id"])]

    asyncio.run(scenario())


def test_a_restart_marks_interrupted_feedback_as_failed_instead_of_stuck(tmp_path):
    from companion.reflector import recover_feedback

    path = tmp_path / "restart.sqlite3"
    with open_store(path) as db:
        session = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                             "VALUES ('2026-10-17T09:00:00+00:00', '2026-10-17T09:30:00+00:00', 'Done.', '[\"A lesson.\"]', 1)").lastrowid
        note = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES ('2026-10-17T09:30:00+00:00', "
                          "'recap_feedback', 'That drop was wrong.', ?)", (session,)).lastrowid
        db.execute("INSERT INTO recap_feedback_pending (session_id, note_id) VALUES (?, ?)", (session, note))

    recover_feedback(path)

    item = recaps_state(path)["recaps"][0]
    assert item["status"] == "feedback_failed"
    assert "interrupted" in item["message"].lower()
    with open_store(path) as db:
        assert db.execute("SELECT count(*) FROM recap_feedback_pending").fetchone() == (0,)


def test_starting_the_companion_recovers_an_interrupted_feedback_call(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    path = tmp_path / "serve.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    with open_store(path) as db:
        session = db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets, recap_seen) "
                             "VALUES ('2026-10-17T09:00:00+00:00', '2026-10-17T09:30:00+00:00', 'Done.', '[\"A lesson.\"]', 1)").lastrowid
        note = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES ('2026-10-17T09:30:00+00:00', "
                          "'recap_feedback', 'That drop was wrong.', ?)", (session,)).lastrowid
        db.execute("INSERT INTO recap_feedback_pending (session_id, note_id) VALUES (?, ?)", (session, note))

    @asynccontextmanager
    async def fake_server(*args, **kwargs):
        yield object()

    monkeypatch.setattr(socket, "serve", fake_server)
    monkeypatch.setattr(socket, "_schedule_grading", lambda trigger="initial": None)

    async def scenario():
        async with socket.serve_companion(port=0):
            assert recaps_state(path)["recaps"][0]["status"] == "feedback_failed"

    asyncio.run(scenario())
