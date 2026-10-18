"""Final regressions for attempt identity, review identity, and retryable storage errors."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
import companion.grading as grading
from companion.grading import grade_pending
from companion.store import MIGRATIONS, open_store
from tests.test_grading import (EASY, EASY_TITLE, MEDIUM, FakeGraders, Run,
                                attempt, left_ids, problem_grade, put_reference, reply)


def test_full_solution_after_accept_and_reload_keeps_the_accepted_attempt(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.accept()
    run.open()  # a reload of the same slug
    _, turn = run.coord.begin_turn("chat", "show another approach")
    run.coord.finish_turn(turn, reply("full_solution"), "x-test-2")
    run.end()

    assert problem_grade(run.path) == []
    assert grade_pending(FakeGraders(), db_path=run.path).graded == left_ids(run.path)
    assert problem_grade(run.path) == ["good"]


def test_locked_result_write_is_retried_and_later_attempt_is_graded(tmp_path, monkeypatch):
    run = Run(tmp_path)
    put_reference(run.path)
    put_reference(run.path, slug=EASY)
    attempt(run)
    attempt(run, slug=EASY, title=EASY_TITLE)
    first, second = left_ids(run.path)
    real_open = grading.open_store
    calls = 0

    @contextmanager
    def locked_once(path):
        nonlocal calls
        calls += 1
        if calls == 3:  # result write for the first attempt
            raise sqlite3.OperationalError("database is locked")
        with real_open(path) as db:
            yield db

    monkeypatch.setattr(grading, "open_store", locked_once)
    result = grade_pending(FakeGraders(), db_path=run.path)
    assert result.failed == [first]
    assert result.graded == [second]
    with open_store(run.path) as db:
        assert db.execute("SELECT failed_tries FROM grader_retries").fetchall() == [(1,)]
        assert db.execute("SELECT count(*) FROM grading_failures").fetchone()[0] == 0
    assert grade_pending(FakeGraders(), db_path=run.path, trigger="next_problem").graded == [first]


def test_failure_record_write_error_does_not_stop_later_attempts(tmp_path, monkeypatch):
    run = Run(tmp_path)
    put_reference(run.path)
    put_reference(run.path, slug=EASY)
    attempt(run)
    attempt(run, slug=EASY, title=EASY_TITLE)
    first, second = left_ids(run.path)
    real_open = grading.open_store
    calls = 0

    @contextmanager
    def locked_twice(path):
        nonlocal calls
        calls += 1
        if calls in {3, 4}:  # result write, then failure bookkeeping
            raise sqlite3.OperationalError("database is locked")
        with real_open(path) as db:
            yield db

    monkeypatch.setattr(grading, "open_store", locked_twice)
    result = grade_pending(FakeGraders(), db_path=run.path)
    assert result.failed == [first]
    assert result.graded == [second]
    with open_store(run.path) as db:
        assert db.execute("SELECT count(*) FROM handled_attempts").fetchone()[0] == 1
    assert grade_pending(FakeGraders(), db_path=run.path).graded == [first]


def test_migration_deduplicates_reviews_and_enforces_one_per_event_and_type(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        for migration in MIGRATIONS[:3]:
            for statement in migration:
                db.execute(statement)
        db.execute("PRAGMA user_version = 3")
        db.execute("INSERT INTO reviews (event_id, item_type, item_key, grade, first_attempt, reviewed_at) "
                   "VALUES (12, 'problem', 'two-sum', NULL, 1, '2026-10-18')")
        db.execute("INSERT INTO reviews (event_id, item_type, item_key, grade, first_attempt, reviewed_at) "
                   "VALUES (12, 'problem', 'two-sum', 'good', 1, '2026-10-18')")
    with open_store(path) as db:
        assert db.execute("SELECT grade FROM reviews WHERE event_id = 12").fetchall() == [("good",)]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO reviews (event_id, item_type, item_key, grade, first_attempt, reviewed_at) "
                       "VALUES (12, 'problem', 'two-sum', NULL, 1, '2026-10-18')")


def test_concurrent_grading_records_one_review_per_item(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    left = attempt(run)
    both_started = threading.Barrier(2)

    class ConcurrentGraders(FakeGraders):
        def call_role(self, *args, **kwargs):
            if not self.calls:
                both_started.wait(timeout=5)
            return super().call_role(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: grade_pending(ConcurrentGraders(), db_path=run.path), range(2)))

    assert sum(left in result.graded for result in results) == 1
    with open_store(run.path) as db:
        assert db.execute("SELECT item_type, grade FROM reviews WHERE event_id = ? ORDER BY item_type", (left,)).fetchall() == [
            ("category:solve", "good"), ("problem", "good")]
