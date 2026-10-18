"""FSRS memory is always recomputed from the review log, in attempt order (SPEC §8, §11).

Expected stabilities are py-fsrs 6.3.2's published defaults, no learning steps, 0.9 retention:
first review Good 2.3065 days, Hard 1.2931; Good then Good a week later 21.4 (SPEC §9)."""

from datetime import datetime, timedelta, timezone

import pytest

from companion.memory import item_memory
from companion.store import open_store

DAY0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
SLUG = "top-k-elements-in-list"


def day(n):
    return DAY0 + timedelta(days=n)


def add_review(db, grade, at, *, item_type="problem", item_key=SLUG, first_attempt=False):
    return db.execute(
        "INSERT INTO reviews (item_type, item_key, grade, meets_expected, first_attempt, difficulty, reviewed_at) "
        "VALUES (?, ?, ?, 1, ?, 'Medium', ?)", (item_type, item_key, grade, int(first_attempt), at.isoformat())).lastrowid


def test_an_item_with_no_graded_review_has_no_memory(tmp_path):
    with open_store(tmp_path / "db") as db:
        assert item_memory(db, "problem", SLUG, day(1)) is None


def test_one_clean_solve_gives_about_2_3_days(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))
        memory = item_memory(db, "problem", SLUG, day(0))
    assert memory.stability == pytest.approx(2.3065, abs=0.01)


def test_a_first_hard_gives_about_1_3_days(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "hard", day(0), item_type="category:solve", item_key="Arrays & Hashing")
        memory = item_memory(db, "category:solve", "Arrays & Hashing", day(0))
    assert memory.stability == pytest.approx(1.2931, abs=0.01)


def test_two_clean_solves_a_week_apart_give_about_21_4_days(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))
        add_review(db, "good", day(7))
        assert item_memory(db, "problem", SLUG, day(7)).stability == pytest.approx(21.4, abs=0.1)


def test_reviews_are_replayed_in_attempt_order_not_write_order(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(7))  # a pending attempt graded later is written later
        add_review(db, "good", day(0))
        assert item_memory(db, "problem", SLUG, day(7)).stability == pytest.approx(21.4, abs=0.1)


def test_an_again_collapses_stability(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))
        add_review(db, "good", day(7))
        add_review(db, "again", day(28))
        memory = item_memory(db, "problem", SLUG, day(28))
    assert memory.stability < 5
    assert memory.last_grade == "again"


def test_recall_now_crosses_ninety_percent_at_the_stability(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))  # 2.3 days
        assert item_memory(db, "problem", SLUG, day(2)).recall > 0.9 > item_memory(db, "problem", SLUG, day(3)).recall


def test_an_ungraded_attempt_is_not_a_review(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))
        add_review(db, None, day(3))  # pending or both graders failed
        assert item_memory(db, "problem", SLUG, day(3)).stability == pytest.approx(2.3065, abs=0.01)


def test_a_correction_to_the_log_is_reflected_the_next_time_memory_is_read(tmp_path):
    with open_store(tmp_path / "db") as db:
        first = add_review(db, "good", day(0))
        add_review(db, "good", day(7))
        assert item_memory(db, "problem", SLUG, day(7)).stability == pytest.approx(21.4, abs=0.1)
        db.execute("UPDATE reviews SET grade = 'hard' WHERE id = ?", (first,))
        assert item_memory(db, "problem", SLUG, day(7)).stability == pytest.approx(11.6, abs=0.1)
        db.execute("DELETE FROM reviews WHERE id = ?", (first,))
        assert item_memory(db, "problem", SLUG, day(7)).stability == pytest.approx(2.3065, abs=0.01)


def test_items_are_kept_apart(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))
        add_review(db, "again", day(0), item_type="category:solve", item_key="Arrays & Hashing")
        assert item_memory(db, "problem", SLUG, day(0)).last_grade == "good"
        assert item_memory(db, "category:solve", "Arrays & Hashing", day(0)).last_grade == "again"
        assert item_memory(db, "category:analysis", "Arrays & Hashing", day(0)) is None


def test_the_pinned_fsrs_version_and_retention_are_stored(tmp_path):
    with open_store(tmp_path / "db") as db:
        add_review(db, "good", day(0))
        item_memory(db, "problem", SLUG, day(0))
        row = db.execute("SELECT library_version, desired_retention, strong_threshold_days FROM fsrs").fetchone()
    assert row == ("6.3.2", 0.9, 21)
