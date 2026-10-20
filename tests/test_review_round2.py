"""Regressions for the second 2026-10-19 review: grading timestamps, attempt order, isolation, reset waits, reflection."""

import asyncio
import json
import threading
from datetime import datetime, timezone

import companion.socket as socket_module
from companion.grading import AnalysisVerdict, grade_pending
from companion.planner import Planner
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_analysis_grading import FakeAllGraders, answer as answer_question, ask
from tests.test_grading import MEDIUM, MEDIUM_TITLE, Run, down, put_reference, reviews
from tests.test_planner_socket import Connection
from tests.test_progress import add_review, add_session, at, category_state, problem_item
from tests.test_reflector import RecordedReflector, answer
from tests.test_session import msg, opened

OTHER = "is-palindrome"
OTHER_TITLE = "Valid Palindrome"


def _answered_on(run, slug, title):
    run.start()
    run.open(slug, title)
    run.accept(slug)
    question = ask(run, slug)
    answer_question(run, question)
    return question


# A1: an analysis_resolved written after a slow grader must not predate events stored meanwhile.
def test_analysis_resolved_is_never_stamped_before_an_event_stored_while_grading(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, MEDIUM)
    put_reference(run.path, OTHER)
    question = _answered_on(run, MEDIUM, MEDIUM_TITLE)
    run.send(msg("session_done"))
    wrote = False

    class SlowGrader(FakeAllGraders):
        def call_role(self, role, messages, schema, *, model=None, **kwargs):
            nonlocal wrote
            if schema is AnalysisVerdict and not wrote:
                wrote = True
                run.clock.advance(600)
                run.send(opened(OTHER, OTHER_TITLE))
            return super().call_role(role, messages, schema, model=model, **kwargs)

    grade_pending(SlowGrader("no_reasoning"), db_path=run.path)

    with open_store(run.path) as db:
        session = run.coord.session_id
        rows = db.execute("SELECT id, ts FROM events WHERE session_id = ? ORDER BY id", (session,)).fetchall()
        assert [ts for _, ts in rows] == sorted(ts for _, ts in rows)
        resolved = [ts for event_id, ts in rows
                    if db.execute("SELECT type FROM events WHERE id = ?", (event_id,)).fetchone()[0] == "analysis_resolved"]
        assert resolved and resolved[0] >= rows[-1][1]

    fresh = SessionCoordinator(run.path, run.clock)
    assert fresh.session_id == run.coord.session_id
    assert fresh.state == "paused" and fresh.paused_reason == "restart"
    assert run.coord.session_id not in fresh.interrupted
    assert question > 0


# A2: an attempt whose left arrives after the next problem opened still owns that left event.
def test_progress_uses_an_attempt_left_after_the_next_problem_was_opened(tmp_path):
    path = tmp_path / "db"
    slug = "top-k-elements-in-list"
    with open_store(path) as db:
        session = add_session(db)
        from companion.catalog import load_catalog
        title = load_catalog().by_slug(slug)["title"]
        ts = at(0).isoformat()
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_opened', ?)",
                   (session, ts, json.dumps({"site": "neetcode", "slug": slug, "title": title})))
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'help_turn', ?)",
                   (session, ts, json.dumps({"turn_id": 1, "slug": slug, "request": "help", "press_index": 1,
                                             "gap": "none", "gap_note": None, "help_kind": "question",
                                             "reply_text": "Some help.", "follow_up_question": None,
                                             "model": "x-test-2"})))
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'submit_result', ?)",
                   (session, ts, json.dumps({"slug": slug, "accepted": True, "error_type": None,
                                             "error_message": "", "runtime": "0.02", "memory": 1,
                                             "passed": 1, "total": 1, "failing_case": None})))
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_opened', ?)",
                   (session, ts, json.dumps({"site": "neetcode", "slug": "two-sum", "title": "Two Sum"})))
        left = db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
                          (session, ts, json.dumps({"slug": slug, "accepted": True}))).lastrowid
        add_review(db, slug, "hard", at(0), meets=True, session=session, event_id=left)

    item = problem_item(category_state(path, "Arrays & Hashing", now=datetime(2026, 10, 20, tzinfo=timezone.utc)), slug)
    assert item["latest"]["reason"] == "needed help, with a question"


def test_past_links_use_an_attempt_left_after_the_next_problem_was_opened(tmp_path):
    from tests.test_past_links import attempt, current_reference, event, links, seed_reference, session
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "alpha", title="Alpha", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the alpha thing.")
    event(db, 1, 1, "problem_opened", {"site": "neetcode", "slug": "alpha", "title": "Alpha"})
    event(db, 1, 1, "help_turn", {"turn_id": 0, "slug": "alpha", "request": "help", "press_index": 1,
                                  "gap": "weak_intuition", "gap_note": None, "help_kind": "question",
                                  "reply_text": "A hint.", "follow_up_question": None, "model": "x-test-1"})
    event(db, 1, 1, "problem_opened", {"site": "neetcode", "slug": "beta", "title": "Beta"})
    event(db, 1, 1, "problem_left", {"slug": "alpha", "accepted": True})
    event(db, 1, 1, "problem_left", {"slug": "beta", "accepted": False})
    found = links(db, current_reference())
    assert [(link.slug, link.outcome) for link in found] == [("alpha", "solved with a question")]


# A3: one question that fails unexpectedly does not stop the others being graded.
def test_one_bad_analysis_question_does_not_stop_the_next_question(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, MEDIUM)
    put_reference(run.path, OTHER)
    first = _answered_on(run, MEDIUM, MEDIUM_TITLE)
    run.send(msg("session_done"))
    grade_pending(FakeAllGraders(down("x-test-1"), down("x-test-2")), db_path=run.path)  # solve grades only
    second = _answered_on(run, OTHER, OTHER_TITLE)
    run.end()
    with open_store(run.path) as db:
        db.execute('UPDATE "references" SET content = ? WHERE problem_key = ?', ("not json", MEDIUM))

    fake = FakeAllGraders("right")
    outcome = grade_pending(fake, db_path=run.path, trigger="next_problem")

    assert outcome.failures.get(first)
    assert [row["item_key"] for row in reviews(run.path) if row["item_type"] == "category:analysis"] == ["Two Pointers"]
    assert len(fake.analysis_calls) == 2  # only the second question's two graders


# A4: a hung worker fails the reset loudly instead of leaving _resetting set forever.
def test_a_hung_worker_fails_the_reset_and_clears_resetting(tmp_path, monkeypatch):
    path = tmp_path / "reset.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    now = lambda: datetime(2026, 10, 18, tzinfo=timezone.utc)
    socket_module._clients.clear()
    socket_module._coordinator = SessionCoordinator(path, now)
    socket_module._planner = Planner(path, now)
    socket_module._resetting = False
    socket_module._pending_workers.clear()
    socket_module._turns.clear()
    socket_module._planner_tasks.clear()
    socket_module._grading_tasks.clear()
    socket_module._reflection_tasks.clear()
    socket_module._reference_tasks.clear()
    monkeypatch.setattr(socket_module, "RESET_WORKER_WAIT", 0.05)
    with open_store(path) as db:
        db.execute("INSERT INTO settings (key, value) VALUES ('name', 'Ada')")

    entered, release = threading.Event(), threading.Event()
    socket_module._coordinator.turn, socket_module._coordinator.turn_slug, socket_module._coordinator.turn_session = 7, "two-sum", 3

    async def scenario():
        task = asyncio.create_task(asyncio.to_thread(socket_module._tracked(
            lambda: (entered.set(), release.wait(5)))))
        socket_module._grading_tasks.add(task)
        task.add_done_callback(socket_module._grading_tasks.discard)
        assert await asyncio.to_thread(entered.wait, 2)
        connection = Connection([msg("reset_progress", {"confirm": "RESET"})])
        await asyncio.wait_for(socket_module._handle(connection), 5)
        assert not socket_module._resetting
        assert socket_module._coordinator.turn is None
        assert any(item["type"] == "error" and "did not finish" in item["message"] for item in connection.sent)
        assert not any(item["type"] == "reset_done" for item in connection.sent)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    with open_store(path) as db:
        assert db.execute("SELECT value FROM settings WHERE key = 'name'").fetchone()[0] == "Ada"


# A5: startup asks the Reflector for only the latest session that still needs a recap.
def test_startup_reflects_only_the_latest_interrupted_session(tmp_path, monkeypatch):
    path = tmp_path / "recaps.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    with open_store(path) as db:
        old = db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-16T09:00:00+00:00')").lastrowid
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, '2026-10-16T09:00:00+00:00', "
                   "'session_started', '{}')", (old,))
        latest = db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-17T09:00:00+00:00')").lastrowid
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, '2026-10-17T09:00:00+00:00', "
                   "'session_started', '{}')", (latest,))
        current = db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-18T09:00:00+00:00')").lastrowid
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, '2026-10-18T09:00:00+00:00', "
                   "'session_started', '{}')", (current,))
    client = RecordedReflector(answer())

    async def scenario():
        async with socket_module.serve_companion(port=0, models=client):
            await asyncio.gather(*tuple(socket_module._reflection_tasks))

    asyncio.run(scenario())
    assert len(client.calls) == 1
    with open_store(path) as db:
        assert db.execute("SELECT summary FROM sessions WHERE id = ?", (latest,)).fetchone()[0] == "A short session."
        assert db.execute("SELECT summary FROM sessions WHERE id = ?", (old,)).fetchone()[0] is None
