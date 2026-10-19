"""Planner eligibility, learner context, validated choices, and suggestion lifecycle."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from companion.catalog import CATEGORIES, load_catalog
from companion.memory import item_memory
from companion.session import TIME_CAP_MINUTES
from companion.store import open_store

logger = logging.getLogger(__name__)
PLANNER_WAIT = 60
SHORTLIST_PER_CATEGORY = 3

SYSTEM_PROMPT = """You are Leetotter's Planner. Suggest exactly one eligible problem, by title only. Keep your reason internal: never name the suggested problem's category, pattern, or approach before the learner solves it, unless they asked for that category or pattern themselves. Use the learner summary, imported history, unfinished problems, open hypotheses, preferences, today's date, this session's problems and load, allowed list, current card, chat, and active steer. Sample categories while evidence is thin, starting with imported history; balance weaknesses, mix categories after initial practice, check transfer on unseen problems, and occasionally use free problems beyond the 150. Avoid stacking demanding problems. Read steering words in context, including the current card and active steer; do not use a phrase table. Decide whether they ask for the next problem, set or extend one steer, clear it, or say something unrelated. A steer has catalog focus ids, direction and a session or until-cleared horizon, and may also carry difficulty levels (Easy, Medium, Hard) and a short note holding the learner's own words; take the shorter session reading if ambiguous. Turn two kinds of difficulty request. A relative one (easier, harder, a bit tougher) is not turned into levels: put no difficulty in the focus, keep their words in the note, and at every pick judge the note against the problem you just gave or the current card, so harder keeps meaning harder than the last one; a topic they also named still goes in the focus (for harder dynamic programming, the focus is dynamic programming and the note says harder). An absolute one puts the level in the focus' difficulties: "only mediums" is Medium with direction more, "no hards" or "no more hards" is Hard with direction fewer (never the other two levels), "more easies" is Easy with direction more; name only topics the learner named (a request about difficulty alone leaves categories and patterns empty), spell topics correctly; anything the parameters cannot express (length, style, taste) goes in the note and the steer is still saved. Always write the reply in English. Reply to the learner in the otter's voice: one or two short, warm sentences that name their topic in their own words so they can see you understood, react to it the way a friendly study buddy would, and lead into the problem you picked. Speak to the learner directly as "you", never about them in the third person. Vary the phrasing every time and never use a fixed template such as "Focusing on X this session". Keep it to two short sentences at most. Do not name the suggested problem (the card already shows it), do not give study advice or lessons, and never repeat the learner's message back word for word. Never reveal a pattern or approach they did not ask for. "Focus on X" or "more X" means direction more; "less X" or "no X" means fewer. The steer label is a short plain name of that focus, such as \"Tries\"; it is the plain restatement, so the reply need not be one. A word you cannot match to a topic, difficulty or problem must be reading unreadable, quoting the word and asking what they meant; never guess it into a steer, even when you have a good guess (offer the guess in the question). If an ask or steer cannot be met within eligibility, say so plainly once and offer the nearest eligible alternative. Reading none is only for requests that are not a chat message; a chat message that is not about practice is unrelated, never none. Set understood to null when reading is none or unrelated (never the word \"none\"), and to that reply for ask, steer, clear or unreadable. Fill ask only when reading is ask (otherwise null), steer only when reading is steer (otherwise null), and named_by_you true only when reading is ask and they named a specific problem (otherwise false). Return structured JSON only."""


class Focus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    categories: list[str]
    patterns: list[str]
    difficulties: list[Literal["Easy", "Medium", "Hard"]]
    direction: Literal["more", "fewer"]

    @model_validator(mode="after")
    def nonempty(self):
        if not self.categories and not self.patterns and not self.difficulties:
            raise ValueError("focus must name a category, pattern or difficulty")
        return self


class SteerReading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    focus: list[Focus]
    horizon: Literal["session", "until_cleared"]
    label: str = Field(min_length=1)
    note: str

    @model_validator(mode="after")
    def something_to_steer_by(self):
        if not self.focus and not self.note.strip():
            raise ValueError("a steer with no focus needs a note holding the request")
        return self


class PlannerReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slug: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    reading: Literal["none", "ask", "steer", "clear", "unreadable", "unrelated"]
    ask: list[Focus] | None
    steer: SteerReading | None
    understood: str | None
    named_by_you: bool

    @model_validator(mode="after")
    def consistent(self):
        if self.reading == "ask":
            if self.ask is None or (not self.ask and not self.named_by_you):
                raise ValueError("ask needs a focus or named problem")
        elif self.ask is not None or self.named_by_you:
            raise ValueError("ask fields only apply to an ask")
        if (self.reading == "steer") != (self.steer is not None):
            raise ValueError("steer reading needs steer data")
        if (self.reading in {"ask", "steer", "clear", "unreadable"}) != bool(self.understood):
            raise ValueError("reading needs the appropriate understood line")
        return self


@dataclass(frozen=True)
class PlannerRequest:
    trigger: str
    session_id: int | None
    started: datetime
    allowed: list[dict]
    messages: list[dict]
    known_patterns: frozenset[str]
    learner_text: str = ""


def _reviews(db):
    rows = db.execute("SELECT item_key, grade, meets_expected, difficulty, reviewed_at FROM reviews WHERE item_type = 'problem' ORDER BY reviewed_at, id").fetchall()
    grouped = {}
    for slug, grade, meets, difficulty, at in rows:
        grouped.setdefault(slug, []).append({"grade": grade, "meets": meets, "difficulty": difficulty, "at": at})
    return grouped


def _patterns(db):
    return {slug: json.loads(content).get("patterns", []) for slug, content in db.execute('SELECT problem_key, content FROM "references"')}


def learner_summary(db, *, now):
    catalog = load_catalog()
    reviews = _reviews(db)
    imported = {json.loads(raw)["slug"] for (raw,) in db.execute("SELECT payload FROM events WHERE type = 'prior_solve'")}
    result = {}
    for category in CATEGORIES:
        recent = {difficulty: [] for difficulty in ("Easy", "Medium", "Hard")}
        recent_dated = {difficulty: [] for difficulty in ("Easy", "Medium", "Hard")}
        done = imports = 0
        for entry in catalog.entries:
            if entry["category"] != category:
                continue
            imports += entry["slug"] in imported
            graded = [r for r in reviews.get(entry["slug"], []) if r["grade"] is not None]
            if graded:
                latest = graded[-1]
                done += latest["grade"] in ("good", "hard") and bool(latest["meets"])
                for row in graded[-5:]:
                    difficulty = row["difficulty"] or entry["difficulty"]
                    recent[difficulty].append(row["grade"])
                    recent_dated[difficulty].append({"grade": row["grade"], "at": row["at"]})
        result[category] = {"solve": item_memory(db, "category:solve", category, now),
                            "analysis": item_memory(db, "category:analysis", category, now),
                            "recent": recent, "recent_dated": recent_dated, "done": done, "imported": imports}
    return result


def _session_attempts(db, session_id):
    if session_id is None:
        return []
    rows = db.execute("SELECT ts, type, payload FROM events WHERE session_id = ? ORDER BY id", (session_id,))
    attempts = []
    current = {}
    for ts, kind, raw in rows:
        p = json.loads(raw)
        if kind == "problem_opened":
            current[p["slug"]] = {"slug": p["slug"], "opened": ts, "accepted": False, "help": []}
        elif kind == "submit_result" and p.get("slug") in current and p.get("accepted"):
            current[p["slug"]]["accepted"] = True
        elif kind == "help_turn" and p.get("slug") in current:
            current[p["slug"]]["help"].append(p.get("help_kind"))
        elif kind == "problem_left" and p["slug"] in current:
            left = current.pop(p["slug"])
            left["left"] = ts
            left["accepted"] = p["accepted"]
            attempts.append(left)
    return attempts


def _unfinished(db):
    catalog = load_catalog()
    reviewed = {slug for (slug,) in db.execute("SELECT DISTINCT item_key FROM reviews WHERE item_type = 'problem'")}
    latest = {}
    for (session_id,) in db.execute("SELECT id FROM sessions ORDER BY id"):
        for attempt in _session_attempts(db, session_id):
            latest[attempt["slug"]] = attempt
    return [{"slug": slug, "title": catalog.by_slug(slug)["title"], "last_left": attempt["left"]}
            for slug, attempt in latest.items() if not attempt["accepted"] and slug not in reviewed
            and catalog.by_slug(slug) is not None]


def allowed_next(db, *, session_id, now):
    catalog = load_catalog()
    reviews = _reviews(db)
    patterns = _patterns(db)
    attempts = _session_attempts(db, session_id)
    imported = {json.loads(raw)["slug"] for (raw,) in db.execute("SELECT payload FROM events WHERE type = 'prior_solve'")}
    skipped = {a["slug"] for a in attempts if not a["accepted"]}
    pending_session = {a["slug"] for a in attempts if a["accepted"] and not any(r["grade"] and r["at"] >= a["left"] for r in reviews.get(a["slug"], []))}
    eligible_untried_150 = any(e["neetcode150"] and not e["locked"] and e["slug"] not in skipped | pending_session and
                               not reviews.get(e["slug"]) for e in catalog.entries)
    last_retry = bool(attempts and any(r["grade"] and r["at"] < attempts[-1]["opened"]
                                       for r in reviews.get(attempts[-1]["slug"], [])))
    candidates = []
    for entry in catalog.entries:
        slug = entry["slug"]
        history = reviews.get(slug, [])
        if entry["locked"] or slug in skipped | pending_session or any(r["grade"] is None for r in history):
            continue
        graded = [r for r in history if r["grade"]]
        retry = bool(graded)
        done = bool(graded and graded[-1]["grade"] in ("good", "hard") and graded[-1]["meets"])
        if retry:
            if last_retry and eligible_untried_150:
                continue
            memory = item_memory(db, "problem", slug, now)
            if done:
                category_memory = item_memory(db, "category:solve", entry["category"], now)
                if not category_memory or category_memory.recall >= .9 or category_memory.stability >= 7:
                    continue
                if any(catalog.by_slug(a["slug"])["category"] == entry["category"] and
                       any(r["grade"] and r["meets"] for r in reviews.get(a["slug"], [])) for a in attempts):
                    continue
            elif memory and memory.recall >= .9:
                continue
            if graded[-1]["grade"] == "again" and patterns.get(slug):
                siblings = {e["slug"] for e in catalog.entries if not e["locked"] and e["slug"] != slug and
                            set(patterns.get(e["slug"], [])) & set(patterns[slug])}
                unseen_siblings = {other for other in siblings if other not in imported | skipped | pending_session
                                   and not reviews.get(other)}
                practiced = any(r["grade"] and r["at"] > graded[-1]["at"] for other in siblings for r in reviews.get(other, []))
                practiced |= any(a["slug"] in siblings and a["left"] > graded[-1]["at"] for a in attempts)
                if unseen_siblings and not practiced:
                    continue
        candidates.append({"slug": slug, "title": entry["title"], "category": entry["category"],
                           "difficulty": entry["difficulty"], "retry": retry, "patterns": patterns.get(slug, []),
                           "neetcode150": entry["neetcode150"], "done": done})
    return candidates


def _matches(candidate, focus):
    categories = focus.get("categories", [])
    patterns = focus.get("patterns", [])
    difficulties = focus.get("difficulties", [])
    topic = not categories and not patterns or candidate["category"] in categories or bool(set(candidate["patterns"]) & set(patterns))
    difficulty = not difficulties or candidate["difficulty"] in difficulties
    matched = topic and difficulty
    return matched if focus["direction"] == "more" else not matched


def _focused(candidates, focuses):
    filtered = [c for c in candidates if all(_matches(c, f) for f in focuses)]
    return filtered or candidates


def _rank_key(c, summary):
    return (summary[c["category"]]["solve"].recall if summary[c["category"]]["solve"] else -1,
            3 if c["done"] else 0 if c["retry"] else 2 if not c["neetcode150"] else 1,
            c["title"])


def code_ranked(candidates, summary, steer=None):
    if not candidates:
        raise ValueError("No eligible problems to suggest")
    pool = _focused(candidates, steer["focus"]) if steer else candidates
    return min(pool, key=lambda c: _rank_key(c, summary))


def shortlist(candidates, summary, steer=None):
    pool = sorted(_focused(candidates, steer["focus"]) if steer else candidates, key=lambda c: _rank_key(c, summary))
    kept = {}
    for c in pool:
        group = kept.setdefault(c["category"], [])
        if len(group) < SHORTLIST_PER_CATEGORY or not c["neetcode150"] and all(k["neetcode150"] for k in group):
            group.append(c)
    return [c for group in kept.values() for c in group]


def active_steer(db, *, session_id):
    for kind, raw, owner in db.execute("SELECT kind, text, session_id FROM notes WHERE kind IN ('steer', 'steer_clear') ORDER BY id DESC"):
        if kind == "steer_clear":
            return None
        steer = json.loads(raw)
        steer.setdefault("note", "")
        for focus in steer.get("focus", []):
            focus.setdefault("difficulties", [])
        if steer["horizon"] == "until_cleared" or owner == session_id:
            return steer
        return None
    return None


def _validate_reply(reply, request):
    if request.trigger == "chat" and reply.reading == "none" or request.trigger != "chat" and reply.reading != "none":
        raise ValueError("Reading does not match request: a chat message is ask, steer, clear, unreadable or unrelated, never none; any other request is none")
    catalog = load_catalog()
    known_patterns = request.known_patterns
    for focus in (reply.ask or []) + (reply.steer.focus if reply.steer else []):
        if any(c not in CATEGORIES for c in focus.categories) or any(p not in known_patterns for p in focus.patterns):
            raise ValueError("Unknown focus id")
    allowed = {c["slug"]: c for c in request.allowed}
    entry = catalog.by_slug(reply.slug)
    if reply.slug not in allowed and not (request.trigger == "chat" and reply.named_by_you and entry and not entry["locked"]):
        raise ValueError("Suggested problem is not eligible")
    if reply.reading == "ask" and reply.ask and reply.slug in allowed:
        satisfying = [c for c in request.allowed if all(_matches(c, f.model_dump()) for f in reply.ask)]
        if satisfying and reply.slug not in {c["slug"] for c in satisfying}:
            raise ValueError("Direct ask has an eligible match")
    if reply.understood and len(reply.understood) > 280:
        raise ValueError("Understood line is too long; reply in one or two short sentences to the learner")
    if reply.understood and ("{" in reply.understood or "}" in reply.understood):
        raise ValueError("Understood line contains stray braces; write plain sentences only")
    if reply.understood and request.learner_text:
        def bare(text):
            return "".join(ch for ch in text.lower() if ch.isalnum())
        if bare(reply.understood) == bare(request.learner_text):
            raise ValueError("Understood line only repeats the learner's message; reply in your own words")
    if reply.understood and entry:
        volunteered = [entry["category"], *allowed.get(reply.slug, {}).get("patterns", [])]
        requested = [name for f in (reply.ask or []) + (reply.steer.focus if reply.steer else [])
                     for name in [*f.categories, *f.patterns]]
        learner = request.learner_text.lower().replace("-", " ")
        for name in volunteered:
            spoken = name.lower().replace("-", " ")
            if spoken not in [r.lower().replace("-", " ") for r in requested] and spoken in reply.understood.lower() and spoken not in learner:
                raise ValueError(f"Understood line names '{name}', which the learner did not ask for; rewrite it without that word")


def ask_planner(client, request):
    messages = request.messages
    for attempt in range(2):
        reply = client.call_role("planner", messages, PlannerReply)
        try:
            _validate_reply(reply, request)
            return reply
        except ValueError as exc:
            if attempt:
                raise ValueError(f"Planner reply invalid after retry: {exc}") from exc
            messages = [*messages, {"role": "assistant", "content": reply.model_dump_json()},
                        {"role": "user", "content": f"Invalid reply: {exc}. Correct it."}]
    raise AssertionError("unreachable")


class Planner:
    def __init__(self, db_path, clock):
        self.db_path = db_path
        self.clock = clock
        self.card = None
        self._card_session_id = None
        self._waiting = None
        self._chat = []

    def _request(self, trigger, session_id, text=None):
        now = self.clock()
        with open_store(self.db_path) as db:
            allowed = allowed_next(db, session_id=session_id, now=now)
            summary = learner_summary(db, now=now)
            steer = active_steer(db, session_id=session_id)
            imported = [json.loads(raw) for (raw,) in db.execute("SELECT payload FROM events WHERE type = 'prior_solve'")]
            hypotheses = [dict(text=t, category=c, skill=s) for t, c, s in db.execute("SELECT text, category, skill FROM hypotheses WHERE status = 'open'")]
            preferences = [t for (t,) in db.execute("SELECT text FROM notes WHERE kind = 'preference' ORDER BY id DESC LIMIT 20")]
            attempts = _session_attempts(db, session_id)
            unfinished = _unfinished(db)
            known_patterns = frozenset(p for patterns in _patterns(db).values() for p in patterns)
        catalog = load_catalog()
        session_problems = []
        for a in attempts:
            entry = catalog.by_slug(a["slug"])
            if entry:
                session_problems.append({"slug": a["slug"], "title": entry["title"], "difficulty": entry["difficulty"],
                                         "active_seconds": max(0, int((datetime.fromisoformat(a["left"]) - datetime.fromisoformat(a["opened"])).total_seconds())),
                                         "help": a["help"], "outcome": "accepted" if a["accepted"] else "unfinished"})
        rendered = {category: {"solve": vars(v["solve"]) if v["solve"] else None,
                               "analysis": vars(v["analysis"]) if v["analysis"] else None,
                               "recent": v["recent"], "recent_dated": v["recent_dated"],
                               "done": v["done"], "imported": v["imported"]}
                    for category, v in summary.items()}
        offered = allowed if trigger == "chat" else shortlist(allowed, summary, steer) if allowed else []
        content = {"today": now.date().isoformat(), "allowed": offered, "learner_summary": rendered,
                   "session_problems": session_problems, "imported_history": imported, "hypotheses": hypotheses,
                   "preferences": preferences, "unfinished": unfinished,
                   "card": self.card, "chat": self._chat, "steer": steer, "message": text}
        request = PlannerRequest(trigger, session_id, now, allowed,
                                 [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(content)}],
                                 known_patterns, learner_text=" ".join(self._chat))
        self._waiting = request
        return request

    def panel_opened(self, state):
        if state not in ("idle", "ended") or self.card or self._waiting:
            return None
        return self._request("start", None)

    def resume(self, session_id):
        """An active session with no problem open, no card and no request out (the companion restarted, or a
        request was lost) asks again, so the panel never waits on nothing."""
        if self.card or self._waiting:
            return None
        return self._request("start", session_id)

    def problem_left(self, session_id):
        self.card = None
        self._chat = []
        return self._request("between", session_id)

    def _card_owner(self, session_id):
        if self._card_session_id is None and session_id is not None:
            with open_store(self.db_path) as db:
                active = db.execute("SELECT 1 FROM sessions WHERE id = ? AND ended_at IS NULL", (session_id,)).fetchone()
            if active:
                self.session_started(session_id)
        return self._card_session_id

    def swap(self, session_id):
        if not self.card:
            # The panel showed a card the companion no longer has (it restarted): swapping just asks again.
            return self._request("swap", session_id)
        session_id = self._card_owner(session_id)
        with open_store(self.db_path) as db:
            db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, 'preference', ?, ?)",
                       (self.clock().isoformat(), f"Swapped {self.card['slug']}", session_id))
        self.card = None
        self._chat = []
        return self._request("swap", session_id)

    def chat(self, session_id, text):
        if not self.card and not self._waiting:
            return None
        owner = self._card_owner(session_id) if self.card else self._waiting.session_id
        self._chat.append(text)
        return self._request("chat", owner, text)

    def clear_steer(self, session_id):
        if not self.card:
            raise ValueError("No suggestion to clear")
        session_id = self._card_owner(session_id)
        with open_store(self.db_path) as db:
            db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, 'steer_clear', '', ?)",
                       (self.clock().isoformat(), session_id))
        self.card = None
        return self._request("clear", session_id)

    def go(self):
        if not self.card:
            raise ValueError("No suggestion to go with")
        problem = {key: self.card[key] for key in ("slug", "title")}
        self.card = None
        self._waiting = None
        return problem

    def session_ended(self):
        self.card = None
        self._waiting = None
        self._chat = []
        self._card_session_id = None

    def session_started(self, session_id):
        """Adopts the start card the panel already showed. With no card, a request still out from the panel opening
        is dropped (it knows nothing of this session's steer) and a new one for this session is returned, so a
        Start never waits on nothing."""
        adopt = self.card is not None and self._card_session_id is None
        if adopt:
            self._waiting = None
            self._chat = []
            self._card_session_id = session_id
        else:
            self.session_ended()
        with open_store(self.db_path) as db:
            latest = db.execute("SELECT id, kind, text, session_id FROM notes WHERE kind IN ('steer', 'steer_clear') ORDER BY id DESC LIMIT 1").fetchone()
            if latest and latest[1] == "steer" and latest[3] is None and json.loads(latest[2])["horizon"] == "session":
                db.execute("UPDATE notes SET session_id = ? WHERE id = ?", (session_id, latest[0]))
        return None if adopt else self._request("start", session_id)

    def _show(self, request, candidate, source, message):
        with open_store(self.db_path) as db:
            steer = active_steer(db, session_id=request.session_id)
        self.card = {"type": "suggestion", "ts": self.clock().isoformat(), "slug": candidate["slug"],
                     "title": candidate["title"], "difficulty": candidate["difficulty"],
                     "cap_minutes": TIME_CAP_MINUTES[candidate["difficulty"]],
                     "steer": steer["label"] if steer else None, "message": message, "source": source}
        self._card_session_id = request.session_id
        self._waiting = None
        return [self.card]

    def answer(self, request, reply):
        if request is not self._waiting:
            return []
        if reply.reading == "unrelated":
            self._waiting = None
            return [self.card] if self.card else []
        if reply.reading in ("steer", "clear"):
            with open_store(self.db_path) as db:
                kind = "steer" if reply.reading == "steer" else "steer_clear"
                body = reply.steer.model_dump_json() if reply.steer else ""
                db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, ?, ?, ?)",
                           (self.clock().isoformat(), kind, body, request.session_id))
        candidate = next((c for c in request.allowed if c["slug"] == reply.slug), None)
        if candidate is None:
            candidate = load_catalog().by_slug(reply.slug)
        return self._show(request, candidate, "planner", reply.understood)

    def fail(self, request, error):
        if request is not self._waiting:
            return []
        logger.warning("Planner failed (%s): %s", request.trigger, error)
        with open_store(self.db_path) as db:
            summary = learner_summary(db, now=self.clock())
            steer = active_steer(db, session_id=request.session_id)
            db.execute("INSERT INTO notes (ts, kind, text, session_id) VALUES (?, 'planner_failure', ?, ?)",
                       (self.clock().isoformat(), str(error), request.session_id))
        if not request.allowed:
            self.session_ended()
            return [{"type": "error", "message": "The Planner failed and no eligible problems are available to suggest."}]
        message = "The Planner failed; your steer wasn't saved. Here's a suggestion from the fallback." if request.trigger == "chat" else "The Planner didn't answer; here's a code-ranked suggestion."
        return self._show(request, code_ranked(request.allowed, summary, steer), "fallback", message)

    def tick(self):
        if self._waiting and (self.clock() - self._waiting.started).total_seconds() >= PLANNER_WAIT:
            return self.fail(self._waiting, TimeoutError("Planner timed out"))
        return []
