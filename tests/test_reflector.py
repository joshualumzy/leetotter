"""Reflector behavior at the recorded model and store boundary."""

import json

import pytest

from companion.reflector import reflect_session
from companion.store import open_store
from tests.test_session import make, msg, start_msg


class RecordedReflector:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []

    def call_role(self, role, messages, schema_class):
        self.calls.append((role, messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return schema_class.model_validate(reply)


def ended(tmp_path):
    coordinator, _, path = make(tmp_path)
    coordinator.handle(start_msg())
    coordinator.handle(msg("session_end"))
    with open_store(path) as db:
        event_id = db.execute("SELECT id FROM events WHERE type = 'session_ended'").fetchone()[0]
    return path, coordinator.session_id, event_id


def answer(changes=(), teaching=(), bullets=()):
    return {"summary": "A short session.", "changes": list(changes),
            "teaching": list(teaching), "bullets": list(bullets)}


def added(citations):
    return {"action": "add", "hypothesis_id": None, "rejected_id": None,
            "text": "Visited marking needs practice", "category": "Graphs", "skill": "solve",
            "cited_event_ids": citations, "cited_note_id": None,
            "recap_line": "You revisited visited marking."}


@pytest.mark.parametrize("citations", [[], [999999]])
def test_uncited_or_missing_event_hypothesis_is_rejected(tmp_path, citations):
    path, session_id, _ = ended(tmp_path)
    client = RecordedReflector(answer([added(citations)]), answer([added(citations)]))
    with pytest.raises(ValueError):
        reflect_session(client, path, session_id)
    with open_store(path) as db:
        assert db.execute("SELECT summary, recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone() == (None, None)
        assert db.execute("SELECT count(*) FROM hypotheses").fetchone()[0] == 0
    assert len(client.calls) == 2


def test_smooth_session_has_an_empty_recap(tmp_path):
    path, session_id, _ = ended(tmp_path)
    state = reflect_session(RecordedReflector(answer()), path, session_id)
    assert state == {"type": "recap_state", "session_id": session_id, "status": "ready", "bullets": []}


def test_carried_over_bullet_needs_changed_evidence(tmp_path):
    path, session_id, _ = ended(tmp_path)
    carried = {"text": "The same edge case returned.", "carried_over": True, "cited_event_ids": []}
    client = RecordedReflector(answer(bullets=[carried]), answer(bullets=[carried]))
    with pytest.raises(ValueError, match="Carried-over"):
        reflect_session(client, path, session_id)
    with open_store(path) as db:
        assert db.execute("SELECT recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] is None


def test_changes_and_teaching_are_saved_with_citations_and_idea(tmp_path):
    coordinator, _, path = make(tmp_path)
    coordinator.handle(start_msg())
    coordinator.handle(msg("problem_opened", {"site": "neetcode", "slug": "duplicate-integer", "title": "Duplicate Integer"}))
    coordinator.handle(msg("session_end"))
    with open_store(path) as db:
        event_id = db.execute("SELECT id FROM events WHERE type = 'problem_opened'").fetchone()[0]
    change = added([event_id])
    teaching = {"problem_slug": "duplicate-integer", "cited_event_ids": [event_id],
                "idea": "A set detects a repeat as we scan.", "category": "Arrays & Hashing",
                "framing": "compare each new element with seen values", "help_kind": "question", "outcome": "helped"}
    state = reflect_session(RecordedReflector(answer([change], [teaching])), path, coordinator.session_id)
    assert state["bullets"] == ["You revisited visited marking."]
    with open_store(path) as db:
        assert db.execute("SELECT problem_slug, cited_event_ids, idea FROM teaching").fetchone() == (
            "duplicate-integer", json.dumps([event_id]), "A set detects a repeat as we scan.")


def test_feedback_is_saved_immediately_then_rejects_a_weakness_without_touching_reviews(tmp_path):
    from companion.reflector import reflect_feedback, submit_feedback

    path, session_id, event_id = ended(tmp_path)
    reflect_session(RecordedReflector(answer([added([event_id])])), path, session_id)
    with open_store(path) as db:
        hypothesis_id = db.execute("SELECT id FROM hypotheses").fetchone()[0]
        before_reviews = db.execute("SELECT * FROM reviews").fetchall()
    note_id = submit_feedback(None, path, session_id, "That weakness is wrong.")
    drop = {"action": "drop", "hypothesis_id": hypothesis_id, "rejected_id": None,
            "text": None, "category": None, "skill": None, "cited_event_ids": [],
            "cited_note_id": note_id, "recap_line": "Dropped: visited marking."}
    state = reflect_feedback(RecordedReflector({"changes": [drop, drop]}), path, session_id, note_id)
    assert state["bullets"].count("Dropped: visited marking.") == 1
    with open_store(path) as db:
        assert db.execute("SELECT status, rejected_note_id, cited_event_ids FROM hypotheses").fetchone() == (
            "rejected", note_id, json.dumps([event_id]))
        assert db.execute("SELECT * FROM reviews").fetchall() == before_reviews


def test_rejected_weakness_returns_only_with_named_id_and_later_evidence(tmp_path):
    from companion.reflector import reflect_feedback, submit_feedback

    path, first_id, event_id = ended(tmp_path)
    reflect_session(RecordedReflector(answer([added([event_id])])), path, first_id)
    with open_store(path) as db:
        old_id = db.execute("SELECT id FROM hypotheses").fetchone()[0]
    note_id = submit_feedback(None, path, first_id, "Drop this.")
    drop = {"action": "drop", "hypothesis_id": old_id, "rejected_id": None,
            "text": None, "category": None, "skill": None, "cited_event_ids": [],
            "cited_note_id": note_id, "recap_line": "Dropped: visited marking."}
    reflect_feedback(RecordedReflector({"changes": [drop]}), path, first_id, note_id)
    coordinator, _, _ = make(tmp_path)
    coordinator.handle(start_msg())
    coordinator.handle(msg("session_end"))
    with open_store(path) as db:
        later_id = db.execute("SELECT MAX(id) FROM events").fetchone()[0]
    returning = added([later_id])
    returning["rejected_id"] = old_id
    returning["recap_line"] = "Visited marking is back after new evidence."
    state = reflect_session(RecordedReflector(answer([returning])), path, coordinator.session_id)
    assert state["bullets"] == ["Visited marking is back after new evidence."]


def test_failed_reflection_retries_at_next_start_without_waiting_for_it(tmp_path, monkeypatch):
    import asyncio
    import companion.socket as socket

    path, session_id, _ = ended(tmp_path)
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    client = RecordedReflector(RuntimeError("offline"), answer())
    socket._models = client
    socket._reflection_lock = asyncio.Lock()

    async def scenario():
        await socket._run_reflection(session_id)
        with open_store(path) as db:
            assert db.execute("SELECT summary FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] is None
        assert recap_state(path, session_id)["status"] == "failed"
        task = asyncio.create_task(socket._run_reflection(session_id))
        assert not task.done()
        await task
        assert recap_state(path, session_id)["status"] == "ready"

    from companion.reflector import recap_state
    asyncio.run(scenario())


def finish_150(path, session_id, last_grade):
    from companion.catalog import load_catalog

    slugs = [item["slug"] for item in load_catalog().neetcode150()]
    assert len(slugs) == 150
    with open_store(path) as db:
        for slug in slugs[:149]:
            event_id = db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
                                  (session_id - 1 if session_id > 1 else None, "2026-01-01T00:00:00+00:00",
                                   json.dumps({"slug": slug, "accepted": True}))).lastrowid
            db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, reviewed_at) "
                       "VALUES (?, ?, 'problem', ?, 'good', 1, 1, '2026-01-01T00:00:00+00:00')",
                       (None, event_id, slug))
        last_event = db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
                                (session_id, "2026-01-02T00:00:00+00:00", json.dumps({"slug": slugs[-1], "accepted": True}))).lastrowid
        db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, reviewed_at) "
                   "VALUES (?, ?, 'problem', ?, ?, 1, 1, '2026-01-02T00:00:00+00:00')",
                   (session_id, last_event, slugs[-1], last_grade))


def test_the_150th_done_gets_one_quiet_line(tmp_path):
    from companion.reflector import QUIET_LINE

    path, session_id, _ = ended(tmp_path)
    finish_150(path, session_id, "good")
    state = reflect_session(RecordedReflector(answer()), path, session_id)
    assert state["bullets"] == [QUIET_LINE]


def test_a_hard_latest_grade_is_not_the_150th_done(tmp_path):
    from companion.reflector import QUIET_LINE

    path, session_id, _ = ended(tmp_path)
    finish_150(path, session_id, "hard")
    state = reflect_session(RecordedReflector(answer()), path, session_id)
    assert QUIET_LINE not in state["bullets"]


def test_restart_retries_in_background_while_new_session_starts(tmp_path, monkeypatch):
    import asyncio
    import threading
    from contextlib import asynccontextmanager

    import companion.socket as socket

    path, session_id, _ = ended(tmp_path)
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    entered = threading.Event()
    release = threading.Event()

    class WaitingReply(RecordedReflector):
        def call_role(self, role, messages, schema_class):
            entered.set()
            assert release.wait(5)
            return super().call_role(role, messages, schema_class)

    client = WaitingReply(answer())

    @asynccontextmanager
    async def fake_server(*args, **kwargs):
        yield object()

    monkeypatch.setattr(socket, "serve", fake_server)
    monkeypatch.setattr(socket, "_schedule_grading", lambda trigger="initial": None)

    class Connection:
        sent = []

        def __aiter__(self):
            self.messages = iter([start_msg()])
            return self

        async def __anext__(self):
            try:
                return json.dumps(next(self.messages))
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    async def scenario():
        async with socket.serve_companion(port=0, models=client):
            assert await asyncio.to_thread(entered.wait, 2)
            connection = Connection()
            try:
                await socket._handle(connection)
                assert any(item["type"] == "session_state" and item["state"] == "active"
                           for item in connection.sent)
                assert recap_state(path, session_id)["status"] == "preparing"
            finally:
                release.set()

    from companion.reflector import recap_state
    asyncio.run(scenario())
    assert recap_state(path, session_id)["status"] == "ready"


@pytest.mark.parametrize("text", [
    "Kept: spam recurred (evs 516-531) with no actions.",
    "The inverted edit persisted (ev 493) and was never fixed.",
    "Restore events (493/504/512) were logged after each pause.",
    "Many session_restored events fired after the pause.",
    "x" * 161,
])
def test_recap_text_with_event_ids_system_names_or_length_is_sent_back_and_corrected(tmp_path, text):
    path, session_id, event_id = ended(tmp_path)
    bad = answer(bullets=[{"text": text, "carried_over": False, "cited_event_ids": [event_id]}])
    good = answer(bullets=[{"text": "You left a broken edit in the editor.", "carried_over": False, "cited_event_ids": [event_id]}])
    client = RecordedReflector(bad, good)
    state = reflect_session(client, path, session_id)
    assert state["bullets"] == ["You left a broken edit in the editor."]
    assert "Return corrected JSON only" in client.calls[1][1][-1]["content"]


def test_rejected_reply_is_logged_whole_so_a_real_failure_can_be_read(tmp_path, caplog):
    path, session_id, _ = ended(tmp_path)
    client = RecordedReflector(answer([added([999999])]), answer([added([999999])]))
    with caplog.at_level("WARNING"), pytest.raises(ValueError):
        reflect_session(client, path, session_id)
    assert "Reflector reply rejected" in caplog.text and "Visited marking needs practice" in caplog.text


def test_only_the_last_session_can_be_pending(tmp_path):
    from companion.reflector import pending_sessions
    path, first, _ = ended(tmp_path)
    with open_store(path) as db:
        db.execute("INSERT INTO sessions (started_at, ended_at) VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:10:00+00:00')")
        newest = db.execute("SELECT max(id) FROM sessions").fetchone()[0]
    assert pending_sessions(path) == [newest]
    reflect_session(RecordedReflector(answer()), path, newest)
    assert pending_sessions(path) == []
