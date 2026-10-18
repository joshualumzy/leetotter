"""Per-problem time cap, time-up card and debrief (#50; SPEC §2, §5, §8): the rules only, driven through the
session coordinator with a fake clock. Socket wiring, the panel and the Tutor's debrief prompt are tested elsewhere.

The smallest public interface these tests need (beyond what the coordinator already has):

- `companion.session.TIME_CAP_MINUTES == {"Easy": 20, "Medium": 35, "Hard": 50}` and
  `companion.session.EXTENSION_MINUTES == 10`, constants in code, read from the catalog's difficulty.
- `session_state` gains, for the current problem (all None without one):
    `cap_minutes`       the problem's cap (20, 35 or 50);
    `cap_seconds_left`  active seconds left on the cap (plus the extension once taken), never below 0;
    `time_up`           None, or `{"can_extend": bool}` while the time-up card shows;
  and `mode` is "debrief" while the debrief runs.
  The cap counts the problem's active time within the session: paused time, time between problems and time on
  another problem don't count, and leaving the problem and opening it again later in the session doesn't restart it.
- `tick()` shows the time-up card once the cap (or the extension) has run out; it fires once per problem per
  session and never after an accept. An accept counted while the card shows clears the card.
- A new message `{"type": "session_time_up", "payload": {"choice": "extend" | "debrief"}}`: `extend` (only once)
  hides the card for EXTENSION_MINUTES more active minutes, after which the card offers the debrief alone;
  `debrief` writes the Again at once (`grading.write_missed`, no Grader, not held for a reference) and enters the
  debrief. Anything else, or either choice with no card showing, is an `error` that changes nothing.
- `session_done` is also accepted in the debrief without an accept; `session_skip` and `session_end` work as before
  at the card and in the debrief and never write or remove a grade.
- `SessionCoordinator.debrief_timeline() -> list[dict]`: the current problem's compact timeline, built by code, for
  the Tutor's debrief. Oldest first; each entry has `kind` and `at` (int seconds since the problem was first opened
  in this session). Runs and submits (`kind` "run" / "submit") carry `passed` and `total`; full code (`code`) appears
  only for the first real attempt (the code at the first run or submit) and the final code; runs between carry a
  short `diff`; help presses and chat messages appear in gist. Over 10 entries all runs and submits are kept and the
  rest merged, so the list stays at 10.
- No nudge, chime, quiet-time or Observer code remains in the companion.
"""

import json
import re
from pathlib import Path

import pytest

from companion import session as session_module
from companion.grading import grade_pending
from companion.store import open_store
from tests.test_grading import FakeGraders, grades, put_reference, reply
from tests.test_session import make, msg, opened, reopen, snapshot, start_msg, submit

EASY, EASY_TITLE = "duplicate-integer", "Contains Duplicate"            # catalog: Easy, Arrays & Hashing
MEDIUM, MEDIUM_TITLE = "daily-temperatures", "Daily Temperatures"       # catalog: Medium, Stack
HARD, HARD_TITLE = "trapping-rain-water", "Trapping Rain Water"         # catalog: Hard, Two Pointers
OTHER, OTHER_TITLE = "longest-substring-without-duplicates", "Longest Substring Without Repeating Characters"  # Medium
MINUTE = 60


# --- helpers -------------------------------------------------------------------------

def get(coord):
    out = coord.handle(msg("session_get_state"))
    assert out[0]["type"] == "session_state"
    return out[0]


def started(tmp_path, slug=MEDIUM, title=MEDIUM_TITLE):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened(slug, title))
    return coord, clock, path


def work(coord, clock, seconds):
    """Active time passes on the current problem; tick runs as the socket's timer would."""
    clock.advance(seconds)
    coord.tick()


def choose(coord, choice):
    return coord.handle(msg("session_time_up", {"choice": choice}))


def reviews_of(path):
    with open_store(path) as db:
        return db.execute("SELECT item_type, item_key, grade FROM reviews ORDER BY id").fetchall()


def left(path):
    with open_store(path) as db:
        return [json.loads(row[0]) for row in
                db.execute("SELECT payload FROM events WHERE type = 'problem_left' ORDER BY id")]


def run_msg(slug, passed, total):
    return msg("run_result", {"slug": slug, "cases": [
        {"passed": i < passed, "error_type": None if i < passed else "wrong_answer", "error_message": "",
         "runtime": "0.03", "failing_case": None} for i in range(total)]})


def failed_submit(slug, passed, total):
    return msg("submit_result", {"slug": slug, "accepted": False, "error_type": "wrong_answer", "error_message": "",
                                 "runtime": "0.03", "memory": 14000, "passed": passed, "total": total,
                                 "failing_case": None})


def chat(coord, text, kind="question"):
    out, turn = coord.begin_turn("chat", text)
    assert turn is not None, out
    coord.finish_turn(turn, reply(kind), "x-test-2")


def press(coord, kind="question"):
    out, turn = coord.begin_turn("help")
    assert turn is not None, out
    coord.finish_turn(turn, reply(kind), "x-test-2")


def at_card(tmp_path, slug=MEDIUM, title=MEDIUM_TITLE, cap=35):
    coord, clock, path = started(tmp_path, slug, title)
    work(coord, clock, cap * MINUTE)
    assert get(coord)["time_up"] == {"can_extend": True}
    return coord, clock, path


# --- the cap -------------------------------------------------------------------------

def test_the_caps_and_the_extension_are_the_spec_constants():
    assert session_module.TIME_CAP_MINUTES == {"Easy": 20, "Medium": 35, "Hard": 50}
    assert session_module.EXTENSION_MINUTES == 10


@pytest.mark.parametrize("slug,title,cap", [(EASY, EASY_TITLE, 20), (MEDIUM, MEDIUM_TITLE, 35), (HARD, HARD_TITLE, 50)])
def test_the_card_shows_exactly_when_the_problems_cap_runs_out(tmp_path, slug, title, cap):
    coord, clock, _ = started(tmp_path, slug, title)
    state = get(coord)
    assert (state["cap_minutes"], state["cap_seconds_left"], state["time_up"]) == (cap, cap * MINUTE, None)
    work(coord, clock, cap * MINUTE - 1)
    state = get(coord)
    assert (state["cap_seconds_left"], state["time_up"]) == (1, None)
    work(coord, clock, 1)
    state = get(coord)
    assert (state["cap_seconds_left"], state["time_up"]) == (0, {"can_extend": True})


def test_there_is_no_cap_without_a_problem(tmp_path):
    coord, clock, _ = make(tmp_path)
    coord.handle(start_msg())
    work(coord, clock, 120 * MINUTE)
    state = get(coord)
    assert (state["cap_minutes"], state["cap_seconds_left"], state["time_up"]) == (None, None, None)


@pytest.mark.parametrize("pause", [msg("session_pause"), msg("focus_changed", {"tab_active": False, "window_focused": True})])
def test_paused_time_does_not_count_toward_the_cap(tmp_path, pause):
    coord, clock, _ = started(tmp_path)
    work(coord, clock, 10 * MINUTE)
    coord.handle(pause)
    work(coord, clock, 60 * MINUTE)
    assert get(coord)["time_up"] is None
    coord.handle(msg("session_resume") if pause["type"] == "session_pause"
                 else msg("focus_changed", {"tab_active": True, "window_focused": True}))
    assert get(coord)["cap_seconds_left"] == 25 * MINUTE
    work(coord, clock, 25 * MINUTE - 1)
    assert get(coord)["time_up"] is None
    work(coord, clock, 1)
    assert get(coord)["time_up"] == {"can_extend": True}


def test_leaving_and_reopening_the_problem_does_not_restart_its_cap(tmp_path):
    coord, clock, _ = started(tmp_path)
    work(coord, clock, 20 * MINUTE)
    coord.handle(msg("session_skip"))
    work(coord, clock, 5 * MINUTE)                  # between problems: not the problem's time
    coord.handle(opened(OTHER, OTHER_TITLE))
    work(coord, clock, 30 * MINUTE)                 # on another problem: not this one's time
    assert get(coord)["time_up"] is None
    coord.handle(opened(MEDIUM, MEDIUM_TITLE))
    state = get(coord)
    assert (state["cap_minutes"], state["cap_seconds_left"]) == (35, 15 * MINUTE)
    work(coord, clock, 15 * MINUTE - 1)
    assert get(coord)["time_up"] is None
    work(coord, clock, 1)
    assert get(coord)["time_up"] == {"can_extend": True}


def test_each_problem_has_its_own_cap(tmp_path):
    coord, clock, _ = started(tmp_path, MEDIUM, MEDIUM_TITLE)
    work(coord, clock, 30 * MINUTE)
    coord.handle(opened(EASY, EASY_TITLE))          # off-plan: the Medium's 30 minutes don't carry over
    state = get(coord)
    assert (state["cap_minutes"], state["cap_seconds_left"], state["time_up"]) == (20, 20 * MINUTE, None)


def test_the_cap_fires_once_per_problem_in_a_session(tmp_path):
    coord, clock, path = at_card(tmp_path)
    coord.handle(msg("session_skip"))
    coord.handle(opened(MEDIUM, MEDIUM_TITLE))
    work(coord, clock, 60 * MINUTE)
    assert get(coord)["time_up"] is None
    assert reviews_of(path) == []


def test_a_new_session_gives_the_problem_a_fresh_cap(tmp_path):
    coord, clock, _ = started(tmp_path)
    work(coord, clock, 30 * MINUTE)
    coord.handle(msg("session_end"))
    coord.handle(start_msg())
    coord.handle(opened(MEDIUM, MEDIUM_TITLE))
    assert get(coord)["cap_seconds_left"] == 35 * MINUTE


def test_a_restart_keeps_the_problems_active_time_toward_its_cap(tmp_path):
    coord, clock, path = started(tmp_path)
    clock.advance(30 * MINUTE)
    coord.handle(snapshot())                        # last recorded moment before the companion dies
    del coord
    fresh = reopen(path, clock, 3600)
    assert get(fresh)["cap_seconds_left"] == 5 * MINUTE
    fresh.handle(msg("session_resume"))
    work(fresh, clock, 5 * MINUTE - 1)
    assert get(fresh)["time_up"] is None
    work(fresh, clock, 1)
    assert get(fresh)["time_up"] == {"can_extend": True}


def test_a_restart_keeps_a_card_that_was_showing(tmp_path):
    coord, clock, path = at_card(tmp_path)
    del coord
    fresh = reopen(path, clock, 600)
    assert get(fresh)["time_up"] == {"can_extend": True}


def test_the_card_never_appears_after_an_accept(tmp_path):
    coord, clock, path = started(tmp_path)
    work(coord, clock, 10 * MINUTE)
    coord.handle(submit(MEDIUM, True))
    work(coord, clock, 90 * MINUTE)                 # still improving the solution
    assert get(coord)["time_up"] is None
    assert choose(coord, "debrief")[0]["type"] == "error"
    assert reviews_of(path) == []


# --- the time-up card ------------------------------------------------------------------

def test_the_extension_is_ten_active_minutes_once_then_the_debrief_is_offered_again(tmp_path):
    coord, clock, _ = at_card(tmp_path)
    state = choose(coord, "extend")[0]
    assert state["type"] == "session_state"
    assert (state["time_up"], state["cap_seconds_left"], state["mode"]) == (None, 10 * MINUTE, "watching")
    coord.handle(msg("session_pause"))
    work(coord, clock, 30 * MINUTE)                 # paused: the extension doesn't run
    coord.handle(msg("session_resume"))
    work(coord, clock, 10 * MINUTE - 1)
    assert get(coord)["time_up"] is None
    work(coord, clock, 1)
    assert get(coord)["time_up"] == {"can_extend": False}
    assert choose(coord, "extend")[0]["type"] == "error"
    assert get(coord)["time_up"] == {"can_extend": False}


@pytest.mark.parametrize("payload", [None, {}, {"choice": "finish"}, {"choice": "stop"}, {"choice": "more"}])
def test_a_bad_time_up_choice_is_rejected_and_changes_nothing(tmp_path, payload):
    coord, _, path = at_card(tmp_path)
    before = get(coord)
    out = coord.handle(msg("session_time_up", payload))
    assert len(out) == 1 and out[0]["type"] == "error"
    after = get(coord)
    assert {k: v for k, v in after.items() if k != "ts"} == {k: v for k, v in before.items() if k != "ts"}
    assert reviews_of(path) == []


@pytest.mark.parametrize("choice", ["extend", "debrief"])
def test_a_time_up_choice_without_the_card_is_rejected(tmp_path, choice):
    coord, clock, path = started(tmp_path)
    work(coord, clock, 10 * MINUTE)
    assert choose(coord, choice)[0]["type"] == "error"
    assert get(coord)["mode"] == "watching"
    assert reviews_of(path) == []


# --- grades at the card and after it -------------------------------------------------------

def test_choosing_the_debrief_writes_the_again_at_once_with_no_reference(tmp_path):
    coord, _, path = at_card(tmp_path)               # no reference written yet: the Again is not held for it
    state = choose(coord, "debrief")[0]
    assert state["mode"] == "debrief" and state["time_up"] is None
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


def test_choosing_the_debrief_after_the_extension_writes_the_again(tmp_path):
    coord, clock, path = at_card(tmp_path)
    choose(coord, "extend")
    work(coord, clock, 10 * MINUTE)
    choose(coord, "debrief")
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


def test_nothing_after_the_debrief_is_chosen_changes_the_grade(tmp_path):
    coord, clock, path = at_card(tmp_path)
    put_reference(path, slug=MEDIUM)
    choose(coord, "debrief")
    chat(coord, "Where did I go wrong?", "full_solution")
    coord.handle(snapshot("def dailyTemperatures(t):\n    return solved"))
    coord.handle(submit(MEDIUM, True))              # the solution typed in afterwards
    coord.handle(msg("session_done"))
    fake = FakeGraders()
    grade_pending(fake, db_path=path)
    assert fake.calls == []
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


def test_done_leaves_the_debrief_without_an_accept(tmp_path):
    coord, _, path = at_card(tmp_path)
    choose(coord, "debrief")
    state = coord.handle(msg("session_done"))[0]
    assert state["type"] == "session_state" and state["problem"] is None and state["mode"] == "watching"
    assert left(path) == [{"slug": MEDIUM, "accepted": False}]
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


def test_skipping_in_the_debrief_keeps_the_again(tmp_path):
    coord, _, path = at_card(tmp_path)
    choose(coord, "debrief")
    coord.handle(msg("session_skip"))
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


def test_ending_in_the_debrief_keeps_the_again(tmp_path):
    coord, _, path = at_card(tmp_path)
    choose(coord, "debrief")
    coord.handle(msg("session_end"))
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


@pytest.mark.parametrize("leave", ["session_skip", "session_end"])
@pytest.mark.parametrize("extended", [False, True])
def test_skipping_or_ending_at_the_card_leaves_the_problem_unfinished(tmp_path, leave, extended):
    coord, clock, path = at_card(tmp_path)
    put_reference(path, slug=MEDIUM)
    if extended:
        choose(coord, "extend")
        work(coord, clock, 10 * MINUTE)
        assert get(coord)["time_up"] == {"can_extend": False}
    coord.handle(msg(leave))
    assert left(path) == [{"slug": MEDIUM, "accepted": False}]
    fake = FakeGraders()
    grade_pending(fake, db_path=path)
    assert fake.calls == [] and reviews_of(path) == []


def test_skipping_before_the_cap_gives_no_grade(tmp_path):
    coord, clock, path = started(tmp_path)
    press(coord, "snippet")
    work(coord, clock, 34 * MINUTE)                 # just under the cap: intended to avoid an Again
    coord.handle(msg("session_skip"))
    assert reviews_of(path) == []


def test_skipping_at_the_card_never_removes_a_full_solution_again(tmp_path):
    coord, clock, path = started(tmp_path)
    chat(coord, "Just show me the solution", "full_solution")
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]
    work(coord, clock, 35 * MINUTE)
    assert get(coord)["time_up"] == {"can_extend": True}
    coord.handle(msg("session_skip"))
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


def test_the_debrief_after_a_full_solution_writes_no_second_again(tmp_path):
    coord, clock, path = started(tmp_path)
    chat(coord, "Just show me the solution", "full_solution")
    work(coord, clock, 35 * MINUTE)
    assert choose(coord, "debrief")[0]["mode"] == "debrief"
    assert reviews_of(path) == [("problem", MEDIUM, "again"), ("category:solve", "Stack", "again")]


@pytest.mark.parametrize("extended", [False, True])
def test_an_accept_while_the_card_shows_is_graded_normally(tmp_path, extended):
    coord, clock, path = at_card(tmp_path)
    put_reference(path, slug=MEDIUM)
    if extended:
        choose(coord, "extend")
        work(coord, clock, 10 * MINUTE)
    coord.handle(snapshot("def dailyTemperatures(t):\n    return solved"))
    state = coord.handle(submit(MEDIUM, True))[0]
    assert state["accepted"] is True and state["time_up"] is None
    assert choose(coord, "debrief")[0]["type"] == "error"
    coord.handle(msg("session_done"))
    assert reviews_of(path) == []
    fake = FakeGraders()
    grade_pending(fake, db_path=path)
    assert len(fake.calls) == 2
    assert reviews_of(path) == [("problem", MEDIUM, "good"), ("category:solve", "Stack", "good")]


def test_an_accept_during_the_extension_is_graded_normally(tmp_path):
    coord, clock, path = at_card(tmp_path)
    put_reference(path, slug=MEDIUM)
    choose(coord, "extend")
    work(coord, clock, 5 * MINUTE)
    coord.handle(submit(MEDIUM, True))
    work(coord, clock, 30 * MINUTE)
    assert get(coord)["time_up"] is None            # never after an accept
    coord.handle(msg("session_done"))
    grade_pending(FakeGraders(), db_path=path)
    assert reviews_of(path) == [("problem", MEDIUM, "good"), ("category:solve", "Stack", "good")]


# --- the debrief timeline ---------------------------------------------------------------

CODES = ["def dailyTemperatures(t):\n    return []",
         "def dailyTemperatures(t):\n    return [0] * len(t)",
         "def dailyTemperatures(t):\n    stack = []\n    return [0] * len(t)",
         "def dailyTemperatures(t):\n    stack = []\n    answer = [0] * len(t)\n    return answer"]


def test_the_timeline_has_runs_and_submits_with_results_help_and_chat_with_times(tmp_path):
    coord, clock, _ = started(tmp_path)
    clock.advance(2 * MINUTE)
    coord.handle(snapshot(CODES[0]))
    coord.handle(run_msg(MEDIUM, 1, 3))
    clock.advance(9 * MINUTE)                       # a long quiet stretch must show
    press(coord)
    clock.advance(MINUTE)
    chat(coord, "is a stack useful here")
    clock.advance(MINUTE)
    coord.handle(snapshot(CODES[1]))
    coord.handle(failed_submit(MEDIUM, 18, 20))
    timeline = coord.debrief_timeline()
    assert len(timeline) <= 10
    assert all(type(entry["at"]) is int for entry in timeline)
    assert [entry["at"] for entry in timeline] == sorted(entry["at"] for entry in timeline)
    run, = [e for e in timeline if e["kind"] == "run"]
    submitted, = [e for e in timeline if e["kind"] == "submit"]
    assert (run["passed"], run["total"], run["at"]) == (1, 3, 2 * MINUTE)
    assert (submitted["passed"], submitted["total"], submitted["at"]) == (18, 20, 13 * MINUTE)
    after_gap = [e for e in timeline if e["at"] > run["at"]]
    assert after_gap and after_gap[0]["at"] - run["at"] >= 9 * MINUTE
    assert "is a stack useful here" in json.dumps(timeline)
    assert len(timeline) >= 4                       # run, press, chat, submit


def test_full_code_only_for_the_first_real_attempt_and_the_final_code_with_diffs_between(tmp_path):
    coord, clock, _ = started(tmp_path)
    coord.handle(snapshot("class Solution:\n    pass"))    # the starter code, never run
    for code in CODES[:3]:
        clock.advance(2 * MINUTE)
        coord.handle(snapshot(code))
        coord.handle(run_msg(MEDIUM, 0, 3))
    clock.advance(2 * MINUTE)
    coord.handle(snapshot(CODES[3]))                # the final code, never run
    timeline = coord.debrief_timeline()
    assert [e["code"] for e in timeline if "code" in e] == [CODES[0], CODES[3]]
    runs = [e for e in timeline if e["kind"] == "run"]
    assert len(runs) == 3
    assert "[0] * len(t)" in runs[1]["diff"] and "stack = []" in runs[2]["diff"]
    assert "class Solution" not in json.dumps(timeline)


def test_over_ten_entries_every_run_and_submit_is_kept_and_the_rest_merged(tmp_path):
    coord, clock, _ = started(tmp_path)
    texts = [f"question number {n}" for n in range(8)]
    for n in range(3):
        clock.advance(MINUTE)
        coord.handle(snapshot(CODES[n]))
        coord.handle(run_msg(MEDIUM, n, 3))
        press(coord)
        chat(coord, texts[2 * n])
        chat(coord, texts[2 * n + 1])
    for n in range(2):
        clock.advance(MINUTE)
        coord.handle(snapshot(CODES[3]))
        coord.handle(failed_submit(MEDIUM, 15 + n, 20))
        chat(coord, texts[6 + n])
    timeline = coord.debrief_timeline()                  # 3 runs, 2 submits, 3 presses, 8 chats: 16 before merging
    assert len(timeline) <= 10
    assert [(e["passed"], e["total"]) for e in timeline if e["kind"] == "run"] == [(0, 3), (1, 3), (2, 3)]
    assert [(e["passed"], e["total"]) for e in timeline if e["kind"] == "submit"] == [(15, 20), (16, 20)]
    dumped = json.dumps(timeline)
    assert all(text in dumped for text in texts)
    assert [e["code"] for e in timeline if "code" in e] == [CODES[0], CODES[3]]
    assert [e["at"] for e in timeline] == sorted(e["at"] for e in timeline)


def test_the_timeline_covers_this_problem_in_this_session_only(tmp_path):
    coord, clock, _ = started(tmp_path)
    clock.advance(MINUTE)
    coord.handle(snapshot(CODES[0]))
    coord.handle(run_msg(MEDIUM, 1, 3))
    coord.handle(msg("session_skip"))
    coord.handle(opened(OTHER, OTHER_TITLE))
    coord.handle(snapshot("def lengthOfLongestSubstring(s):\n    return 0"))
    coord.handle(run_msg(OTHER, 2, 4))
    chat(coord, "something about substrings")
    coord.handle(opened(MEDIUM, MEDIUM_TITLE))
    clock.advance(MINUTE)
    coord.handle(snapshot(CODES[1]))
    coord.handle(run_msg(MEDIUM, 2, 3))
    timeline = coord.debrief_timeline()
    assert [(e["passed"], e["total"]) for e in timeline if e["kind"] == "run"] == [(1, 3), (2, 3)]
    dumped = json.dumps(timeline)
    assert "substrings" not in dumped and "lengthOfLongestSubstring" not in dumped


# --- no nudges left -----------------------------------------------------------------------

def test_no_nudge_chime_quiet_time_or_observer_code_remains():
    root = Path(session_module.__file__).resolve().parent
    files = sorted(root.rglob("*.py")) + [root.parent / "roles.toml"]
    banned = re.compile(r"nudge|chime|quiet[ _-]?time|stuck[ _-]?episode|observer", re.IGNORECASE)
    found = [f"{file.name}:{number}: {line.strip()}" for file in files
             for number, line in enumerate(file.read_text().splitlines(), 1) if banned.search(line)]
    assert found == []
