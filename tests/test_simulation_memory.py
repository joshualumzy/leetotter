"""The simulator's FSRS use against py-fsrs 6.3.2's published behaviour (SPEC §9: 2.3 and 21.4 days)."""

import pytest

from simulation.memory import Item, Rating


def test_one_clean_solve_gives_about_2_3_days():
    item = Item()
    item.review(Rating.Good, day=0)
    assert item.stability == pytest.approx(2.3, abs=0.05)


def test_two_clean_solves_a_week_apart_give_about_21_4_days():
    item = Item()
    item.review(Rating.Good, day=0)
    item.review(Rating.Good, day=7)
    assert item.stability == pytest.approx(21.4, abs=0.1)


def test_recall_crosses_ninety_percent_at_the_stability():
    item = Item()
    item.review(Rating.Good, day=0)  # stability 2.3 days
    assert item.recall(day=2) > 0.9 > item.recall(day=3)


def test_a_failure_collapses_stability():
    item = Item()
    item.review(Rating.Good, day=0)
    item.review(Rating.Good, day=7)
    item.review(Rating.Again, day=28)
    assert item.stability < 5
    assert item.last_grade == Rating.Again


def test_same_day_repeats_add_little():
    item = Item()
    item.review(Rating.Good, day=0)
    item.review(Rating.Good, day=0)
    assert item.stability < 4


def test_hard_grows_stability_less_than_good():
    hard, good = Item(), Item()
    for item, grade in ((hard, Rating.Hard), (good, Rating.Good)):
        item.review(Rating.Good, day=0)
        item.review(grade, day=7)
    assert hard.stability < good.stability


def test_defaults_are_unfuzzed_with_no_learning_steps_and_retention_point_nine():
    item = Item()
    item.review(Rating.Good, day=0)
    assert item.card.due.timestamp() - item.card.last_review.timestamp() == 2 * 86400
