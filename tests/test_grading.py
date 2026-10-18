"""Grading the solve (SPEC §5, §7, §8, §11, §13): attempts recorded through the session coordinator, Graders
faked with recorded replies, the result read from the `reviews` log."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from companion.grading import grade_pending, write_missed
from companion.history import import_history
from companion.memory import item_memory
from companion.model import ModelUnavailable, TutorReply
from companion.store import open_store
from tests.test_session import make, msg, opened, snapshot, start_msg, submit

MEDIUM, MEDIUM_TITLE = "top-k-elements-in-list", "Top K Frequent Elements"  # catalog: Medium, Arrays & Hashing
EASY, EASY_TITLE = "duplicate-integer", "Contains Duplicate"                # catalog: Easy, Arrays & Hashing
CATEGORY = "Arrays & Hashing"
GRADERS = ["x-test-1", "x-test-2"]
GRADE_WORDS = {"good", "hard", "again", "solid", "shaky", "missed"}


# --- fakes and fixtures ------------------------------------------------------------

def verdict(approach="heap", meets=True, corrections=(), accepts=None):
    return {"accepts": [{"submission_event_id": index, "approach_id": name, "meets_expected": expected}
                        for index, name, expected in (accepts or [(-1, approach, meets)])],
            "corrected_help_labels": [{"turn_id": turn_id, "help_kind": kind} for turn_id, kind in corrections]}


def down(model="x-test-2"):
    return ModelUnavailable(model, "DeepSeek V4.1 Flash", ("tutor", "grader", "checker"))


class FakeGraders:
    """Stands in for ModelClient: one recorded Grader reply (or failure) per model, validated like the real client."""

    def __init__(self, x1=None, x2=None):
        self.config = SimpleNamespace(roles={"grader": list(GRADERS)})
        self.replies = {"x-test-1": x1 if x1 is not None else verdict(),
                        "x-test-2": x2 if x2 is not None else verdict()}
        self.calls = []

    def call_role(self, role, messages, schema_class, *, model=None, **kwargs):
        self.calls.append((role, model))
        reply = self.replies[model]
        if isinstance(reply, Exception):
            raise reply
        reply = json.loads(json.dumps(reply))
        events = json.loads(messages[1]["content"])["events"]
        accepted = [event[0] for event in events if event[1] == "submit_result" and event[2]["accepted"]]
        for item in reply["accepts"]:
            if item["submission_event_id"] < 0:
                item["submission_event_id"] = accepted[item["submission_event_id"]]
        return schema_class.model_validate_json(json.dumps(reply))


def put_reference(path, slug=MEDIUM, status="checked"):
    content = {"slug": slug, "title": slug, "difficulty": "Medium", "neetcode_category": CATEGORY,
               "patterns": ["bucket-sort"], "analysis_concepts": ["counting"],
               "approaches": [
                   {"id": "sort", "name": "sort by count", "idea": "count then sort", "time": "O(n log n)",
                    "space": "O(n)", "interview_expected": "sometimes", "trick": False},
                   {"id": "heap", "name": "heap", "idea": "count then a size-k heap", "time": "O(n log k)",
                    "space": "O(n)", "interview_expected": "usually", "trick": False}],
               "uncertain": [] if status == "checked" else ["heap.interview_expected"]}
    with open_store(path) as db:
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?) '
                   "ON CONFLICT(problem_key) DO UPDATE SET content = excluded.content, status = excluded.status",
                   (slug, json.dumps(content), status))


def set_reference_status(path, slug, status):
    with open_store(path) as db:
        db.execute('UPDATE "references" SET status = ? WHERE problem_key = ?', (status, slug))


def reply(kind):
    return TutorReply.model_validate({"gap": "none" if kind == "none" else "stuck_optimising", "gap_note": None,
                                      "help_kind": kind, "reply_text": "Some help.", "follow_up_question": None})


class Run:
    """A session driven through the coordinator, collecting everything it sends to the panel."""

    def __init__(self, tmp_path):
        self.coord, self.clock, self.path = make(tmp_path)
        self.sent = []

    def send(self, message):
        self.sent += self.coord.handle(message)

    def start(self):
        self.send(start_msg(120))

    def open(self, slug=MEDIUM, title=MEDIUM_TITLE):
        self.send(opened(slug, title))
        self.send(snapshot("def topKFrequent(nums, k):\n    pass"))
        self.clock.advance(60)

    def help(self, kind):
        """One Tutor turn labelled `kind`; returns its turn id."""
        out, turn = self.coord.begin_turn("chat", "I'm stuck")
        assert turn is not None, out
        self.sent += out
        self.sent += self.coord.finish_turn(turn, reply(kind), "x-test-2")
        self.clock.advance(60)
        return turn.turn_id

    def run(self, slug=MEDIUM):
        self.send(msg("run_result", {"slug": slug, "cases": [{"passed": False, "error_type": "wrong_answer",
                  "error_message": "", "runtime": "0.03", "failing_case": None}]}))
        self.clock.advance(60)

    def accept(self, slug=MEDIUM):
        self.send(snapshot("def topKFrequent(nums, k):\n    return solved"))
        self.send(submit(slug, True))
        self.clock.advance(60)

    def end(self):
        self.send(msg("session_end"))

    def advance_days(self, days):
        self.clock.advance(days * 86400)


def attempt(run, *, slug=MEDIUM, title=MEDIUM_TITLE, help=(), accept=True, after=()):
    """One whole session on one problem, left by ending the session. Returns the attempt's problem_left event id."""
    run.start()
    run.open(slug, title)
    for kind in help:
        run.help(kind)
    if accept:
        run.accept(slug)
    for kind in after:
        run.help(kind)
    run.end()
    return left_ids(run.path)[-1]


def left_ids(path):
    with open_store(path) as db:
        return [row[0] for row in db.execute("SELECT id FROM events WHERE type = 'problem_left' ORDER BY id")]


def event_ts(path, event_id):
    with open_store(path) as db:
        return db.execute("SELECT ts FROM events WHERE id = ?", (event_id,)).fetchone()[0]


def last_event_id(path, kind):
    with open_store(path) as db:
        return db.execute("SELECT max(id) FROM events WHERE type = ?", (kind,)).fetchone()[0]


def reviews(path):
    with open_store(path) as db:
        cols = ("item_type", "item_key", "grade", "meets_expected", "first_attempt", "difficulty",
                "grader_disagreement", "failed_grader", "event_id", "reviewed_at", "session_id")
        rows = db.execute(f"SELECT {', '.join(cols)} FROM reviews ORDER BY id").fetchall()
    return [dict(zip(cols, row)) for row in rows]


def grades(path):
    return [(r["item_type"], r["item_key"], r["grade"]) for r in reviews(path)]


def problem_grade(path, slug=MEDIUM):
    return [r["grade"] for r in reviews(path) if r["item_type"] == "problem" and r["item_key"] == slug]


def graded_once(tmp_path, *, help=(), after=(), x1=None, x2=None):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run, help=help, after=after)
    fake = FakeGraders(x1, x2)
    grade_pending(fake, db_path=run.path)
    return run, fake


# --- the grade table, through a real attempt ----------------------------------------

def test_a_clean_solve_is_good_for_the_problem_and_on_a_first_attempt_for_the_category(tmp_path):
    run, fake = graded_once(tmp_path)
    left = left_ids(run.path)[-1]
    problem, category = reviews(run.path)
    assert problem == {"item_type": "problem", "item_key": MEDIUM, "grade": "good", "meets_expected": 1,
                       "first_attempt": 1, "difficulty": "Medium", "grader_disagreement": 0, "failed_grader": None,
                       "event_id": left, "reviewed_at": event_ts(run.path, left), "session_id": 1}
    assert (category["item_type"], category["item_key"], category["grade"], category["first_attempt"]) == \
        ("category:solve", CATEGORY, "good", 1)


def test_each_grader_is_called_once_independently(tmp_path):
    _, fake = graded_once(tmp_path)
    assert sorted(fake.calls) == [("grader", "x-test-1"), ("grader", "x-test-2")]


def test_solved_after_questions_traces_analogies_and_a_taught_tool_is_hard(tmp_path):
    run, _ = graded_once(tmp_path, help=("question", "trace", "analogy", "teach_tool"))
    assert grades(run.path) == [("problem", MEDIUM, "hard"), ("category:solve", CATEGORY, "hard")]


def test_solved_without_help_with_a_worse_approach_is_hard(tmp_path):
    run, _ = graded_once(tmp_path, x1=verdict("sort", meets=False), x2=verdict("sort", meets=False))
    assert problem_grade(run.path) == ["hard"]
    assert reviews(run.path)[0]["meets_expected"] == 0


@pytest.mark.parametrize("kind", ["snippet", "partial_logic"])
def test_needing_snippets_or_partial_logic_is_again(tmp_path, kind):
    run, _ = graded_once(tmp_path, help=("question", kind))
    assert grades(run.path) == [("problem", MEDIUM, "again"), ("category:solve", CATEGORY, "again")]


def test_syntax_help_does_not_lower_the_grade(tmp_path):
    run, _ = graded_once(tmp_path, help=("syntax",))
    assert problem_grade(run.path) == ["good"]


def test_a_chat_reply_labelled_none_does_not_lower_the_grade(tmp_path):
    run, _ = graded_once(tmp_path, help=("none",))
    assert problem_grade(run.path) == ["good"]


def test_the_attempt_is_graded_by_the_best_approach_reached_yourself(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.accept()  # brute force first
    run.accept()  # then optimised it, on its own
    run.end()
    multi = verdict(accepts=[(-2, "sort", False), (-1, "heap", True)])
    grade_pending(FakeGraders(multi, multi), db_path=run.path)
    assert problem_grade(run.path) == ["good"]


def test_help_after_the_solution_does_not_lower_the_grade(tmp_path):
    run, _ = graded_once(tmp_path, after=("snippet", "partial_logic"))
    assert problem_grade(run.path) == ["good"]


def test_graders_corrected_help_labels_replace_the_logged_ones(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    turn = run.help("snippet")  # logged as a snippet, but it was only Python mechanics
    run.accept()
    run.end()
    grade_pending(FakeGraders(verdict(corrections=[(turn, "syntax")]), verdict(corrections=[(turn, "syntax")])),
                  db_path=run.path)
    assert problem_grade(run.path) == ["good"]


# --- two graders -------------------------------------------------------------------

def test_disagreeing_graders_keep_the_lower_grade_and_note_it(tmp_path):
    run, _ = graded_once(tmp_path, x1=verdict("heap", meets=True), x2=verdict("sort", meets=False))
    problem, category = reviews(run.path)
    assert (problem["grade"], problem["grader_disagreement"], problem["meets_expected"]) == ("hard", 1, 0)
    assert category["grade"] == "hard"


def test_each_grader_grades_with_its_own_label_corrections(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    turn = run.help("question")
    run.accept()
    run.end()
    grade_pending(FakeGraders(verdict(corrections=[(turn, "partial_logic")]), verdict()), db_path=run.path)
    problem = reviews(run.path)[0]
    assert (problem["grade"], problem["grader_disagreement"]) == ("again", 1)


def test_agreeing_graders_note_no_disagreement(tmp_path):
    run, _ = graded_once(tmp_path, x1=verdict("heap"), x2=verdict("heap"))
    assert [r["grader_disagreement"] for r in reviews(run.path)] == [0, 0]


# --- a grader failing (SPEC §13) ---------------------------------------------------------

def test_a_failing_grader_is_retried_twice_then_the_other_grade_is_used_and_noted(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)
    fake = FakeGraders(verdict(), down("x-test-2"))
    grade_pending(fake, db_path=run.path)  # leaving the problem
    assert problem_grade(run.path) == [None]
    grade_pending(fake, db_path=run.path, trigger="next_problem")  # the next problem
    assert problem_grade(run.path) == [None]
    grade_pending(fake, db_path=run.path, trigger="next_start")  # the next companion start
    problem = reviews(run.path)[0]
    assert (problem["grade"], problem["failed_grader"], problem["grader_disagreement"]) == ("good", "x-test-2", 0)
    assert [r["item_type"] for r in reviews(run.path)] == ["problem", "category:solve"]


def test_a_grader_that_recovers_on_retry_grades_normally(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)
    fake = FakeGraders(verdict(), ValueError("grader reply invalid after retry"))
    grade_pending(fake, db_path=run.path)
    fake.replies["x-test-2"] = verdict("sort", meets=False)
    grade_pending(fake, db_path=run.path, trigger="next_problem")
    problem = reviews(run.path)[0]
    assert (problem["grade"], problem["failed_grader"], problem["grader_disagreement"]) == ("hard", None, 1)


def test_both_graders_failing_leaves_the_attempt_ungraded_and_reported_never_guessed(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    left = attempt(run)
    fake = FakeGraders(down("x-test-1"), down("x-test-2"))
    for trigger in ("initial", "next_problem", "next_start"):
        outcome = grade_pending(fake, db_path=run.path, trigger=trigger)
        assert outcome.failed == [left]
        assert outcome.graded == []
        assert problem_grade(run.path) == [None]
    fake.replies = {"x-test-1": verdict(), "x-test-2": verdict()}
    outcome = grade_pending(fake, db_path=run.path, trigger="next_start")
    assert outcome.graded == [left] and outcome.failed == []
    problem = reviews(run.path)[0]
    assert (problem["grade"], problem["failed_grader"], problem["reviewed_at"]) == ("good", None, event_ts(run.path, left))


def test_a_graded_attempt_is_never_graded_again(tmp_path):
    run, fake = graded_once(tmp_path)
    before = reviews(run.path)
    outcome = grade_pending(fake, db_path=run.path)
    assert reviews(run.path) == before
    assert len(fake.calls) == 2
    assert outcome.graded == outcome.pending == outcome.failed == []


# --- full solution and debrief: Again at once, no Grader ----------------------------------

def test_a_full_solution_before_an_accept_writes_again_at_once_with_no_grader(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.help("question")
    run.help("full_solution")
    help_turn = last_event_id(run.path, "help_turn")
    assert grades(run.path) == [("problem", MEDIUM, "again"), ("category:solve", CATEGORY, "again")]
    problem = reviews(run.path)[0]
    assert (problem["event_id"], problem["first_attempt"], problem["reviewed_at"]) == \
        (help_turn, 1, event_ts(run.path, help_turn))


@pytest.mark.parametrize("status", ["needs_review", None])
def test_the_full_solution_again_is_not_held_for_an_unsettled_reference(tmp_path, status):
    run = Run(tmp_path)
    if status:
        put_reference(run.path, status=status)
    run.start()
    run.open()
    run.help("full_solution")
    assert problem_grade(run.path) == ["again"]


def test_typing_in_the_shown_solution_and_submitting_it_changes_nothing(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.help("full_solution")
    run.accept()
    run.end()
    fake = FakeGraders()
    grade_pending(fake, db_path=run.path)
    assert fake.calls == []
    assert grades(run.path) == [("problem", MEDIUM, "again"), ("category:solve", CATEGORY, "again")]


def test_a_second_full_solution_writes_no_second_again(tmp_path):
    run = Run(tmp_path)
    run.start()
    run.open()
    run.help("full_solution")
    run.help("full_solution")
    assert len(reviews(run.path)) == 2  # one for the problem, one for the category


def test_a_full_solution_after_an_accept_changes_nothing(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.accept()                 # brute force, on your own
    run.help("full_solution")    # asked for the optimal one
    assert reviews(run.path) == []
    run.accept()                 # typed in the optimal one
    run.end()
    labels = verdict(accepts=[(-2, "sort", False), (-1, "heap", True)])
    grade_pending(FakeGraders(labels, labels), db_path=run.path)
    assert grades(run.path) == [("problem", MEDIUM, "hard"), ("category:solve", CATEGORY, "hard")]


def test_the_debrief_again_is_written_at_once_and_nothing_afterwards_changes_it(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, status="needs_review")
    run.start()
    run.open()
    run.run()
    chosen = last_event_id(run.path, "run_result")  # stands in for the debrief-choice event (#50)
    with open_store(run.path) as db:
        write_missed(db, session_id=1, slug=MEDIUM, event_id=chosen)
    assert grades(run.path) == [("problem", MEDIUM, "again"), ("category:solve", CATEGORY, "again")]
    assert reviews(run.path)[0]["reviewed_at"] == event_ts(run.path, chosen)
    run.accept()  # code typed in during the debrief
    run.end()
    set_reference_status(run.path, MEDIUM, "checked")
    fake = FakeGraders()
    grade_pending(fake, db_path=run.path)
    assert fake.calls == []
    assert grades(run.path) == [("problem", MEDIUM, "again"), ("category:solve", CATEGORY, "again")]


# --- skip and end: no grade, and never removing one --------------------------------------

def test_leaving_without_an_accept_writes_no_review_and_calls_no_grader(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run, help=("question",), accept=False)
    fake = FakeGraders()
    outcome = grade_pending(fake, db_path=run.path)
    assert reviews(run.path) == []
    assert fake.calls == []
    assert outcome.graded == outcome.pending == outcome.failed == []


def test_leaving_without_an_accept_never_removes_an_again_already_written(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    run.help("full_solution")
    run.send(opened(EASY, EASY_TITLE))  # off to another problem: the first is left, unaccepted
    run.end()
    grade_pending(FakeGraders(), db_path=run.path)
    assert grades(run.path) == [("problem", MEDIUM, "again"), ("category:solve", CATEGORY, "again")]


# --- a solve is graded only against a checked reference --------------------------------------

@pytest.mark.parametrize("initial, resolve", [
    ("needs_review", lambda path: set_reference_status(path, MEDIUM, "resolved_by_you")),
    (None, lambda path: put_reference(path)),  # written later, once the Reference writer is back
])
def test_an_attempt_without_a_checked_reference_is_stored_ungraded_then_graded_once_settled(tmp_path, initial, resolve):
    run = Run(tmp_path)
    if initial:
        put_reference(run.path, status=initial)
    left = attempt(run)
    fake = FakeGraders()
    outcome = grade_pending(fake, db_path=run.path)
    assert fake.calls == []
    assert outcome.pending == [left]
    assert [(r["item_type"], r["grade"], r["first_attempt"], r["event_id"]) for r in reviews(run.path)] == \
        [("problem", None, 1, left), ("category:solve", None, 1, left)]
    grade_pending(fake, db_path=run.path)
    assert problem_grade(run.path) == [None]  # still waiting: nothing guessed, no grader called
    assert fake.calls == []
    resolve(run.path)
    outcome = grade_pending(fake, db_path=run.path)
    assert outcome.graded == [left]
    assert grades(run.path) == [("problem", MEDIUM, "good"), ("category:solve", CATEGORY, "good")]  # never capped
    assert reviews(run.path)[0]["reviewed_at"] == event_ts(run.path, left)


def test_a_dispute_arising_mid_attempt_holds_the_grade(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    set_reference_status(run.path, MEDIUM, "needs_review")  # e.g. "Seems wrong" on a Tutor message
    run.accept()
    run.end()
    fake = FakeGraders()
    grade_pending(fake, db_path=run.path)
    assert fake.calls == []
    assert problem_grade(run.path) == [None]


def test_two_pending_attempts_are_dated_and_ordered_by_attempt(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, status="needs_review")
    first = attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    run.advance_days(7)
    second = attempt(run)  # the pending problem opened again
    grade_pending(FakeGraders(), db_path=run.path)
    set_reference_status(run.path, MEDIUM, "resolved_by_you")
    grade_pending(FakeGraders(), db_path=run.path)
    problem_rows = [r for r in reviews(run.path) if r["item_type"] == "problem"]
    assert [(r["event_id"], r["grade"], r["first_attempt"], r["reviewed_at"]) for r in problem_rows] == [
        (first, "good", 1, event_ts(run.path, first)), (second, "good", 0, event_ts(run.path, second))]
    assert [r["event_id"] for r in reviews(run.path) if r["item_type"] == "category:solve"] == [first]
    with open_store(run.path) as db:
        now = datetime.fromisoformat(event_ts(run.path, second))
        assert item_memory(db, "problem", MEDIUM, now).stability == pytest.approx(21.4, abs=0.1)


# --- first attempts ---------------------------------------------------------------------

def test_a_repeat_goes_to_the_problem_only(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    run.advance_days(7)
    second = attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    later = [r for r in reviews(run.path) if r["event_id"] == second]
    assert [(r["item_type"], r["grade"], r["first_attempt"]) for r in later] == [("problem", "good", 0)]


def test_an_imported_solve_makes_the_problem_seen(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    import_history(["https://leetcode.com/problems/top-k-frequent-elements/"], path=run.path)
    attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    assert [(r["item_type"], r["grade"], r["first_attempt"]) for r in reviews(run.path)] == [("problem", "good", 0)]


@pytest.mark.parametrize("earlier, first", [("opened", 1), ("ran", 0), ("submitted", 0), ("asked", 0)])
def test_an_unfinished_attempt_makes_the_problem_seen_only_if_you_worked_on_it(tmp_path, earlier, first):
    run = Run(tmp_path)
    put_reference(run.path)
    run.start()
    run.open()
    if earlier == "ran":
        run.run()
    elif earlier == "submitted":
        run.send(submit(MEDIUM, False))
    elif earlier == "asked":
        run.help("question")
    run.end()
    run.advance_days(1)
    attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    problem = [r for r in reviews(run.path) if r["item_type"] == "problem"]
    assert [(r["grade"], r["first_attempt"]) for r in problem] == [("good", first)]
    assert len(reviews(run.path)) == (2 if first else 1)


def test_first_attempt_is_stored_with_the_review_and_survives_a_correction(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    first = attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    run.advance_days(7)
    second = attempt(run)
    grade_pending(FakeGraders(), db_path=run.path)
    with open_store(run.path) as db:  # a Progress-tab correction removes the first attempt's reviews
        db.execute("DELETE FROM reviews WHERE event_id = ?", (first,))
    grade_pending(FakeGraders(), db_path=run.path)
    assert [(r["item_type"], r["first_attempt"], r["event_id"]) for r in reviews(run.path)] == [("problem", 0, second)]


# --- category: Good on Easy counts as Hard ------------------------------------------------

def test_a_good_on_an_easy_problem_counts_as_hard_for_the_category_only(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, slug=EASY)
    attempt(run, slug=EASY, title=EASY_TITLE)
    grade_pending(FakeGraders(), db_path=run.path)
    assert grades(run.path) == [("problem", EASY, "good"), ("category:solve", CATEGORY, "hard")]
    assert [r["difficulty"] for r in reviews(run.path)] == ["Easy", "Easy"]


# --- FSRS state after grading -----------------------------------------------------------

def test_memory_after_a_clean_first_solve_comes_from_the_new_reviews(tmp_path):
    run, _ = graded_once(tmp_path)
    left = left_ids(run.path)[-1]
    now = datetime.fromisoformat(event_ts(run.path, left))
    with open_store(run.path) as db:
        assert item_memory(db, "problem", MEDIUM, now).stability == pytest.approx(2.3065, abs=0.01)
        assert item_memory(db, "category:solve", CATEGORY, now).stability == pytest.approx(2.3065, abs=0.01)


# --- grades are never shown after a problem -------------------------------------------------

def _mentions_grade(value):
    if isinstance(value, dict):
        return any("grade" in str(key) or _mentions_grade(v) for key, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_mentions_grade(v) for v in value)
    return isinstance(value, str) and value.lower() in GRADE_WORDS


def test_no_grade_is_sent_to_the_panel(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path)
    attempt(run, help=("question",))
    outcome = grade_pending(FakeGraders(), db_path=run.path)
    run.start()
    run.open(EASY, EASY_TITLE)
    run.help("full_solution")
    run.end()
    assert problem_grade(run.path, EASY) == ["again"]
    assert not _mentions_grade(run.sent)
    assert not _mentions_grade(vars(outcome))
