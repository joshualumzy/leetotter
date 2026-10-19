"""Regressions for the 2026-10-19 review findings."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedError

import companion.socket as socket_module
from companion.catalog import load_catalog
from companion.grading import GradingOutcome, grade_pending
from companion.memory import item_memory
from companion.model import TutorReply
from companion.progress import category_state, correct_grade, exclude_session
from companion.reflector import _reached_150, reflect_session
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_grading import (CATEGORY, EASY, EASY_TITLE, MEDIUM, MEDIUM_TITLE, FakeGraders, Run, attempt,
                                problem_grade, put_reference, reply, reviews, verdict)
from tests.test_planner_socket import Connection
from tests.test_reflector import RecordedReflector, answer, ended
from tests.test_session import (SLUG_A, SLUG_B, TITLE_A, TITLE_B, make, msg, of_type, opened, reopen, snapshot,
                                start_msg, submit)

QUESTION = "What is the time complexity of your approach?"


def solve_reply(**changes):
    return TutorReply.model_validate({
        "gap": "none", "gap_note": None, "help_kind": "none", "reply_text": "That works.",
        "follow_up_question": QUESTION, "follow_up_complexity": True, "postpone_reason": None, **changes})


# H1: a late Tutor reply about the previous problem is not part of the current attempt.
def test_a_late_full_solution_for_the_previous_problem_does_not_grade_the_new_one(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    put_reference(run.path, slug=EASY)
    run.start()
    run.open(MEDIUM, MEDIUM_TITLE)
    _, turn = run.coord.begin_turn("chat", "show me")
    run.send(opened(EASY, EASY_TITLE))
    run.coord.finish_turn(turn, reply("full_solution"), "x-test-2")
    run.accept(EASY)
    run.end()

    grade_pending(FakeGraders(), db_path=run.path)

    assert problem_grade(run.path, EASY) == ["good"]


# H2: a miss is seen even when its Again was keyed to the time-up choice.
def test_a_debrief_miss_makes_the_next_attempt_not_a_first_attempt(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open(MEDIUM, MEDIUM_TITLE)
    run.clock.advance(35 * 60)
    run.coord.tick()
    run.send(msg("session_time_up", {"choice": "debrief"}))
    run.end()
    run.advance_days(1)

    attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)

    problem_rows = [row for row in reviews(run.path) if row["item_type"] == "problem"]
    assert [(row["grade"], row["first_attempt"]) for row in problem_rows] == [("again", 1), ("good", 0)]
    assert len([row for row in reviews(run.path) if row["item_type"] == "category:solve"]) == 1


# H3: excluding a session survives a later grading pass and the memory read.
def test_an_excluded_missed_or_pending_session_is_never_regraded_or_counted(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)
    session_id = run.coord.session_id
    exclude_session(run.path, session_id)

    grade_pending(FakeGraders(), db_path=run.path)

    assert reviews(run.path) == []
    with open_store(run.path) as db:
        assert item_memory(db, "category:solve", CATEGORY, datetime.now(timezone.utc)) is None


def test_reached_150_ignores_an_excluded_session(tmp_path):
    path = tmp_path / "db"
    entries = load_catalog().neetcode150()
    with open_store(path) as db:
        session = db.execute("INSERT INTO sessions (started_at, ended_at, excluded) VALUES "
                             "('2026-10-18T00:00:00+00:00', '2026-10-18T00:30:00+00:00', 1)").lastrowid
        for entry in entries:
            event = db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
                               (session, "2026-10-18T00:20:00+00:00",
                                json.dumps({"slug": entry["slug"], "accepted": True}))).lastrowid
            db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, "
                       "first_attempt, difficulty, reviewed_at) VALUES (?, ?, 'problem', ?, 'good', 1, 1, ?, ?)",
                       (session, event, entry["slug"], entry["difficulty"], "2026-10-18T00:20:00+00:00"))
        assert _reached_150(db, session) is False


# H11: a correction to a passing grade also settles meets_expected.
def test_correcting_a_miss_to_solid_makes_the_problem_done_on_your_own(tmp_path):
    path = tmp_path / "db"
    slug = MEDIUM
    with open_store(path) as db:
        session = db.execute("INSERT INTO sessions (started_at, ended_at) VALUES "
                             "('2026-10-18T00:00:00+00:00', '2026-10-18T00:30:00+00:00')").lastrowid
        event = db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
                           (session, "2026-10-18T00:20:00+00:00",
                            json.dumps({"slug": slug, "accepted": False}))).lastrowid
        review = db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, "
                            "first_attempt, difficulty, reviewed_at) VALUES (?, ?, 'problem', ?, 'again', NULL, 1, "
                            "'Medium', '2026-10-18T00:20:00+00:00')", (session, event, slug)).lastrowid
        db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, "
                   "first_attempt, difficulty, reviewed_at) VALUES (?, ?, 'category:solve', ?, 'again', NULL, 1, "
                   "'Medium', '2026-10-18T00:20:00+00:00')", (session, event, CATEGORY))

    correct_grade(path, review_id=review, grade="solid")

    with open_store(path) as db:
        rows = db.execute("SELECT item_type, grade, meets_expected FROM reviews ORDER BY id").fetchall()
    assert rows == [("problem", "good", 1), ("category:solve", "good", 1)]
    state = category_state(path, CATEGORY, now=datetime(2026, 10, 19, tzinfo=timezone.utc))
    item = next(item for item in state["problems"] if item["problem"] == slug)
    assert item["status"] == "on_your_own"


# L2: an interrupted session's accepted attempt is still graded.
def test_an_accepted_attempt_in_an_interrupted_session_is_graded(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.accept()
    with open_store(run.path) as db:
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (1, '2026-10-15T00:01:00+00:00', "
                   "'code_snapshot', ?)", (json.dumps({"code": 5}),))

    reopen(run.path, run.clock, 0)
    grade_pending(FakeGraders(), db_path=run.path)

    assert problem_grade(run.path) == ["good"]


# GLM#4: both graders failing is retried at the next problem and next start, then stops.
def test_both_graders_failing_is_not_retried_after_the_next_start(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    left = attempt(run)
    fake = FakeGraders(RuntimeError("down"), RuntimeError("down"))
    for trigger in ("initial", "next_problem", "next_start"):
        assert grade_pending(fake, db_path=run.path, trigger=trigger).failed == [left]
    assert len(fake.calls) == 6

    fake.replies = {"x-test-1": verdict(), "x-test-2": verdict()}
    outcome = grade_pending(fake, db_path=run.path, trigger="next_start")

    assert outcome.graded == outcome.failed == [] and len(fake.calls) == 6
    assert problem_grade(run.path) == [None]


# Reset must wait for worker threads before erasing, so none can write into the fresh store.
def test_reset_waits_for_an_in_flight_grading_worker(tmp_path, monkeypatch):
    path = tmp_path / "reset.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    socket_module._clients.clear()
    socket_module._turns.clear()
    socket_module._grading_tasks.clear()
    socket_module._reflection_tasks.clear()
    socket_module._reference_tasks.clear()
    socket_module._pending_workers.clear()
    now = lambda: datetime(2026, 10, 18, tzinfo=timezone.utc)
    socket_module._coordinator = SessionCoordinator(path, now)
    socket_module._planner = None
    socket_module._grading_lock = asyncio.Lock()
    socket_module._reflection_lock = asyncio.Lock()
    entered, release = threading.Event(), threading.Event()

    def slow_grading(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        with open_store(path) as db:
            db.execute("INSERT INTO model_calls (role, model, latency_ms, validation_result, created_at) "
                       "VALUES ('grader', 'stale', 10, 'valid', '2026-10-18T00:00:00+00:00')")
        return GradingOutcome()

    monkeypatch.setattr(socket_module, "grade_pending", slow_grading)

    async def scenario():
        socket_module._schedule_grading("initial")
        assert await asyncio.to_thread(entered.wait, 2)
        reset = asyncio.create_task(socket_module._handle(Connection([msg("reset_progress", {"confirm": "RESET"})])))
        await asyncio.sleep(0.1)
        assert not reset.done()  # the reset is waiting for the worker
        release.set()
        await asyncio.wait_for(reset, 5)

    asyncio.run(scenario())
    with open_store(path) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


# H5: a failed help_turn write still logs help_failed and tells the panel.
def test_a_failed_help_turn_write_still_logs_help_failed_and_tutor_error(tmp_path, monkeypatch):
    coord, _, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    started, turn = coord.begin_turn("chat", "hello")
    assert turn is not None, started
    original = coord._event

    def flaky(db, session_id, ts, kind, payload):
        if kind == "help_turn":
            raise RuntimeError("database is locked")
        return original(db, session_id, ts, kind, payload)

    monkeypatch.setattr(coord, "_event", flaky)
    sent = []

    async def broadcast(outgoing, sender=None, broadcast=True):
        sent.extend(outgoing)

    socket_module._coordinator = coord
    socket_module._models = SimpleNamespace(config=SimpleNamespace(roles={"tutor": ["x-test-2"]}))
    socket_module._resetting = False
    monkeypatch.setattr(socket_module, "ask_tutor", lambda *args, **kwargs: reply("question"))
    monkeypatch.setattr(socket_module, "_broadcast", broadcast)
    asyncio.run(socket_module._run_turn(turn, started))

    assert of_type(path, "help_failed")
    assert any(item["type"] == "tutor_error" and item["turn_id"] == turn.turn_id for item in sent)
    assert coord.turn is None


# H6: a solve reply after the session ended never opens an unanswerable question.
def test_a_solve_reply_after_end_does_not_open_a_follow_up(tmp_path):
    coord, _, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(submit(SLUG_A, True))
    started, turn = coord.begin_turn("solve")
    assert turn is not None, started
    coord.handle(msg("session_end"))

    outgoing = coord.finish_turn(turn, solve_reply(), "x-test-2")

    assert outgoing[0]["follow_up_question"] is None
    assert of_type(path, "followup_asked") == []


# H7: the crash gap is not charged to the problem's time cap.
def test_a_restart_gap_does_not_use_up_the_problem_time_cap(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened(MEDIUM, MEDIUM_TITLE))
    coord.begin_turn("help")
    clock.advance(60)
    coord.handle(snapshot())
    first = reopen(path, clock, 3600)
    assert first.state == "paused"
    second = reopen(path, clock, 3600)
    state = second.handle(msg("session_get_state"))[0]

    assert state["active_seconds"] == 60
    assert state["cap_seconds_left"] == 35 * 60 - 60


# H8: reflection waits for an in-flight Tutor turn before taking its snapshot.
def test_reflection_waits_for_an_in_flight_tutor_turn(tmp_path, monkeypatch):
    order = []

    async def scenario():
        socket_module._reflection_lock = asyncio.Lock()
        socket_module._grading_tasks.clear()
        socket_module._turns.clear()
        gate = asyncio.Event()

        async def in_flight():
            await gate.wait()
            order.append("turn")

        task = asyncio.create_task(in_flight())
        socket_module._turns.add(task)
        task.add_done_callback(socket_module._turns.discard)

        def fake_reflect(*args):
            order.append("reflect")
            return {"type": "recap_state", "session_id": 1, "status": "ready", "bullets": []}

        async def broadcast(outgoing, sender=None, broadcast=True):
            return None

        monkeypatch.setattr(socket_module, "reflect_session", fake_reflect)
        monkeypatch.setattr(socket_module, "_get_models", lambda: None)
        monkeypatch.setattr(socket_module, "_broadcast", broadcast)
        socket_module._resetting = False
        reflection = asyncio.create_task(socket_module._run_reflection(1))
        await asyncio.sleep(0.05)
        assert order == []
        gate.set()
        await asyncio.wait_for(reflection, 5)

    asyncio.run(scenario())
    assert order == ["turn", "reflect"]


# L3: restoring from accept_counted brings back the pending after-solve turn.
def test_a_pending_solve_turn_survives_a_restart(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(submit(SLUG_A, True))

    fresh = reopen(path, clock, 60)
    fresh.handle(msg("session_resume"))

    assert fresh.solve_turn_due() == SLUG_A


# GLM#3: a problem opened while paused is not lost.
def test_a_problem_opened_while_paused_becomes_current(tmp_path):
    coord, _, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened(SLUG_A, TITLE_A))
    coord.handle(msg("session_pause"))
    coord.handle(opened(SLUG_B, TITLE_B))
    assert coord.problem == {"slug": SLUG_B, "title": TITLE_B}
    coord.handle(msg("session_resume"))
    coord.handle(submit(SLUG_B, True))
    assert of_type(path, "accept_counted")[-1][1]["slug"] == SLUG_B


# GLM#5: one dead panel does not stop the grading failure reaching the others.
def test_a_closed_client_does_not_stop_grading_errors_reaching_other_clients(tmp_path, monkeypatch):
    class Dead:
        async def send(self, raw):
            raise ConnectionClosedError(None, None)

    class Good:
        def __init__(self):
            self.sent = []

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    good = Good()
    socket_module._clients = {Dead(), good}
    socket_module._grading_lock = asyncio.Lock()
    socket_module._resetting = False
    monkeypatch.setattr(socket_module, "grade_pending",
                        lambda *args, **kwargs: GradingOutcome(_errors=[1], failures={1: "Grader down"}))
    asyncio.run(socket_module._run_grading())

    assert any(item["type"] == "error" and "Grader down" in item["message"] for item in good.sent)


# GLM#6: an older interrupted session gets its recap instead of preparing forever.
def test_an_older_interrupted_session_is_reflected_at_startup(tmp_path, monkeypatch):
    path = tmp_path / "recaps.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    with open_store(path) as db:
        old = db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-17T09:00:00+00:00')").lastrowid
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, '2026-10-17T09:00:00+00:00', "
                   "'session_started', '{}')", (old,))
        new = db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-18T09:00:00+00:00')").lastrowid
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, '2026-10-18T09:00:00+00:00', "
                   "'session_started', '{}')", (new,))
    client = RecordedReflector(answer())

    async def scenario():
        async with socket_module.serve_companion(port=0, models=client):
            await asyncio.gather(*tuple(socket_module._reflection_tasks))

    asyncio.run(scenario())
    with open_store(path) as db:
        assert db.execute("SELECT summary FROM sessions WHERE id = ?", (old,)).fetchone()[0] == "A short session."


# H10: re-dropping a hypothesis the session already dropped is a no-op.
def test_re_dropping_a_dropped_hypothesis_is_a_no_op(tmp_path):
    path, session_id, event_id = ended(tmp_path)
    with open_store(path) as db:
        hypothesis = db.execute("INSERT INTO hypotheses (text, status, cited_event_ids) "
                                "VALUES ('Visited marking', 'dropped', '[]')").lastrowid
    drop = {"action": "drop", "hypothesis_id": hypothesis, "rejected_id": None, "text": None,
            "category": None, "skill": None, "cited_event_ids": [event_id], "cited_note_id": None,
            "recap_line": "Dropped: visited marking."}
    client = RecordedReflector(answer([drop]))

    state = reflect_session(client, path, session_id)

    assert state["bullets"] == [] and len(client.calls) == 1


# L5: a legacy rejected hypothesis without rejected_after_event_id does not crash the Reflector.
def test_a_rejected_hypothesis_without_a_rejection_event_can_return(tmp_path):
    path, session_id, event_id = ended(tmp_path)
    with open_store(path) as db:
        note = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES "
                          "('2026-10-18T00:00:00+00:00', 'recap_feedback', 'I do mark visits', ?)",
                          (session_id,)).lastrowid
        hypothesis = db.execute("INSERT INTO hypotheses (text, status, cited_event_ids, rejected_note_id, "
                                "rejected_after_event_id) VALUES ('Visited marking', 'rejected', '[]', ?, NULL)",
                                (note,)).lastrowid
    change = {"action": "add", "hypothesis_id": None, "rejected_id": hypothesis,
              "text": "Visited marking is a real strength", "category": "Graphs", "skill": "solve",
              "cited_event_ids": [event_id], "cited_note_id": None,
              "recap_line": "Visited marking is back."}
    state = reflect_session(RecordedReflector(answer([change])), path, session_id)
    assert state["bullets"] == ["Visited marking is back."]


# L4: opening a different problem than the suggestion is logged.
def test_opening_an_off_plan_problem_is_logged(tmp_path):
    coord, _, path = make(tmp_path)
    coord.handle(start_msg())
    coord.suggested_slug = SLUG_B
    coord.handle(opened(SLUG_A, TITLE_A))

    off_plan, = of_type(path, "off_plan")
    assert off_plan[1] == {"slug": SLUG_A, "suggested": SLUG_B}
