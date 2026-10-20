from dataclasses import replace

import pytest

from simulation.catalog import CATEGORIES, build_problems
from simulation.memory import Rating
from simulation.simulate import Scenario, _Run, simulate

import random


def test_catalog_is_the_neetcode_150_with_its_difficulty_mix():
    assert sum(sum(mix) for mix in CATEGORIES.values()) == 150
    easy, medium, hard = (sum(mix[i] for mix in CATEGORIES.values()) for i in range(3))
    assert (easy, medium, hard) == (28, 101, 21)
    assert CATEGORIES["Tries"] == (0, 2, 1)


def test_build_marks_imported_among_the_150_and_spreads_100_extras():
    problems = build_problems(random.Random(1), imported=75)
    core = [p for p in problems if not p.extra]
    assert len(core) == 150 and sum(p.imported for p in core) == 75
    extras = [p for p in problems if p.extra]
    assert len(extras) == 100 and not any(p.imported for p in extras)


def test_a_flawless_learner_reaches_the_bar_and_the_run_is_repeatable():
    sc = Scenario(clean=1.0, analysis_good=1.0)
    first = simulate(sc, seed=3)
    assert first.day_reached is not None
    assert simulate(sc, seed=3) == first


def test_a_learner_who_never_solves_cleanly_never_reaches_the_bar():
    result = simulate(Scenario(clean=0.0, horizon_days=200), seed=1)
    assert result.day_reached is None


def test_a_break_delays_sessions():
    base = simulate(Scenario(clean=1.0, analysis_good=1.0), seed=3)
    broken = simulate(replace(Scenario(clean=1.0, analysis_good=1.0), break_start=14, break_days=90), seed=3)
    assert broken.day_reached > base.day_reached


def test_current_policy_results_are_unchanged():
    result = simulate(Scenario(clean=1.0, analysis_good=1.0), seed=3)
    assert (result.day_reached, result.attempts) == (165, 166)


def _run_with_done_problem(policy: str):
    run = _Run(Scenario(policy=policy), seed=0)
    run.day = 100
    problem = run.core[0]
    problem.memory.review(Rating.Good, day=0)
    problem.graded = 1
    problem.done = True
    problem.last_day = 0
    return run, problem


def test_split_reviews_a_done_problem_once_its_recall_falls():
    run, problem = _run_with_done_problem("split")
    assert run.pick() == (problem, "review")


def test_current_never_reviews_a_done_problem():
    run, problem = _run_with_done_problem("current")
    picked, kind = run.pick()
    assert kind != "review"
    assert picked is not problem


@pytest.mark.parametrize("return_kind", ["review", "retry"])
def test_split_does_not_return_twice_while_an_ungraded_problem_exists(return_kind: str):
    run = _Run(Scenario(policy="split"), seed=0)
    run.day = 100 if return_kind == "review" else 3
    problem = run.core[0]
    problem.memory.review(Rating.Good if return_kind == "review" else Rating.Again, day=0)
    problem.graded = 1
    problem.done = return_kind == "review"
    problem.last_day = 0
    assert run.pick() == (problem, return_kind)
    assert any(p.graded == 0 for p in run.core)
    assert run.pick()[1] not in ("retry", "review")
