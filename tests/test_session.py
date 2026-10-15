import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from websockets.asyncio.client import connect

from companion.session import SessionCoordinator
from companion.socket import ALLOWED_ORIGIN, serve_companion
from companion.store import open_store

START = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone.utc)
SLUG_A, TITLE_A = "duplicate-integer", "Duplicate Integer"
SLUG_B, TITLE_B = "two-sum", "Two Sum"


class FakeClock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def at(seconds):
    return START + timedelta(seconds=seconds)


def same_time(value, expected):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) == expected


def make(tmp_path):
    clock = FakeClock()
    path = tmp_path / "companion.sqlite3"
    return SessionCoordinator(path, clock), clock, path


def msg(kind, payload=None):
    message = {"type": kind, "ts": "2026-10-15T00:00:00Z"}
    if payload is not None:
        message["payload"] = payload
    return message


def start_msg(minutes=25):
    return msg("session_start", {"budget_minutes": minutes})


def opened(slug=SLUG_A, title=TITLE_A):
    return msg("problem_opened", {"site": "neetcode", "slug": slug, "title": title})


def submit(slug=SLUG_A, accepted=True):
    return msg("submit_result", {
        "slug": slug, "accepted": accepted, "error_type": None if accepted else "wrong_answer",
        "error_message": "", "runtime": "0.032", "memory": 14184,
        "passed": 34 if accepted else 3, "total": 34, "failing_case": None,
    })


def snapshot(code="print(1)"):
    return msg("code_snapshot", {"code": code})


def changed():
    return msg("code_changed", {"diff": {"start_line": 2, "removed": ["a"], "added": ["b"]}})


def run_result():
    return msg("run_result", {"slug": SLUG_A, "cases": [{"passed": True, "error_type": None, "error_message": "", "runtime": "0.027", "failing_case": None}]})


def focus(tab=True, window=True):
    return msg("focus_changed", {"tab_active": tab, "window_focused": window})


def check(ok, message=None):
    payload = {"ok": ok}
    if message is not None:
        payload["message"] = message
    return msg("reader_check", payload)


def events(path):
    with open_store(path) as db:
        rows = db.execute("SELECT type, payload, session_id, ts FROM events ORDER BY id").fetchall()
    return [(kind, json.loads(payload), session_id, ts) for kind, payload, session_id, ts in rows]


def types(path):
    return [row[0] for row in events(path)]


def of_type(path, kind):
    return [row for row in events(path) if row[0] == kind]


def sessions(path):
    with open_store(path) as db:
        return db.execute("SELECT id, started_at, ended_at, budget_minutes FROM sessions ORDER BY id").fetchall()


def state(out):
    assert out[0]["type"] == "session_state"
    return out[0]


def current(coord):
    return state(coord.handle(msg("session_get_state")))


def assert_rejected(coord, path, message):
    before = (events(path), sessions(path), current(coord))
    out = coord.handle(message)
    assert len(out) == 1 and out[0]["type"] == "error"
    assert isinstance(out[0]["message"], str) and out[0]["message"]
    assert (events(path), sessions(path), current(coord)) == before


# --- session_state content ---------------------------------------------------

def test_idle_state(tmp_path):
    coord, clock, _ = make(tmp_path)
    s = current(coord)
    assert same_time(s["ts"], START)
    assert {k: v for k, v in s.items() if k != "ts"} == {
        "type": "session_state", "state": "idle", "paused_reason": None, "check_message": None,
        "budget_minutes": None, "active_seconds": 0, "running": False, "overrun_prompt": False,
        "problem": None, "accepted": False, "mode": "idle",
    }


def test_active_state_and_on_connect_and_get_state_agree(tmp_path):
    coord, clock, _ = make(tmp_path)
    out = coord.handle(start_msg(25))
    assert out[0] == current(coord) == state(coord.on_connect())
    assert {k: v for k, v in out[0].items() if k != "ts"} == {
        "type": "session_state", "state": "active", "paused_reason": None, "check_message": None,
        "budget_minutes": 25, "active_seconds": 0, "running": True, "overrun_prompt": False,
        "problem": None, "accepted": False, "mode": "watching",
    }


def test_paused_state_for_every_reason(tmp_path):
    coord, clock, _ = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(20)
    s = state(coord.handle(msg("session_pause")))
    assert (s["state"], s["paused_reason"], s["running"], s["mode"], s["active_seconds"]) == ("paused", "button", False, "paused", 20)
    coord.handle(msg("session_resume"))
    s = state(coord.handle(focus(tab=False)))
    assert (s["state"], s["paused_reason"], s["running"], s["mode"]) == ("paused", "focus", False, "paused")
    coord.handle(focus())
    s = state(coord.handle(check(False, "Can't read the editor on this page")))
    assert (s["state"], s["paused_reason"], s["check_message"], s["mode"]) == ("paused", "check", "Can't read the editor on this page", "paused")
    s = state(coord.handle(check(True)))
    assert (s["state"], s["paused_reason"], s["check_message"], s["mode"]) == ("active", None, None, "watching")
    s = state(coord.on_disconnect())
    assert (s["state"], s["paused_reason"], s["running"], s["mode"]) == ("paused", "disconnect", False, "paused")


def test_ended_state_keeps_final_active_seconds(tmp_path):
    coord, clock, _ = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(90)
    s = state(coord.handle(msg("session_end")))
    clock.advance(500)
    s = current(coord)
    assert (s["state"], s["paused_reason"], s["running"], s["mode"], s["active_seconds"]) == ("ended", None, False, "idle", 90)
    assert s["overrun_prompt"] is False


def test_state_names_the_current_problem(tmp_path):
    coord, _, _ = make(tmp_path)
    coord.handle(start_msg())
    s = state(coord.handle(opened()))
    assert s["problem"] == {"slug": SLUG_A, "title": TITLE_A}
    assert s["accepted"] is False


# --- transitions and rejected commands --------------------------------------

def test_start_pause_resume_end_events_and_timestamps(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(25))
    clock.advance(10)
    coord.handle(msg("session_pause"))
    clock.advance(5)
    coord.handle(msg("session_resume"))
    clock.advance(7)
    coord.handle(msg("session_end"))
    rows = events(path)
    assert [r[0] for r in rows] == ["session_started", "session_paused", "session_resumed", "session_ended"]
    assert rows[1][1] == {"reason": "button"}
    assert rows[2][1] == {"reason": "button"}
    assert rows[3][1]["active_seconds"] == 17 and rows[3][1]["overrun_seconds"] == 0
    assert rows[0][1]["budget_minutes"] == 25
    assert [same_time(r[3], at(t)) for r, t in zip(rows, (0, 10, 15, 22))] == [True] * 4
    assert {r[2] for r in rows} == {1}
    (sid, started, ended, budget), = sessions(path)
    assert sid == 1 and budget == 25 and same_time(started, at(0)) and same_time(ended, at(22))


def test_start_is_allowed_again_after_the_session_ended_and_starts_clean(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(25))
    coord.handle(opened())
    coord.handle(submit())
    clock.advance(30)
    coord.handle(msg("session_end"))
    clock.advance(100)
    s = state(coord.handle(start_msg(10)))
    assert (s["state"], s["budget_minutes"], s["active_seconds"], s["problem"], s["accepted"], s["overrun_prompt"]) == ("active", 10, 0, None, False, False)
    rows = sessions(path)
    assert [r[0] for r in rows] == [1, 2] and rows[1][3] == 10 and rows[1][2] is None
    assert of_type(path, "session_started")[1][2] == 2


@pytest.mark.parametrize("setup, command", [
    ([], msg("session_pause")),
    ([], msg("session_resume")),
    ([], msg("session_end")),
    ([], msg("session_overrun", {"choice": "finish"})),
    ([start_msg()], start_msg()),
    ([start_msg()], msg("session_resume")),
    ([start_msg()], msg("session_overrun", {"choice": "stop"})),
    ([start_msg(), msg("session_pause")], start_msg()),
    ([start_msg(), msg("session_pause")], msg("session_pause")),
    ([start_msg(), msg("session_pause")], msg("session_overrun", {"choice": "finish"})),
    ([start_msg(), msg("session_end")], msg("session_pause")),
    ([start_msg(), msg("session_end")], msg("session_resume")),
    ([start_msg(), msg("session_end")], msg("session_end")),
    ([], msg("session_start", {"budget_minutes": 0})),
    ([], msg("session_start", {"budget_minutes": -5})),
    ([], msg("session_start", {"budget_minutes": "25"})),
    ([], msg("session_start", {"budget_minutes": True})),
    ([], msg("session_start", {})),
])
def test_rejected_commands_change_nothing(tmp_path, setup, command):
    coord, clock, path = make(tmp_path)
    for step in setup:
        coord.handle(step)
        clock.advance(5)
    assert_rejected(coord, path, command)


def test_overrun_choice_other_than_finish_or_stop_is_rejected(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(1))
    clock.advance(60)
    coord.tick()
    assert_rejected(coord, path, msg("session_overrun", {"choice": "later"}))
    assert current(coord)["overrun_prompt"] is True


def test_pause_during_a_pause_keeps_the_original_reason_and_paused_time_out_of_the_timer(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(focus(window=False))
    assert_rejected(coord, path, msg("session_pause"))
    assert current(coord)["paused_reason"] == "focus"


# --- active time -------------------------------------------------------------

def test_paused_time_never_counts(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(60)
    assert current(coord)["active_seconds"] == 60
    coord.handle(msg("session_pause"))
    clock.advance(300)
    assert current(coord)["active_seconds"] == 60
    coord.handle(msg("session_resume"))
    clock.advance(30)
    assert current(coord)["active_seconds"] == 90
    coord.handle(focus(tab=False))
    clock.advance(1000)
    coord.handle(focus())
    clock.advance(10)
    assert current(coord)["active_seconds"] == 100
    coord.on_disconnect()
    clock.advance(1000)
    coord.on_connect()
    clock.advance(5)
    coord.handle(check(False, "x"))
    clock.advance(1000)
    coord.handle(check(True))
    clock.advance(5)
    s = state(coord.handle(msg("session_end")))
    assert s["active_seconds"] == 110
    assert of_type(path, "session_ended")[0][1]["active_seconds"] == 110


def test_ending_while_paused_does_not_count_the_paused_stretch(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(40)
    coord.handle(msg("session_pause"))
    clock.advance(500)
    s = state(coord.handle(msg("session_end")))
    assert s["state"] == "ended" and s["active_seconds"] == 40
    assert same_time(sessions(path)[0][2], at(540))
    assert of_type(path, "session_ended")[0][1]["active_seconds"] == 40


# --- timer budget and overrun -----------------------------------------------

def test_overrun_prompt_appears_exactly_once_and_only_through_tick(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(1))
    clock.advance(59)
    assert coord.tick() == []
    clock.advance(1)
    # handle() never raises the prompt; only tick() does
    assert current(coord)["overrun_prompt"] is False
    assert of_type(path, "overrun_prompted") == []
    out = coord.tick()
    assert len(out) == 1 and out[0]["type"] == "session_state"
    assert out[0]["overrun_prompt"] is True and out[0]["state"] == "active" and out[0]["active_seconds"] == 60
    prompted = of_type(path, "overrun_prompted")
    assert len(prompted) == 1 and same_time(prompted[0][3], at(60))
    clock.advance(30)
    assert coord.tick() == []
    assert len(of_type(path, "overrun_prompted")) == 1
    assert current(coord)["overrun_prompt"] is True


def test_tick_does_nothing_when_idle_ended_or_under_budget(tmp_path):
    coord, clock, path = make(tmp_path)
    assert coord.tick() == []
    coord.handle(start_msg(1))
    clock.advance(10)
    assert coord.tick() == []
    coord.handle(msg("session_end"))
    clock.advance(1000)
    assert coord.tick() == []
    assert "overrun_prompted" not in types(path)


def test_timer_cannot_run_out_while_paused(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(1))
    clock.advance(50)
    coord.handle(msg("session_pause"))
    clock.advance(1000)
    assert coord.tick() == []
    assert current(coord)["overrun_prompt"] is False
    coord.handle(msg("session_resume"))
    clock.advance(9)
    assert coord.tick() == []
    clock.advance(1)
    out = coord.tick()
    assert len(out) == 1 and out[0]["overrun_prompt"] is True and out[0]["active_seconds"] == 60


def test_overrun_finish_clears_prompt_keeps_going_and_is_not_asked_again(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(1))
    clock.advance(60)
    coord.tick()
    clock.advance(10)
    s = state(coord.handle(msg("session_overrun", {"choice": "finish"})))
    assert s["overrun_prompt"] is False and s["state"] == "active" and s["running"] is True
    choice = of_type(path, "overrun_choice")
    assert len(choice) == 1 and choice[0][1] == {"choice": "finish"} and same_time(choice[0][3], at(70))
    clock.advance(30)
    assert coord.tick() == []
    assert current(coord)["active_seconds"] == 100
    assert len(of_type(path, "overrun_prompted")) == 1
    s = state(coord.handle(msg("session_end")))
    ended = of_type(path, "session_ended")[0][1]
    assert ended["active_seconds"] == 100 and ended["overrun_seconds"] == 40
    assert_rejected(coord, path, msg("session_overrun", {"choice": "finish"}))


def test_overrun_stop_ends_the_session_with_overrun_seconds(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(2))
    clock.advance(120)
    coord.tick()
    clock.advance(25)
    s = state(coord.handle(msg("session_overrun", {"choice": "stop"})))
    assert s["state"] == "ended" and s["active_seconds"] == 145 and s["mode"] == "idle"
    assert [t for t in types(path) if t in ("overrun_prompted", "overrun_choice", "session_ended")] == ["overrun_prompted", "overrun_choice", "session_ended"]
    assert of_type(path, "overrun_choice")[0][1] == {"choice": "stop"}
    ended = of_type(path, "session_ended")[0]
    assert ended[1]["active_seconds"] == 145 and ended[1]["overrun_seconds"] == 25
    assert same_time(sessions(path)[0][2], at(145))


def test_overrun_seconds_is_zero_when_budget_not_exceeded(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(5))
    clock.advance(299)
    coord.handle(msg("session_end"))
    ended = of_type(path, "session_ended")[0][1]
    assert ended["active_seconds"] == 299 and ended["overrun_seconds"] == 0


def test_overrun_seconds_ignores_paused_time(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(1))
    clock.advance(70)
    coord.tick()
    coord.handle(msg("session_pause"))
    clock.advance(900)
    coord.handle(msg("session_end"))
    ended = of_type(path, "session_ended")[0][1]
    assert ended["active_seconds"] == 70 and ended["overrun_seconds"] == 10


# --- pause reasons -----------------------------------------------------------

def test_focus_pause_resumes_on_its_own_when_both_true(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(10)
    assert state(coord.handle(focus(tab=True, window=False)))["paused_reason"] == "focus"
    clock.advance(10)
    assert current(coord)["state"] == "paused"
    assert state(coord.handle(focus(tab=False, window=True)))["paused_reason"] == "focus"
    clock.advance(10)
    s = state(coord.handle(focus()))
    assert s["state"] == "active" and s["paused_reason"] is None and s["mode"] == "watching"
    assert of_type(path, "session_paused")[0][1] == {"reason": "focus"}
    assert of_type(path, "session_paused")[0][3] == of_type(path, "focus_changed")[0][3]
    resumed = of_type(path, "session_resumed")
    assert len(resumed) == 1 and resumed[0][1] == {"reason": "focus"} and same_time(resumed[0][3], at(30))
    assert [r[1] for r in of_type(path, "focus_changed")] == [
        {"tab_active": True, "window_focused": False},
        {"tab_active": False, "window_focused": True},
        {"tab_active": True, "window_focused": True},
    ]


def test_check_pause_keeps_message_and_resumes_when_the_check_passes(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    s = state(coord.handle(check(False, "Can't read the editor on this page")))
    assert s["state"] == "paused" and s["paused_reason"] == "check"
    assert s["check_message"] == "Can't read the editor on this page"
    assert state(coord.handle(check(False, "still broken")))["paused_reason"] == "check"
    s = state(coord.handle(check(True)))
    assert s["state"] == "active" and s["check_message"] is None
    assert of_type(path, "session_paused")[0][1] == {"reason": "check"}
    assert of_type(path, "session_resumed")[0][1] == {"reason": "check"}
    assert [r[1] for r in of_type(path, "reader_check")] == [
        {"ok": False, "message": "Can't read the editor on this page"},
        {"ok": False, "message": "still broken"},
        {"ok": True},
    ]


def test_button_pause_waits_for_the_button_whatever_else_happens(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(msg("session_pause"))
    for message in (focus(window=False), focus(), check(False, "x"), check(True)):
        s = state(coord.handle(message))
        assert (s["state"], s["paused_reason"]) == ("paused", "button")
    coord.on_disconnect()
    assert (current(coord)["state"], current(coord)["paused_reason"]) == ("paused", "button")
    coord.on_connect()
    assert (current(coord)["state"], current(coord)["paused_reason"]) == ("paused", "button")
    assert len(of_type(path, "session_paused")) == 1
    assert of_type(path, "session_resumed") == []
    assert current(coord)["check_message"] is None
    s = state(coord.handle(msg("session_resume")))
    assert s["state"] == "active"
    assert of_type(path, "session_resumed")[0][1] == {"reason": "button"}


def test_the_button_resumes_a_pause_of_every_reason(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    triggers = [
        (lambda: coord.handle(focus(tab=False)), "focus"),
        (lambda: coord.handle(check(False, "bad")), "check"),
        (lambda: coord.on_disconnect(), "disconnect"),
    ]
    for trigger, reason in triggers:
        trigger()
        assert current(coord)["paused_reason"] == reason
        s = state(coord.handle(msg("session_resume")))
        assert s["state"] == "active" and s["paused_reason"] is None and s["check_message"] is None
        assert of_type(path, "session_resumed")[-1][1] == {"reason": reason}


def test_focus_pause_is_not_changed_by_a_check_failure_or_check_ok(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(focus(tab=False))
    for message in (check(False, "bad"), check(True)):
        s = state(coord.handle(message))
        assert (s["state"], s["paused_reason"]) == ("paused", "focus")
    assert len(of_type(path, "session_paused")) == 1
    assert of_type(path, "session_resumed") == []


def test_check_pause_is_not_changed_by_focus_events(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(check(False, "bad"))
    for message in (focus(window=False), focus()):
        s = state(coord.handle(message))
        assert (s["state"], s["paused_reason"], s["check_message"]) == ("paused", "check", "bad")
    assert len(of_type(path, "session_paused")) == 1
    assert state(coord.handle(check(True)))["state"] == "active"


def test_disconnect_pause_and_connect_resume(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(10)
    out = coord.on_disconnect()
    assert out[0]["type"] == "session_state" and out[0]["paused_reason"] == "disconnect"
    for message in (focus(window=False), focus(), check(False, "x"), check(True)):
        coord.handle(message)
        assert current(coord)["paused_reason"] == "disconnect"
    clock.advance(10)
    out = coord.on_connect()
    assert out[0]["state"] == "active" and out[0]["paused_reason"] is None
    assert of_type(path, "session_paused")[0][1] == {"reason": "disconnect"}
    resumed = of_type(path, "session_resumed")
    assert len(resumed) == 1 and resumed[0][1] == {"reason": "disconnect"}
    assert current(coord)["active_seconds"] == 10


def test_disconnect_and_connect_are_noops_in_other_states(tmp_path):
    coord, clock, path = make(tmp_path)
    before = events(path)
    coord.on_disconnect()
    assert state(coord.on_connect())["state"] == "idle"
    assert events(path) == before
    coord.handle(start_msg())
    coord.on_connect()  # active: connecting changes nothing
    assert current(coord)["state"] == "active"
    coord.handle(focus(tab=False))
    coord.on_disconnect()  # focus pause: stays a focus pause
    assert current(coord)["paused_reason"] == "focus"
    coord.on_connect()
    assert current(coord)["paused_reason"] == "focus"
    coord.handle(msg("session_end"))
    before = (events(path), sessions(path))
    coord.on_disconnect()
    coord.on_connect()
    assert (events(path), sessions(path)) == before
    assert current(coord)["state"] == "ended"


def test_focus_and_checks_outside_a_session_change_nothing_and_are_not_stored(tmp_path):
    coord, clock, path = make(tmp_path)
    for message in (focus(tab=False), check(False, "x"), snapshot()):
        out = coord.handle(message)
        assert out[0]["type"] == "session_state" and out[0]["state"] == "idle"
    assert events(path) == []
    coord.handle(start_msg())
    coord.handle(msg("session_end"))
    count = len(events(path))
    for message in (focus(tab=False), check(False, "x"), snapshot(), opened(), submit()):
        out = coord.handle(message)
        assert out[0]["type"] == "session_state" and out[0]["state"] == "ended"
    assert len(events(path)) == count


# --- reader messages ---------------------------------------------------------

def test_active_reader_messages_are_stored_with_payload_ts_and_session(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    sent = [opened(), changed(), snapshot("x = 1"), run_result(), submit(accepted=False)]
    for i, message in enumerate(sent, start=1):
        clock.advance(3)
        out = coord.handle(message)
        assert out[0]["type"] == "session_state"
    rows = events(path)[1:]
    assert [r[0] for r in rows] == [m["type"] for m in sent]
    assert [r[1] for r in rows] == [m["payload"] for m in sent]
    assert all(same_time(r[3], at(3 * i)) for i, r in enumerate(rows, start=1))
    assert {r[2] for r in rows} == {1}


def test_paused_ignores_all_reader_messages_except_the_three_allowed(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(msg("session_pause"))
    before = events(path)
    for message in (opened(SLUG_B, TITLE_B), changed(), snapshot(), run_result()):
        out = coord.handle(message)
        assert out[0]["type"] == "session_state" and out[0]["state"] == "paused"
    assert events(path) == before
    assert current(coord)["problem"] == {"slug": SLUG_A, "title": TITLE_A}
    coord.handle(focus(tab=False))
    coord.handle(check(False, "x"))
    coord.handle(submit(accepted=False))
    assert types(path)[len(before):] == ["focus_changed", "reader_check", "submit_result"]


# --- problems and accepts ------------------------------------------------------

def test_switching_problems_stores_problem_left_with_accepted_flag(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened(SLUG_A, TITLE_A))
    assert not of_type(path, "problem_left")
    coord.handle(opened(SLUG_A, TITLE_A))  # same problem again: not a switch
    assert not of_type(path, "problem_left")
    clock.advance(20)
    s = state(coord.handle(opened(SLUG_B, TITLE_B)))
    assert s["problem"] == {"slug": SLUG_B, "title": TITLE_B} and s["accepted"] is False
    left = of_type(path, "problem_left")
    assert len(left) == 1 and left[0][1] == {"slug": SLUG_A, "accepted": False} and same_time(left[0][3], at(20))
    coord.handle(submit(SLUG_B, True))
    assert current(coord)["accepted"] is True
    s = state(coord.handle(opened(SLUG_A, TITLE_A)))
    assert s["accepted"] is False
    left = of_type(path, "problem_left")
    assert [r[1] for r in left] == [{"slug": SLUG_A, "accepted": False}, {"slug": SLUG_B, "accepted": True}]


def test_problem_left_without_accept_is_unfinished_with_no_grade_events(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(submit(accepted=False))
    coord.handle(opened(SLUG_B, TITLE_B))
    coord.handle(msg("session_end"))
    allowed = {"session_started", "problem_opened", "submit_result", "problem_left", "session_ended"}
    assert set(types(path)) <= allowed
    assert [r[1] for r in of_type(path, "problem_left")] == [
        {"slug": SLUG_A, "accepted": False}, {"slug": SLUG_B, "accepted": False}]
    assert of_type(path, "accept_counted") == []
    with open_store(path) as db:
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_ending_mid_problem_stores_problem_left_before_session_ended(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    clock.advance(10)
    coord.handle(msg("session_end"))
    assert types(path) == ["session_started", "problem_opened", "problem_left", "session_ended"]
    assert of_type(path, "problem_left")[0][1] == {"slug": SLUG_A, "accepted": False}


def test_ending_after_an_accept_reports_it_in_problem_left(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(submit())
    coord.handle(msg("session_end"))
    assert types(path) == ["session_started", "problem_opened", "submit_result", "accept_counted", "problem_left", "session_ended"]
    assert of_type(path, "problem_left")[0][1] == {"slug": SLUG_A, "accepted": True}


def test_accept_while_active_counts_immediately(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    clock.advance(42)
    s = state(coord.handle(submit()))
    assert s["accepted"] is True
    assert types(path) == ["session_started", "problem_opened", "submit_result", "accept_counted"]
    counted = of_type(path, "accept_counted")[0]
    assert counted[1] == {"slug": SLUG_A} and same_time(counted[3], at(42))
    assert of_type(path, "submit_result")[0][1] == submit()["payload"]


def test_rejected_submission_while_active_is_stored_but_not_counted(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    s = state(coord.handle(submit(accepted=False)))
    assert s["accepted"] is False
    assert types(path) == ["session_started", "problem_opened", "submit_result"]


def test_accept_seen_while_paused_counts_only_on_resume(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(focus(tab=False))
    clock.advance(15)
    s = state(coord.handle(submit()))
    assert s["state"] == "paused" and s["accepted"] is False
    assert len(of_type(path, "submit_result")) == 1 and of_type(path, "accept_counted") == []
    clock.advance(15)
    s = state(coord.handle(focus()))
    assert s["state"] == "active" and s["accepted"] is True
    counted = of_type(path, "accept_counted")
    assert len(counted) == 1 and counted[0][1] == {"slug": SLUG_A} and same_time(counted[0][3], at(30))
    assert "session_resumed" in types(path)


def test_accept_seen_while_button_paused_counts_when_the_button_resumes(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(msg("session_pause"))
    coord.handle(submit())
    assert of_type(path, "accept_counted") == []
    clock.advance(10)
    s = state(coord.handle(msg("session_resume")))
    assert s["accepted"] is True and len(of_type(path, "accept_counted")) == 1


def test_accept_seen_while_paused_is_never_counted_if_the_session_ends_paused(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(msg("session_pause"))
    coord.handle(submit())
    coord.handle(msg("session_end"))
    assert of_type(path, "accept_counted") == []
    assert len(of_type(path, "submit_result")) == 1
    assert of_type(path, "problem_left")[0][1] == {"slug": SLUG_A, "accepted": False}
    assert current(coord)["accepted"] is False


# --- exact event order -------------------------------------------------------

def test_full_session_event_sequence(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(1))
    coord.handle(opened())
    clock.advance(30)
    coord.handle(msg("session_pause"))
    clock.advance(30)
    coord.handle(msg("session_resume"))
    clock.advance(30)
    coord.handle(submit())
    clock.advance(30)
    coord.tick()
    coord.handle(msg("session_overrun", {"choice": "finish"}))
    clock.advance(5)
    coord.handle(opened(SLUG_B, TITLE_B))
    coord.handle(msg("session_end"))
    assert types(path) == [
        "session_started", "problem_opened", "session_paused", "session_resumed", "submit_result",
        "accept_counted", "overrun_prompted", "overrun_choice", "problem_opened", "problem_left",
        "problem_left", "session_ended",
    ] or types(path) == [
        "session_started", "problem_opened", "session_paused", "session_resumed", "submit_result",
        "accept_counted", "overrun_prompted", "overrun_choice", "problem_left", "problem_opened",
        "problem_left", "session_ended",
    ]
    ended = of_type(path, "session_ended")[0][1]
    assert ended["active_seconds"] == 95 and ended["overrun_seconds"] == 35


# --- restart -----------------------------------------------------------------

def test_restart_closes_an_unfinished_session_as_interrupted(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg(25))
    coord.handle(opened())
    clock.advance(120)
    coord.handle(snapshot())
    clock.advance(600)  # the companion dies here
    del coord
    clock.advance(3600)
    fresh = SessionCoordinator(path, clock)
    (sid, started, ended, budget), = sessions(path)
    assert same_time(ended, at(120))
    interrupted = of_type(path, "session_interrupted")
    assert len(interrupted) == 1 and interrupted[0][2] == sid
    assert types(path)[-1] == "session_interrupted"
    s = current(fresh)
    assert s["state"] == "idle" and s["active_seconds"] == 0 and s["problem"] is None and s["budget_minutes"] is None


def test_restart_with_no_events_after_start_uses_started_at(tmp_path):
    clock = FakeClock()
    path = tmp_path / "companion.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO sessions (started_at, budget_minutes) VALUES (?, 10)", (at(5).isoformat(),))
    SessionCoordinator(path, clock)
    (_, _, ended, _), = sessions(path)
    assert same_time(ended, at(5))
    assert len(of_type(path, "session_interrupted")) == 1


def test_restart_leaves_finished_sessions_alone_and_a_new_session_can_start(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    clock.advance(10)
    coord.handle(msg("session_end"))
    before = (events(path), sessions(path))
    fresh = SessionCoordinator(path, clock)
    assert (events(path), sessions(path)) == before
    assert current(fresh)["state"] == "idle"
    assert state(fresh.handle(start_msg(5)))["state"] == "active"
    assert [r[0] for r in sessions(path)] == [1, 2]


def test_restart_twice_does_not_interrupt_again(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    SessionCoordinator(path, clock)
    SessionCoordinator(path, clock)
    assert len(of_type(path, "session_interrupted")) == 1


# --- wire --------------------------------------------------------------------

async def next_state(ws, wanted=None, timeout=2):
    while True:
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if reply["type"] == "session_state" and (wanted is None or reply["state"] == wanted):
            return reply


def ws_message(kind, payload=None):
    message = {"type": kind, "ts": "2026-10-15T00:00:00Z"}
    if payload is not None:
        message["payload"] = payload
    return json.dumps(message)


def test_session_messages_are_accepted_and_answered_with_session_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "wire.sqlite3"))

    async def check_wire():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(ws_message("session_get_state"))
                assert (await next_state(ws))["state"] == "idle"
                await ws.send(ws_message("session_start", {"budget_minutes": 25, "extra": "ignored"}))
                s = await next_state(ws)
                assert (s["state"], s["budget_minutes"], s["mode"]) == ("active", 25, "watching")
                await ws.send(ws_message("session_pause"))
                assert (await next_state(ws))["paused_reason"] == "button"
                await ws.send(ws_message("session_resume"))
                assert (await next_state(ws))["state"] == "active"
                await ws.send(ws_message("reader_check", {"ok": False, "message": "Can't read the editor on this page"}))
                s = await next_state(ws)
                assert (s["paused_reason"], s["check_message"]) == ("check", "Can't read the editor on this page")
                await ws.send(ws_message("reader_check", {"ok": True}))
                assert (await next_state(ws))["state"] == "active"
                await ws.send(ws_message("session_end"))
                assert (await next_state(ws))["state"] == "ended"

    asyncio.run(check_wire())


@pytest.mark.parametrize("kind, payload", [
    ("session_start", {"budget_minutes": 0}),
    ("session_start", {"budget_minutes": "25"}),
    ("session_start", {"budget_minutes": True}),
    ("session_start", {}),
    ("session_start", None),
    ("session_overrun", {"choice": "later"}),
    ("session_overrun", {}),
    ("reader_check", {"ok": "yes"}),
    ("reader_check", {}),
    ("reader_check", {"ok": False, "message": 5}),
])
def test_malformed_session_messages_get_a_visible_error(tmp_path, monkeypatch, kind, payload):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "wire.sqlite3"))

    async def check_wire():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(ws_message(kind, payload))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert reply["type"] == "error"
                assert kind in reply["message"]
                assert "ts" in reply

    asyncio.run(check_wire())
    assert not (tmp_path / "wire.sqlite3").exists() or of_type(tmp_path / "wire.sqlite3", "session_started") == []


def test_a_command_the_state_machine_rejects_gets_a_visible_error_over_the_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "wire.sqlite3"))

    async def check_wire():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(ws_message("session_pause"))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert reply["type"] == "error" and reply["message"]

    asyncio.run(check_wire())


def test_hello_pushes_session_state_and_a_second_connection_receives_pushes(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "wire.sqlite3"))

    async def check_wire():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as first, \
                    connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as second:
                for ws in (first, second):
                    await ws.send(ws_message("hello"))
                    assert (await next_state(ws, "idle"))["mode"] == "idle"
                await first.send(ws_message("session_start", {"budget_minutes": 10}))
                for ws in (first, second):
                    s = await next_state(ws, "active")
                    assert s["budget_minutes"] == 10
                await second.send(ws_message("session_pause"))
                for ws in (first, second):
                    assert (await next_state(ws, "paused"))["paused_reason"] == "button"

    asyncio.run(check_wire())
