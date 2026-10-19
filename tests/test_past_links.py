"""Past links: built in code from the learner's history, ranked and excluded as SPEC §6 says."""

import json
import logging

from companion.past_links import build_past_links
from companion.reference import Approach, Reference, StoredReference
from companion.store import open_store

BASE = "2026-10-01T09:00:00+00:00"


def current_reference(patterns=("hash-map",), category="Arrays & Hashing"):
    return StoredReference(Reference(
        slug="current", title="Current", difficulty="Medium", neetcode_category=category,
        summary="Return the thing the problem asks for.", patterns=list(patterns), analysis_concepts=[],
        approaches=[Approach(id="main", name="Main idea", idea="One line", time="O(n)", space="O(n)",
                             interview_expected="usually", trick=False)]),
        "checked", [])


def seed_reference(db, slug, *, title, category, patterns, summary=None, approach="Main idea"):
    content = {"slug": slug, "title": title, "difficulty": "Medium", "neetcode_category": category,
               "patterns": list(patterns), "analysis_concepts": [],
               "approaches": [{"id": "main", "name": approach, "idea": "One line", "time": "O(n)",
                               "space": "O(n)", "interview_expected": "usually", "trick": False}]}
    if summary is not None:
        content["summary"] = summary
    with open_store(db) as connection:
        connection.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)',
                           (slug, json.dumps(content), "checked"))


def event(db, session_id, at, kind, payload):
    with open_store(db) as connection:
        connection.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                           (session_id, f"2026-10-{at:02d}T09:00:00+00:00", kind, json.dumps(payload)))


def session(db, session_id, *, excluded=0):
    with open_store(db) as connection:
        connection.execute("INSERT INTO sessions (id, started_at, ended_at, excluded) VALUES (?, ?, ?, ?)",
                           (session_id, BASE, BASE, excluded))


def attempt(db, session_id, slug, title, *, at=1, accepted=False, help_kind=None, left=True):
    event(db, session_id, at, "problem_opened", {"site": "neetcode", "slug": slug, "title": title})
    event(db, session_id, at, "run_result", {"slug": slug, "cases": [
        {"passed": True, "error_type": None, "error_message": "", "runtime": "0.02", "failing_case": None}]})
    if help_kind is not None:
        event(db, session_id, at, "help_turn", {"turn_id": 0, "slug": slug, "request": "help", "press_index": 1,
                                                "gap": "weak_intuition", "gap_note": None,
                                                "help_kind": help_kind, "reply_text": "A hint.",
                                                "follow_up_question": None, "model": "x-test-1"})
    if accepted:
        event(db, session_id, at, "submit_result", {"slug": slug, "accepted": True, "passed": 1, "total": 1,
                                                    "failing_case": None})
    if left:
        event(db, session_id, at, "problem_left", {"slug": slug, "accepted": accepted})


def links(db, reference=None):
    with open_store(db) as connection:
        return build_past_links(connection, "current", reference=reference or current_reference())


def test_a_problem_only_imported_as_solved_never_appears(tmp_path):
    db = tmp_path / "db"
    with open_store(db) as connection:
        connection.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (NULL, ?, 'prior_solve', ?)",
                           (BASE, json.dumps({"slug": "old", "source": "neetcode"})))
    seed_reference(db, "old", title="Old", category="Arrays & Hashing", patterns=["hash-map"], summary="Ask the old thing.")
    session(db, 1)
    attempt(db, 1, "current", "Current", at=2)
    assert links(db) == []


def test_the_current_problem_never_appears_even_from_an_earlier_session(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "current", title="Current", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the current thing.")
    attempt(db, 1, "current", "Current", at=1, accepted=True)
    assert links(db) == []


def test_a_session_excluded_from_measurement_never_appears(tmp_path):
    db = tmp_path / "db"
    session(db, 1, excluded=1)
    seed_reference(db, "alpha", title="Alpha", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the alpha thing.")
    attempt(db, 1, "alpha", "Alpha", at=1, accepted=True)
    assert links(db) == []


def test_a_shared_pattern_comes_before_a_shared_category_even_when_older_and_unfinished(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "pattern", title="Pattern One", category="Trees", patterns=["hash-map"],
                   summary="Ask the pattern thing.")
    seed_reference(db, "category", title="Category One", category="Arrays & Hashing", patterns=["two-pointers"],
                   summary="Ask the category thing.")
    attempt(db, 1, "pattern", "Pattern One", at=1, accepted=False)
    attempt(db, 1, "category", "Category One", at=2, accepted=True)
    assert [link.slug for link in links(db)] == ["pattern", "category"]


def test_within_a_bucket_a_solve_or_working_hint_comes_before_unfinished_then_recent(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "alone", title="Alone", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the alone thing.")
    seed_reference(db, "helped", title="Helped", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the helped thing.")
    seed_reference(db, "unfinished", title="Unfinished", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the unfinished thing.")
    attempt(db, 1, "alone", "Alone", at=1, accepted=True)
    attempt(db, 1, "helped", "Helped", at=3, accepted=True, help_kind="question")
    attempt(db, 1, "unfinished", "Unfinished", at=4, accepted=False)
    assert [link.slug for link in links(db)] == ["helped", "alone", "unfinished"]
    assert links(db)[0].outcome == "solved with a question"


def test_a_full_solution_is_left_out_when_anything_else_fits_and_kept_when_alone(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "full", title="Full", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the full thing.")
    seed_reference(db, "other", title="Other", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the other thing.")
    attempt(db, 1, "full", "Full", at=1, accepted=False, help_kind="full_solution")
    attempt(db, 1, "other", "Other", at=2, accepted=False)
    assert [link.slug for link in links(db)] == ["other"]
    with open_store(db) as connection:
        connection.execute("DELETE FROM events WHERE json_extract(payload, '$.slug') = 'other'")
    assert [link.slug for link in links(db)] == ["full"]
    assert links(db)[0].outcome == "shown the full solution"


def test_an_unfinished_attempt_counts_with_its_honest_outcome(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "unfinished", title="Unfinished", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the unfinished thing.")
    attempt(db, 1, "unfinished", "Unfinished", at=1, accepted=False, left=False)
    [link] = links(db)
    assert link.outcome == "unfinished"


def test_a_missing_summary_shows_the_title_only_and_logs_the_gap(tmp_path, caplog):
    db = tmp_path / "db"
    session(db, 1)
    seed_reference(db, "alpha", title="Alpha", category="Arrays & Hashing", patterns=["hash-map"])
    attempt(db, 1, "alpha", "Alpha", at=1, accepted=True)
    with caplog.at_level(logging.WARNING):
        [link] = links(db)
    assert link.title == "Alpha" and link.summary is None
    assert "alpha" in caplog.text and "summary" in caplog.text


def test_the_idea_comes_from_teaching_memory_and_not_from_the_current_session(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    session(db, 2)
    seed_reference(db, "alpha", title="Alpha", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the alpha thing.")
    attempt(db, 1, "alpha", "Alpha", at=1, accepted=True)
    event(db, 1, 1, "session_ended", {})
    with open_store(db) as connection:
        first_event = connection.execute("SELECT MIN(id) FROM events WHERE session_id = 1").fetchone()[0]
        connection.execute("INSERT INTO teaching (problem_slug, cited_event_ids, idea, category, framing, help_kind) "
                           "VALUES ('alpha', ?, 'Sorting lets two pointers move inward.', 'Arrays & Hashing', 'draw it', 'question')",
                           (json.dumps([first_event]),))
    with open_store(db) as connection:
        [link] = build_past_links(connection, "current", reference=current_reference(), current_session_id=2)
    assert link.idea == "Sorting lets two pointers move inward."
    with open_store(db) as connection:
        [link] = build_past_links(connection, "current", reference=current_reference(), current_session_id=1)
    assert link.idea is None


def test_the_same_problem_in_a_second_session_is_a_new_attempt_not_a_merged_one(tmp_path):
    db = tmp_path / "db"
    session(db, 1)
    session(db, 2)
    seed_reference(db, "alpha", title="Alpha", category="Arrays & Hashing", patterns=["hash-map"],
                   summary="Ask the alpha thing.")
    attempt(db, 1, "alpha", "Alpha", at=1, accepted=True)
    attempt(db, 2, "alpha", "Alpha", at=2, accepted=False, left=False)
    [link] = links(db)
    assert link.outcome == "unfinished"
