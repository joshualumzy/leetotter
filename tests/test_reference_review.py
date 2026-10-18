"""A disputed reference is settled through the companion's public review operations."""

import json
import asyncio
from datetime import datetime, timezone

import pytest

from companion.reference_review import apply_verdict, flag_tutor_message, review_state
from companion.reference import write_and_check
from companion.store import open_store
import companion.socket as socket
from companion.session import SessionCoordinator
from tests.test_on_demand_reference import Connection
from tests.test_reference import FakeModels, reference, verdicts
from tests.test_grading import FakeGraders, Run, MEDIUM, attempt, put_reference, reviews


def test_disputed_reference_is_listed_with_a_self_contained_prompt(tmp_path):
    path = tmp_path / "db"
    put_reference(path, status="needs_review")
    state = review_state(path)
    assert state["count"] == 1
    item, = state["references"]
    assert item["slug"] == MEDIUM
    for text in ("top-k-elements-in-list", "count then a size-k heap", "heap.interview_expected",
                 "reference is fine", "interview_expected", "usually", "sometimes", "rarely"):
        assert text in item["prompt"]


def test_seems_wrong_flags_the_message_without_marking_a_label_uncertain(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    turn_id = run.help("question")
    note_id = flag_tutor_message(run.path, turn_id, "This advice misses duplicates")
    with open_store(run.path) as db:
        status, content = db.execute('SELECT status, content FROM "references" WHERE problem_key = ?', (MEDIUM,)).fetchone()
        note = db.execute("SELECT kind, text FROM notes WHERE id = ?", (note_id,)).fetchone()
    assert status == "needs_review"
    assert json.loads(content)["uncertain"] == []
    assert note[0] == "reference_flag" and json.loads(note[1]) == {
        "message_id": turn_id, "line": "This advice misses duplicates"}
    prompt = review_state(run.path)["references"][0]["prompt"]
    assert "Some help." in prompt and "This advice misses duplicates" in prompt


def test_garbled_verdict_refuses_without_changing_reference_or_logging_outcome(tmp_path):
    path = tmp_path / "db"
    put_reference(path, status="needs_review")
    with open_store(path) as db:
        before = db.execute('SELECT content, status FROM "references"').fetchone()
    with pytest.raises(ValueError, match="verdict"):
        apply_verdict(path, MEDIUM, "looks okay")
    with open_store(path) as db:
        assert db.execute('SELECT content, status FROM "references"').fetchone() == before
        assert db.execute("SELECT count(*) FROM events WHERE type = 'reference_resolved'").fetchone()[0] == 0


def test_settled_label_is_applied_and_all_waiting_attempts_are_graded_once(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, status="needs_review")
    with open_store(run.path) as db:
        content = json.loads(db.execute('SELECT content FROM "references" WHERE problem_key = ?', (MEDIUM,)).fetchone()[0])
        content["uncertain"] = ["heap.complexity"]
        db.execute('UPDATE "references" SET content = ? WHERE problem_key = ?', (json.dumps(content), MEDIUM))
    first = attempt(run)
    run.advance_days(7)
    second = attempt(run)
    graders = FakeGraders()
    result = apply_verdict(run.path, MEDIUM, json.dumps({"labels": {"heap.complexity": {
        "time": "O(n + k log n)", "space": "O(n)"}}}), graders)
    assert result["accepted"] is True and result["graded"] == [first, second]
    with open_store(run.path) as db:
        status, content = db.execute('SELECT status, content FROM "references" WHERE problem_key = ?', (MEDIUM,)).fetchone()
        outcomes = db.execute("SELECT payload FROM events WHERE type = 'reference_resolved'").fetchall()
    assert status == "resolved_by_you"
    assert json.loads(content)["approaches"][1]["time"] == "O(n + k log n)"
    assert json.loads(content)["uncertain"] == []
    assert len(outcomes) == 1
    assert [r["event_id"] for r in reviews(run.path) if r["item_type"] == "problem"] == [first, second]
    assert len(graders.calls) == 4
    assert review_state(run.path)["count"] == 0
    writer = FakeModels()
    assert write_and_check(MEDIUM, "Top K Frequent Elements", writer, db_path=run.path).status == "resolved_by_you"
    assert writer.calls == []


def test_socket_exposes_list_refusal_and_apply_results(tmp_path, monkeypatch):
    path = tmp_path / "db"
    put_reference(path, status="needs_review")
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    async def scenario():
        socket._coordinator = SessionCoordinator(path, lambda: datetime.now(timezone.utc))
        socket._grading_lock = asyncio.Lock()
        connection = Connection()
        handler = asyncio.create_task(socket._handle(connection))
        try:
            state = await connection.request("reference_get", response="reference_state")
            assert state["count"] == 1 and state["references"][0]["slug"] == MEDIUM
            refused = await connection.request("reference_apply", {"slug": MEDIUM, "reply": "garbled"},
                                               response="reference_verdict")
            assert refused["accepted"] is False and "Invalid verdict" in refused["reason"]
            accepted = await connection.request("reference_apply", {"slug": MEDIUM,
                                                "reply": '{"verdict":"reference is fine"}'},
                                                response="reference_verdict")
            assert accepted["accepted"] is True and accepted["reason"] == "reference is fine"
            state = await connection.request("reference_get", response="reference_state")
            assert state["count"] == 0
        finally:
            await connection.incoming.put(None)
            await handler

    asyncio.run(scenario())


def test_a_flag_while_the_reference_is_missing_survives_a_late_writer_reply(tmp_path):
    run = Run(tmp_path)
    run.start()
    run.open()
    turn_id = run.help("question")
    flag_tutor_message(run.path, turn_id, "The hint is wrong")
    waiting = review_state(run.path)
    assert waiting["count"] == 1 and waiting["references"][0]["prompt"] is None
    written = {**reference(), "slug": MEDIUM, "title": "Top K Frequent Elements"}
    models = FakeModels([written], [verdicts()])
    result = write_and_check(MEDIUM, "Top K Frequent Elements", models, db_path=run.path)
    assert result.status == "needs_review"
    assert review_state(run.path)["references"][0]["prompt"] is not None


def test_socket_seems_wrong_acknowledges_without_a_tutor_or_model_call(tmp_path, monkeypatch):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    turn_id = run.help("question")
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(run.path))

    async def scenario():
        socket._coordinator = SessionCoordinator(run.path, lambda: datetime.now(timezone.utc))
        socket._grading_lock = asyncio.Lock()
        socket._models = None
        connection = Connection()
        handler = asyncio.create_task(socket._handle(connection))
        try:
            flagged = await connection.request("reference_flag", {"message_id": turn_id, "line": "No"},
                                               response="reference_flagged")
            assert flagged["message_id"] == turn_id
            assert not any(item["type"].startswith("tutor_") for item in connection.sent)
            assert socket._models is None
            assert review_state(run.path)["count"] == 1
        finally:
            await connection.incoming.put(None)
            await handler

    asyncio.run(scenario())
