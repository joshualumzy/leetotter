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
    assert state["unread"] is True
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
