"""Planner eligibility, learner context, validated choices, and suggestion lifecycle."""

import difflib
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

PICKER_PROMPT = """You are Leetotter's Planner. Pick exactly one problem from `allowed`, by slug. Keep the reason short; it is internal and never shown. Use the learner summary, imported history, unfinished problems, open hypotheses, preferences, today's date, this session's problems and load, the card, the chat and message, and the active steer. A steer's note holds the learner's own words: honour it, and read a relative word such as harder or easier against the card or the problem they just had. When the learner's words ask for a kind of problem (for example depth-first search), prefer an allowed problem whose patterns fit. Sample categories while evidence is thin, starting with imported history; balance weaknesses, mix categories after initial practice, check transfer on unseen problems, and occasionally use free problems beyond the 150. Avoid stacking demanding problems. Return JSON only: {"slug", "reason"}."""

READER_PROMPT = """You read one chat message from a learner practising coding problems with Leetotter, a friendly study-buddy otter. A problem card may be showing. Return JSON only. You never pick a problem and never say which one comes next.
reading: ask = they want a kind of problem or the next one, once (a topic, a difficulty, a named problem, or just "another"); steer = they want future suggestions shaped, as a standing preference (more or less of something, a difficulty, a style); clear = back to normal, drop the steer; cannot = you cannot act on it by picking a problem, for a true reason: a word you cannot match to a category, difficulty or problem (never guess it, even when you have a good guess: offer the guess in the question), or a request that needs something you cannot see or do. You see only: this message, the chat since the card appeared, the card's title and difficulty, the active steer and the category names. You do not see past sessions or days, scores, history, or the problem list. unrelated = nothing to do with practice (greeting, small talk, a question about you).
reply: always, in English: one or two short, warm sentences in the otter's voice, to "you", varied every time. For ask, steer and clear, name their topic in their own words so they see you understood, and say you'll find something; for cannot, say the real reason plainly and never blame the wrong cause (a request about something you have no record of is missing information, not a word you failed to understand; an unknown word is quoted and asked about), then offer what you can do instead; for unrelated, answer them like a friendly buddy in a line or two; if they ask to be taught something, say the Tutor helps with that while they work on a problem. Never mention a pattern or approach they did not name, give no lessons, and never repeat their message word for word.
more_categories, fewer_categories: only the catalog categories they named or clearly meant ("DFS" is Graphs and Trees; "dynamic programming" is both dynamic programming categories). A request combining both ("more trees, fewer arrays") fills both groups. more_difficulties, fewer_difficulties: levels they named: "only mediums" is more Medium; "no hards" or "no more hards" is fewer Hard; "more easies" is more Easy. A difficulty request alone leaves the categories empty. A relative word (easier, harder, a bit tougher) for an ask becomes a concrete level using the card's difficulty; for a steer it gets no level and stays in the note.
note: for a steer, the learner's own words for anything the fields cannot express (length, style, taste, a relative word); otherwise "". A wish about length, style or taste ("something short", "a quick one") is a steer saved with that note, never cannot. label: for a steer, a short plain name such as "Tries"; otherwise "". horizon: for a steer, "session" unless they say it should last (always, until I say, from now on); take "session" when unsure; otherwise "session". named_title: for an ask that names one specific problem, its title as they said it; otherwise "". An ask with no category, difficulty or title is only for plain "next one" or "another" requests; if it holds a word you cannot map, it is cannot."""


Category = Literal[CATEGORIES]
Difficulty = Literal["Easy", "Medium", "Hard"]


class Focus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    categories: list[str]
    patterns: list[str]
    difficulties: list[Difficulty]
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


class Reading(BaseModel):
    """What the Reader model returns for a chat message: flat, every field always present, nothing that must agree."""
    model_config = ConfigDict(extra="forbid", strict=True)
    reading: Literal["ask", "steer", "clear", "cannot", "unrelated"]
    reply: str = Field(min_length=1)
    more_categories: list[Category]
    more_difficulties: list[Difficulty]
    fewer_categories: list[Category]
    fewer_difficulties: list[Difficulty]
    horizon: Literal["session", "until_cleared"]
    label: str
    note: str
    named_title: str


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slug: str = Field(min_length=1)
    reason: str = Field(min_length=1)


# Readings that only talk: no problem is picked and the current card stays.
TALK_ONLY = {"cannot", "unrelated"}


@dataclass(frozen=True)
class PlannerReply:
    """The decision ask_planner hands to Planner.answer, assembled by code from the Reader and Picker replies."""
    reading: Literal["none", "ask", "steer", "clear", "cannot", "unrelated"]
    understood: str | None = None
    slug: str | None = None
    steer: SteerReading | None = None


@dataclass(frozen=True)
class PlannerRequest:
    """Everything a Planner call reads, snapshotted when the request is made: ask_planner runs on another thread."""
    trigger: str
    session_id: int | None
    started: datetime
    allowed: list[dict]
    context: dict
    summary: dict
    card: dict | None
    chat: tuple[str, ...]
    steer: dict | None
    message: str | None = None


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


def _focuses(reading):
    return [{"categories": list(categories), "patterns": [], "difficulties": list(difficulties), "direction": direction}
            for direction, categories, difficulties in (
                ("more", reading.more_categories, reading.more_difficulties),
                ("fewer", reading.fewer_categories, reading.fewer_difficulties))
            if categories or difficulties]


def _pool(allowed, focuses):
    """The eligible problems meeting every focus, and whether any did; with none, the whole eligible list."""
    matched = [c for c in allowed if all(_matches(c, f) for f in focuses)]
    return (matched, True) if matched or not focuses else (allowed, False)


def _spoken(title):
    return "".join(ch for ch in title.lower() if ch.isalnum())


def _named(title):
    """The unlocked catalog problem the learner named, or the closest titles to ask about."""
    entries = [e for e in load_catalog().entries if not e["locked"]]
    spoken = _spoken(title)
    for entry in entries:
        if _spoken(entry["title"]) == spoken:
            return entry, []
    close = difflib.get_close_matches(spoken, [_spoken(e["title"]) for e in entries], n=1, cutoff=.6)
    return None, [e["title"] for e in entries if _spoken(e["title"]) in close]


def _pick_messages(request, pool, steer):
    content = {**request.context, "allowed": shortlist(pool, request.summary, None), "card": request.card,
               "chat": list(request.chat), "steer": steer, "message": request.message}
    return [{"role": "system", "content": PICKER_PROMPT}, {"role": "user", "content": json.dumps(content)}]


def _record_pick(client, messages, pick):
    """Keep the Picker's reason and the slugs it was offered on its model_calls row, so an odd pick can be explained."""
    if not hasattr(client, "db_path"):  # test doubles have no store; ModelClient's None means the default store
        return
    offered = [c["slug"] for c in json.loads(messages[1]["content"])["allowed"]]
    try:
        with open_store(client.db_path) as db:
            db.execute("UPDATE model_calls SET prompt_text = ? WHERE id = (SELECT MAX(id) FROM model_calls WHERE role = 'planner')",
                       (json.dumps({"picked": pick.slug, "reason": pick.reason, "offered": offered}),))
    except Exception:
        logger.exception("Could not record the Picker's reason")


def _pick(client, request, pool, steer, valid):
    """The Picker's slug, offered `pool` and accepted from `valid`; None when it can't produce one."""
    messages = _pick_messages(request, pool, steer)
    slugs = {c["slug"] for c in valid}
    for _ in range(2):
        pick = client.call_role("planner", messages, Pick)
        if pick.slug in slugs:
            _record_pick(client, messages, pick)
            return pick.slug
        messages = [*messages, {"role": "assistant", "content": pick.model_dump_json()},
                    {"role": "user", "content": f"{pick.slug} is not in allowed. Choose one slug from allowed."}]
    return None


def _read(client, request):
    content = {"today": request.started.date().isoformat(), "message": request.message, "earlier_chat": list(request.chat[:-1]),
               "card": {"title": request.card["title"], "difficulty": request.card["difficulty"]} if request.card else None,
               "steer": request.steer, "categories": list(CATEGORIES)}
    return client.call_role("planner", [{"role": "system", "content": READER_PROMPT},
                                        {"role": "user", "content": json.dumps(content)}], Reading)


def ask_planner(client, request):
    if request.trigger != "chat":
        pool, _ = _pool(request.allowed, request.steer["focus"] if request.steer else [])
        slug = _pick(client, request, pool, request.steer, request.allowed)
        if slug is None:
            raise ValueError("Planner picked a problem that is not eligible, twice")
        return PlannerReply("none", slug=slug)
    reading = _read(client, request)
    line = reading.reply.strip()
    if reading.reading in TALK_ONLY:
        return PlannerReply(reading.reading, line)
    steer, focuses = request.steer, _focuses(reading)
    if reading.reading == "steer":
        note = reading.note.strip()
        if not focuses and not note:
            return PlannerReply("cannot", line)
        steer = {"focus": focuses, "horizon": reading.horizon, "label": reading.label.strip() or note[:40] or "Your focus", "note": note}
    elif reading.reading == "clear":
        steer, focuses = None, []
    elif reading.named_title.strip():
        entry, close = _named(reading.named_title.strip())
        if entry:
            return PlannerReply("ask", line, entry["slug"])
        guess = f" Did you mean {close[0]}?" if close else ""
        return PlannerReply("cannot", f"I couldn't find a problem called \"{reading.named_title.strip()}\".{guess}")
    elif not focuses and steer:
        focuses = steer["focus"]
    pool, fits = _pool(request.allowed, focuses)
    if not fits:
        line += " Nothing eligible fits that right now, so this is the closest I have."
    try:
        # A direct ask for something eligible must be met; otherwise a due retry outside the focus stays pickable.
        slug = _pick(client, request, pool, steer, pool if reading.reading == "ask" and focuses and fits else request.allowed)
    except Exception:
        logger.exception("Planner picker failed after a valid reading; falling back to the code ranking")
        slug = None
    if slug is None:
        slug = code_ranked(pool, request.summary)["slug"]
        line += " I couldn't choose myself, so this is the top match from my list."
    return PlannerReply(reading.reading, line, slug, SteerReading.model_validate(steer) if reading.reading == "steer" else None)


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
            hypotheses = [dict(text=t, category=c, skill=s) for t, c, s in db.execute(
                "SELECT h.text, h.category, h.skill FROM hypotheses h WHERE h.status = 'open' "
                "AND (h.recap_session_id IS NULL OR EXISTS (SELECT 1 FROM sessions s "
                "WHERE s.id = h.recap_session_id AND s.recap_seen = 1))")]
            preferences = [t for (t,) in db.execute("SELECT text FROM notes WHERE kind = 'preference' ORDER BY id DESC LIMIT 20")]
            attempts = _session_attempts(db, session_id)
            unfinished = _unfinished(db)
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
        context = {"today": now.date().isoformat(), "learner_summary": rendered, "session_problems": session_problems,
                   "imported_history": imported, "hypotheses": hypotheses, "preferences": preferences, "unfinished": unfinished}
        request = PlannerRequest(trigger, session_id, now, allowed, context, summary, dict(self.card) if self.card else None,
                                 tuple(self._chat), steer, text)
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
        if not self.card:
            return None
        owner = self._card_owner(session_id)
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
        self._chat = []
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
        if reply.reading in TALK_ONLY:
            self._waiting = None
            return [{**self.card, "message": reply.understood}] if self.card else []
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
