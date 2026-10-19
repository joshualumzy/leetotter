"""Steering is excluded from Reflector input, and real strict-schema replies are normalised."""

import asyncio
import json

import pytest

import companion.socket as socket
from companion.reflector import recap_state, reflect_session
from companion.store import open_store
from tests.test_reflector import RecordedReflector, added, answer, ended
from tests.test_session import make, msg


def test_reflector_reads_learner_notes_but_never_planner_steering_or_failures(tmp_path):
    path, session_id, _ = ended(tmp_path)
    with open_store(path) as db:
        for kind in ("preference", "correction", "recap_feedback", "seems_wrong", "steer", "steer_clear", "planner_failure"):
            db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, ?, ?, ?)",
                       ("2026-10-18T00:00:00+00:00", kind, kind, session_id))
    client = RecordedReflector(answer())
    reflect_session(client, path, session_id)
    notes = json.loads(client.calls[0][1][-1]["content"])["notes"]
    assert {note["kind"] for note in notes} == {"preference", "correction", "recap_feedback"}


def test_a_real_strict_schema_reply_with_zero_and_empty_unused_fields_is_accepted(tmp_path):
    path, session_id, event_id = ended(tmp_path)
    added_change = {"action": "add", "hypothesis_id": 0, "rejected_id": 0,
                    "text": "Visited marking needs practice", "category": "Graphs", "skill": "solve",
                    "cited_event_ids": [event_id], "cited_note_id": 0,
                    "recap_line": "You revisited visited marking."}
    state = reflect_session(RecordedReflector(answer([added_change])), path, session_id)
    assert state["bullets"] == ["You revisited visited marking."]
    with open_store(path) as db:
        hypothesis_id = db.execute("SELECT id FROM hypotheses").fetchone()[0]

    coordinator, _, _ = make(tmp_path)
    coordinator.handle(msg("session_start"))
    coordinator.handle(msg("session_end"))
    with open_store(path) as db:
        second_event = db.execute("SELECT MAX(id) FROM events").fetchone()[0]
    drop = {"action": "drop", "hypothesis_id": hypothesis_id, "rejected_id": 0,
            "text": "", "category": "", "skill": "", "cited_event_ids": [second_event],
            "cited_note_id": 0, "recap_line": "Dropped: visited marking."}
    state = reflect_session(RecordedReflector(answer([drop])), path, coordinator.session_id)
    assert state["bullets"] == ["Dropped: visited marking."]
    with open_store(path) as db:
        assert db.execute("SELECT status FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone() == ("dropped",)


def test_a_rejected_reply_is_kept_whole_and_the_recap_shows_failed(tmp_path, monkeypatch):
    path, session_id, _ = ended(tmp_path)
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    bad = answer([added([999999])])
    socket._models = RecordedReflector(bad, bad)
    socket._reflection_lock = asyncio.Lock()

    asyncio.run(socket._run_reflection(session_id))

    assert recap_state(path, session_id)["status"] == "failed"
    assert "Session review is missing" in recap_state(path, session_id)["message"]
    with open_store(path) as db:
        stored = [row[0] for row in db.execute("SELECT prompt_text FROM model_calls WHERE role = 'reflector'")]
    assert stored and all("Visited marking needs practice" in raw for raw in stored if raw)


def test_zero_and_empty_ids_do_not_produce_semantic_errors(tmp_path):
    path, session_id, event_id = ended(tmp_path)
    bad = {"action": "add", "hypothesis_id": 0, "rejected_id": 0, "text": "",
           "category": "", "skill": "", "cited_event_ids": [event_id], "cited_note_id": 0,
           "recap_line": "You revisited visited marking."}
    client = RecordedReflector(answer([bad]), answer([bad]))
    with pytest.raises(ValueError, match="Added hypothesis needs text"):
        reflect_session(client, path, session_id)
