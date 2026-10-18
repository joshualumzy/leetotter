"""The fixed grade table (SPEC §8) as pure code: labels in, grade out. Expected grades are the table's rows."""

import pytest

from companion.grading import analysis_counts, analysis_grade, category_grade, combine, solve_grade


# --- solve rows ------------------------------------------------------------------

def test_solved_without_help_with_the_usually_expected_approach_is_good():
    assert solve_grade([], meets_expected=True) == "good"


@pytest.mark.parametrize("kinds", [["question"], ["trace"], ["analogy"], ["teach_tool"],
                                   ["question", "trace", "analogy", "teach_tool"]])
def test_solved_after_questions_traces_analogies_or_a_taught_tool_only_is_hard(kinds):
    assert solve_grade(kinds, meets_expected=True) == "hard"


def test_solved_without_help_but_with_a_worse_approach_than_usually_expected_is_hard():
    assert solve_grade([], meets_expected=False) == "hard"


def test_light_help_and_a_worse_approach_is_still_hard():
    assert solve_grade(["question"], meets_expected=False) == "hard"


@pytest.mark.parametrize("kinds", [["snippet"], ["partial_logic"], ["question", "snippet"], ["trace", "partial_logic"]])
def test_needing_snippets_or_partial_logic_is_again(kinds):
    assert solve_grade(kinds, meets_expected=True) == "again"


def test_a_full_solution_label_is_again():
    assert solve_grade(["full_solution"], meets_expected=True) == "again"


@pytest.mark.parametrize("kinds, expected", [(["syntax"], "good"), (["syntax", "question"], "hard"),
                                             (["syntax", "syntax"], "good")])
def test_syntax_help_does_not_lower_a_grade(kinds, expected):
    assert solve_grade(kinds, meets_expected=True) == expected


def test_a_reply_labelled_none_is_not_help():
    assert solve_grade(["none", "none"], meets_expected=True) == "good"


def test_an_unknown_help_kind_is_a_loud_error():
    with pytest.raises(ValueError):
        solve_grade(["hint"], meets_expected=True)


# --- analysis rows ---------------------------------------------------------------

@pytest.mark.parametrize("verdict, expected", [
    ("right", "good"),                 # right, with right reasoning
    ("right_after_followup", "hard"),  # right after one guiding question that doesn't state the answer
    ("wrong", "again"),                # wrong, or right only after the Tutor stated it
    ("wrong_reasoning", "again"),      # right with wrong reasoning
])
def test_complexity_verdicts_map_to_the_analysis_rows(verdict, expected):
    assert analysis_grade(verdict) == expected


def test_no_reasoning_even_after_being_asked_is_like_skipping_the_question():
    assert analysis_grade("no_reasoning") is None


def test_an_unknown_complexity_verdict_is_a_loud_error():
    with pytest.raises(ValueError):
        analysis_grade("mostly_right")


def test_an_analysis_grade_counts_on_a_first_attempt_whose_solve_was_not_again():
    assert analysis_counts(first_attempt=True, solve_grade="good", complexity_stated=False) is True
    assert analysis_counts(first_attempt=True, solve_grade="hard", complexity_stated=False) is True


def test_an_analysis_grade_never_counts_on_a_repeat():
    assert analysis_counts(first_attempt=False, solve_grade="good", complexity_stated=False) is False


def test_an_analysis_grade_never_counts_when_the_solve_was_again():
    assert analysis_counts(first_attempt=True, solve_grade="again", complexity_stated=False) is False


def test_an_analysis_grade_never_counts_when_the_tutor_stated_the_complexity_earlier():
    assert analysis_counts(first_attempt=True, solve_grade="good", complexity_stated=True) is False


# --- two graders -----------------------------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ("good", "good", ("good", False)), ("hard", "hard", ("hard", False)), ("again", "again", ("again", False)),
    ("good", "hard", ("hard", True)), ("hard", "good", ("hard", True)),
    ("good", "again", ("again", True)), ("again", "hard", ("again", True)),
])
def test_two_graders_keep_the_lower_grade_and_say_whether_they_disagreed(a, b, expected):
    assert combine(a, b) == expected


# --- the category's solve --------------------------------------------------------

@pytest.mark.parametrize("grade, difficulty, expected", [
    ("good", "Easy", "hard"),
    ("good", "Medium", "good"), ("good", "Hard", "good"),
    ("hard", "Easy", "hard"), ("again", "Easy", "again"),
    ("hard", "Medium", "hard"), ("again", "Hard", "again"),
])
def test_a_good_on_an_easy_problem_counts_as_hard_for_the_category_only(grade, difficulty, expected):
    assert category_grade(grade, difficulty) == expected
