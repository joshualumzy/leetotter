"""Progress and readiness from the review log (SPEC §8, §9, §11, §13).

Expected levels, counts and next lines come from the spec. The store rows are the shapes the companion
writes: problem and category reviews, session events, prior solves, and reference status."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import companion.socket as socket
from companion.catalog import CATEGORIES, load_catalog
from companion.memory import item_memory
from companion.progress import category_state, correct_grade, exclude_session, progress_state
from companion.store import open_store

CATALOG = load_catalog()
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
TRIES = [entry["slug"] for entry in CATALOG.neetcode150() if entry["category"] == "Tries"]
ARRAYS = [entry["slug"] for entry in CATALOG.neetcode150() if entry["category"] == "Arrays & Hashing"]
POINTERS = [entry["slug"] for entry in CATALOG.neetcode150() if entry["category"] == "Two Pointers"]
STACK = [entry["slug"] for entry in CATALOG.neetcode150() if entry["category"] == "Stack"]
WINDOW = [entry["slug"] for entry in CATALOG.neetcode150() if entry["category"] == "Sliding Window"]


def at(days=0, hours=0):
    return NOW + timedelta(days=days, hours=hours)


def add_session(db, *, ended=True):
    started = at(-1).isoformat()
    return db.execute("INSERT INTO sessions (started_at, ended_at) VALUES (?, ?)",
                      (started, at(0).isoformat() if ended else None)).lastrowid


def add_review(db, slug, grade, when, *, meets=True, first=False, session=None, event_id=None,
               difficulty=None, disagreement=False):
    return db.execute(
        "INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, "
        "difficulty, grader_disagreement, reviewed_at) VALUES (?, ?, 'problem', ?, ?, ?, ?, ?, ?, ?)",
        (session, event_id, slug, grade, None if grade is None else int(meets), int(first),
         difficulty or CATALOG.by_slug(slug)["difficulty"], int(disagreement), when.isoformat())).lastrowid


def add_category_review(db, category, skill, grade, when, *, difficulty="Medium", first=True):
    return db.execute(
        "INSERT INTO reviews (item_type, item_key, grade, meets_expected, first_attempt, difficulty, reviewed_at) "
        "VALUES (?, ?, ?, 1, ?, ?, ?)",
        (f"category:{skill}", category, grade, int(first), difficulty, when.isoformat())).lastrowid


def add_attempt(db, session, slug, *, accepted=False, help=(), debrief=False):
    """Events exactly as the coordinator writes one attempt, returning its problem_left event id."""
    ts = at(0).isoformat()
    title = CATALOG.by_slug(slug)["title"]
    db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_opened', ?)",
               (session, ts, json.dumps({"site": "neetcode", "slug": slug, "title": title})))
    for index, kind in enumerate(help, start=1):
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'help_turn', ?)",
                   (session, ts, json.dumps({"turn_id": index, "slug": slug, "request": "help", "press_index": index,
                                             "gap": "none", "gap_note": None, "help_kind": kind,
                                             "reply_text": "Some help.", "follow_up_question": None,
                                             "model": "x-test-2"})))
    if accepted:
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'submit_result', ?)",
                   (session, ts, json.dumps({"slug": slug, "accepted": True, "error_type": None,
                                             "error_message": "", "runtime": "0.02", "memory": 1,
                                             "passed": 1, "total": 1, "failing_case": None})))
    if debrief:
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'time_up_choice', ?)",
                   (session, ts, json.dumps({"slug": slug, "choice": "debrief"})))
    return db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'problem_left', ?)",
                      (session, ts, json.dumps({"slug": slug, "accepted": accepted}))).lastrowid


def add_reference(db, slug, status):
    db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)',
               (slug, json.dumps({"slug": slug, "title": CATALOG.by_slug(slug)["title"]}), status))


def add_imported(db, slug):
    db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (NULL, ?, 'prior_solve', ?)",
               (at(-10).isoformat(), json.dumps({"slug": slug, "source": "neetcode"})))


def problem_item(state, slug):
    return next(item for item in state["problems"] if item["problem"] == slug)


def done_everywhere(db, category, *, when=None):
    return [add_review(db, slug, "good", when or at(-1), first=True)
            for slug in (entry["slug"] for entry in CATALOG.neetcode150() if entry["category"] == category)]


# --- the map -----------------------------------------------------------------------

def test_header_counts_sum_to_the_150_and_categories_keep_neetcode_order(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        session = add_session(db)
        for slug in TRIES:
            add_review(db, slug, "good", at(-1), session=session)
        add_review(db, POINTERS[0], "again", at(-1), session=session)
        add_attempt(db, session, STACK[0])
        add_imported(db, WINDOW[0])

    state = progress_state(path, now=NOW)
    assert state["header"] == {"on_your_own": 3, "coming_back": 2, "to_do": 145,
                               "strong": 0, "rusty": 0, "total": 150}
    assert [row["name"] for row in state["categories"]] == list(CATEGORIES)
    rows = {row["name"]: row for row in state["categories"]}
    assert rows["Tries"] == {"name": "Tries", "level": "solid", "done": 3, "total": 3, "rusty": False}
    assert rows["Two Pointers"]["level"] == "building" and rows["Two Pointers"]["done"] == 0
    assert rows["Stack"]["level"] == "building"
    assert rows["Sliding Window"]["level"] == "not started"


def test_done_on_your_own_is_the_latest_graded_good_attempt(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        session = add_session(db)
        add_review(db, ARRAYS[0], "good", at(-3), session=session)
        add_review(db, ARRAYS[0], "again", at(-1), session=session)          # latest is a miss
        add_review(db, ARRAYS[1], "hard", at(-1), meets=True, session=session)   # help is not on your own
        add_review(db, ARRAYS[2], "hard", at(-1), meets=False, session=session)  # brute force only
        add_review(db, ARRAYS[3], "good", at(-2), session=session)
        add_review(db, ARRAYS[3], None, at(-1), session=session, event_id=None)  # pending repeat
        add_imported(db, ARRAYS[4])

    state = progress_state(path, now=NOW)
    assert state["header"]["on_your_own"] == 1
    assert state["header"]["coming_back"] == 3
    assert state["header"]["to_do"] == 146


def test_a_hard_latest_grade_is_not_done_on_your_own(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True, help=("question",))
        add_review(db, slug, "hard", at(-1), meets=True, session=session, event_id=left)

    assert progress_state(path, now=NOW)["header"]["on_your_own"] == 0
    item = problem_item(category_state(path, "Arrays & Hashing", now=NOW), slug)
    assert item["status"] == "needed_help"
    assert item["latest"]["reason"] == "needed help, with a question"


def test_category_detail_lists_only_attempted_problems_with_plain_status_and_reasons(tmp_path):
    path = tmp_path / "db"
    slugs = ARRAYS[:4]
    with open_store(path) as db:
        session = add_session(db)
        helped = add_attempt(db, session, slugs[0], accepted=True, help=("question",))
        add_review(db, slugs[0], "hard", at(-1), meets=True, session=session, event_id=helped)
        missed = add_attempt(db, session, slugs[1], accepted=True, help=("snippet",))
        add_review(db, slugs[1], "again", at(-1), meets=True, session=session, event_id=missed)
        add_attempt(db, session, slugs[2])  # opened and left
        add_imported(db, slugs[3])

    state = category_state(path, "Arrays & Hashing", now=NOW)
    assert [item["problem"] for item in state["problems"]] == [slugs[0], slugs[1], slugs[2]]
    assert state["counts"] == {"not_tried": len(ARRAYS) - 4, "imported_only": 1}

    asked = problem_item(state, slugs[0])
    assert (asked["status"], asked["status_text"]) == ("needed_help", "needed help, coming back")
    assert asked["latest"]["word"] == "shaky"
    assert asked["latest"]["reason"] == "needed help, with a question"

    helped_item = problem_item(state, slugs[1])
    assert (helped_item["status"], helped_item["status_text"]) == ("needed_help", "needed help, coming back")
    assert helped_item["latest"]["word"] == "missed"
    assert helped_item["latest"]["reason"] == "needed a snippet"

    unfinished = problem_item(state, slugs[2])
    assert (unfinished["status"], unfinished["status_text"]) == ("unfinished", "started, unfinished")
    assert unfinished["latest"] is None and unfinished["pending"] is False


def test_earlier_grades_stay_in_the_history_with_the_latest_first(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        first = add_attempt(db, session, slug, accepted=True, help=("full_solution",))
        add_review(db, slug, "again", at(-8), first=True, session=session, event_id=first, disagreement=True)
        second = add_attempt(db, session, slug, accepted=True)
        add_review(db, slug, "good", at(-1), first=False, session=session, event_id=second)

    item = problem_item(category_state(path, "Arrays & Hashing", now=NOW), slug)
    assert item["latest"]["word"] == "solid" and item["latest"]["reason"] == "solved on your own"
    assert item["earlier_count"] == 1
    assert item["earlier"][0]["word"] == "missed"
    assert item["earlier"][0]["reason"] == "the full solution was shown"
    assert item["disagreement"] is True


def test_a_brute_force_solve_is_coming_back(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True)
        add_review(db, slug, "hard", at(-1), meets=False, session=session, event_id=left)

    item = problem_item(category_state(path, "Arrays & Hashing", now=NOW), slug)
    assert item["status"] == "brute_force_only"
    assert item["status_text"] == "brute force only, coming back"
    assert item["latest"]["reason"] == "solved brute force only"


def test_pending_grades_are_marked_and_say_what_they_wait_for(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        session = add_session(db)
        waiting = add_attempt(db, session, ARRAYS[0], accepted=True)
        add_review(db, ARRAYS[0], None, at(-1), session=session, event_id=waiting)
        add_reference(db, ARRAYS[0], "needs_review")
        retrying = add_attempt(db, session, ARRAYS[1], accepted=True)
        add_review(db, ARRAYS[1], None, at(-1), session=session, event_id=retrying)
        add_reference(db, ARRAYS[1], "checked")

    state = category_state(path, "Arrays & Hashing", now=NOW)
    first = problem_item(state, ARRAYS[0])
    second = problem_item(state, ARRAYS[1])
    assert (first["pending"], first["pending_reason"]) == (True, "reference")
    assert (second["pending"], second["pending_reason"]) == (True, "graders")
    assert state["health"]["ungraded"] == 2


def test_grading_health_reports_the_disagreement_share(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        session = add_session(db)
        add_review(db, ARRAYS[0], "good", at(-3), session=session, disagreement=True)
        add_review(db, ARRAYS[1], "good", at(-2), session=session)
        add_review(db, ARRAYS[2], "hard", at(-1), session=session)
        add_review(db, ARRAYS[3], None, at(-1), session=session)

    health = progress_state(path, now=NOW)["health"]
    assert health == {"graded": 3, "disagreements": 1, "disagreement_share": pytest.approx(1 / 3),
                      "ungraded": 1}
    assert category_state(path, "Arrays & Hashing", now=NOW)["health"] == health


# --- what's next: one template per missing criterion, never a pattern ----------------

def test_next_line_templates(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        add_review(db, TRIES[0], "good", at(-1), first=True)
    assert category_state(path, "Tries", now=NOW)["next"] == \
        "To reach solid: 2 problems still to do on your own"
    with open_store(path) as db:
        add_review(db, TRIES[1], "good", at(-1), first=True)
    assert category_state(path, "Tries", now=NOW)["next"] == \
        "To reach solid: 1 problem still to do on your own"


def test_whats_next_for_an_untouched_category(tmp_path):
    path = tmp_path / "db"
    state = category_state(path, "Tries", now=NOW)
    assert state["level"] == "not started"
    assert state["next"] == "To reach solid: 3 problems still to do on your own"


def solid_tries(db, *, solve=(), analysis=()):
    for slug in TRIES:
        add_review(db, slug, "good", at(-1), first=True)
    for grade, when, difficulty in solve:
        add_category_review(db, "Tries", "solve", grade, when, difficulty=difficulty)
    for grade, when, difficulty in analysis:
        add_category_review(db, "Tries", "analysis", grade, when, difficulty=difficulty)


def test_whats_next_for_solid_missing_a_new_medium_or_hard_clean_solve(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        solid_tries(db, solve=[("good", at(-1), "Easy")])
    assert category_state(path, "Tries", now=NOW)["next"] == (
        "To reach strong: a clean solve on a new Medium or Hard problem; "
        "another clean solve on a new problem, a week or more from now; complexity not yet tested")


def test_whats_next_for_solid_waiting_on_stability(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        solid_tries(db, solve=[("good", at(-1), "Medium")])
    assert category_state(path, "Tries", now=NOW)["next"] == (
        "To reach strong: another clean solve on a new problem, a week or more from now; "
        "complexity not yet tested")


def test_whats_next_for_analysis_without_a_good_new_problem(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        solid_tries(db, solve=[("good", at(-8), "Medium"), ("good", at(-1), "Medium")],
                    analysis=[("again", at(-1), "Medium")])
    assert category_state(path, "Tries", now=NOW)["next"] == (
        "To reach strong: a good complexity answer on a new Medium or Hard problem; "
        "another complexity answer on a new problem, a week or more from now")


def test_whats_next_for_analysis_waiting_on_stability(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        solid_tries(db, solve=[("good", at(-8), "Medium"), ("good", at(-1), "Medium")],
                    analysis=[("good", at(-1), "Medium")])
    assert category_state(path, "Tries", now=NOW)["next"] == (
        "To reach strong: another complexity answer on a new problem, a week or more from now")


def test_whats_next_for_a_strong_category(tmp_path):
    path = tmp_path / "db"
    with open_store(path) as db:
        solid_tries(db, solve=[("good", at(-8), "Medium"), ("good", at(-1), "Medium")],
                    analysis=[("good", at(-8), "Medium"), ("good", at(-1), "Medium")])
    state = category_state(path, "Tries", now=NOW)
    assert state["level"] == "strong"
    assert state["next"] == "strong; holds for about 3 weeks without practice"


def test_no_whats_next_line_names_a_pattern(tmp_path):
    path = tmp_path / "db"
    lines = [category_state(path, "Tries", now=NOW)["next"]]
    with open_store(path) as db:
        add_review(db, TRIES[0], "good", at(-1), first=True)
    lines.append(category_state(path, "Tries", now=NOW)["next"])
    with open_store(path) as db:
        solid_tries(db, solve=[("good", at(-1), "Easy")])
    lines.append(category_state(path, "Tries", now=NOW)["next"])
    forbidden = ("sliding window", "two pointer", "fast and slow", "monotonic stack", "binary search",
                 "linked list", "tree", "trie", "heap", "graph", "backtrack", "dynamic programming",
                 "greedy", "interval", "bit manipulation", "prefix sum", "brute force")
    for line in lines:
        lowered = line.lower()
        assert not [word for word in forbidden if word in lowered], line


# --- corrections ---------------------------------------------------------------------

def test_correcting_a_grade_changes_the_log_and_fsrs_from_it(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True)
        review_id = add_review(db, slug, "good", at(-1), first=True, session=session, event_id=left)
        db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, "
                   "first_attempt, difficulty, reviewed_at) VALUES (?, ?, 'category:solve', ?, 'good', 1, 1, ?, ?)",
                   (session, left, "Arrays & Hashing", "Medium", at(-1).isoformat()))

    assert correct_grade(path, review_id=review_id, grade="hard") == review_id
    with open_store(path) as db:
        rows = {row[0]: row[1:] for row in db.execute(
            "SELECT item_type, grade, first_attempt FROM reviews WHERE event_id = ?", (left,))}
        assert rows == {"problem": ("hard", 1), "category:solve": ("hard", 1)}
        assert item_memory(db, "problem", slug, NOW).stability == pytest.approx(1.2931, abs=0.01)
        assert item_memory(db, "category:solve", "Arrays & Hashing", NOW).stability == pytest.approx(1.2931, abs=0.01)


def test_a_correction_can_name_the_problem_and_attempt_and_accepts_plain_words(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True)
        add_review(db, slug, "again", at(-1), session=session, event_id=left)

    correct_grade(path, problem=slug, attempt=left, grade="solid")
    with open_store(path) as db:
        assert db.execute("SELECT grade, first_attempt FROM reviews WHERE item_type = 'problem'").fetchone() == ("good", 0)


def test_correcting_a_later_attempt_never_makes_it_a_first_attempt(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        first_left = add_attempt(db, session, slug, accepted=True)
        add_review(db, slug, "good", at(-8), first=True, session=session, event_id=first_left)
        db.execute("INSERT INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, "
                   "first_attempt, difficulty, reviewed_at) VALUES (?, ?, 'category:solve', ?, 'good', 1, 1, ?, ?)",
                   (session, first_left, "Arrays & Hashing", "Medium", at(-8).isoformat()))
        second_left = add_attempt(db, session, slug, accepted=True)
        second_id = add_review(db, slug, "good", at(-1), first=False, session=session, event_id=second_left)

    correct_grade(path, review_id=second_id, grade="again")
    with open_store(path) as db:
        assert db.execute("SELECT grade, first_attempt FROM reviews WHERE id = ?", (second_id,)).fetchone() == ("again", 0)
        assert db.execute("SELECT grade, first_attempt FROM reviews WHERE event_id = ?", (first_left,)).fetchone() == ("good", 1)
        assert db.execute("SELECT count(*) FROM reviews WHERE event_id = ? AND item_type = 'category:solve'",
                          (second_left,)).fetchone()[0] == 0
        assert item_memory(db, "category:solve", "Arrays & Hashing", NOW).stability == pytest.approx(2.3065, abs=0.01)


def test_an_unknown_unwritten_or_category_grade_cannot_be_corrected(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        pending = add_attempt(db, session, slug, accepted=True)
        pending_id = add_review(db, slug, None, at(-1), session=session, event_id=pending)
        category_id = add_category_review(db, "Arrays & Hashing", "solve", "good", at(-1))

    with pytest.raises(ValueError, match="not found"):
        correct_grade(path, review_id=999, grade="good")
    with pytest.raises(ValueError, match="no grade yet"):
        correct_grade(path, review_id=pending_id, grade="good")
    with pytest.raises(ValueError, match="review not found"):
        correct_grade(path, review_id=category_id, grade="good")
    with pytest.raises(ValueError, match="grade"):
        correct_grade(path, review_id=pending_id, grade="great")


# --- excluding a session -------------------------------------------------------------

def test_excluding_a_session_removes_it_from_every_measurement(tmp_path):
    path = tmp_path / "db"
    first_slug, second_slug = ARRAYS[0], ARRAYS[1]
    with open_store(path) as db:
        bad = add_session(db)
        left = add_attempt(db, bad, first_slug, accepted=True)
        add_review(db, first_slug, "good", at(-1), session=bad, event_id=left)
        good = add_session(db)
        add_review(db, second_slug, "good", at(-1), session=good)

    assert progress_state(path, now=NOW)["header"]["on_your_own"] == 2
    exclude_session(path, bad)

    with open_store(path) as db:
        assert db.execute("SELECT count(*) FROM reviews WHERE session_id = ?", (bad,)).fetchone()[0] == 0
        assert db.execute("SELECT excluded FROM sessions WHERE id = ?", (bad,)).fetchone()[0] == 1
        assert item_memory(db, "problem", first_slug, NOW) is None
    state = progress_state(path, now=NOW)
    assert state["header"]["on_your_own"] == 1
    assert state["header"]["coming_back"] == 0
    assert state["header"]["to_do"] == 149
    detail = category_state(path, "Arrays & Hashing", now=NOW)
    assert first_slug not in [item["problem"] for item in detail["problems"]]

    exclude_session(path, bad)  # idempotent
    with pytest.raises(ValueError, match="No session"):
        exclude_session(path, 999)
    with open_store(path) as db:
        active = add_session(db, ended=False)
    with pytest.raises(ValueError, match="ended"):
        exclude_session(path, active)


# --- socket wiring -------------------------------------------------------------------

class Connection:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    def __aiter__(self):
        self.remaining = iter(self.messages)
        return self

    async def __anext__(self):
        try:
            return json.dumps(next(self.remaining))
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def wire(kind, payload=None):
    message = {"type": kind, "ts": "2026-10-19T09:00:00+00:00"}
    if payload is not None:
        message["payload"] = payload
    return message


def test_socket_serves_progress_and_broadcasts_after_a_change(tmp_path, monkeypatch):
    path = tmp_path / "db"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True)
        review_id = add_review(db, slug, "good", at(-1), first=True, session=session, event_id=left)

    connection = Connection([
        wire("progress_get"),
        wire("progress_category", {"category": "Arrays & Hashing"}),
        wire("grade_correct", {"review_id": review_id, "grade": "again"}),
        wire("session_exclude", {"session_id": session}),
        wire("progress_get"),
    ])
    asyncio.run(socket._handle(connection))
    assert [message["type"] for message in connection.sent] == [
        "progress_state", "progress_category_state", "progress_state", "progress_state", "progress_state"]
    assert connection.sent[0]["header"]["on_your_own"] == 1
    assert connection.sent[1]["category"] == "Arrays & Hashing"
    assert connection.sent[2]["header"]["coming_back"] == 1
    assert connection.sent[3]["header"]["to_do"] == 150


def test_progress_wire_payloads_are_validated():
    for message in (wire("progress_get"), wire("progress_category", {"category": "Tries"}),
                    wire("grade_correct", {"review_id": 1, "grade": "good"}),
                    wire("grade_correct", {"problem": ARRAYS[0], "attempt": 2, "grade": "solid"}),
                    wire("session_exclude", {"session_id": 1})):
        assert socket._validate(json.dumps(message)) == message
    bad = [
        wire("progress_get", {}),
        wire("progress_category", {"category": "Not A Category"}),
        wire("progress_category", {}),
        wire("grade_correct", {"review_id": 0, "grade": "good"}),
        wire("grade_correct", {"review_id": 1, "problem": ARRAYS[0], "attempt": 2, "grade": "good"}),
        wire("grade_correct", {"review_id": 1, "grade": "great"}),
        wire("session_exclude", {"session_id": "1"}),
    ]
    for message in bad:
        with pytest.raises(ValueError):
            socket._validate(json.dumps(message))


def test_a_corrected_grade_says_so_instead_of_repeating_the_events(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True)
        review_id = add_review(db, slug, "good", at(-1), session=session, event_id=left)
    correct_grade(path, review_id=review_id, grade="again")
    item = problem_item(category_state(path, "Arrays & Hashing", now=NOW), slug)
    assert item["latest"]["word"] == "missed"
    assert item["latest"]["reason"] == "you corrected this grade"


def test_a_debrief_miss_keeps_its_own_reason(tmp_path):
    path = tmp_path / "db"
    slug = ARRAYS[0]
    with open_store(path) as db:
        session = add_session(db)
        left = add_attempt(db, session, slug, accepted=True, debrief=True)
        add_review(db, slug, "again", at(-1), session=session, event_id=left)
    item = problem_item(category_state(path, "Arrays & Hashing", now=NOW), slug)
    assert item["latest"]["reason"] == "chose the debrief at the time cap"
