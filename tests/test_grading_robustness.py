"""Regressions for independent grading, attempt isolation, and delayed Tutor replies."""

import json

from companion.grading import grade_pending
from companion.model import ModelClient
from companion.store import open_store
from companion.reference import write_and_check
from tests.test_grading import (EASY, EASY_TITLE, MEDIUM, MEDIUM_TITLE, FakeGraders,
                                Run, attempt, left_ids, problem_grade, put_reference, reviews, verdict)
from tests.test_model import fake_client
from tests.test_reference import FakeModels, reference, verdicts


def test_real_model_client_can_record_both_grader_calls(tmp_path, monkeypatch):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    submission = next(row[0] for row in open_events(run.path) if row[1] == "submit_result")
    answer = json.dumps(verdict())
    answer = answer.replace('"submission_event_id": -1', f'"submission_event_id": {submission}')
    client, _ = fake_client(answer, answer)
    outcome = grade_pending(ModelClient(client=client, db_path=run.path), db_path=run.path)
    assert outcome.graded == left_ids(run.path)
    with open_store(run.path) as db:
        assert db.execute("SELECT count(*) FROM model_calls WHERE role = 'grader'").fetchone()[0] == 2


def open_events(path):
    with open_store(path) as db:
        return db.execute("SELECT id, type FROM events ORDER BY id").fetchall()


def test_another_connection_can_write_during_each_grader_call(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)

    class RecordingGraders(FakeGraders):
        def call_role(self, *args, **kwargs):
            with open_store(run.path) as db:
                db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (1, '2026-10-18', 'grading_probe', '{}')")
            return super().call_role(*args, **kwargs)

    assert grade_pending(RecordingGraders(), db_path=run.path).graded == left_ids(run.path)
    assert len([row for row in open_events(run.path) if row[1] == "grading_probe"]) == 2


def test_a_restart_before_the_next_problem_does_not_consume_the_retry(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)
    failing = FakeGraders(verdict(), RuntimeError("unavailable"))
    grade_pending(failing, db_path=run.path)
    assert len(failing.calls) == 2
    grade_pending(failing, db_path=run.path, trigger="next_start")
    grade_pending(failing, db_path=run.path, trigger="next_start")
    assert len(failing.calls) == 2
    grade_pending(failing, db_path=run.path, trigger="next_problem")
    assert len(failing.calls) == 4 and problem_grade(run.path) == [None]
    grade_pending(failing, db_path=run.path, trigger="next_start")
    assert problem_grade(run.path) == ["good"]


def test_both_graders_still_down_after_scheduled_retries_are_reported_as_ungraded(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    left = attempt(run)
    failing = FakeGraders(RuntimeError("down"), RuntimeError("down"))
    grade_pending(failing, db_path=run.path)
    grade_pending(failing, db_path=run.path, trigger="next_problem")
    outcome = grade_pending(failing, db_path=run.path, trigger="next_start")
    assert outcome.failed == [left]
    assert "Both graders failed" in outcome.failures[left]
    assert problem_grade(run.path) == [None]


def test_a_grader_must_label_every_accept_once(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.accept()
    run.accept()
    run.end()
    incomplete = verdict()
    graders = FakeGraders(incomplete, incomplete)
    assert grade_pending(graders, db_path=run.path).failed == left_ids(run.path)
    assert len(graders.calls) == 4  # one validation retry for each model


def test_multiple_accepts_are_graded_at_their_own_help_cutoffs(tmp_path):
    for index, (first, intervening, later, expected) in enumerate([
        (("sort", False), None, ("heap", True), "good"),
        (("sort", False), "snippet", ("heap", True), "hard"),
        (("sort", False), "question", ("heap", True), "hard"),
    ]):
        run = Run(tmp_path / str(index))
        put_reference(run.path)
        run.start()
        run.open()
        run.accept()
        if intervening:
            run.help(intervening)
        run.accept()
        run.end()
        answer = verdict(accepts=[(-2, *first), (-1, *later)])
        assert grade_pending(FakeGraders(answer, answer), db_path=run.path).graded
        assert problem_grade(run.path) == [expected]


def test_unknown_attempt_does_not_block_other_attempt_and_is_reported_once(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run, slug="not-in-catalog", title="Unknown")
    attempt(run)
    first, second = left_ids(run.path)
    result = grade_pending(FakeGraders(), db_path=run.path)
    assert result.graded == [second]
    assert result.failed == [first]
    assert "Unknown problem" in result.failures[first]
    assert grade_pending(FakeGraders(), db_path=run.path).failed == []


def test_pending_review_makes_a_later_attempt_seen_even_without_activity(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.end()
    first = left_ids(run.path)[0]
    with open_store(run.path) as db:
        db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, first_attempt, reviewed_at) VALUES (1, ?, 'problem', ?, NULL, 1, '2026-10-18')", (first, MEDIUM))
    attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    assert reviews(run.path)[-1]["first_attempt"] == 0


def test_full_solution_for_previous_problem_survives_navigation(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    _, turn = run.coord.begin_turn("chat", "show me")
    run.open(EASY, EASY_TITLE)
    from tests.test_grading import reply
    outgoing = run.coord.finish_turn(turn, reply("full_solution"), "x-test-2")
    assert outgoing[0]["type"] == "tutor_done"
    assert problem_grade(run.path) == ["again"]


def test_full_solution_miss_failure_keeps_help_turn_and_finishes_stream(tmp_path):
    run = Run(tmp_path)
    run.start()
    run.open("not-in-catalog", "Unknown")
    _, turn = run.coord.begin_turn("chat", "show me")
    from tests.test_grading import reply
    outgoing = run.coord.finish_turn(turn, reply("full_solution"), "x-test-2")
    assert any(item["type"] == "error" for item in outgoing)
    assert run.coord.turn is None
    assert [row for row in open_events(run.path) if row[1] == "help_turn"]


def test_reference_is_saved_even_if_grading_raises(tmp_path, monkeypatch):
    run = Run(tmp_path)
    attempt(run)
    data = reference()
    data.update(slug=MEDIUM, title=MEDIUM_TITLE, difficulty="Medium")

    def broken_grading(*args, **kwargs):
        raise RuntimeError("grading crash")

    monkeypatch.setattr("companion.grading.grade_pending", broken_grading)
    assert write_and_check(MEDIUM, MEDIUM_TITLE, FakeModels([data], [verdicts()]), db_path=run.path).status == "checked"
    with open_store(run.path) as db:
        assert db.execute('SELECT status FROM "references" WHERE problem_key = ?', (MEDIUM,)).fetchone() == ("checked",)
