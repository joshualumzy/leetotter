"""Tutor turns in the session coordinator: refusals, press counts, logging, code and statement tracking, restarts."""

import json

import pytest

from companion.model import TutorReply
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_session import SLUG_A, SLUG_B, TITLE_B, events, make, msg, of_type, opened, snapshot, start_msg

STATEMENT = "Given an integer array nums, return true if any value appears more than once."


def reply(**changes):
    return TutorReply.model_validate({"gap": "stuck_optimising", "gap_note": None, "help_kind": "question",
                                      "reply_text": "What could you remember as you go?", "follow_up_question": None, **changes})


def statement(slug=SLUG_A, text=STATEMENT):
    return msg("problem_statement", {"slug": slug, "statement": text})


def diff(start_line, removed, added):
    return msg("code_changed", {"diff": {"start_line": start_line, "removed": removed, "added": added}})


def context(turn):
    return turn.messages[-1]["content"]


def active(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    return coord, clock, path


def begin(coord, kind="help", text=None):
    out, turn = coord.begin_turn(kind, text)
    assert turn is not None, out
    return out, turn


# --- starting a turn -------------------------------------------------------------

def test_a_press_starts_a_turn_logs_the_request_and_names_its_press(tmp_path):
    coord, clock, path = active(tmp_path)
    out, turn = begin(coord)
    request, = of_type(path, "help_request")
    assert request[1] == {"slug": SLUG_A, "press_index": 1}
    turn_id = turn.turn_id
    assert out == [{"type": "tutor_start", "ts": out[0]["ts"], "turn_id": turn_id, "request": "help", "press_index": 1}]
    with open_store(path) as db:
        assert db.execute("SELECT type FROM events WHERE id = ?", (turn_id,)).fetchone() == ("help_request",)
    assert "first time on this problem" in context(turn)


def test_a_chat_message_is_logged_with_its_text_and_quoted_to_the_tutor(tmp_path):
    coord, _, path = active(tmp_path)
    out, turn = begin(coord, "chat", "why does [1,1] fail?")
    assert of_type(path, "chat_message")[0][1] == {"slug": SLUG_A, "text": "why does [1,1] fail?"}
    assert out[0]["request"] == "chat" and out[0]["press_index"] is None
    assert "why does [1,1] fail?" in context(turn)


@pytest.mark.parametrize("setup, words", [
    (lambda c: None, "only during a session"),
    (lambda c: c.handle(start_msg()), "Open a problem"),
    (lambda c: (c.handle(start_msg()), c.handle(opened()), c.handle(msg("session_pause"))), "paused"),
    (lambda c: (c.handle(start_msg()), c.handle(opened()), c.handle(msg("session_end"))), "only during a session"),
])
@pytest.mark.parametrize("kind, text", [("help", None), ("chat", "hello?")])
def test_help_and_chat_are_refused_outside_an_active_session_with_a_problem(tmp_path, setup, words, kind, text):
    coord, _, path = make(tmp_path)
    setup(coord)
    before = events(path) if path.exists() else []
    out, turn = coord.begin_turn(kind, text)
    assert turn is None
    assert out[0]["type"] == "tutor_error" and out[0]["turn_id"] is None and words in out[0]["message"]
    assert (events(path) if path.exists() else []) == before


def test_one_turn_at_a_time_a_second_request_is_refused_until_the_first_ends(tmp_path):
    coord, _, path = active(tmp_path)
    _, first = begin(coord)
    out, turn = coord.begin_turn("chat", "hello?")
    assert turn is None and "still answering" in out[0]["message"]
    assert of_type(path, "chat_message") == []
    coord.finish_turn(first, reply(), "x-test-1")
    begin(coord, "chat", "hello?")


# --- finishing and failing ---------------------------------------------------------

def test_a_finished_turn_is_logged_with_its_labels_and_sent_to_the_panel(tmp_path):
    coord, _, path = active(tmp_path)
    _, turn = begin(coord)
    out = coord.finish_turn(turn, reply(gap="other", gap_note="confuses index and value"), "x-test-1")
    logged, = of_type(path, "help_turn")
    assert logged[1] == {"turn_id": turn.turn_id, "slug": SLUG_A, "request": "help", "press_index": 1,
                         "gap": "other", "gap_note": "confuses index and value", "help_kind": "question",
                         "reply_text": "What could you remember as you go?", "follow_up_question": None, "model": "x-test-1"}
    assert out == [{"type": "tutor_done", "ts": out[0]["ts"], "turn_id": turn.turn_id, "reply_text": "What could you remember as you go?",
                    "gap": "other", "gap_note": "confuses index and value", "help_kind": "question", "follow_up_question": None}]


def test_each_further_press_is_the_next_one_and_asks_for_more_concrete_help(tmp_path):
    coord, _, path = active(tmp_path)
    _, turn = begin(coord)
    coord.finish_turn(turn, reply(), "x-test-1")
    _, turn = begin(coord, "chat", "still lost")
    coord.finish_turn(turn, reply(help_kind="trace"), "x-test-1")
    out, turn = begin(coord)
    assert out[0]["press_index"] == 2 and of_type(path, "help_request")[-1][1]["press_index"] == 2
    text = context(turn)
    assert "press 2" in text and "question, trace" in text and "What could you remember" in text


def test_a_failed_turn_is_logged_and_does_not_count_as_a_press(tmp_path):
    coord, _, path = active(tmp_path)
    _, turn = begin(coord)
    out = coord.fail_turn(turn, "timeout", "The Tutor didn't answer in time. Ask again.")
    assert out == [{"type": "tutor_error", "ts": out[0]["ts"], "turn_id": turn.turn_id, "message": "The Tutor didn't answer in time. Ask again."}]
    assert of_type(path, "help_failed")[0][1] == {"turn_id": turn.turn_id, "slug": SLUG_A, "reason": "timeout",
                                                   "message": "The Tutor didn't answer in time. Ask again."}
    out, _ = begin(coord)
    assert out[0]["press_index"] == 1


def test_a_reply_streaming_when_the_session_pauses_finishes_and_is_stored(tmp_path):
    coord, _, path = active(tmp_path)
    _, turn = begin(coord)
    coord.handle(msg("session_pause"))
    out = coord.finish_turn(turn, reply(), "x-test-1")
    assert out[0]["type"] == "tutor_done"
    assert len(of_type(path, "help_turn")) == 1
    assert coord.handle(msg("session_get_state"))[0]["state"] == "paused"


def test_a_press_after_switching_problems_is_a_first_press_again(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord)
    coord.finish_turn(turn, reply(), "x-test-1")
    coord.handle(opened(SLUG_B, TITLE_B))
    out, turn = begin(coord)
    assert out[0]["press_index"] == 1 and "What could you remember" not in context(turn)


# --- context from the session ------------------------------------------------------

def test_the_context_has_code_statement_time_and_never_the_name(tmp_path):
    coord, clock, path = make(tmp_path)
    with open_store(path) as db:
        db.execute("INSERT INTO settings (key, value) VALUES ('name', 'Zephyrine')")
    coord.handle(start_msg())
    clock.advance(120)
    coord.handle(opened())
    coord.handle(statement())
    coord.handle(snapshot("def f(nums):\n    pass"))
    clock.advance(600)
    coord.handle(diff(2, ["    pass"], ["    return len(set(nums)) < len(nums)"]))
    _, turn = begin(coord)
    text = context(turn)
    assert STATEMENT in text and "return len(set(nums)) < len(nums)" in text
    assert "on this problem: 10 min" in text and "10 min left on the problem's cap" in text
    assert "Zephyrine" not in json.dumps(turn.messages)


def test_statements_and_code_belong_to_the_current_problem_only(tmp_path):
    coord, _, _ = active(tmp_path)
    coord.handle(statement())
    coord.handle(snapshot("code for A"))
    coord.handle(opened(SLUG_B, TITLE_B))
    coord.handle(statement(SLUG_A, "a late statement for A"))
    _, turn = begin(coord)
    text = context(turn)
    assert STATEMENT not in text and "late statement" not in text and "statement isn't available" in text
    assert "code for A" not in text and "hasn't written any code" in text


def test_a_statement_is_ignored_while_paused_and_outside_a_session(tmp_path):
    coord, _, path = make(tmp_path)
    coord.handle(statement())
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(msg("session_pause"))
    coord.handle(statement())
    assert of_type(path, "problem_statement") == []


def test_reference_and_prior_solve_come_from_the_store(tmp_path):
    coord, _, path = active(tmp_path)
    content = {"slug": SLUG_A, "title": "Duplicate Integer", "difficulty": "Easy", "neetcode_category": "Arrays & Hashing",
               "patterns": ["hash-set"], "analysis_concepts": [], "uncertain": [],
               "approaches": [{"id": "set", "name": "Hash set", "idea": "Remember seen values", "time": "O(n)", "space": "O(n)",
                               "interview_expected": "usually", "trick": False}]}
    with open_store(path) as db:
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)', (SLUG_A, json.dumps(content), "checked"))
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (NULL, '2026-10-01T00:00:00+00:00', 'prior_solve', ?)",
                   (json.dumps({"slug": SLUG_A, "source": "neetcode"}),))
    _, turn = begin(coord)
    text = context(turn)
    assert "Hash set" in text and "Status: checked" in text
    assert "solved this problem before on NeetCode" in text


def test_a_broken_reference_fails_the_turn_loudly_and_logs_it(tmp_path):
    coord, _, path = active(tmp_path)
    with open_store(path) as db:
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)', (SLUG_A, "{not json", "checked"))
    out, turn = coord.begin_turn("help", None)
    assert turn is None and out[0]["type"] == "tutor_error" and out[0]["turn_id"] is not None
    assert of_type(path, "help_failed")[0][1]["reason"] == "context"
    with open_store(path) as db:
        db.execute('DELETE FROM "references"')
    begin(coord)


# --- restarts ----------------------------------------------------------------------

def test_code_statement_presses_and_problem_time_are_rebuilt_after_a_restart(tmp_path):
    coord, clock, path = make(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened())
    coord.handle(statement())
    coord.handle(snapshot("a\nb"))
    clock.advance(300)
    coord.handle(diff(2, ["b"], ["c", "d"]))
    _, turn = begin(coord)
    coord.finish_turn(turn, reply(), "x-test-1")
    del coord
    clock.advance(5000)
    fresh = SessionCoordinator(path, clock)
    fresh.handle(msg("session_resume"))
    clock.advance(60)
    out, turn = begin(fresh)
    text = context(turn)
    assert out[0]["press_index"] == 2
    assert "a\nc\nd" in text and STATEMENT in text and "on this problem: 6 min" in text


def test_a_turn_open_when_the_companion_died_is_logged_failed_on_restore(tmp_path):
    coord, clock, path = active(tmp_path)
    _, turn = begin(coord)
    del coord
    clock.advance(30)
    fresh = SessionCoordinator(path, clock)
    failed, = of_type(path, "help_failed")
    assert failed[1] == {"turn_id": turn.turn_id, "slug": SLUG_A, "reason": "restart",
                         "message": "The companion restarted before the Tutor finished."}
    fresh.handle(msg("session_resume"))
    out, again = begin(fresh)
    assert out[0]["press_index"] == 1
    fresh.finish_turn(again, reply(), "x-test-1")
    SessionCoordinator(path, clock)
    assert len(of_type(path, "help_failed")) == 1


# --- help history belongs to the problem, across the session -------------------------

def test_a_reply_finishing_after_a_switch_does_not_count_on_the_new_problem(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord)
    coord.handle(opened(SLUG_B, TITLE_B))
    coord.finish_turn(turn, reply(reply_text="A late hint for A"), "x-test-1")
    out, turn_b = begin(coord)
    assert out[0]["press_index"] == 1 and turn_b.schema.__name__ == "FirstHelpReply"
    assert "A late hint for A" not in context(turn_b)
    assert [t for t in coord.chat_state()["turns"] if t["turn_id"] == turn.turn_id] == []


def test_coming_back_to_a_problem_in_the_same_session_continues_its_help(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord)
    coord.finish_turn(turn, reply(reply_text="Hint one on A"), "x-test-1")
    coord.handle(opened(SLUG_B, TITLE_B))
    coord.handle(opened())
    out, again = begin(coord)
    assert out[0]["press_index"] == 2 and "Hint one on A" in context(again)
    assert [t["kind"] for t in coord.chat_state()["turns"]] == ["press", "reply", "press"]


def test_a_new_session_starts_the_problem_at_press_one(tmp_path):
    coord, _, _ = active(tmp_path)
    _, turn = begin(coord)
    coord.finish_turn(turn, reply(reply_text="Old session hint"), "x-test-1")
    coord.handle(msg("session_end"))
    coord.handle(start_msg())
    coord.handle(opened())
    out, fresh = begin(coord)
    assert out[0]["press_index"] == 1 and "Old session hint" not in context(fresh)


def test_failing_a_turn_that_already_finished_logs_nothing(tmp_path):
    coord, _, path = active(tmp_path)
    _, turn = begin(coord)
    coord.finish_turn(turn, reply(), "x-test-1")
    assert coord.fail_turn(turn, "shutdown", "stopped") == []
    assert of_type(path, "help_failed") == []
