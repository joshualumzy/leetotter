"""Progress and readiness read from the review log (SPEC §8, §9, §11, §13).

Nothing here tracks progress of its own: levels, counts, memory and history are recomputed from `reviews`
and the session events. A corrected grade rewrites the log; an excluded session's reviews are removed and
the exclusion is recorded on the session (`sessions.excluded`), so later Past links leave that session out too."""

import json
from datetime import datetime, timezone

from companion.attempts import attempts as grouped_attempts
from companion.catalog import CATEGORIES, load_catalog
from companion.grading import GRADES, category_grade, solve_grade
from companion.memory import item_memory
from companion.store import open_store


GRADE_WORDS = {"good": "solid", "hard": "shaky", "again": "missed"}
CORRECTION_WORDS = {"solid": "good", "shaky": "hard", "missed": "again"}
STATUS_TEXT = {
    "on_your_own": "on your own",
    "needed_help": "needed help, coming back",
    "brute_force_only": "brute force only, coming back",
    "unfinished": "started, unfinished",
}
HELP_WORDS = {"question": "a question", "trace": "a trace", "analogy": "an analogy",
              "teach_tool": "a taught tool"}
STRONG_SOLVE = "a clean solve on a new Medium or Hard problem"
STRONG_SOLVE_TIME = "another clean solve on a new problem, a week or more from now"
STRONG_COMPLEXITY = "a good complexity answer on a new Medium or Hard problem"
STRONG_COMPLEXITY_TIME = "another complexity answer on a new problem, a week or more from now"
COMPLEXITY_UNTESTED = "complexity not yet tested"


def _now(now):
    return now or datetime.now(timezone.utc)


def _excluded_sessions(db):
    return {session for (session,) in db.execute("SELECT id FROM sessions WHERE excluded = 1")}


def _events(db):
    return [(id_, session, ts, kind, json.loads(raw)) for id_, session, ts, kind, raw in
            db.execute("SELECT id, session_id, ts, type, payload FROM events ORDER BY id")]


def _attempts(events):
    """Each time a problem was opened in Leetotter, oldest first, including one still open."""
    attempts = []
    for attempt in grouped_attempts(events):
        if attempt["session"] is None:
            continue
        accepted = any(kind == "submit_result" and payload.get("accepted")
                       for _, _, _, kind, payload in attempt["events"])
        if attempt["left"] is not None:
            accepted = accepted or bool(attempt["left"][4].get("accepted"))
        attempts.append({"session": attempt["session"], "slug": attempt["slug"], "opened": attempt["opened"],
                         "opened_id": attempt["start"],
                         "left_id": attempt["left"][0] if attempt["left"] is not None else None,
                         "events": attempt["events"], "accepted": accepted})
    return attempts


def _help_kinds(attempt, before=None):
    kinds = []
    for id_, _, _, kind, payload in attempt["events"]:
        if kind == "help_turn" and (before is None or id_ < before):
            kinds.append(payload.get("help_kind"))
    return kinds


def _accepts(attempt):
    return [id_ for id_, _, _, kind, payload in attempt["events"]
            if kind == "submit_result" and payload.get("accepted")]


def _candidates(attempt, meets):
    candidates = []
    for accepted in _accepts(attempt):
        kinds = _help_kinds(attempt, before=accepted)
        try:
            candidates.append((accepted, solve_grade(kinds, meets_expected=bool(meets)), kinds))
        except ValueError:
            return []
    return candidates


def _debriefed(attempt):
    return any(kind == "time_up_choice" and payload.get("choice") == "debrief"
               for _, _, _, kind, payload in attempt["events"])


def _reason(review, attempt):
    grade, meets = review["grade"], bool(review["meets_expected"])
    candidates = _candidates(attempt, meets) if attempt is not None else []
    derived = max((candidate[1] for candidate in candidates), key=GRADES.index) if candidates else None
    if grade != derived and ((derived is not None and not (grade == "again" and _debriefed(attempt)))
                             or (derived is None and attempt is not None and _debriefed(attempt) and grade != "again")):
        return "you corrected this grade"
    if grade == "good":
        return "solved on your own"
    if grade == "hard":
        matching = [candidate for candidate in candidates if candidate[1] == grade] or candidates
        kinds = matching[-1][2] if matching else []
        helped = [HELP_WORDS[kind] for kind in dict.fromkeys(kinds) if kind in HELP_WORDS]
        if meets:
            if helped:
                return "solved on your own, with " + _join(helped)
            return "solved on your own"
        if helped:
            return "needed help, with a simpler approach than expected"
        return "solved brute force only"
    if grade == "again" and attempt is not None:
        matching = [candidate for candidate in candidates if candidate[1] == grade] or candidates
        kinds = matching[-1][2] if matching else _help_kinds(attempt)
        if "full_solution" in kinds:
            return "the full solution was shown"
        if _debriefed(attempt):
            return "chose the debrief at the time cap"
        if "snippet" in kinds:
            return "needed a snippet"
        if "partial_logic" in kinds:
            return "needed part of the logic"
    return "needed help" if grade == "again" else "missed"


def _join(parts):
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _has_good_medium_or_hard(db, item_type, item_key):
    return db.execute(
        "SELECT 1 FROM reviews LEFT JOIN sessions ON sessions.id = reviews.session_id "
        "WHERE reviews.item_type = ? AND reviews.item_key = ? AND reviews.grade = 'good' "
        "AND reviews.difficulty IN ('Medium', 'Hard') AND COALESCE(sessions.excluded, 0) = 0 LIMIT 1",
        (item_type, item_key)).fetchone() is not None


def _settings(db):
    row = db.execute("SELECT strong_threshold_days, desired_retention FROM fsrs WHERE id = 1").fetchone()
    return row if row else (21, 0.9)


def _skill_strong(db, category, skill, memory, threshold):
    return bool(memory and memory.stability >= threshold and memory.last_grade != "again"
                and _has_good_medium_or_hard(db, f"category:{skill}", category))


def _next_line(db, category, level, done, total, solve_memory, analysis_memory, threshold):
    if level in ("not started", "building"):
        remaining = total - done
        problem = "problem" if remaining == 1 else "problems"
        return f"To reach solid: {remaining} {problem} still to do on your own"
    if level == "strong":
        weeks = max(1, round(threshold / 7))
        return f"strong; holds for about {weeks} weeks without practice"
    missing = []
    if not _has_good_medium_or_hard(db, "category:solve", category):
        missing.append(STRONG_SOLVE)
    if solve_memory is None:
        missing.append("a clean solve on a new problem")
    elif solve_memory.stability < threshold or solve_memory.last_grade == "again":
        missing.append(STRONG_SOLVE_TIME)
    if analysis_memory is None:
        missing.append(COMPLEXITY_UNTESTED)
    else:
        if not _has_good_medium_or_hard(db, "category:analysis", category):
            missing.append(STRONG_COMPLEXITY)
        if analysis_memory.stability < threshold or analysis_memory.last_grade == "again":
            missing.append(STRONG_COMPLEXITY_TIME)
    return "To reach strong: " + "; ".join(missing)


def _snapshot(db, now):
    catalog = load_catalog()
    entries = catalog.neetcode150()
    entries_by_category = {category: [entry for entry in entries if entry["category"] == category]
                           for category in CATEGORIES}
    excluded = _excluded_sessions(db)
    reviews = []
    for row in db.execute("SELECT id, session_id, event_id, item_type, item_key, grade, meets_expected, "
                          "first_attempt, difficulty, grader_disagreement, failed_grader, reviewed_at "
                          "FROM reviews ORDER BY reviewed_at, id"):
        review = dict(zip(("id", "session_id", "event_id", "item_type", "item_key", "grade", "meets_expected",
                           "first_attempt", "difficulty", "grader_disagreement", "failed_grader", "reviewed_at"), row))
        if review["session_id"] not in excluded:
            reviews.append(review)
    events = [event for event in _events(db) if event[1] not in excluded]
    attempts = _attempts(events)
    attempts_by_event = {}
    for attempt in attempts:
        for id_, _, _, _, _ in attempt["events"]:
            attempts_by_event[id_] = attempt

    imported = {json.loads(raw)["slug"] for (raw,) in db.execute("SELECT payload FROM events WHERE type = 'prior_solve'")}
    problem_reviews = {}
    for review in reviews:
        if review["item_type"] == "problem":
            problem_reviews.setdefault(review["item_key"], []).append(review)

    attempted = {attempt["slug"] for attempt in attempts} | set(problem_reviews)
    done = set()
    for slug, rows in problem_reviews.items():
        graded = [row for row in rows if row["grade"] is not None]
        if graded and graded[-1]["grade"] in ("good", "hard") and graded[-1]["meets_expected"]:
            done.add(slug)
    nc150 = {entry["slug"] for entry in entries}
    attempted &= nc150
    done &= nc150
    coming_back = attempted - done
    threshold, retention = _settings(db)

    categories = []
    for category in CATEGORIES:
        members = entries_by_category[category]
        attempted_members = [entry["slug"] for entry in members if entry["slug"] in attempted]
        done_count = sum(entry["slug"] in done for entry in members)
        solve_memory = item_memory(db, "category:solve", category, now)
        analysis_memory = item_memory(db, "category:analysis", category, now)
        solve_strong = _skill_strong(db, category, "solve", solve_memory, threshold)
        analysis_strong = _skill_strong(db, category, "analysis", analysis_memory, threshold)
        if not attempted_members:
            level = "not started"
        elif done_count < len(members):
            level = "building"
        elif solve_strong and analysis_strong:
            level = "strong"
        else:
            level = "solid"
        rusty = bool(level in ("solid", "strong") and solve_memory is not None
                     and solve_memory.recall < retention)
        categories.append({
            "name": category, "level": level, "done": done_count, "total": len(members), "rusty": rusty,
            "solve_memory": solve_memory, "analysis_memory": analysis_memory,
        })
    return {
        "entries": entries, "attempted": attempted, "done": done, "reviews": problem_reviews,
        "attempts": attempts, "attempts_by_event": attempts_by_event, "imported": imported,
        "categories": categories, "threshold": threshold,
        "reviews_all": reviews,
    }


def _health(reviews):
    graded = [review for review in reviews if review["item_type"] == "problem" and review["grade"] is not None]
    ungraded = [review for review in reviews if review["item_type"] == "problem" and review["grade"] is None]
    disagreements = sum(bool(review["grader_disagreement"]) for review in graded)
    return {"graded": len(graded), "disagreements": disagreements,
            "disagreement_share": disagreements / len(graded) if graded else 0.0,
            "ungraded": len(ungraded)}


def _grade_entry(review, attempt):
    return {"review_id": review["id"], "attempt": review["event_id"], "grade": review["grade"],
            "word": GRADE_WORDS[review["grade"]], "reason": _reason(review, attempt),
            "at": review["reviewed_at"], "disagreement": bool(review["grader_disagreement"]),
            "failed_grader": review["failed_grader"]}


def _problem_state(snapshot, slug, entries_by_slug):
    rows = snapshot["reviews"].get(slug, [])
    graded = [row for row in rows if row["grade"] is not None]
    latest = graded[-1] if graded else None
    if latest is None:
        status = "unfinished"
    elif latest["grade"] in ("good", "hard") and latest["meets_expected"]:
        status = "on_your_own"
    elif latest["grade"] == "again":
        status = "needed_help"
    else:
        status = "brute_force_only"
    pending_rows = [row for row in rows if row["grade"] is None]
    return {"problem": slug, "title": entries_by_slug[slug]["title"], "status": status,
            "status_text": STATUS_TEXT[status], "pending": bool(pending_rows), "pending_reason": None,
            "disagreement": any(row["grader_disagreement"] for row in graded),
            "latest": _grade_entry(latest, snapshot["attempts_by_event"].get(latest["event_id"])) if latest else None,
            "earlier": [_grade_entry(row, snapshot["attempts_by_event"].get(row["event_id"])) for row in graded[:-1]],
            "earlier_count": max(0, len(graded) - 1)}


def _pending_reason(db, slug):
    reference = db.execute('SELECT status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
    return "graders" if reference is not None and reference[0] in ("checked", "resolved_by_you") else "reference"


def progress_state(db_path=None, *, now=None):
    now = _now(now)
    with open_store(db_path) as db:
        snapshot = _snapshot(db, now)
        categories = []
        for row in snapshot["categories"]:
            categories.append({key: row[key] for key in ("name", "level", "done", "total", "rusty")})
        total = len(snapshot["entries"])
        header = {
            "on_your_own": len(snapshot["done"]),
            "coming_back": len(snapshot["attempted"] - snapshot["done"]),
            "to_do": total - len(snapshot["done"]) - len(snapshot["attempted"] - snapshot["done"]),
            "strong": sum(row["level"] == "strong" for row in snapshot["categories"]),
            "rusty": sum(row["rusty"] for row in snapshot["categories"]),
            "total": total,
        }
        return {"type": "progress_state", "ts": now.isoformat(), "header": header,
                "categories": categories, "health": _health(snapshot["reviews_all"])}


def category_state(db_path, category, *, now=None):
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category}")
    now = _now(now)
    with open_store(db_path) as db:
        snapshot = _snapshot(db, now)
        row = next(item for item in snapshot["categories"] if item["name"] == category)
        entries_by_slug = {entry["slug"]: entry for entry in snapshot["entries"]}
        members = [entry for entry in snapshot["entries"] if entry["category"] == category]
        problems = []
        for entry in members:
            slug = entry["slug"]
            if slug not in snapshot["attempted"]:
                continue
            item = _problem_state(snapshot, slug, entries_by_slug)
            if item["pending"]:
                item["pending_reason"] = _pending_reason(db, slug)
            problems.append(item)
        not_tried = sum(1 for entry in members if entry["slug"] not in snapshot["attempted"]
                        and entry["slug"] not in snapshot["imported"])
        imported_only = sum(1 for entry in members if entry["slug"] not in snapshot["attempted"]
                            and entry["slug"] in snapshot["imported"])
        return {
            "type": "progress_category_state", "ts": now.isoformat(), "category": category,
            "level": row["level"], "done": row["done"], "total": row["total"], "rusty": row["rusty"],
            "next": _next_line(db, category, row["level"], row["done"], row["total"],
                               row["solve_memory"], row["analysis_memory"], snapshot["threshold"]),
            "problems": problems, "counts": {"not_tried": not_tried, "imported_only": imported_only},
            "health": _health(snapshot["reviews_all"]),
        }


def _normal_grade(grade):
    if grade in GRADES:
        return grade
    if grade in CORRECTION_WORDS:
        return CORRECTION_WORDS[grade]
    raise ValueError(f"Unknown grade: {grade}")


def correct_grade(db_path, *, grade, review_id=None, problem=None, attempt=None):
    grade = _normal_grade(grade)
    if review_id is not None and (problem is not None or attempt is not None):
        raise ValueError("Name either a review id or a problem and attempt")
    if review_id is None and (problem is None or attempt is None):
        raise ValueError("Name a review id or a problem and attempt")
    with open_store(db_path) as db:
        if review_id is not None:
            row = db.execute("SELECT id, event_id, item_type, item_key, grade, first_attempt, difficulty "
                             "FROM reviews WHERE id = ?", (review_id,)).fetchone()
        else:
            row = db.execute("SELECT id, event_id, item_type, item_key, grade, first_attempt, difficulty "
                             "FROM reviews WHERE item_type = 'problem' AND item_key = ? AND event_id = ?",
                             (problem, attempt)).fetchone()
        if row is None or (review_id is not None and row[2] != "problem"):
            raise ValueError("Problem review not found")
        review_id, event_id, _, _, old_grade, _, difficulty = row
        if old_grade is None:
            raise ValueError("This attempt has no grade yet")
        # Hard keeps the grader's own verdict: a worse approach is Hard with meets_expected false.
        meets = None if grade == "hard" else int(grade == "good")
        db.execute("UPDATE reviews SET grade = ?, meets_expected = COALESCE(?, meets_expected) WHERE id = ?",
                   (grade, meets, review_id))
        category = db.execute("SELECT id, difficulty FROM reviews WHERE event_id = ? AND item_type = 'category:solve'",
                              (event_id,)).fetchone()
        if category is not None:
            db.execute("UPDATE reviews SET grade = ?, meets_expected = COALESCE(?, meets_expected) WHERE id = ?",
                       (category_grade(grade, category[1] or difficulty), meets, category[0]))
        return review_id


def exclude_session(db_path, session_id):
    if type(session_id) is not int or session_id <= 0:
        raise ValueError("Invalid session id")
    with open_store(db_path) as db:
        row = db.execute("SELECT ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"No session {session_id}")
        if row[0] is None:
            raise ValueError("A session must be ended before it can be excluded")
        db.execute("UPDATE sessions SET excluded = 1 WHERE id = ?", (session_id,))
        db.execute("DELETE FROM reviews WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM grading_failures WHERE start_event_id IN "
                   "(SELECT id FROM events WHERE session_id = ?)", (session_id,))
        db.execute("DELETE FROM grader_retries WHERE start_event_id IN "
                   "(SELECT id FROM events WHERE session_id = ?)", (session_id,))
