"""Evidence-checked session reflection and recap feedback (SPEC §5 and §8)."""

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from companion.catalog import load_catalog
from companion.store import open_store


PROMPT = ("Review only the given session. Summarize briefly. Hypotheses and teaching entries need real "
          "event citations. Every hypothesis change needs a one-line recap_line. Carried-over bullets "
          "need new evidence of recurrence or improvement. A smooth session may have no bullets. "
          "A rejected hypothesis can return only with its rejected_id and later event evidence.")
FEEDBACK_PROMPT = ("Use the learner's note to add, reword, or drop hypotheses. Cite the note id or "
                   "existing event ids. Every change needs a one-line recap_line. Drop means rejection.")
QUIET_LINE = "You finished all 150 problems on your own."


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Change(StrictModel):
    action: Literal["add", "reword", "drop"]
    hypothesis_id: int | None
    rejected_id: int | None
    text: str | None
    category: str | None
    skill: Literal["solve", "analysis"] | None
    cited_event_ids: list[int]
    cited_note_id: int | None
    recap_line: str = Field(min_length=1)


class Teaching(StrictModel):
    problem_slug: str = Field(min_length=1)
    cited_event_ids: list[int] = Field(min_length=1)
    idea: str = Field(min_length=1)
    category: str = Field(min_length=1)
    framing: str = Field(min_length=1)
    help_kind: str = Field(min_length=1)
    outcome: str | None


class Bullet(StrictModel):
    text: str = Field(min_length=1)
    carried_over: bool
    cited_event_ids: list[int]


class Reflection(StrictModel):
    summary: str = Field(min_length=1)
    changes: list[Change]
    teaching: list[Teaching]
    bullets: list[Bullet]


class FeedbackChanges(StrictModel):
    changes: list[Change]


def _one_line(value):
    return bool(value and value.strip() and "\n" not in value and "\r" not in value)


def _context(db, session_id):
    row = db.execute("SELECT ended_at, summary, recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None or row[0] is None:
        raise ValueError("Reflector needs an ended session")
    events = [(id_, kind, json.loads(payload)) for id_, kind, payload in db.execute(
        "SELECT id, type, payload FROM events WHERE session_id = ? ORDER BY id", (session_id,))]
    hypotheses = [dict(id=id_, text=text, category=category, skill=skill, status=status,
                       cited_event_ids=json.loads(cites), rejected_note_id=rejected)
                  for id_, text, category, skill, status, cites, rejected in db.execute(
                      "SELECT id, text, category, skill, status, cited_event_ids, rejected_note_id FROM hypotheses ORDER BY id")]
    notes = [dict(id=id_, kind=kind, text=text) for id_, kind, text in db.execute(
        "SELECT id, kind, text FROM notes WHERE kind IN ('preference', 'correction', 'recap_feedback') ORDER BY id")]
    teaching = [dict(problem_slug=slug, idea=idea, framing=framing, help_kind=kind, outcome=outcome)
                for slug, idea, framing, kind, outcome in db.execute(
                    "SELECT problem_slug, idea, framing, help_kind, outcome FROM teaching ORDER BY id")]
    return row, events, hypotheses, notes, teaching


def _validate_citations(db, change, event_ids, allowed_note_id):
    if not change.cited_event_ids and change.cited_note_id is None:
        raise ValueError("Hypothesis cites nothing")
    if set(change.cited_event_ids) - event_ids:
        raise ValueError("Hypothesis cites a missing event id")
    if change.cited_note_id is not None and change.cited_note_id != allowed_note_id:
        raise ValueError("Hypothesis cites an unavailable note id")
    if not _one_line(change.recap_line):
        raise ValueError("Hypothesis change needs a one-line recap")
    if change.action == "add":
        if change.hypothesis_id is not None or not (change.text or "").strip():
            raise ValueError("Added hypothesis needs text and no current id")
        if change.rejected_id is not None:
            old = db.execute("SELECT status, rejected_note_id, rejected_after_event_id FROM hypotheses WHERE id = ?", (change.rejected_id,)).fetchone()
            if old is None or old[0] != "rejected" or old[1] is None:
                raise ValueError("Named rejected hypothesis does not exist")
            if not change.cited_event_ids or any(id_ <= old[2] for id_ in change.cited_event_ids):
                raise ValueError("Returning hypothesis needs evidence after rejection")
            if "back" not in change.recap_line.lower() and "return" not in change.recap_line.lower():
                raise ValueError("Returning hypothesis recap must say it is back")
    else:
        if change.rejected_id is not None or change.hypothesis_id is None:
            raise ValueError("Changed hypothesis needs its existing id")
        old = db.execute("SELECT status FROM hypotheses WHERE id = ?", (change.hypothesis_id,)).fetchone()
        if old is None or (old[0] != "open" and not (change.action == "drop" and old[0] == "rejected")):
            raise ValueError("Changed hypothesis is not open")
        if change.action == "reword" and not (change.text or "").strip():
            raise ValueError("Reworded hypothesis needs text")


def _call_validated(client, db_path, messages, schema, validate):
    for attempt in range(2):
        reply = client.call_role("reflector", messages, schema)
        try:
            with open_store(db_path) as db:
                validate(db, reply)
            return reply
        except ValueError as error:
            if attempt:
                raise ValueError(f"Reflector reply invalid after retry: {error}") from error
            messages = [*messages, {"role": "assistant", "content": reply.model_dump_json()},
                        {"role": "user", "content": f"Invalid evidence or content: {error}. Return corrected JSON only."}]


def _effective_changes(db, changes):
    effective, dropped = [], set()
    for change in changes:
        if change.action == "drop":
            if change.hypothesis_id in dropped or db.execute(
                    "SELECT status FROM hypotheses WHERE id = ?", (change.hypothesis_id,)).fetchone()[0] == "rejected":
                continue
            dropped.add(change.hypothesis_id)
        effective.append(change)
    return effective


def _apply_changes(db, changes, note_id=None):
    for change in changes:
        if change.action == "add":
            db.execute("INSERT INTO hypotheses (text, category, skill, status, cited_event_ids, cited_note_id) "
                       "VALUES (?, ?, ?, 'open', ?, ?)",
                       (change.text, change.category, change.skill, json.dumps(change.cited_event_ids), change.cited_note_id))
        elif change.action == "reword":
            earlier = json.loads(db.execute("SELECT cited_event_ids FROM hypotheses WHERE id = ?", (change.hypothesis_id,)).fetchone()[0])
            db.execute("UPDATE hypotheses SET text = ?, category = ?, skill = ?, cited_event_ids = ?, cited_note_id = ? WHERE id = ?",
                       (change.text, change.category, change.skill,
                        json.dumps(sorted(set(earlier) | set(change.cited_event_ids))),
                        change.cited_note_id, change.hypothesis_id))
        else:
            floor = db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0] if note_id else None
            db.execute("UPDATE hypotheses SET status = ?, rejected_note_id = ?, rejected_after_event_id = ? WHERE id = ?",
                       ("rejected" if note_id else "dropped", note_id, floor, change.hypothesis_id))


def _reached_150(db, session_id):
    slugs = {entry["slug"] for entry in load_catalog().neetcode150()}
    if len(slugs) != 150:
        return False
    latest = {}
    prior = {}
    for slug, session, grade, meets, event_id, payload in db.execute(
        "SELECT reviews.item_key, reviews.session_id, reviews.grade, reviews.meets_expected, "
        "reviews.event_id, events.payload FROM reviews LEFT JOIN events ON events.id = reviews.event_id "
        "WHERE reviews.item_type = 'problem' AND reviews.grade IS NOT NULL ORDER BY reviews.reviewed_at, reviews.id"):
        if slug in slugs:
            result = (session, grade, meets, event_id, payload)
            latest[slug] = result
            if session != session_id:
                prior[slug] = result

    def count(rows):
        return sum(bool(meets and grade in ("good", "hard") and event_id is not None
                        and payload and json.loads(payload).get("accepted"))
                   for _, grade, meets, event_id, payload in rows.values())

    return count(prior) < 150 and count(latest) == 150


def reflect_session(client, db_path, session_id):
    with open_store(db_path) as db:
        row, events, hypotheses, notes, teaching = _context(db, session_id)
        if row[1] is not None:
            return {"type": "recap_state", "session_id": session_id, "status": "ready",
                    "bullets": json.loads(row[2])}
        event_ids = {event[0] for event in events}
        payload = {"events": events, "hypotheses": hypotheses, "notes": notes, "teaching": teaching}
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(payload)}]

    def validate(db, reply):
        if not reply.summary.strip():
            raise ValueError("Session summary is empty")
        for change in reply.changes:
            _validate_citations(db, change, event_ids, None)
        for item in reply.teaching:
            if set(item.cited_event_ids) - event_ids or not item.cited_event_ids:
                raise ValueError("Teaching cites a missing event id")
            if not _one_line(item.idea):
                raise ValueError("Teaching idea must be one line")
            if not any(payload.get("slug") == item.problem_slug for _, _, payload in events):
                raise ValueError("Teaching names a problem absent from the session")
        for bullet in reply.bullets:
            if not _one_line(bullet.text) or set(bullet.cited_event_ids) - event_ids:
                raise ValueError("Recap bullet has invalid text or citation")
            if bullet.carried_over and not bullet.cited_event_ids:
                raise ValueError("Carried-over bullet needs changed evidence")

    reply = _call_validated(client, db_path, messages, Reflection, validate)
    with open_store(db_path) as db:
        if db.execute("SELECT summary FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] is not None:
            return {"type": "recap_state", "session_id": session_id, "status": "ready",
                    "bullets": json.loads(db.execute("SELECT recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()[0])}
        effective = _effective_changes(db, reply.changes)
        _apply_changes(db, effective)
        for item in reply.teaching:
            db.execute("INSERT INTO teaching (problem_slug, cited_event_ids, idea, category, framing, help_kind, outcome) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (item.problem_slug, json.dumps(item.cited_event_ids),
                       item.idea, item.category, item.framing, item.help_kind, item.outcome))
        bullets = [change.recap_line for change in effective] + [bullet.text for bullet in reply.bullets]
        if _reached_150(db, session_id):
            bullets.append(QUIET_LINE)
        db.execute("UPDATE sessions SET summary = ?, recap_bullets = ? WHERE id = ?",
                   (reply.summary, json.dumps(bullets), session_id))
        db.execute("DELETE FROM reflector_failures WHERE session_id = ?", (session_id,))
    return recap_state(db_path, session_id)


def recap_state(db_path, session_id):
    with open_store(db_path) as db:
        row = db.execute("SELECT ended_at, recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()
        failure = db.execute("SELECT message FROM reflector_failures WHERE session_id = ?", (session_id,)).fetchone()
    if row is None or row[0] is None:
        raise ValueError("No ended session for recap")
    return {"type": "recap_state", "session_id": session_id,
            "status": "ready" if row[1] is not None else "failed" if failure else "preparing",
            "bullets": json.loads(row[1]) if row[1] is not None else [],
            **({"message": failure[0]} if failure and row[1] is None else {})}


def submit_feedback(client, db_path, session_id, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Feedback text is required")
    with open_store(db_path) as db:
        row = db.execute("SELECT recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None or row[0] is None or not json.loads(row[0]):
            raise ValueError("Feedback needs a recap with bullets")
        note_id = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, 'recap_feedback', ?, ?)",
                             (datetime.now(timezone.utc).isoformat(), text.strip(), session_id)).lastrowid
    return note_id


def reflect_feedback(client, db_path, session_id, note_id):
    with open_store(db_path) as db:
        row = db.execute("SELECT text FROM notes WHERE id = ? AND session_id = ? AND kind = 'recap_feedback'",
                         (note_id, session_id)).fetchone()
        if row is None:
            raise ValueError("Feedback note is missing")
        bullets = json.loads(db.execute("SELECT recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()[0])
        hypotheses = [dict(id=id_, text=text) for id_, text in db.execute(
            "SELECT id, text FROM hypotheses WHERE status = 'open'")]
        known_events = {id_ for (id_,) in db.execute("SELECT id FROM events")}
    messages = [{"role": "system", "content": FEEDBACK_PROMPT},
                {"role": "user", "content": json.dumps({"note_id": note_id, "note": row[0],
                    "recap": bullets, "hypotheses": hypotheses})}]

    def validate(db, reply):
        for change in reply.changes:
            _validate_citations(db, change, known_events, note_id)

    reply = _call_validated(client, db_path, messages, FeedbackChanges, validate)
    with open_store(db_path) as db:
        effective = _effective_changes(db, reply.changes)
        _apply_changes(db, effective, note_id)
        bullets = json.loads(db.execute("SELECT recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()[0])
        bullets += [change.recap_line for change in effective]
        db.execute("UPDATE sessions SET recap_bullets = ? WHERE id = ?", (json.dumps(bullets), session_id))
    return recap_state(db_path, session_id)


def pending_sessions(db_path):
    with open_store(db_path) as db:
        return [id_ for (id_,) in db.execute(
            "SELECT id FROM sessions WHERE ended_at IS NOT NULL AND summary IS NULL ORDER BY id")]
