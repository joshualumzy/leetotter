"""What the Tutor is told: every SPEC section 6 context item, present or honestly absent."""

import pytest

from companion.reference import Approach, Reference, StoredReference
from companion.tutor import TutorRequest, answered_presses, build_messages, reply_schema

PROBLEM = {"slug": "contains-duplicate", "title": "Contains Duplicate"}
STATEMENT = "Given an integer array nums, return true if any value appears more than once."
REFERENCE = StoredReference(Reference(
    slug="contains-duplicate", title="Contains Duplicate", difficulty="Easy", neetcode_category="Arrays & Hashing",
    patterns=["hash-set"], analysis_concepts=["hash-lookup"],
    approaches=[Approach(id="brute", name="Compare every pair", idea="Two loops", time="O(n^2)", space="O(1)", interview_expected="rarely", trick=False),
                Approach(id="set", name="Hash set", idea="Remember seen values", time="O(n)", space="O(n)", interview_expected="usually", trick=False)]),
    "needs_review", ["set.complexity"])

HELP_1 = TutorRequest("help", 1, None)


def text(messages):
    """The context the Tutor is given (the user message); the system prompt is the same every time."""
    return messages[-1]["content"]


def build(events=(), *, request=HELP_1, reference=REFERENCE, prior_solve=False, problem_seconds=18 * 60, seconds_left=27 * 60):
    return text(build_messages(PROBLEM, list(events), reference=reference, prior_solve=prior_solve,
                               problem_seconds=problem_seconds, seconds_left=seconds_left, request=request))


def stmt(statement=STATEMENT, slug="contains-duplicate"):
    return ("problem_statement", {"slug": slug, "statement": statement})


def turn(reply, gap="stuck_optimising", kind="question", request="help", press_index=1):
    return ("help_turn", {"turn_id": 7, "slug": PROBLEM["slug"], "request": request, "press_index": press_index,
                          "gap": gap, "gap_note": None, "help_kind": kind, "reply_text": reply,
                          "follow_up_question": None, "model": "x-test-1"})


def test_system_prompt_comes_first_and_carries_the_gap_table_and_rules():
    messages = build_messages(PROBLEM, [], reference=None, prior_solve=False, problem_seconds=0, seconds_left=60, request=HELP_1)
    assert [m["role"] for m in messages] == ["system", "user"]
    system = messages[0]["content"]
    for gap in ("misread_problem", "missing_tool", "pattern_not_recognised", "weak_intuition", "wrong_intuition",
                "stuck_optimising", "cant_write_code", "bugs_and_edge_cases", "other"):
        assert gap in system
    assert "Brute force is never a wrong path" in system
    assert "follow their approach" in system.lower()


def test_problem_title_slug_and_statement():
    out = build([stmt()])
    assert "Contains Duplicate" in out and "contains-duplicate" in out and STATEMENT in out
    assert "statement isn't available" not in out


def test_missing_statement_is_said_plainly():
    out = build([stmt(slug="two-sum")])
    assert STATEMENT not in out
    assert "statement isn't available" in out


def test_the_latest_statement_wins():
    out = build([stmt("old text"), stmt("new text")])
    assert "new text" in out and "old text" not in out


def test_reference_is_helper_knowledge_with_its_status_and_uncertain_labels():
    out = build()
    assert "Hash set" in out and "O(n^2)" in out and "usually" in out
    assert "needs_review" in out and "set.complexity" in out
    assert "follow the learner's approach" in out.lower() and "not for quoting" in out
    assert "Arrays & Hashing" not in out and "hash-set" not in out


def test_seems_wrong_flag_does_not_make_any_tutor_label_uncertain():
    out = build(reference=StoredReference(REFERENCE.reference, "needs_review", []))
    assert "needs_review" in out
    assert "uncertain" not in out and "unspecified" not in out


def test_missing_reference_is_said_plainly():
    out = build(reference=None)
    assert "no reference" in out.lower() and "Hash set" not in out


def test_current_code_and_recent_edits():
    out = build([("code_snapshot", {"code": "def f(nums):\n    pass"}),
                 ("code_changed", {"diff": {"start_line": 2, "removed": ["    pass"], "added": ["    return len(set(nums)) < len(nums)"]}})])
    assert "return len(set(nums)) < len(nums)" in out
    assert "Recent edits" in out and "line 2" in out


def test_no_code_yet_is_said_and_help_still_given():
    out = build()
    assert "hasn't written any code" in out


def test_code_that_may_be_behind_is_flagged():
    out = build([("code_snapshot", {"code": "a = 1"}), ("code_changed", {"diff": {"start_line": 9, "removed": ["x"], "added": []}})])
    assert "a = 1" in out and "slightly behind" in out


def test_run_and_submit_results_with_failing_case_and_error():
    run = ("run_result", {"slug": "contains-duplicate", "cases": [
        {"passed": True, "error_type": None, "error_message": "", "runtime": "0.02", "failing_case": None},
        {"passed": False, "error_type": "wrong_answer", "error_message": "", "runtime": "0.02",
         "failing_case": {"input": "nums=[1,1]", "expected": "true", "actual": "false"}}]})
    submit = ("submit_result", {"slug": "contains-duplicate", "accepted": False, "error_type": "time_limit_exceeded",
                                "error_message": "Time Limit Exceeded", "runtime": "", "memory": 0, "passed": 20, "total": 34, "failing_case": None})
    other = ("run_result", {"slug": "two-sum", "cases": [{"passed": False, "error_type": "runtime_error", "error_message": "KeyError: 9", "runtime": "0", "failing_case": None}]})
    out = build([run, submit, other])
    assert "1 of 2" in out and "nums=[1,1]" in out and "expected true" in out and "got false" in out
    assert "20 of 34" in out and "time_limit_exceeded" in out and "Time Limit Exceeded" in out
    assert "KeyError" not in out


def test_no_runs_yet_is_said():
    assert "hasn't run or submitted" in build()


def test_time_on_problem_and_time_left():
    out = build(problem_seconds=18 * 60 + 20, seconds_left=27 * 60)
    assert "18 min" in out and "27 min" in out
    assert "the problem's cap ran out 4 min ago" in build(seconds_left=-4 * 60)


def test_prior_solve_on_neetcode():
    assert "solved this problem before on NeetCode" in build(prior_solve=True)
    assert "solved this problem before on NeetCode" not in build(prior_solve=False)


def test_conversation_so_far_with_labels():
    out = build([("chat_message", {"slug": PROBLEM["slug"], "text": "is a set ok?"}),
                 turn("What does a set give you?", gap="weak_intuition"),
                 ("help_request", {"slug": PROBLEM["slug"]}),
                 turn("Think about lookups.", kind="trace", press_index=1)])
    assert "is a set ok?" in out and "What does a set give you?" in out and "Think about lookups." in out
    assert "weak_intuition" in out and "trace" in out and 'pressed "I need help"' in out


def test_first_press_asks_for_the_smallest_hint_and_never_the_solution():
    out = build(request=TutorRequest("help", 1, None))
    assert "smallest hint beyond what you've already given" in out and "never the solution" in out


def test_a_further_press_asks_for_the_next_more_concrete_hint_with_no_questions_first():
    out = build([turn("What repeats?")], request=TutorRequest("help", 2, None))
    assert "press 2" in out and "one rung more concrete" in out and "the last was question" in out
    assert "Don't ask a diagnostic question before helping" in out


def test_after_a_full_solution_only_what_is_still_needed():
    out = build([turn("Here is the whole thing", kind="full_solution")], request=TutorRequest("help", 2, None))
    assert "full solution has already been shown" in out


def test_a_chat_message_is_quoted_and_answered_the_same_way():
    out = build(request=TutorRequest("chat", None, "why does [1,1] fail?"))
    assert "why does [1,1] fail?" in out and "diagnose" in out.lower()
    assert "lighter help has already been tried" in out


def test_long_code_is_trimmed_with_a_notice():
    code = "x = 1\n" * 5000
    out = build([("code_snapshot", {"code": code})])
    assert "characters trimmed" in out and len(out) < 30000


def test_long_conversation_keeps_the_latest_and_says_how_many_were_dropped():
    events = [("chat_message", {"slug": PROBLEM["slug"], "text": f"message {i} " + "y" * 500}) for i in range(100)]
    out = build(events)
    assert "message 99 " in out and "message 0 " not in out
    assert "earlier messages were left out" in out


def test_answered_presses_count_only_presses_that_got_a_reply():
    events = [("help_request", {}), turn("a"), ("help_request", {}),
              ("help_failed", {"turn_id": 3, "reason": "timeout"}), ("chat_message", {"text": "hi"}),
              turn("b", request="chat", press_index=None)]
    assert answered_presses(events) == 1


@pytest.mark.parametrize("request_, kinds", [
    (TutorRequest("help", 1, None), {"question", "trace", "analogy", "teach_tool", "syntax", "snippet", "partial_logic"}),
    (TutorRequest("help", 2, None), {"question", "trace", "analogy", "teach_tool", "syntax", "snippet", "partial_logic", "full_solution"}),
    (TutorRequest("chat", None, "thanks"), {"question", "trace", "analogy", "teach_tool", "syntax", "snippet", "partial_logic", "full_solution", "none"}),
])
def test_reply_schema_allows_only_the_help_kinds_a_request_may_get(request_, kinds):
    schema = reply_schema(request_).model_json_schema()
    assert set(schema["properties"]["help_kind"]["enum"]) == kinds


def test_the_prompt_guards_the_measured_skills_and_uses_only_invented_examples():
    from companion.catalog import load_catalog
    from companion.tutor import SYSTEM_PROMPT
    assert "unless the learner has already named or used it" in SYSTEM_PROMPT
    assert "Never state the time or space complexity" in SYSTEM_PROMPT
    assert "never syntax" in SYSTEM_PROMPT
    titles = [entry["title"] for entry in load_catalog().entries]
    assert [t for t in titles if t in SYSTEM_PROMPT] == []


def test_prompt_allows_light_markdown_but_no_headings_or_links():
    from companion.tutor import SYSTEM_PROMPT
    assert "fenced code blocks (each line of code on its own line, indented as in Python)" in SYSTEM_PROMPT
    assert "no headings, tables, images or links" in SYSTEM_PROMPT
    assert "Plain text;" not in SYSTEM_PROMPT
