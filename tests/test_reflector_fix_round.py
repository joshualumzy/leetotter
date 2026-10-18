"""Steering is excluded from Reflector input."""

import json

from companion.reflector import reflect_session
from companion.store import open_store
from tests.test_reflector import RecordedReflector, answer, ended


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
