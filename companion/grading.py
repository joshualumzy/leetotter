"""Fixed grading rules and review logging for completed attempts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

from companion.attempts import attempts as grouped_attempts
from companion.catalog import load_catalog
from companion.model import help_floor
from companion.store import open_store


logger = logging.getLogger(__name__)
GRADES = ("again", "hard", "good")
HELP = {"question", "trace", "analogy", "teach_tool", "syntax", "snippet", "partial_logic", "full_solution", "none"}
SOLVE_PROMPT = """Judge this accepted coding attempt against the checked reference. Read the problem statement,
code submissions and runs, and help turns in event order. Return one accepts entry for every accepted
submit_result event, with its submission_event_id, approach_id (a reference id or 'new'), and meets_expected.
Set meets_expected when that approach's time complexity is no worse than the best reference approach
marked 'usually', breaking ties by space complexity. Correct only help labels that are wrong:
return their turn_id and correct help_kind.
Judge independently from the other grader. Do not assign an FSRS grade; code applies the fixed table."""
ANALYSIS_PROMPT = """Judge the learner's complexity answer and reasoning against the checked reference.
Return complexity_verdict: right, right_after_followup, right_after_stated, wrong, wrong_reasoning, or no_reasoning.
right_after_stated means the learner gave the right answer only after the Tutor stated the complexity during the
follow-up discussion. Set complexity_stated_earlier if the Tutor gave the complexity before the question. Judge
independently; code applies the fixed grade table."""


def solve_grade(kinds, *, meets_expected):
    if unknown := set(kinds) - HELP:
        raise ValueError(f"Unknown help kind: {unknown}")
    if set(kinds) & {"snippet", "partial_logic", "full_solution"}:
        return "again"
    if not meets_expected or set(kinds) & {"question", "trace", "analogy", "teach_tool"}:
        return "hard"
    return "good"


def done_on_your_own(grade, meets):
    return grade == "good" and bool(meets)


def analysis_grade(verdict):
    mapping = {"right": "good", "right_after_followup": "hard", "right_after_stated": "again",
               "wrong": "again", "wrong_reasoning": "again", "no_reasoning": None}
    if verdict not in mapping:
        raise ValueError(f"Unknown complexity verdict: {verdict}")
    return mapping[verdict]


def analysis_counts(*, first_attempt, solve_grade, complexity_stated):
    return bool(first_attempt and solve_grade in {"good", "hard"} and not complexity_stated)


def combine(a, b):
    if a not in GRADES or b not in GRADES:
        raise ValueError("Unknown grade")
    return min((a, b), key=GRADES.index), a != b


def category_grade(grade, difficulty):
    if grade not in GRADES or difficulty not in {"Easy", "Medium", "Hard"}:
        raise ValueError("Unknown grade or difficulty")
    return "hard" if grade == "good" and difficulty == "Easy" else grade


class HelpCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    turn_id: int
    help_kind: Literal["question", "trace", "analogy", "teach_tool", "syntax", "snippet", "partial_logic", "full_solution", "none"]


class AcceptedVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    submission_event_id: int
    approach_id: str
    meets_expected: bool


class SolveVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    accepts: list[AcceptedVerdict]
    corrected_help_labels: list[HelpCorrection]


class AnalysisVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    complexity_verdict: Literal["right", "right_after_followup", "right_after_stated", "wrong",
                                "wrong_reasoning", "no_reasoning"]
    complexity_stated_earlier: bool


@dataclass
class GradingOutcome:
    _done: list[int] = field(default_factory=list)
    _waiting: list[int] = field(default_factory=list)
    _errors: list[int] = field(default_factory=list)
    failures: dict[int, str] = field(default_factory=dict)

    @property
    def graded(self):
        return self._done

    @property
    def pending(self):
        return self._waiting

    @property
    def failed(self):
        return self._errors


def _events(db):
    return [(id_, session, ts, kind, json.loads(raw)) for id_, session, ts, kind, raw in
            db.execute("SELECT id, session_id, ts, type, payload FROM events ORDER BY id")]


def _excluded_sessions(db):
    return {session for (session,) in db.execute("SELECT id FROM sessions WHERE excluded = 1")}


def _event_timestamp(db, session):
    """A current timestamp that never goes backwards for this session's stored events."""
    now = datetime.now(timezone.utc)
    row = db.execute("SELECT ts FROM events WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session,)).fetchone()
    if row is not None:
        try:
            last = datetime.fromisoformat(row[0])
        except (TypeError, ValueError):
            last = None
        if last is not None and last.tzinfo is not None and last > now:
            return row[0]
    return now.isoformat()


def _attempts(events):
    for attempt in grouped_attempts(events):
        if attempt["left"] is not None:
            attempt["accepted"] = attempt["left"][4]["accepted"]
            yield attempt


def _first_attempt(db, events, start, slug):
    for id_, _, _, kind, payload in events:
        if id_ >= start:
            break
        if kind == "prior_solve" and payload.get("slug") == slug:
            return False
    for earlier in _attempts(events):
        if earlier["start"] >= start:
            break
        if earlier["slug"] != slug:
            continue
        if db.execute("SELECT 1 FROM reviews WHERE item_type = 'problem' AND session_id = ? "
                      "AND event_id BETWEEN ? AND ?",
                      (earlier["session"], earlier["start"], earlier["left"][0])).fetchone():
            return False
        if db.execute("SELECT 1 FROM handled_attempts WHERE start_event_id = ?", (earlier["start"],)).fetchone():
            return False
        if earlier["accepted"] or any(
                (kind in {"run_result", "submit_result", "help_request", "time_up_choice", "debrief_request"}
                 or kind == "help_turn" and payload.get("help_kind") != "none")
                and payload.get("slug") == slug for _, _, _, kind, payload in earlier["events"]):
            return False
    return True


def _details(db, slug):
    catalog = load_catalog().by_slug(slug)
    row = db.execute('SELECT content FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
    reference = json.loads(row[0]) if row else None
    category = (catalog or {}).get("category") or (reference or {}).get("neetcode_category")
    difficulty = (catalog or {}).get("difficulty") or (reference or {}).get("difficulty")
    if not category or not difficulty:
        raise ValueError(f"Unknown problem: {slug}")
    return category, difficulty


def _review(db, *, session, event_id, ts, slug, first, grade, meets=None, disagreement=False, failed=None):
    category, difficulty = _details(db, slug)
    db.execute("INSERT OR IGNORE INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, difficulty, grader_disagreement, failed_grader, reviewed_at) VALUES (?, ?, 'problem', ?, ?, ?, ?, ?, ?, ?, ?)",
               (session, event_id, slug, grade, meets, int(first), difficulty, int(disagreement), failed, ts))
    if first:
        db.execute("INSERT OR IGNORE INTO reviews (session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, difficulty, grader_disagreement, failed_grader, reviewed_at) VALUES (?, ?, 'category:solve', ?, ?, ?, 1, ?, ?, ?, ?)",
                   (session, event_id, category, category_grade(grade, difficulty) if grade else None,
                    meets, difficulty, int(disagreement), failed, ts))


def write_missed(db, *, session_id, slug, event_id, attempt_start=None):
    """Write an immediate Again, once per attempt, regardless of reference status."""
    events = _events(db)
    target = next((event for event in events if event[0] == event_id and event[1] == session_id), None)
    if target is None:
        raise ValueError("Missing miss event")
    active_slug = start = None
    for id_, session, _, kind, payload in events:
        if id_ > event_id:
            break
        if session != session_id:
            continue
        if kind == "problem_opened" and payload["slug"] != active_slug:
            active_slug, start = payload["slug"], id_
        elif kind == "problem_left" and payload["slug"] == active_slug:
            active_slug = start = None
    if attempt_start is not None:
        opening = next((event for event in events if event[0] == attempt_start), None)
        if opening is None or opening[1] != session_id or opening[3] != "problem_opened" or opening[4]["slug"] != slug or attempt_start > event_id:
            raise ValueError("Missing problem opening")
        start = attempt_start
    elif active_slug != slug or start is None:
        raise ValueError("Missing problem opening")
    if db.execute("SELECT 1 FROM handled_attempts WHERE start_event_id = ?", (start,)).fetchone():
        return
    first = _first_attempt(db, events, start, slug)
    _review(db, session=session_id, event_id=event_id, ts=target[2], slug=slug, first=first, grade="again")
    db.execute("INSERT INTO handled_attempts (start_event_id) VALUES (?)", (start,))


def _grader_grade(client, model, slug, reference, attempt):
    messages = [{"role": "system", "content": SOLVE_PROMPT},
                {"role": "user", "content": json.dumps({"slug": slug, "reference": reference,
                    "events": [(id_, kind, payload) for id_, _, _, kind, payload in attempt["events"]]})}]
    known = {approach["id"] for approach in reference["approaches"]} | {"new"}
    turn_ids = {payload["turn_id"] for _, _, _, kind, payload in attempt["events"] if kind == "help_turn"}
    accepted = {id_ for id_, _, _, kind, payload in attempt["events"] if kind == "submit_result" and payload.get("accepted")}
    for retry in range(2):
        reply = client.call_role("grader", messages, SolveVerdict, model=model)
        try:
            if {item.submission_event_id for item in reply.accepts} != accepted or len(reply.accepts) != len(accepted):
                raise ValueError("Grader must label every accepted submission exactly once")
            if any(item.approach_id not in known for item in reply.accepts):
                raise ValueError("Grader named an unknown approach")
            corrections = {item.turn_id: item.help_kind for item in reply.corrected_help_labels}
            if corrections.keys() - turn_ids:
                raise ValueError("Grader corrected a help turn outside this attempt")
            candidates = []
            for item in reply.accepts:
                kinds = [help_floor(corrections.get(payload["turn_id"], payload["help_kind"]), payload.get("linked"))
                         for id_, _, _, kind, payload in attempt["events"] if kind == "help_turn" and id_ < item.submission_event_id]
                candidates.append((solve_grade(kinds, meets_expected=item.meets_expected), item.meets_expected))
            return max(candidates, key=lambda pair: GRADES.index(pair[0]))
        except ValueError as error:
            if retry:
                raise
            messages = [*messages, {"role": "assistant", "content": reply.model_dump_json()},
                        {"role": "user", "content": f"Invalid grader labels: {error}. Return corrected JSON only."}]


def _record_analysis_retry(db_path, question_id, permanent):
    with open_store(db_path) as db:
        if permanent:
            db.execute("INSERT INTO grader_retries (start_event_id, failed_tries) VALUES (?, 3) "
                       "ON CONFLICT(start_event_id) DO UPDATE SET failed_tries = 3", (question_id,))
        else:
            db.execute("INSERT INTO grader_retries (start_event_id, failed_tries) VALUES (?, 1) "
                       "ON CONFLICT(start_event_id) DO UPDATE SET failed_tries = grader_retries.failed_tries + 1",
                       (question_id,))


def _grade_analysis_question(client, db_path, outcome, question_id, session, question, events, trigger):
    later = [(id_, kind, payload) for id_, event_session, _, kind, payload in events
             if event_session == session and id_ > question_id and payload.get("question_id") == question_id]
    if any(kind == "followup_skipped" for _, kind, _ in later):
        return
    answers = [payload for _, kind, payload in later if kind == "followup_answered"]
    if not answers or not answers[-1]["has_reasoning"]:
        return
    slug = question["slug"]
    openings = [id_ for id_, event_session, _, kind, payload in events
                if event_session == session and id_ < question_id and kind == "problem_opened" and payload["slug"] == slug]
    if not openings:
        outcome.failures[question_id] = f"Complexity question {question_id} has no problem attempt."
        outcome.failed.append(question_id)
        return
    start = openings[-1]
    left = next((id_ for id_, event_session, _, kind, payload in events
                 if event_session == session and id_ > start and kind == "problem_left" and payload["slug"] == slug), None)
    if left is None:
        return
    with open_store(db_path) as db:
        if db.execute("SELECT 1 FROM reviews WHERE event_id = ? AND item_type = 'category:analysis'", (question_id,)).fetchone():
            return
        retry = db.execute("SELECT failed_tries FROM grader_retries WHERE start_event_id = ?", (question_id,)).fetchone()
        tries = retry[0] if retry else 0
        if tries >= 3 or (tries == 1 and trigger != "next_problem") or (tries == 2 and trigger != "next_start"):
            return
        solve = db.execute("SELECT grade, first_attempt, reviewed_at FROM reviews WHERE event_id = ? AND item_type = 'problem'", (left,)).fetchone()
        if solve is None or solve[0] is None:
            return
        if not solve[1] or solve[0] == "again":
            return
        status = db.execute('SELECT content, status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
    if status is None or status[1] not in {"checked", "resolved_by_you"}:
        return
    reference = json.loads(status[0])
    attempt_events = [(id_, kind, payload) for id_, event_session, _, kind, payload in events
                      if event_session == session and start <= id_ <= question_id]
    messages = [{"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user", "content": json.dumps({"slug": slug, "reference": reference,
                    "question": question["question"], "answers": answers, "events": attempt_events})}]
    replies = []
    failures = []
    for model in client.config.roles["grader"]:
        try:
            replies.append(client.call_role("grader", messages, AnalysisVerdict, model=model))
        except Exception as error:
            logger.exception("Analysis grader %s failed for question %s", model, question_id)
            outcome.failures[question_id] = f"Analysis grader {model} failed for question {question_id}: {error}"
            failures.append(model)
    if len(replies) != 2:
        tries += 1
        with open_store(db_path) as db:
            db.execute("INSERT INTO grader_retries (start_event_id, failed_tries) VALUES (?, ?) "
                       "ON CONFLICT(start_event_id) DO UPDATE SET failed_tries = excluded.failed_tries",
                       (question_id, tries))
        if not replies or tries < 3:
            outcome.failed.append(question_id)
            return
    grades = [analysis_grade(reply.complexity_verdict) for reply in replies]
    if None in grades:  # no reasoning gives no credit, even when the other grader supplies a grade
        with open_store(db_path) as db:
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'analysis_resolved', ?)",
                       (session, _event_timestamp(db, session), json.dumps({"question_id": question_id, "slug": slug, "reason": "no_reasoning"})))
        return
    grade, disagreement = combine(*grades) if len(grades) == 2 else (grades[0], False)
    if not analysis_counts(first_attempt=solve[1], solve_grade=solve[0],
                           complexity_stated=any(reply.complexity_stated_earlier for reply in replies)):
        with open_store(db_path) as db:
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, 'analysis_resolved', ?)",
                       (session, _event_timestamp(db, session), json.dumps({"question_id": question_id, "slug": slug, "reason": "complexity_stated"})))
        return
    with open_store(db_path) as db:
        category, difficulty = _details(db, slug)
        db.execute("INSERT OR IGNORE INTO reviews (session_id, event_id, item_type, item_key, grade, "
                   "first_attempt, difficulty, grader_disagreement, failed_grader, reviewed_at) "
                   "VALUES (?, ?, 'category:analysis', ?, ?, 1, ?, ?, ?, ?)",
                   (session, question_id, category, grade, difficulty, int(disagreement),
                    failures[0] if failures else None, solve[2]))


def _grade_analysis(client, db_path, outcome, only_slug, trigger):
    with open_store(db_path) as db:
        events = _events(db)
        excluded = _excluded_sessions(db)
    questions = [(id_, session, ts, payload) for id_, session, ts, kind, payload in events
                 if kind == "followup_asked" and payload.get("complexity")
                 and session not in excluded
                 and (only_slug is None or payload["slug"] == only_slug)]
    for question_id, session, ts, question in questions:
        if any(event_session == session and kind == "analysis_resolved" and payload.get("question_id") == question_id
               for _, event_session, _, kind, payload in events):
            continue
        try:
            _grade_analysis_question(client, db_path, outcome, question_id, session, question, events, trigger)
        except Exception as error:
            message = f"Cannot grade complexity question {question_id} ({question.get('slug')}): {error}"
            logger.exception(message)
            outcome.failed.append(question_id)
            outcome.failures[question_id] = message
            try:
                _record_analysis_retry(db_path, question_id, isinstance(error, ValueError))
            except Exception:
                logger.exception("Could not record analysis grading failure for question %s", question_id)


def grade_pending(client, *, db_path=None, only_slug=None, trigger="initial"):
    """Grade accepted, unhandled attempts against a checked reference."""
    if trigger not in {"initial", "next_problem", "next_start"}:
        raise ValueError(f"Unknown grading trigger: {trigger}")
    outcome = GradingOutcome()
    with open_store(db_path) as db:
        events = _events(db)
        excluded = _excluded_sessions(db)
    for attempt in _attempts(events):
        if attempt["session"] in excluded:
            continue
        if only_slug is not None and attempt["slug"] != only_slug:
            continue
        if not attempt["accepted"]:
            continue
        start, slug = attempt["start"], attempt["slug"]
        left_id, session, ts, _, _ = attempt["left"]
        tries = 0
        try:
            with open_store(db_path) as db:
                if db.execute("SELECT 1 FROM handled_attempts WHERE start_event_id = ?", (start,)).fetchone():
                    continue
                count = db.execute("SELECT failed_tries FROM grader_retries WHERE start_event_id = ?", (start,)).fetchone()
                tries = count[0] if count else 0
                if tries >= 3 or (tries == 1 and trigger != "next_problem") or (tries == 2 and trigger != "next_start"):
                    continue
                first = _first_attempt(db, events, start, slug)
                existing = db.execute("SELECT id FROM reviews WHERE item_type = 'problem' AND event_id = ?", (left_id,)).fetchone()
                if not existing:
                    _review(db, session=session, event_id=left_id, ts=ts, slug=slug, first=first, grade=None)
                status = db.execute('SELECT content, status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
            if status is None or status[1] not in {"checked", "resolved_by_you"}:
                outcome.pending.append(left_id)
                continue
            reference = json.loads(status[0])
            replies = []
            failures = []
            for model in client.config.roles["grader"]:
                try:
                    replies.append(_grader_grade(client, model, slug, reference, attempt))
                except Exception:
                    logger.exception("Grader %s failed for attempt %s", model, left_id)
                    failures.append(model)
            with open_store(db_path) as db:
                if db.execute("SELECT 1 FROM handled_attempts WHERE start_event_id = ?", (start,)).fetchone():
                    continue
                if len(replies) == 2:
                    grade, disagreement = combine(replies[0][0], replies[1][0])
                    meets = next(meets for candidate, meets in replies if candidate == grade)
                    failed = None
                else:
                    tries += 1
                    db.execute("INSERT INTO grader_retries (start_event_id, failed_tries) VALUES (?, ?) ON CONFLICT(start_event_id) DO UPDATE SET failed_tries = excluded.failed_tries", (start, tries))
                    if len(replies) == 0 or tries < 3:
                        outcome.failed.append(left_id)
                        if len(replies) == 0 and tries >= 3:
                            outcome.failures[left_id] = f"Both graders failed for attempt {left_id} ({slug}); it remains ungraded."
                        continue
                    grade, meets = replies[0]
                    disagreement, failed = False, failures[0]
                db.execute("UPDATE reviews SET grade = ?, meets_expected = ?, grader_disagreement = ?, failed_grader = ? WHERE event_id = ?",
                           (grade, int(meets), int(disagreement), failed, left_id))
                db.execute("UPDATE reviews SET grade = ? WHERE event_id = ? AND item_type = 'category:solve'",
                           (category_grade(grade, _details(db, slug)[1]), left_id))
                db.execute("INSERT INTO handled_attempts (start_event_id) VALUES (?)", (start,))
                outcome.graded.append(left_id)
        except Exception as error:
            message = f"Cannot grade attempt {left_id} ({slug}): {error}"
            logger.exception(message)
            outcome.failed.append(left_id)
            outcome.failures[left_id] = message
            try:
                with open_store(db_path) as db:
                    if db.execute("SELECT 1 FROM handled_attempts WHERE start_event_id = ?", (start,)).fetchone():
                        continue
                    if isinstance(error, ValueError):
                        db.execute("INSERT INTO grading_failures (start_event_id, message) VALUES (?, ?)", (start, message))
                        db.execute("INSERT INTO handled_attempts (start_event_id) VALUES (?)", (start,))
                    else:
                        db.execute("INSERT INTO grader_retries (start_event_id, failed_tries) VALUES (?, 1) "
                                   "ON CONFLICT(start_event_id) DO UPDATE SET failed_tries = grader_retries.failed_tries + 1",
                                   (start,))
            except Exception:
                logger.exception("Could not record grading failure for attempt %s", left_id)
    _grade_analysis(client, db_path, outcome, only_slug, trigger)
    return outcome
