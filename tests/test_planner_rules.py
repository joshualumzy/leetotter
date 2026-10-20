"""The Planner's rules in code (SPEC §8 "Planning", §9, §13): which problems it may suggest next, the
learner summary it reads, and the code-ranked fallback, which ranks from the same list.

Interface (companion/planner.py) used by this file:

    learner_summary(db, *, now) -> dict[str, dict]
        One entry per NeetCode category (all 18 of companion.catalog.CATEGORIES), each with at least:
          "solve", "analysis": the category's ItemMemory (companion.memory.item_memory) or None
          "recent": {"Easy": [...], "Medium": [...], "Hard": [...]}: the grades ("good" / "hard" / "again") of
                    graded problem reviews in the category, by the review's difficulty; pending ones are left out
          "done": how many of the category's problems are done on your own (latest graded attempt good or
                  hard with meets_expected, SPEC §9)
          "imported": how many of its problems you imported as solved (prior_solve events)

    allowed_next(db, *, session_id, now) -> list[dict]
        The problems code allows next (`session_id` None before a session exists). Each candidate is a dict
        with at least "slug", "title", "category", "difficulty", "retry" (True when the problem already has a
        graded attempt in Leetotter) and "patterns" (its stored reference's patterns, [] without one).

    code_ranked(candidates, summary, steer=None) -> dict
        The fallback pick, one of `candidates` (SPEC §13): with `steer` (an active_steer dict, see
        tests/test_planner_steer.py) the candidates are first filtered on its focus, a filter that is dropped
        when nothing passes it; then the weakest category among them (no `solve` evidence is weakest, else the
        lowest `solve` recall now); within it, a problem not yet done on your own (untried of the 150, or a
        retry not yet done) before an unseen problem beyond the 150, and a done problem (a refresh) last.

Readings chosen where SPEC leaves room (listed in the report):
  - a retry is due when its own recall now is below 0.9; a not-done problem that isn't due isn't offered
  - a skipped problem is one left in this session unaccepted and ungraded
  - "no two retries in a row" looks at the problem left last in this session
  - a rusty category's refresh with a done problem is offered once per session
  - after an Again, one attempt at a same-pattern problem since the Again lets the retry back; patterns are
    known only from stored references
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from companion.catalog import CATEGORIES, load_catalog
from companion.memory import item_memory
from companion.planner import allowed_next, code_ranked, learner_summary
from companion.store import open_store

DAY0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
NOW = DAY0 + timedelta(days=60)
CATALOG = load_catalog()
UNLOCKED = {entry["slug"] for entry in CATALOG.entries if not entry["locked"]}
LOCKED = {entry["slug"] for entry in CATALOG.entries if entry["locked"]}
NC150 = {entry["slug"] for entry in CATALOG.neetcode150()}

# Real catalog entries (companion/data/catalog.json)
CLIMBING = "climbing-stairs"                       # Climbing Stairs, Easy, 1-D DP, 150
HOUSE_ROBBER = "house-robber"                      # House Robber, Medium, 1-D DP, 150
COIN_CHANGE = "coin-change"                        # Coin Change, Medium, 1-D DP, 150
INVERT = "invert-a-binary-tree"                    # Invert Binary Tree, Easy, Trees, 150
SAME_TREE = "same-binary-tree"                     # Same Tree, Easy, Trees, 150
LEVEL_ORDER = "level-order-traversal-of-binary-tree"  # Binary Tree Level Order Traversal, Medium, Trees, 150
RIGHT_VIEW = "binary-tree-right-side-view"         # Binary Tree Right Side View, Medium, Trees, 150
MAX_PATH = "binary-tree-maximum-path-sum"          # Binary Tree Maximum Path Sum, Hard, Trees, 150
GOOD_NODES = "count-good-nodes-in-binary-tree"     # Count Good Nodes In Binary Tree, Medium, Trees, 150
LONGEST_SUB = "longest-substring-without-duplicates"  # Longest Substring Without Repeating Characters, Medium, Sliding Window
PERMUTATION = "permutation-string"                 # Permutation In String, Medium, Sliding Window, 150
REPLACEMENT = "longest-repeating-substring-with-replacement"  # Longest Repeating Character Replacement, Medium, Sliding Window
MIN_SUBARRAY = "minimum-size-subarray-sum"         # Minimum Size Subarray Sum, Medium, Sliding Window, beyond the 150
ISLANDS = "count-number-of-islands"                # Number of Islands, Medium, Graphs, 150
CLONE_GRAPH = "clone-graph"                        # Clone Graph, Medium, Graphs, 150
TRIES = ("implement-prefix-tree", "design-word-search-data-structure", "search-for-word-ii",
         "count-prefix-and-suffix-pairs-i", "counting-words-with-a-given-prefix",
         "remove-sub-folders-from-the-filesystem", "extra-characters-in-a-string")  # every unlocked Tries problem


# --- store fixtures: rows as the companion writes them ------------------------------------------

def day(n):
    return DAY0 + timedelta(days=n)


def entry(slug):
    found = CATALOG.by_slug(slug)
    assert found is not None, slug
    return found


def review(db, slug, grade, at, *, meets=True, first=False, event_id=None):
    """A problem review (grade None is a pending one), optionally tied to its attempt's problem_left event."""
    db.execute("INSERT INTO reviews (event_id, item_type, item_key, grade, meets_expected, first_attempt, difficulty, reviewed_at) "
               "VALUES (?, 'problem', ?, ?, ?, ?, ?, ?)",
               (event_id, slug, grade, None if grade is None else int(meets), int(first), entry(slug)["difficulty"], at.isoformat()))


def category_review(db, category, grade, at, *, skill="solve", difficulty="Medium"):
    db.execute("INSERT INTO reviews (item_type, item_key, grade, meets_expected, first_attempt, difficulty, reviewed_at) "
               "VALUES (?, ?, ?, 1, 1, ?, ?)", (f"category:{skill}", category, grade, difficulty, at.isoformat()))


def steady(db, category):
    """The category's own `solve` memory is fresh (two clean first attempts a week apart, the last one yesterday)."""
    category_review(db, category, "good", NOW - timedelta(days=8))
    category_review(db, category, "good", NOW - timedelta(days=1))


def rusty(db, category):
    """One clean first attempt 30 days ago: stability about 2.3 days, recall now well below 0.9."""
    category_review(db, category, "good", NOW - timedelta(days=30))


def reference(db, slug, patterns):
    found = entry(slug)
    content = {"slug": slug, "title": found["title"], "difficulty": found["difficulty"],
               "neetcode_category": found["category"], "summary": None, "patterns": patterns,
               "analysis_concepts": [], "approaches": [
                   {"id": "main", "name": "main idea", "idea": "the usual approach", "time": "O(n)", "space": "O(1)",
                    "interview_expected": "usually", "trick": False}]}
    db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)',
               (slug, json.dumps(content), "checked"))


def imported(db, slug):
    db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (NULL, ?, 'prior_solve', ?)",
               (DAY0.isoformat(), json.dumps({"slug": slug, "source": "neetcode"})))


def new_session(db, started=NOW):
    return db.execute("INSERT INTO sessions (started_at) VALUES (?)", (started.isoformat(),)).lastrowid


def event(db, session_id, at, kind, payload):
    return db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                      (session_id, at.isoformat(), kind, json.dumps(payload))).lastrowid


def attempt(db, session_id, slug, at=NOW, *, accepted, ran=True):
    """One attempt in a session as the coordinator logs it, left (done, skip or another problem opened)."""
    event(db, session_id, at, "problem_opened", {"site": "neetcode", "slug": slug, "title": entry(slug)["title"]})
    if ran:
        event(db, session_id, at, "run_result", {"slug": slug, "cases": [{"passed": False, "error_type": "wrong_answer",
              "error_message": "", "runtime": "0.03", "failing_case": None}]})
    if accepted:
        event(db, session_id, at, "submit_result", {"slug": slug, "accepted": True, "error_type": None, "error_message": "",
              "runtime": "0.03", "memory": 14000, "passed": 10, "total": 10, "failing_case": None})
    return event(db, session_id, at, "problem_left", {"slug": slug, "accepted": accepted})


def allowed(db, session_id=None, now=NOW):
    return {candidate["slug"] for candidate in allowed_next(db, session_id=session_id, now=now)}


def every_150_tried(db, *, except_=()):
    for slug in NC150 - set(except_):
        review(db, slug, "good", day(1))


@pytest.fixture
def db(tmp_path):
    with open_store(tmp_path / "planner.sqlite3") as connection:
        yield connection


# --- what the list holds --------------------------------------------------------------------------

def test_with_no_history_every_free_problem_is_allowed_and_no_locked_one(db):
    candidates = allowed_next(db, session_id=None, now=NOW)
    assert {c["slug"] for c in candidates} == UNLOCKED
    assert not {c["slug"] for c in candidates} & LOCKED
    assert all(c["retry"] is False for c in candidates)
    first = next(c for c in candidates if c["slug"] == HOUSE_ROBBER)
    assert (first["title"], first["category"], first["difficulty"]) == ("House Robber", "1-D Dynamic Programming", "Medium")


def test_imported_solves_stay_allowed_as_untested_and_are_not_retries(db):
    imported(db, INVERT)
    candidate = next(c for c in allowed_next(db, session_id=None, now=NOW) if c["slug"] == INVERT)
    assert candidate["retry"] is False


def test_candidates_carry_their_reference_patterns(db):
    reference(db, PERMUTATION, ["sliding-window", "frequency-count"])
    by_slug = {c["slug"]: c for c in allowed_next(db, session_id=None, now=NOW)}
    assert by_slug[PERMUTATION]["patterns"] == ["sliding-window", "frequency-count"]
    assert by_slug[MIN_SUBARRAY]["patterns"] == []


# --- the retry rule ---------------------------------------------------------------------------------

def test_a_done_problem_is_not_retried_because_its_recall_dropped(db):
    review(db, INVERT, "good", NOW - timedelta(days=30), first=True)  # its own recall is now about 0.67
    steady(db, "Trees")
    assert INVERT not in allowed(db)


@pytest.mark.parametrize("grade, meets", [("again", False), ("hard", False)])  # real help, or brute force only
def test_a_problem_not_done_on_your_own_comes_back_once_due(db, grade, meets):
    review(db, RIGHT_VIEW, grade, NOW - timedelta(days=10), meets=meets, first=True)
    steady(db, "Trees")
    candidate = next(c for c in allowed_next(db, session_id=None, now=NOW) if c["slug"] == RIGHT_VIEW)
    assert candidate["retry"] is True


def test_a_problem_not_done_on_your_own_waits_until_it_is_due(db):
    review(db, RIGHT_VIEW, "hard", NOW - timedelta(minutes=5), meets=False, first=True)
    assert RIGHT_VIEW not in allowed(db)


def test_a_rusty_category_gets_a_refresh_with_a_done_problem(db):
    review(db, INVERT, "good", NOW - timedelta(days=30), first=True)
    rusty(db, "Trees")
    candidate = next(c for c in allowed_next(db, session_id=None, now=NOW) if c["slug"] == INVERT)
    assert candidate["retry"] is True


def test_a_rusty_category_gets_one_refresh_per_session(db):
    review(db, INVERT, "good", NOW - timedelta(days=30), first=True)
    review(db, SAME_TREE, "good", NOW - timedelta(days=30), first=True)
    rusty(db, "Trees")
    session = new_session(db)
    assert {INVERT, SAME_TREE} <= allowed(db, session)
    attempt(db, session, INVERT, accepted=True)
    attempt(db, session, CLIMBING, NOW + timedelta(minutes=30), accepted=True)  # a new problem in between
    assert SAME_TREE not in allowed(db, session, NOW + timedelta(hours=1))


# --- coverage rules ----------------------------------------------------------------------------------

def due_retries(db):
    review(db, HOUSE_ROBBER, "again", NOW - timedelta(days=10), meets=False, first=True)
    review(db, COIN_CHANGE, "again", NOW - timedelta(days=10), meets=False, first=True)


def test_no_two_retries_in_a_row_while_one_of_the_150_is_untried(db):
    due_retries(db)
    session = new_session(db)
    assert COIN_CHANGE in allowed(db, session)
    attempt(db, session, HOUSE_ROBBER, accepted=True)  # a retry; its grade is still pending
    after = allowed(db, session, NOW + timedelta(minutes=5))
    assert COIN_CHANGE not in after and HOUSE_ROBBER not in after
    assert CLIMBING in after


def test_a_retry_may_follow_a_new_problem(db):
    due_retries(db)
    session = new_session(db)
    attempt(db, session, CLIMBING, accepted=True)
    assert COIN_CHANGE in allowed(db, session, NOW + timedelta(minutes=5))


def test_retries_may_run_back_to_back_once_all_150_are_tried(db):
    every_150_tried(db, except_=(HOUSE_ROBBER, COIN_CHANGE))
    due_retries(db)
    session = new_session(db)
    attempt(db, session, HOUSE_ROBBER, accepted=True)
    assert COIN_CHANGE in allowed(db, session, NOW + timedelta(minutes=5))


def test_an_untried_problem_whose_grade_is_pending_does_not_hold_back_retries_and_is_not_offered(db):
    every_150_tried(db, except_=(HOUSE_ROBBER, COIN_CHANGE, CLIMBING))
    review(db, CLIMBING, None, day(2))  # attempted, graded later (reference not checked yet)
    due_retries(db)
    session = new_session(db)
    attempt(db, session, HOUSE_ROBBER, accepted=True)
    after = allowed(db, session, NOW + timedelta(minutes=5))
    assert CLIMBING not in after
    assert COIN_CHANGE in after


def test_an_imported_but_untested_problem_counts_as_untried(db):
    every_150_tried(db, except_=(HOUSE_ROBBER, COIN_CHANGE, CLIMBING))
    imported(db, CLIMBING)
    due_retries(db)
    session = new_session(db)
    attempt(db, session, HOUSE_ROBBER, accepted=True)
    after = allowed(db, session, NOW + timedelta(minutes=5))
    assert COIN_CHANGE not in after
    assert CLIMBING in after


def test_the_problem_just_left_is_out_of_the_list_while_its_grade_is_pending(db):
    session = new_session(db)
    attempt(db, session, CLIMBING, accepted=True)  # left with an accept; the Graders haven't run
    assert CLIMBING not in allowed(db, session, NOW + timedelta(minutes=1))


def test_a_problem_with_a_pending_review_is_not_offered(db):
    review(db, LEVEL_ORDER, None, day(3))
    assert LEVEL_ORDER not in allowed(db)


def test_a_skipped_problem_is_not_suggested_again_in_the_same_session_but_is_in_the_next(db):
    first = new_session(db)
    attempt(db, first, GOOD_NODES, accepted=False)
    assert GOOD_NODES not in allowed(db, first, NOW + timedelta(minutes=1))
    second = new_session(db, NOW + timedelta(days=1))
    assert GOOD_NODES in allowed(db, second, NOW + timedelta(days=1))


def test_after_an_again_an_unseen_same_pattern_problem_comes_before_the_retry(db):
    reference(db, LONGEST_SUB, ["sliding-window"])
    reference(db, PERMUTATION, ["sliding-window"])
    review(db, LONGEST_SUB, "again", NOW - timedelta(days=2), meets=False, first=True)  # due again by now
    after = allowed(db)
    assert LONGEST_SUB not in after
    assert PERMUTATION in after


def test_after_an_again_the_retry_comes_back_once_the_idea_was_practised_elsewhere(db):
    for slug in (LONGEST_SUB, PERMUTATION, REPLACEMENT):
        reference(db, slug, ["sliding-window"])
    review(db, LONGEST_SUB, "again", NOW - timedelta(days=3), meets=False, first=True)
    review(db, PERMUTATION, "good", NOW - timedelta(days=2), first=True)
    after = allowed(db)
    assert REPLACEMENT in after  # still unseen
    assert LONGEST_SUB in after


def test_after_an_again_with_no_same_pattern_problem_left_the_retry_comes_back_when_due(db):
    reference(db, LONGEST_SUB, ["sliding-window"])
    review(db, LONGEST_SUB, "again", NOW - timedelta(days=2), meets=False, first=True)
    assert LONGEST_SUB in allowed(db)


# --- learner summary ---------------------------------------------------------------------------------

def test_learner_summary_covers_every_category_with_recent_results_by_difficulty(db):
    review(db, INVERT, "good", NOW - timedelta(days=20), first=True)
    review(db, LEVEL_ORDER, "good", NOW - timedelta(days=15), first=True)
    review(db, RIGHT_VIEW, "hard", NOW - timedelta(days=10), meets=False, first=True)
    review(db, MAX_PATH, "again", NOW - timedelta(days=14), meets=False, first=True)
    review(db, GOOD_NODES, None, NOW - timedelta(days=1))  # pending: not a result yet
    category_review(db, "Trees", "hard", NOW - timedelta(days=20), difficulty="Easy")
    category_review(db, "Trees", "good", NOW - timedelta(days=15))
    imported(db, SAME_TREE)
    imported(db, MAX_PATH)
    summary = learner_summary(db, now=NOW)
    assert set(summary) == set(CATEGORIES)
    trees = summary["Trees"]
    assert {level: sorted(grades) for level, grades in trees["recent"].items()} == {
        "Easy": ["good"], "Medium": ["good", "hard"], "Hard": ["again"]}
    assert trees["solve"].stability == pytest.approx(item_memory(db, "category:solve", "Trees", NOW).stability)
    assert trees["analysis"] is None
    assert trees["done"] == 2
    assert trees["imported"] == 2
    assert summary["Graphs"]["solve"] is None
    assert summary["Graphs"]["recent"] == {"Easy": [], "Medium": [], "Hard": []}


# --- the code-ranked fallback ------------------------------------------------------------------------

def only(db, *slugs, session_id=None):
    candidates = [c for c in allowed_next(db, session_id=session_id, now=NOW) if c["slug"] in slugs]
    assert {c["slug"] for c in candidates} == set(slugs)
    return candidates


def test_fallback_samples_a_category_with_no_evidence_before_going_deeper(db):
    steady(db, "Trees")
    candidates = only(db, GOOD_NODES, ISLANDS)
    assert code_ranked(candidates, learner_summary(db, now=NOW))["slug"] == ISLANDS


def test_fallback_takes_the_weakest_category(db):
    category_review(db, "Trees", "again", NOW - timedelta(days=3))
    steady(db, "Graphs")
    candidates = only(db, GOOD_NODES, ISLANDS)
    assert code_ranked(candidates, learner_summary(db, now=NOW))["slug"] == GOOD_NODES


def test_fallback_prefers_a_problem_not_done_on_your_own_to_an_unseen_one_beyond_the_150(db):
    candidates = only(db, MIN_SUBARRAY, PERMUTATION)
    assert code_ranked(candidates, learner_summary(db, now=NOW))["slug"] == PERMUTATION


def test_fallback_ranks_from_the_list_it_is_given(db):
    review(db, INVERT, "good", NOW - timedelta(days=30), first=True)
    steady(db, "Trees")
    candidates = allowed_next(db, session_id=None, now=NOW)
    pick = code_ranked(candidates, learner_summary(db, now=NOW))
    assert pick in candidates
    assert pick["slug"] in NC150


def test_with_no_untried_problem_of_the_150_left_the_weakest_category_still_leads(db):
    every_150_tried(db)
    for category in CATEGORIES:
        if category != "Trees":
            steady(db, category)
    category_review(db, "Trees", "again", NOW - timedelta(days=3))
    pick = code_ranked(allowed_next(db, session_id=None, now=NOW), learner_summary(db, now=NOW))
    assert pick["category"] == "Trees"
