"""Human review of disputed references. Verdicts are pasted data, never model requests."""

import json
from datetime import datetime, timezone

from companion.catalog import load_catalog
from companion.grading import grade_pending
from companion.reference import LABELS, Reference
from companion.store import open_store


def _now():
    return datetime.now(timezone.utc).isoformat()


def _flag(db, slug):
    row = db.execute('SELECT content, status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
    if row is None:
        return
    if row[1] not in ("checked", "needs_review"):
        raise ValueError(f"Reference for {slug} cannot be flagged from {row[1]}")
    db.execute('UPDATE "references" SET status = ? WHERE problem_key = ?', ("needs_review", slug))


def flag_tutor_message(db_path, message_id: int, line: str = "") -> int:
    if type(message_id) is not int or message_id <= 0 or not isinstance(line, str) or "\n" in line or len(line) > 500:
        raise ValueError("Invalid reference flag: message_id or one-line note")
    with open_store(db_path) as db:
        row = db.execute("SELECT session_id, payload FROM events WHERE type = 'help_turn' "
                         "AND CAST(json_extract(payload, '$.turn_id') AS INTEGER) = ?", (message_id,)).fetchone()
        if row is None:
            raise ValueError(f"Tutor message {message_id} was not found")
        session_id, payload = row[0], json.loads(row[1])
        slug = payload["slug"]
        _flag(db, slug)
        note_id = db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, 'reference_flag', ?, ?)",
                             (_now(), json.dumps({"message_id": message_id, "line": line.strip()}), session_id)).lastrowid
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'reference_flagged', ?)",
                   (session_id, _now(), json.dumps({"slug": slug, "message_id": message_id, "note_id": note_id})))
        return note_id


def _flags(db, slug):
    resolved = db.execute("SELECT id, payload FROM events WHERE type = 'reference_resolved' ORDER BY id DESC").fetchall()
    cutoff = next((event_id for event_id, raw in resolved if json.loads(raw)["slug"] == slug), 0)
    rows = db.execute("SELECT e.payload, n.text FROM events e JOIN notes n ON n.id = json_extract(e.payload, '$.note_id') "
                      "WHERE e.type = 'reference_flagged' AND e.id > ? ORDER BY e.id", (cutoff,)).fetchall()
    flags = []
    for raw, note in rows:
        event = json.loads(raw)
        if event["slug"] != slug:
            continue
        message = db.execute("SELECT payload FROM events WHERE type = 'help_turn' "
                             "AND CAST(json_extract(payload, '$.turn_id') AS INTEGER) = ?",
                             (event["message_id"],)).fetchone()
        if message:
            flags.append({**json.loads(note), "message": json.loads(message[0])["reply_text"]})
    return flags


def _prompt(db, slug, content, flags):
    problem = load_catalog().by_slug(slug)
    statements = db.execute("SELECT payload FROM events WHERE type = 'problem_statement' ORDER BY id DESC").fetchall()
    statement = next((payload["statement"] for (raw,) in statements
                      if (payload := json.loads(raw)).get("slug") == slug), None)
    lines = ["Settle this coding-problem reference. Return only JSON in the format below.",
             f"Problem: {content['title']} ({slug}), {content['difficulty']}, {content['neetcode_category']}."]
    if problem:
        lines.append(f"Catalog title: {problem['title']}.")
    if statement:
        lines.append(f"Problem statement: {statement}")
    if content.get("summary"):
        lines.append(f"Problem summary: {content['summary']}")
    lines.append("Reference: " + json.dumps({k: v for k, v in content.items() if k != "uncertain"}, indent=2))
    if content["uncertain"]:
        lines.append("Disputed labels: " + ", ".join(content["uncertain"]))
    for flag in flags:
        lines.append(f"Flagged Tutor message id {flag['message_id']}: {flag['message']}")
        lines.append(f"User's line: {flag['line']}")
    example_keys = content["uncertain"] or [f"{content['approaches'][0]['id']}.complexity"]
    approaches = {approach["id"]: approach for approach in content["approaches"]}
    example = {}
    for key in example_keys:
        approach_id, label = key.rsplit(".", 1)
        approach = approaches[approach_id]
        example[key] = ({"time": approach["time"], "space": approach["space"]}
                        if label == "complexity" else approach[label])
    lines.append('Verdict format: return only one JSON object: {"verdict":"reference is fine"} or '
                 f'{json.dumps({"labels": example})}. The latter shows the exact keys and value types; '
                 'replace the example values with your settled values. For Checker disputes, supply every '
                 'disputed label and no others. For a Tutor flag, supply each corrected label; you may use '
                 'any approach id and label in the reference. Complexity has both time and space strings; '
                 'interview_expected is usually, sometimes, or rarely; trick is true or false. At least one '
                 'approach must remain usually and a trick cannot be usually.')
    return "\n\n".join(lines)


def review_state(db_path):
    with open_store(db_path) as db:
        rows = db.execute('SELECT problem_key, content FROM "references" WHERE status = ? ORDER BY problem_key',
                          ("needs_review",)).fetchall()
        items = []
        for slug, raw in rows:
            content = json.loads(raw)
            items.append({"slug": slug, "title": content["title"], "uncertain": content["uncertain"],
                          "prompt": _prompt(db, slug, content, _flags(db, slug))})
        flagged = db.execute("SELECT DISTINCT json_extract(payload, '$.slug') FROM events "
                             "WHERE type = 'reference_flagged'").fetchall()
        listed = {item["slug"] for item in items}
        for (slug,) in flagged:
            if slug in listed or not _flags(db, slug):
                continue
            status = db.execute('SELECT status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
            if status is not None:
                continue
            problem = load_catalog().by_slug(slug)
            items.append({"slug": slug, "title": problem["title"] if problem else slug,
                          "uncertain": [], "prompt": None, "reason": "Reference is not ready yet"})
        items.sort(key=lambda item: item["slug"])
    return {"count": len(items), "references": items}


def _parse_verdict(reply, content, flags):
    try:
        data = json.loads(reply)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid verdict: expected JSON") from error
    if data == {"verdict": "reference is fine"}:
        return content, "reference is fine"
    if not isinstance(data, dict) or set(data) != {"labels"} or not isinstance(data["labels"], dict) or not data["labels"]:
        raise ValueError('Invalid verdict: expected {"verdict":"reference is fine"} or {"labels":{...}}')
    labels = data["labels"]
    uncertain = set(content["uncertain"])
    if uncertain and set(labels) != uncertain:
        raise ValueError("Invalid verdict: labels must match disputed labels exactly")
    if not uncertain and not flags:
        raise ValueError("Invalid verdict: no disputed labels or Tutor flag")
    approaches = {a["id"]: a for a in content["approaches"]}
    updated = json.loads(json.dumps(content))
    targets = {a["id"]: a for a in updated["approaches"]}
    for key, value in labels.items():
        try:
            approach_id, label = key.rsplit(".", 1)
        except ValueError as error:
            raise ValueError(f"Invalid verdict label: {key}") from error
        if approach_id not in approaches or label not in LABELS:
            raise ValueError(f"Invalid verdict label: {key}")
        if label == "complexity":
            if not isinstance(value, dict) or set(value) != {"time", "space"} or any(not isinstance(v, str) or not v for v in value.values()):
                raise ValueError(f"Invalid verdict value for {key}")
            targets[approach_id].update(value)
        elif label == "interview_expected":
            if value not in ("usually", "sometimes", "rarely"):
                raise ValueError(f"Invalid verdict value for {key}")
            targets[approach_id][label] = value
        else:
            if type(value) is not bool:
                raise ValueError(f"Invalid verdict value for {key}")
            targets[approach_id][label] = value
    try:
        Reference.model_validate({k: v for k, v in updated.items() if k != "uncertain"})
    except ValueError as error:
        raise ValueError(f"Invalid verdict reference: {error}") from error
    updated["uncertain"] = []
    return updated, "labels settled"


def apply_verdict(db_path, slug: str, reply: str, models=None):
    with open_store(db_path) as db:
        row = db.execute('SELECT content, status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
        if row is None or row[1] != "needs_review":
            raise ValueError(f"Invalid verdict: {slug} has no flagged reference")
        content = json.loads(row[0])
        updated, outcome = _parse_verdict(reply, content, _flags(db, slug))
        updated["uncertain"] = []
        db.execute('UPDATE "references" SET content = ?, status = ? WHERE problem_key = ?',
                   (json.dumps(updated), "resolved_by_you", slug))
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (NULL, ?, 'reference_resolved', ?)",
                   (_now(), json.dumps({"slug": slug, "outcome": outcome, "verdict": json.loads(reply)})))
    graded = []
    if models is not None:
        result = grade_pending(models, db_path=db_path, only_slug=slug)
        graded = result.graded
    return {"accepted": True, "reason": outcome, "graded": graded}
