"""The past problems the Tutor may link to, built from the store (SPEC §6, "Past links")."""

import json
import logging
import re
from dataclasses import dataclass

from companion.catalog import load_catalog
from companion.reference import StoredReference

logger = logging.getLogger(__name__)

HINT_KINDS = ("question", "trace", "analogy", "teach_tool", "syntax")
HINT_WORDS = {"question": "question", "trace": "trace", "analogy": "analogy",
              "teach_tool": "taught tool", "syntax": "syntax pointer"}
CONCRETE = ("full_solution", "partial_logic", "snippet")
ACTIVITY = frozenset({
    "run_result", "submit_result", "help_request", "chat_message", "help_turn", "help_failed",
    "followup_asked", "followup_answered", "reasoning_requested", "accept_counted",
})
CODE = frozenset({"code_snapshot", "code_changed"})


@dataclass(frozen=True)
class PastLink:
    slug: str
    title: str
    summary: str | None
    outcome: str
    approach: str | None
    idea: str | None
    shared_patterns: tuple[str, ...]
    shared_category: bool
    good: bool
    order: int

    @property
    def full_solution(self) -> bool:
        return self.outcome in ("shown the full solution", "solved after the full solution")


@dataclass
class _Attempt:
    slug: str
    title: str
    session_id: int
    events: list[tuple[int, str, dict]]
    accepted: bool = False
    accept_id: int | None = None
    last_id: int = 0

    def add(self, event_id, kind, payload):
        self.events.append((event_id, kind, payload))
        self.last_id = max(self.last_id, event_id)

    @property
    def worked(self) -> bool:
        return self.accepted or any(kind in ACTIVITY or kind in CODE for _, kind, _ in self.events)

    @property
    def help(self):
        return [(event_id, payload["help_kind"]) for event_id, kind, payload in self.events
                if kind == "help_turn" and payload.get("help_kind") in (*HINT_KINDS, *CONCRETE)]

    def help_before_accept(self):
        return [(event_id, kind) for event_id, kind in self.help
                if self.accept_id is None or event_id < self.accept_id]

    @property
    def used_full_solution(self) -> bool:
        return any(kind == "full_solution" for _, kind in self.help_before_accept())

    def outcome(self):
        before = self.help_before_accept()
        if not self.accepted:
            return "shown the full solution" if self.used_full_solution else "unfinished"
        if any(kind == "full_solution" for _, kind in before):
            return "solved after the full solution"
        if any(kind == "partial_logic" for _, kind in before):
            return "solved with partial logic"
        if any(kind == "snippet" for _, kind in before):
            return "solved with a snippet"
        hints = [(event_id, kind) for event_id, kind in before if kind in HINT_KINDS]
        if hints:
            return f"solved with a {HINT_WORDS[hints[-1][1]]}"
        return "solved alone"

    @property
    def good(self):
        return self.accepted and not any(kind in CONCRETE for _, kind in self.help_before_accept())


def _excluded_sessions(db) -> set[int]:
    columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    if "excluded" not in columns:
        return set()
    return {row[0] for row in db.execute("SELECT id FROM sessions WHERE excluded = 1")}


def _attempts(db) -> list[_Attempt]:
    """Every worked attempt in a session, including one never left; imported solves have no session and never appear."""
    excluded = _excluded_sessions(db)
    attempts = []
    current: _Attempt | None = None

    def finish():
        nonlocal current
        if current is not None:
            attempts.append(current)
            current = None

    rows = db.execute("SELECT id, session_id, type, payload FROM events WHERE session_id IS NOT NULL ORDER BY id")
    for event_id, session_id, kind, raw in rows:
        if session_id in excluded:
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("Past links skipped an unreadable %s event %s", kind, event_id)
            continue
        if current is not None and current.session_id != session_id:
            finish()
        if kind == "problem_opened":
            if current is not None and current.slug != payload.get("slug"):
                finish()
            if current is None:
                current = _Attempt(payload["slug"], payload["title"], session_id, [], last_id=event_id)
            else:
                current.title = payload.get("title") or current.title
                current.last_id = event_id
            continue
        if current is None:
            continue
        if kind == "problem_left":
            if payload.get("slug") != current.slug:
                continue
            current.add(event_id, kind, payload)
            if payload.get("accepted"):
                current.accepted = True
                current.accept_id = current.accept_id or event_id
            finish()
            continue
        if payload.get("slug") == current.slug:
            current.add(event_id, kind, payload)
            if kind == "submit_result" and payload.get("accepted"):
                current.accepted = True
                current.accept_id = current.accept_id or event_id
            elif kind == "accept_counted":
                current.accepted = True
                current.accept_id = current.accept_id or event_id
        elif kind in CODE:
            current.add(event_id, kind, payload)
    finish()
    latest = {}
    for attempt in attempts:
        if attempt.worked:
            latest[attempt.slug] = attempt
    return list(latest.values())


def _reference_rows(db, slugs):
    rows = db.execute(f'SELECT problem_key, content FROM "references" WHERE problem_key IN ({",".join("?" for _ in slugs)})',
                      tuple(slugs)).fetchall()
    found = {}
    for slug, content in rows:
        try:
            found[slug] = json.loads(content)
        except ValueError:
            logger.warning("Past links skipped the unreadable reference for %s", slug)
    return found


def _ideas(db, current_session_id):
    current_events = set()
    if current_session_id is not None:
        current_events = {row[0] for row in db.execute("SELECT id FROM events WHERE session_id = ?", (current_session_id,))}
    ideas = {}
    for slug, raw, idea in db.execute("SELECT problem_slug, cited_event_ids, idea FROM teaching ORDER BY id DESC"):
        if not slug or not idea:
            continue
        if slug in ideas:
            continue
        try:
            cited = set(json.loads(raw or "[]"))
        except ValueError:
            cited = set()
        if cited & current_events:
            continue
        ideas[slug] = idea
    return ideas


def build_past_links(db, slug: str, *, reference: StoredReference | None = None,
                     current_session_id: int | None = None) -> list[PastLink]:
    """Earlier worked problems sharing this problem's category or one of its patterns, in SPEC order.

    The current problem, imported-only solves and excluded sessions never appear. A missing summary leaves the
    entry by title only, with the gap logged.
    """
    catalog = load_catalog()
    current = catalog.by_slug(slug)
    current_patterns = set(reference.reference.patterns) if reference is not None else set()
    current_category = (reference.reference.neetcode_category if reference is not None else None) or (current or {}).get("category")

    candidates = [attempt for attempt in _attempts(db) if attempt.slug != slug]
    if not candidates:
        return []
    references = _reference_rows(db, sorted({attempt.slug for attempt in candidates}))
    ideas = _ideas(db, current_session_id)

    links = []
    for attempt in candidates:
        content = references.get(attempt.slug) or {}
        patterns = set(content.get("patterns") or [])
        category = content.get("neetcode_category") or (catalog.by_slug(attempt.slug) or {}).get("category")
        shared_patterns = tuple(sorted(current_patterns & patterns))
        shared_category = bool(current_category and category == current_category)
        if not shared_patterns and not shared_category:
            continue
        summary = content.get("summary")
        if summary is not None and not str(summary).strip():
            summary = None
        links.append(PastLink(
            slug=attempt.slug, title=attempt.title, summary=summary, outcome=attempt.outcome(),
            approach=None, idea=ideas.get(attempt.slug), shared_patterns=shared_patterns,
            shared_category=shared_category, good=attempt.good, order=attempt.last_id))

    links.sort(key=lambda link: (not link.shared_patterns, not link.shared_category, not link.good, -link.order))
    ordinary = [link for link in links if not link.full_solution]
    chosen = ordinary or links
    for link in chosen:
        if link.summary is None:
            logger.warning("Past link for %s has no summary; showing its title only", link.slug)
    return chosen


def asks_about_similar(text: str | None) -> bool:
    """Whether the learner asked what they have seen before; only then may a reply cite several links."""
    if not text:
        return False
    return bool(re.search(r"\b(similar|something like|anything like|seen (?:this|something|it)|"
                          r"(?:what|anything) (?:did we|have we|we) (?:do|done|did) before|last time|"
                          r"reminds? me)\b", text, re.IGNORECASE))
