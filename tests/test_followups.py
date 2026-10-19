"""Follow-up questions between problems (SPEC §5 "Between problems", §8): the one rule in code for complexity
questions, its postponement, and answers without reasoning. Sessions are driven through the coordinator; the
Tutor's choices (which question, postpone or not, whether an answer gave reasoning) are passed in as the
recorded decisions they would be.

Interface to build, in a new module `companion/followups.py` (every function takes an open store; events are
appended to `events` in the given session and must survive a session restore):

    complexity_question_due(db, *, session_id, slug) -> bool
        The one rule: True when `slug` is accepted in this session, on a first attempt at an unseen problem
        (§8 "Unseen"), with no full solution shown before the accept, in a category whose `analysis` isn't
        strong (§9: stability >= 21 days, latest review not Again, a Good on a Medium or Hard problem), and no
        complexity question has been asked in that category this session. A postponement doesn't use it up.

    ask_followup(db, *, session_id, slug, ts, question, complexity) -> int
        Logs a `followup_asked` event {slug, question, complexity}; returns its event id (the question id).
        `complexity` is True for a complexity question (your approach or another one).

    postpone_complexity(db, *, session_id, slug, ts, reason) -> int
        The Tutor's judgment that session load is high; logs a `complexity_postponed` event {slug, reason}.

    answer_followup(db, *, question_id, ts, text, has_reasoning) -> "answered" | "ask_reasoning" | "skipped"
        Logs the answer. A complexity answer without reasoning gets "ask_reasoning" (logged as
        `reasoning_requested`) the first time and "skipped" (logged as `followup_skipped`) if it still has
        none. Other questions are never asked for reasoning. Answering a skipped question is a ValueError.

    skip_followup(db, *, question_id, ts) -> None
        You skipped the question; logs `followup_skipped`.
"""

from datetime import timedelta

import pytest

from companion.followups import (answer_followup, ask_followup, complexity_question_due, postpone_complexity,
                                 skip_followup)
from companion.history import import_history
from companion.model import TutorReply
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_grading import CATEGORY, MEDIUM, MEDIUM_TITLE, Run, put_reference
from tests.test_session import START, msg, of_type, opened, submit

SAME_CATEGORY, SAME_TITLE = "is-anagram", "Valid Anagram"          # catalog: Easy, Arrays & Hashing
OTHER_CATEGORY, OTHER_TITLE = "is-palindrome", "Valid Palindrome"  # catalog: Easy, Two Pointers


def ts(run):
    run.clock.advance(5)
    return run.clock().isoformat()


def due(run, slug=MEDIUM):
    with open_store(run.path) as db:
        return complexity_question_due(db, session_id=run.coord.session_id, slug=slug)


def ask(run, slug=MEDIUM, complexity=True):
    with open_store(run.path) as db:
        return ask_followup(db, session_id=run.coord.session_id, slug=slug, ts=ts(run),
                            question="What's the time complexity of your solution?", complexity=complexity)


def answer(run, question_id, text="O(n log k)", has_reasoning=True):
    with open_store(run.path) as db:
        return answer_followup(db, question_id=question_id, ts=ts(run), text=text, has_reasoning=has_reasoning)


def solve(run, slug=MEDIUM, title=MEDIUM_TITLE, help=()):
    run.open(slug, title)
    for kind in help:
        run.help(kind)
    run.accept(slug)


def started(tmp_path):
    run = Run(tmp_path)
    for slug in (MEDIUM, SAME_CATEGORY, OTHER_CATEGORY):
        put_reference(run.path, slug)
    run.start()
    return run


def analysis_reviews(path, category, grades, difficulty="Medium", days_apart=7):
    """Past category:analysis reviews, `days_apart` apart, the last a week before the session."""
    with open_store(path) as db:
        for index, grade in enumerate(grades):
            when = (START - timedelta(days=7 + days_apart * (len(grades) - 1 - index))).isoformat()
            db.execute("INSERT INTO reviews (item_type, item_key, grade, first_attempt, difficulty, reviewed_at) "
                       "VALUES ('category:analysis', ?, ?, 1, ?, ?)", (category, grade, difficulty, when))


# --- the one rule ------------------------------------------------------------------

def test_a_first_attempt_at_an_unseen_problem_solved_alone_is_due_a_complexity_question(tmp_path):
    run = started(tmp_path)
    solve(run)
    assert due(run) is True


def test_solved_after_questions_is_still_due(tmp_path):
    run = started(tmp_path)
    solve(run, help=("question",))
    assert due(run) is True


def test_nothing_is_due_before_an_accept(tmp_path):
    run = started(tmp_path)
    run.open(MEDIUM, MEDIUM_TITLE)
    run.send(submit(MEDIUM, False))
    assert due(run) is False


def test_nothing_is_due_after_the_full_solution_was_shown_before_the_accept(tmp_path):
    run = started(tmp_path)
    solve(run, help=("full_solution",))
    assert due(run) is False


def test_nothing_is_due_on_an_imported_solve(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    import_history(["https://leetcode.com/problems/top-k-frequent-elements/"], path=run.path)
    run.start()
    solve(run)
    assert due(run) is False


def test_nothing_is_due_on_a_problem_worked_on_before(tmp_path):
    run = started(tmp_path)
    run.open(MEDIUM, MEDIUM_TITLE)
    run.run()  # ran code, left unfinished: the problem is seen
    run.end()
    run.advance_days(1)
    run.start()
    solve(run)
    assert due(run) is False


def test_nothing_is_due_when_the_category_analysis_is_strong(tmp_path):
    run = started(tmp_path)
    # Two clean reviews a week apart give 21.4 days of stability (§9), with a Good on a Medium.
    analysis_reviews(run.path, CATEGORY, ["good", "good"], difficulty="Medium")
    solve(run)
    assert due(run) is False


@pytest.mark.parametrize("grades, difficulty", [
    (["good"], "Medium"),                   # one clean review: 2.3 days of stability
    (["good", "good"], "Easy"),             # stable, but no Good on a Medium or Hard problem
    (["good", "good", "again"], "Medium"),  # the latest review is Again
])
def test_a_category_whose_analysis_is_not_strong_is_due(tmp_path, grades, difficulty):
    run = started(tmp_path)
    analysis_reviews(run.path, CATEGORY, grades, difficulty=difficulty)
    solve(run)
    assert due(run) is True


def test_the_rule_asks_at_most_once_per_category_per_session(tmp_path):
    run = started(tmp_path)
    solve(run)
    assert due(run) is True
    ask(run)
    solve(run, SAME_CATEGORY, SAME_TITLE)
    assert due(run, SAME_CATEGORY) is False  # same category, same session
    solve(run, OTHER_CATEGORY, OTHER_TITLE)
    assert due(run, OTHER_CATEGORY) is True  # another category


def test_a_new_session_can_ask_in_the_same_category_again(tmp_path):
    run = started(tmp_path)
    solve(run)
    ask(run)
    run.end()
    run.advance_days(1)
    run.start()
    solve(run, SAME_CATEGORY, SAME_TITLE)
    assert due(run, SAME_CATEGORY) is True


def test_a_postponement_is_logged_and_moves_the_question_to_the_next_first_attempt_in_the_category(tmp_path):
    run = started(tmp_path)
    solve(run)
    assert due(run) is True
    with open_store(run.path) as db:
        postpone_complexity(db, session_id=run.coord.session_id, slug=MEDIUM, ts=ts(run),
                            reason="Long, hard problem; enough for now.")
    logged = of_type(run.path, "complexity_postponed")
    assert len(logged) == 1
    payload, session_id = logged[0][1], logged[0][2]
    assert payload["slug"] == MEDIUM and payload["reason"] == "Long, hard problem; enough for now."
    assert session_id == run.coord.session_id
    solve(run, SAME_CATEGORY, SAME_TITLE)
    assert due(run, SAME_CATEGORY) is True


# --- answers -----------------------------------------------------------------------

def test_a_complexity_answer_with_reasoning_is_answered(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    assert answer(run, question, "O(n log k): a heap of size k for each of n items") == "answered"
    assert of_type(run.path, "reasoning_requested") == [] and of_type(run.path, "followup_skipped") == []


def test_a_complexity_answer_without_reasoning_is_asked_for_its_reasoning(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    assert answer(run, question, "O(n log k)", has_reasoning=False) == "ask_reasoning"
    assert len(of_type(run.path, "reasoning_requested")) == 1
    assert answer(run, question, "each push and pop on the size-k heap is log k") == "answered"
    assert of_type(run.path, "followup_skipped") == []


def test_no_reasoning_even_after_asking_is_treated_as_skipped(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    assert answer(run, question, "O(n log k)", has_reasoning=False) == "ask_reasoning"
    assert answer(run, question, "it just is", has_reasoning=False) == "skipped"
    assert len(of_type(run.path, "reasoning_requested")) == 1
    assert len(of_type(run.path, "followup_skipped")) == 1
    with pytest.raises(ValueError):
        answer(run, question, "O(n log k) because of the heap")


def test_a_discussion_answer_is_never_asked_for_reasoning(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run, complexity=False)
    assert answer(run, question, "bucket sort", has_reasoning=False) == "answered"
    assert of_type(run.path, "reasoning_requested") == []


def test_you_can_skip_the_question(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    with open_store(run.path) as db:
        skip_followup(db, question_id=question, ts=ts(run))
    assert len(of_type(run.path, "followup_skipped")) == 1
    with pytest.raises(ValueError):
        answer(run, question)


def test_a_session_with_follow_up_events_restores_after_a_restart(tmp_path):
    run = started(tmp_path)
    solve(run)
    with open_store(run.path) as db:
        postpone_complexity(db, session_id=run.coord.session_id, slug=MEDIUM, ts=ts(run), reason="tired")
    solve(run, SAME_CATEGORY, SAME_TITLE)
    question = ask(run, SAME_CATEGORY)
    answer(run, question, "O(n)", has_reasoning=False)
    answer(run, question, "O(n)", has_reasoning=False)
    restarted = SessionCoordinator(run.path, run.clock)
    assert restarted.state == "paused" and restarted.paused_reason == "restart"
    assert restarted.session_id == run.coord.session_id


# --- the open question in session state --------------------------------------------

def state(coord):
    out = coord.handle(msg("session_get_state"))
    assert out[0]["type"] == "session_state"
    return out[0]


def test_session_state_shows_the_open_question_for_the_panel(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    assert state(run.coord)["followup"] == {
        "question_id": question,
        "question": "What's the time complexity of your solution?",
        "complexity": True,
    }


def test_session_state_hides_the_question_once_answered_with_reasoning(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    answer(run, question, "O(n log k), because each item touches a size-k heap")
    assert state(run.coord)["followup"] is None


def test_an_open_question_is_closed_as_skipped_when_the_problem_is_left(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    run.send(opened(SAME_CATEGORY, SAME_TITLE))
    skipped = of_type(run.path, "followup_skipped")
    assert len(skipped) == 1 and skipped[0][1]["question_id"] == question
    assert state(run.coord)["followup"] is None


def test_an_open_question_is_closed_as_skipped_when_the_session_ends(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    run.end()
    skipped = of_type(run.path, "followup_skipped")
    assert len(skipped) == 1 and skipped[0][1]["question_id"] == question
    assert state(run.coord)["followup"] is None


def test_an_open_question_is_rebuilt_from_events_after_a_restart_and_can_be_answered(tmp_path):
    run = started(tmp_path)
    solve(run)
    question = ask(run)
    fresh = SessionCoordinator(run.path, run.clock)
    fresh.handle(msg("session_resume"))
    assert state(fresh)["followup"]["question_id"] == question
    out, turn = fresh.begin_turn("chat", "O(n log k): each push and pop on the heap costs log k")
    assert turn is not None, out
    reply = turn.schema.model_validate({"gap": "none", "gap_note": None, "help_kind": "none",
                                        "reply_text": "Right, that follows.", "follow_up_question": None,
                                        "has_reasoning": True})
    fresh.finish_turn(turn, reply, "x-test-1")
    answered = of_type(run.path, "followup_answered")
    assert answered[-1][1]["question_id"] == question and answered[-1][1]["has_reasoning"] is True
    assert state(fresh)["followup"] is None
