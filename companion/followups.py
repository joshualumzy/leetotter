"""Recorded decisions and answers for questions between problems."""

from datetime import datetime
import json

from companion.catalog import load_catalog
from companion.grading import _events, _first_attempt
from companion.memory import item_memory


def _event(db, session_id, ts, kind, payload):
    return db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                      (session_id, ts, kind, json.dumps(payload))).lastrowid


def _question(db, question_id):
    row = db.execute("SELECT session_id, payload FROM events WHERE id = ? AND type = 'followup_asked'",
                     (question_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown follow-up question")
    return row[0], json.loads(row[1])


def _question_events(db, question_id):
    events = []
    for kind, raw in db.execute("SELECT type, payload FROM events WHERE type IN "
                                "('followup_answered', 'reasoning_requested', 'followup_skipped') ORDER BY id"):
        payload = json.loads(raw)
        if payload.get("question_id") == question_id:
            events.append((kind, payload))
    return events


def complexity_question_due(db, *, session_id, slug):
    catalog = load_catalog().by_slug(slug)
    if catalog is None:
        raise ValueError(f"Unknown problem: {slug}")
    category = catalog["category"]
    events = _events(db)
    openings = [(id_, ts) for id_, session, ts, kind, payload in events
                if session == session_id and kind == "problem_opened" and payload["slug"] == slug]
    if not openings:
        return False
    start, _ = openings[-1]
    if not _first_attempt(db, events, start, slug):
        return False
    accepted = [id_ for id_, session, _, kind, payload in events
                if session == session_id and id_ > start and kind == "submit_result"
                and payload.get("slug") == slug and payload.get("accepted")]
    if not accepted:
        return False
    if any(session == session_id and start < id_ < accepted[0] and kind == "help_turn"
           and payload.get("slug") == slug and payload.get("help_kind") == "full_solution"
           for id_, session, _, kind, payload in events):
        return False
    if any(session == session_id and kind == "followup_asked" and payload.get("complexity")
           and (load_catalog().by_slug(payload["slug"]) or {}).get("category") == category
           for _, session, _, kind, payload in events):
        return False
    now = datetime.fromisoformat(events[-1][2])
    memory = item_memory(db, "category:analysis", category, now)
    if memory is None or memory.last_grade == "again":
        return True
    threshold = db.execute("SELECT strong_threshold_days FROM fsrs WHERE id = 1").fetchone()[0]
    if memory.stability < threshold:
        return True
    return db.execute("SELECT 1 FROM reviews WHERE item_type = 'category:analysis' AND item_key = ? "
                      "AND grade = 'good' AND difficulty IN ('Medium', 'Hard') LIMIT 1", (category,)).fetchone() is None


def ask_followup(db, *, session_id, slug, ts, question, complexity):
    if not isinstance(question, str) or not question or type(complexity) is not bool:
        raise ValueError("Invalid follow-up question")
    return _event(db, session_id, ts, "followup_asked", {"slug": slug, "question": question, "complexity": complexity})


def postpone_complexity(db, *, session_id, slug, ts, reason):
    if not isinstance(reason, str) or not reason:
        raise ValueError("Postponement needs a reason")
    return _event(db, session_id, ts, "complexity_postponed", {"slug": slug, "reason": reason})


def answer_followup(db, *, question_id, ts, text, has_reasoning):
    session_id, question = _question(db, question_id)
    prior = _question_events(db, question_id)
    if any(kind == "followup_skipped" for kind, _ in prior) or any(
            kind == "followup_answered" and (payload["has_reasoning"] or not question["complexity"])
            for kind, payload in prior):
        raise ValueError("Follow-up question is closed")
    if not isinstance(text, str) or not text or type(has_reasoning) is not bool:
        raise ValueError("Invalid follow-up answer")
    payload = {"question_id": question_id, "slug": question["slug"], "text": text, "has_reasoning": has_reasoning}
    _event(db, session_id, ts, "followup_answered", payload)
    if question["complexity"] and not has_reasoning:
        if any(kind == "reasoning_requested" for kind, _ in prior):
            _event(db, session_id, ts, "followup_skipped", {"question_id": question_id, "slug": question["slug"]})
            return "skipped"
        _event(db, session_id, ts, "reasoning_requested", {"question_id": question_id, "slug": question["slug"]})
        return "ask_reasoning"
    return "answered"


def skip_followup(db, *, question_id, ts):
    session_id, question = _question(db, question_id)
    if any(kind == "followup_skipped" or kind == "followup_answered"
           and (payload["has_reasoning"] or not question["complexity"])
           for kind, payload in _question_events(db, question_id)):
        raise ValueError("Follow-up question is closed")
    _event(db, session_id, ts, "followup_skipped", {"question_id": question_id, "slug": question["slug"]})
