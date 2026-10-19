"""The `analysis` grade (SPEC §7, §8): complexity answers graded by both Graders, independently of the `solve`
grade, into the category's `analysis` memory. Graders are faked with recorded replies.

Interface to build:

    companion.grading.AnalysisVerdict (pydantic, strict, extra forbidden):
        complexity_verdict: "right" | "right_after_followup" | "wrong" | "wrong_reasoning" | "no_reasoning"
        complexity_stated_earlier: bool   # the Tutor had stated the complexity earlier in the attempt (§8)

    companion.grading.grade_pending(...)  (same signature) also grades answered complexity questions
    (`companion.followups`): each configured grader is called once per answered question with
    `call_role("grader", messages, AnalysisVerdict, model=...)`, its messages carrying the answer and reasoning
    text. The verdict maps through `analysis_grade`; a review with item_type 'category:analysis' and the
    category as item_key is written only when `analysis_counts` holds (first attempt, `solve` grade not Again,
    complexity not stated earlier); `no_reasoning` writes none. Two graders that disagree store the lower grade
    with `grader_disagreement` 1. While the solve grade is pending (reference not checked) the analysis grade
    is pending too. A question that is unanswered, skipped or failing to grade never holds up the `solve` grade,
    and an answer given after the solve was graded is graded by a later `grade_pending` call.
"""

import json

import pytest

from companion.followups import answer_followup, ask_followup, skip_followup
from companion.grading import AnalysisVerdict, grade_pending
from companion.history import import_history
from companion.memory import item_memory
from companion.store import open_store
from tests.test_grading import (CATEGORY, MEDIUM, MEDIUM_TITLE, FakeGraders, Run, down, put_reference, reviews,
                                set_reference_status)
from tests.test_session import of_type, opened

OTHER, OTHER_TITLE = "is-palindrome", "Valid Palindrome"  # catalog: Easy, Two Pointers
ANSWER = "O(n log k)"
REASONING = "counting is O(n) and each of the n pushes onto the size-k heap is O(log k)"


class FakeAllGraders(FakeGraders):
    """The solve fake from test_grading plus one recorded analysis reply (or failure) per model."""

    def __init__(self, a1="right", a2=None, *, stated=False, x1=None, x2=None):
        super().__init__(x1, x2)
        self.analysis = {"x-test-1": a1, "x-test-2": a2 if a2 is not None else a1}
        self.stated = stated
        self.analysis_calls = []

    def call_role(self, role, messages, schema_class, *, model=None, **kwargs):
        if schema_class is AnalysisVerdict:
            self.analysis_calls.append((role, model, json.dumps(messages)))
            verdict = self.analysis[model]
            if isinstance(verdict, Exception):
                raise verdict
            return AnalysisVerdict.model_validate(
                {"complexity_verdict": verdict, "complexity_stated_earlier": self.stated})
        return super().call_role(role, messages, schema_class, model=model, **kwargs)


def stamp(run):
    run.clock.advance(5)
    return run.clock().isoformat()


def ask(run, slug=MEDIUM):
    with open_store(run.path) as db:
        return ask_followup(db, session_id=run.coord.session_id, slug=slug, ts=stamp(run),
                            question="What's the time complexity of your solution?", complexity=True)


def answer(run, question):
    with open_store(run.path) as db:
        assert answer_followup(db, question_id=question, ts=stamp(run), text=ANSWER, has_reasoning=False) == "ask_reasoning"
    with open_store(run.path) as db:
        assert answer_followup(db, question_id=question, ts=stamp(run), text=REASONING, has_reasoning=True) == "answered"


def answered_attempt(run, *, help=(), question="answered"):
    """One session on the Medium: solved, then a complexity question answered, skipped or left open."""
    run.start()
    run.open(MEDIUM, MEDIUM_TITLE)
    for kind in help:
        run.help(kind)
    run.accept(MEDIUM)
    asked = ask(run)
    if question == "answered":
        answer(run, asked)
    elif question == "skipped":
        with open_store(run.path) as db:
            skip_followup(db, question_id=asked, ts=stamp(run))
    run.end()
    return asked


def graded(tmp_path, fake, **kwargs):
    run = Run(tmp_path)
    put_reference(run.path)
    answered_attempt(run, **kwargs)
    grade_pending(fake, db_path=run.path)
    return run


def analysis_rows(path):
    return [r for r in reviews(path) if r["item_type"] == "category:analysis"]


def solve_rows(path):
    return [(r["item_type"], r["grade"]) for r in reviews(path) if r["item_type"] != "category:analysis"]


# --- the analysis rows of the grade table -------------------------------------------

@pytest.mark.parametrize("verdict, grade", [
    ("right", "good"),                 # right, with right reasoning
    ("right_after_followup", "hard"),  # right after one guiding question that doesn't state the answer
    ("wrong", "again"),                # wrong, or right only after the Tutor stated it
    ("wrong_reasoning", "again"),      # right with wrong reasoning
])
def test_a_complexity_answer_is_graded_into_the_category_analysis_memory(tmp_path, verdict, grade):
    run = graded(tmp_path, FakeAllGraders(verdict))
    rows = analysis_rows(run.path)
    assert [(r["item_key"], r["grade"], r["first_attempt"], r["difficulty"], r["grader_disagreement"])
            for r in rows] == [(CATEGORY, grade, 1, "Medium", 0)]
    with open_store(run.path) as db:
        memory = item_memory(db, "category:analysis", CATEGORY, run.clock())
    assert memory is not None and memory.last_grade == grade


def test_each_grader_judges_the_answer_and_its_reasoning_once(tmp_path):
    fake = FakeAllGraders()
    graded(tmp_path, fake)
    assert sorted((role, model) for role, model, _ in fake.analysis_calls) == [("grader", "x-test-1"), ("grader", "x-test-2")]
    assert all(ANSWER in text and REASONING in text for _, _, text in fake.analysis_calls)


def test_no_reasoning_writes_no_analysis_review(tmp_path):
    run = graded(tmp_path, FakeAllGraders("no_reasoning"))
    assert analysis_rows(run.path) == []
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]


def test_disagreeing_graders_keep_the_lower_analysis_grade_and_note_it(tmp_path):
    run = graded(tmp_path, FakeAllGraders("right", "wrong"))
    assert [(r["grade"], r["grader_disagreement"]) for r in analysis_rows(run.path)] == [("again", 1)]


# --- when an analysis grade counts ----------------------------------------------------

def test_a_hard_solve_still_counts_for_analysis(tmp_path):
    run = graded(tmp_path, FakeAllGraders("right"), help=("question",))
    assert solve_rows(run.path) == [("problem", "hard"), ("category:solve", "hard")]
    assert [r["grade"] for r in analysis_rows(run.path)] == ["good"]


@pytest.mark.parametrize("kind", ["snippet", "partial_logic"])
def test_no_analysis_grade_when_the_solve_grade_is_again(tmp_path, kind):
    run = graded(tmp_path, FakeAllGraders("right"), help=(kind,))
    assert solve_rows(run.path) == [("problem", "again"), ("category:solve", "again")]
    assert analysis_rows(run.path) == []


def test_no_analysis_grade_when_the_tutor_had_stated_the_complexity_earlier(tmp_path):
    run = graded(tmp_path, FakeAllGraders("right", stated=True))
    assert analysis_rows(run.path) == []
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]


def test_no_analysis_grade_on_a_seen_problem(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    import_history(["https://leetcode.com/problems/top-k-frequent-elements/"], path=run.path)
    answered_attempt(run)
    grade_pending(FakeAllGraders("right"), db_path=run.path)
    assert analysis_rows(run.path) == []
    assert solve_rows(run.path) == [("problem", "good")]


def test_a_repeat_asks_but_never_grades_analysis(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    answered_attempt(run)
    grade_pending(FakeAllGraders("right"), db_path=run.path)
    run.advance_days(7)
    answered_attempt(run)
    grade_pending(FakeAllGraders("wrong"), db_path=run.path)
    assert [r["grade"] for r in analysis_rows(run.path)] == ["good"]


def test_a_discussion_question_is_not_graded(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open(MEDIUM, MEDIUM_TITLE)
    run.accept(MEDIUM)
    with open_store(run.path) as db:
        question = ask_followup(db, session_id=run.coord.session_id, slug=MEDIUM, ts=stamp(run),
                                question="What if the input were a stream?", complexity=False)
    with open_store(run.path) as db:
        answer_followup(db, question_id=question, ts=stamp(run), text="keep a heap as items arrive", has_reasoning=True)
    run.end()
    fake = FakeAllGraders("right")
    grade_pending(fake, db_path=run.path)
    assert fake.analysis_calls == [] and analysis_rows(run.path) == []


def test_the_analysis_grade_is_pending_with_the_solve_grade_until_the_reference_is_checked(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, status="needs_review")
    answered_attempt(run)
    grade_pending(FakeAllGraders("right"), db_path=run.path)
    assert analysis_rows(run.path) == []
    set_reference_status(run.path, MEDIUM, "checked")
    grade_pending(FakeAllGraders("right"), db_path=run.path)
    assert [r["grade"] for r in analysis_rows(run.path)] == ["good"]
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]


# --- a question never holds up the solve grade ----------------------------------------

@pytest.mark.parametrize("question", ["skipped", "unanswered"])
def test_a_skipped_or_unanswered_question_never_holds_up_the_solve_grade(tmp_path, question):
    fake = FakeAllGraders("right")
    run = graded(tmp_path, fake, question=question)
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]
    assert fake.analysis_calls == [] and analysis_rows(run.path) == []


def test_no_reasoning_after_being_asked_is_skipped_and_never_holds_up_the_solve_grade(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open(MEDIUM, MEDIUM_TITLE)
    run.accept(MEDIUM)
    question = ask(run)
    for text in ("O(n log k)", "it just is"):
        with open_store(run.path) as db:
            outcome = answer_followup(db, question_id=question, ts=stamp(run), text=text, has_reasoning=False)
    assert outcome == "skipped"
    run.end()
    fake = FakeAllGraders("right")
    grade_pending(fake, db_path=run.path)
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]
    assert fake.analysis_calls == [] and analysis_rows(run.path) == []


def test_failing_analysis_graders_never_hold_up_the_solve_grade(tmp_path):
    run = graded(tmp_path, FakeAllGraders(down("x-test-1"), down("x-test-2")))
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]
    assert analysis_rows(run.path) == []


def test_leaving_with_an_open_question_skips_it_and_never_grades_analysis(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open(MEDIUM, MEDIUM_TITLE)
    run.accept(MEDIUM)
    question = ask(run)
    run.send(opened(OTHER, OTHER_TITLE))  # leaving the problem closes the question
    skipped, = of_type(run.path, "followup_skipped")
    assert skipped[1]["question_id"] == question
    grade_pending(FakeAllGraders("right"), db_path=run.path)
    assert solve_rows(run.path) == [("problem", "good"), ("category:solve", "good")]
    assert analysis_rows(run.path) == []
    with pytest.raises(ValueError):
        answer(run, question)
    run.end()
