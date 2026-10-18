"""A preexisting version 1 store upgrades without losing reviews."""

import sqlite3

from companion.store import MIGRATIONS, open_store
from companion.grading import grade_pending
from companion.reference import write_and_check
from tests.test_grading import MEDIUM, MEDIUM_TITLE, FakeGraders, Run, attempt, problem_grade, put_reference, verdict
from tests.test_reference import FakeModels, reference, verdicts


def test_newly_checked_reference_grades_a_waiting_attempt(tmp_path):
    run = Run(tmp_path)
    attempt(run)
    assert grade_pending(FakeModels(), db_path=run.path).pending

    data = reference()
    data["slug"] = MEDIUM
    data["title"] = MEDIUM_TITLE
    data["difficulty"] = "Medium"

    class Models(FakeModels):
        config = type("Config", (), {"roles": {"grader": ["x-test-1", "x-test-2"]}})()

        def call_role(self, role, messages, schema_class, **kwargs):
            if role == "grader":
                import json
                events = json.loads(messages[1]["content"])["events"]
                submission = next(id_ for id_, kind, payload in events if kind == "submit_result" and payload["accepted"])
                return schema_class.model_validate(verdict("hash", accepts=[(submission, "hash", True)]))
            return super().call_role(role, messages, schema_class, **kwargs)

    write_and_check(MEDIUM, MEDIUM_TITLE, Models([data], [verdicts()]), db_path=run.path)
    assert problem_grade(run.path) == ["good"]


def test_help_between_accepted_submissions_does_not_lower_the_first_grade(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.accept()
    run.help("snippet")
    run.accept()
    run.end()
    graders = FakeGraders(verdict(accepts=[(-2, "heap", True), (-1, "heap", True)]),
                          verdict(accepts=[(-2, "heap", True), (-1, "heap", True)]))
    outcome = grade_pending(graders, db_path=run.path)
    assert outcome.graded and problem_grade(run.path) == ["good"]
    assert len(graders.calls) == 2


def test_version_one_reviews_survive_the_nullable_grade_migration(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as db:
        for statement in MIGRATIONS[0]:
            db.execute(statement)
        db.execute("PRAGMA user_version = 1")
        db.execute("INSERT INTO reviews (item_type, item_key, grade, first_attempt, reviewed_at) VALUES ('problem', 'two-sum', 'good', 1, '2026-10-01T00:00:00+00:00')")

    with open_store(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8
        assert db.execute("SELECT item_key, grade FROM reviews").fetchall() == [("two-sum", "good")]
        db.execute("INSERT INTO reviews (item_type, item_key, grade, first_attempt, reviewed_at) VALUES ('problem', 'other', NULL, 0, '2026-10-02T00:00:00+00:00')")
