"""Session state, active-time accounting, and append-only session events."""

import difflib
import json
import logging
from datetime import datetime
from companion.catalog import load_catalog
from companion.grading import write_missed
from companion.reference import load_reference
from companion.store import open_store
from companion.tutor import Turn, TutorRequest, answered_presses, build_messages, reply_schema


TIME_CAP_MINUTES = {"Easy": 20, "Medium": 35, "Hard": 50}
EXTENSION_MINUTES = 10
TIMELINE_LIMIT = 10

READER_TYPES = frozenset({"problem_opened", "problem_statement", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"})
# Tutor turn events. A turn's id is the id of its request event.
#   chat_message {slug, text}           help_request {slug, press_index}       debrief_request {slug}
#   help_turn {turn_id, slug, request: "help" | "chat" | "debrief", press_index (None for chat and debrief),
#              gap, gap_note, help_kind, reply_text, follow_up_question, model}
#   help_failed {turn_id, slug, reason, message}   reason: model_unavailable, rate_limited, timeout,
#              invalid_output, error, context (the prompt couldn't be built), restart, shutdown
TURN_REQUESTS = frozenset({"chat_message", "help_request", "debrief_request"})
TURN_EVENTS = TURN_REQUESTS | {"help_turn", "help_failed"}
FOLLOWUP_EVENTS = frozenset({"followup_asked", "complexity_postponed", "followup_answered", "reasoning_requested", "followup_skipped", "analysis_resolved"})
EVENT_TYPES = READER_TYPES | TURN_EVENTS | FOLLOWUP_EVENTS | {"session_started", "session_paused", "session_resumed", "session_ended", "problem_left", "accept_counted", "time_up_shown", "time_up_choice", "session_restored"}
CODE_EVENTS = frozenset({"code_snapshot", "code_changed"})
RESTART_MESSAGE = "The companion restarted before the Tutor finished."
logger = logging.getLogger(__name__)


class SessionCoordinator:
    def __init__(self, db_path, clock):
        self.db_path = db_path
        self.clock = clock
        self.state = "idle"
        self.session_id = None
        self.elapsed = 0.0
        self.active_since = None
        self.paused_reason = None
        self.check_message = None
        self.problem = None
        self.accepted = False
        self.pending_accept = False
        self.attempt_start = None  # id of the event that made the current problem current
        self.problem_times = {}  # active seconds per problem in this session, up to the current segment
        self.problem_since = None  # when the current problem's active segment began
        self.time_up = None  # {"can_extend": bool} while the current problem's time-up card shows
        self.time_up_shows = {}  # problem slug -> how many times the card has shown this session
        self.extensions = set()  # problem slugs that have taken their one extension
        self.debrief = False  # the current problem is in the Tutor's debrief mode
        self.turn = None  # id of the Tutor turn in flight; one at a time
        with open_store(db_path) as db:
            rows = db.execute("SELECT id, started_at FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC, id DESC").fetchall()
            if rows:
                ids = [row[0] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                event_rows = db.execute(
                    f"SELECT id, session_id, ts, type, payload FROM events WHERE session_id IN ({placeholders}) ORDER BY id", ids
                ).fetchall()
                history = {session_id: [] for session_id in ids}
                for event_id, session_id, ts, kind, payload in event_rows:
                    history[session_id].append((event_id, ts, kind, payload))
                for session_id in ids:
                    self._close_open_turns(db, session_id, history[session_id], clock().isoformat())
                for session_id, started_at in rows[1:]:
                    self._interrupt(db, session_id, self._last_event_time(history[session_id], started_at))
                session_id, started_at = rows[0]
                try:
                    restored = self._replay(started_at, history[session_id])
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    logger.error("Cannot restore session %s: %s", session_id, exc, exc_info=True)
                    self._interrupt(db, session_id, self._last_event_time(history[session_id], started_at))
                else:
                    self.session_id = session_id
                    for name, value in restored.items():
                        setattr(self, name, value)
                    self.state = "paused"
                    self.paused_reason = "restart"
                    self._event(db, session_id, clock().isoformat(), "session_restored", {"active_seconds": int(self.elapsed)})

    def _close_open_turns(self, db, session_id, history, now):
        """A Tutor turn still open when the companion died is logged as failed, never left open."""
        open_turns = {}
        for event_id, _, kind, raw in history:
            if kind not in TURN_EVENTS:
                continue
            try:
                payload = json.loads(raw)
            except ValueError:
                logger.error("Unreadable %s event %s", kind, event_id)
                continue
            if kind in TURN_REQUESTS:
                open_turns[event_id] = payload.get("slug") if isinstance(payload, dict) else None
            elif isinstance(payload, dict):
                open_turns.pop(payload.get("turn_id"), None)
        for turn_id, slug in open_turns.items():
            self._event(db, session_id, now, "help_failed",
                        {"turn_id": turn_id, "slug": slug, "reason": "restart", "message": RESTART_MESSAGE})

    def _interrupt(self, db, session_id, ended_at):
        db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (ended_at, session_id))
        self._event(db, session_id, ended_at, "session_interrupted", {})

    @staticmethod
    def _last_event_time(history, started_at):
        if history:
            try:
                last = datetime.fromisoformat(history[-1][1])
                if last.tzinfo is not None:
                    return history[-1][1]
            except (TypeError, ValueError):
                pass
        return started_at

    @staticmethod
    def _replay(started_at, history):
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            raise ValueError("Session start has no timezone")
        elapsed = 0.0
        started_seen = False
        active_since = None
        problem = attempt_start = None
        accepted = pending_accept = False
        problem_times = {}
        time_up = None
        time_up_shows = {}
        extensions = set()
        debrief = False
        if not history or history[0][2] != "session_started":
            raise ValueError("Missing session_started event")
        previous = started
        for event_id, ts, kind, raw_payload in history:
            at = datetime.fromisoformat(ts)
            if at.tzinfo is None or at < previous:
                raise ValueError("Invalid event timestamp")
            payload = json.loads(raw_payload)
            if kind not in EVENT_TYPES or not isinstance(payload, dict):
                raise ValueError(f"Unexpected {kind} event payload")
            if kind != "session_restored" and active_since is not None and problem is not None:
                problem_times[problem["slug"]] = problem_times.get(problem["slug"], 0.0) + (at - previous).total_seconds()
            previous = at
            if kind == "session_started":
                if started_seen or at != started:
                    raise ValueError("Invalid session_started event")
                started_seen = True
                active_since = at
            elif kind == "session_paused":
                if payload.get("reason") not in ("button", "focus", "check", "disconnect", "closed") or active_since is None:
                    raise ValueError("Invalid session_paused event")
                elapsed += (at - active_since).total_seconds()
                active_since = None
            elif kind == "session_resumed":
                if payload.get("reason") not in ("button", "focus", "check", "disconnect", "closed", "restart") or active_since is not None:
                    raise ValueError("Invalid session_resumed event")
                active_since = at
                if pending_accept and problem and not accepted:
                    accepted = True
                pending_accept = False
            elif kind == "session_restored":
                seconds = payload.get("active_seconds")
                if type(seconds) is not int or seconds < 0:
                    raise ValueError("Invalid session_restored event")
                elapsed = seconds
                active_since = None
            elif kind == "problem_opened":
                if (payload.get("site") != "neetcode" or not isinstance(payload.get("slug"), str)
                        or not payload["slug"] or not isinstance(payload.get("title"), str) or not payload["title"]):
                    raise ValueError("Invalid problem_opened event")
                if problem is None or problem["slug"] != payload["slug"]:
                    accepted = pending_accept = False
                    attempt_start = event_id
                    time_up = None
                    debrief = False
                problem = {"slug": payload["slug"], "title": payload["title"]}
                problem_times.setdefault(problem["slug"], 0.0)
            elif kind == "problem_left":
                if not isinstance(payload.get("slug"), str) or not payload["slug"] or type(payload.get("accepted")) is not bool:
                    raise ValueError("Invalid problem_left event")
                if problem and problem["slug"] == payload["slug"]:
                    problem = None
                    accepted = pending_accept = False
                    time_up = None
                    debrief = False
            elif kind == "accept_counted":
                if not isinstance(payload.get("slug"), str) or not payload["slug"]:
                    raise ValueError("Invalid accept_counted event")
                if problem and problem["slug"] == payload["slug"]:
                    accepted = True
                    pending_accept = False
                    time_up = None
            elif kind == "submit_result":
                if type(payload.get("accepted")) is not bool or not isinstance(payload.get("slug"), str) or not payload["slug"]:
                    raise ValueError("Invalid submit_result event")
                if active_since is None and payload["accepted"] and problem and payload["slug"] == problem["slug"]:
                    pending_accept = True
            elif kind == "time_up_shown":
                if (not isinstance(payload.get("slug"), str) or not payload["slug"]
                        or type(payload.get("can_extend")) is not bool):
                    raise ValueError("Invalid time_up_shown event")
                if problem and payload["slug"] == problem["slug"] and not accepted and not debrief:
                    time_up = {"can_extend": payload["can_extend"]}
                    time_up_shows[payload["slug"]] = time_up_shows.get(payload["slug"], 0) + 1
            elif kind == "time_up_choice":
                if (not isinstance(payload.get("slug"), str) or not payload["slug"]
                        or payload.get("choice") not in ("extend", "debrief")):
                    raise ValueError("Invalid time_up_choice event")
                if problem and payload["slug"] == problem["slug"]:
                    time_up = None
                    if payload["choice"] == "extend":
                        extensions.add(payload["slug"])
                    else:
                        debrief = True
            elif kind == "focus_changed":
                if type(payload.get("tab_active")) is not bool or type(payload.get("window_focused")) is not bool:
                    raise ValueError("Invalid focus_changed event")
            elif kind == "reader_check":
                if type(payload.get("ok")) is not bool or ("message" in payload and not isinstance(payload["message"], str)):
                    raise ValueError("Invalid reader_check event")
            elif kind == "problem_statement":
                if not isinstance(payload.get("slug"), str) or not payload["slug"] or not isinstance(payload.get("statement"), str):
                    raise ValueError("Invalid problem_statement event")
            elif kind == "code_snapshot":
                if not isinstance(payload.get("code"), str):
                    raise ValueError("Invalid code_snapshot event")
            elif kind == "code_changed":
                diff = payload.get("diff")
                if (not isinstance(diff, dict) or type(diff.get("start_line")) is not int or diff["start_line"] <= 0
                        or not isinstance(diff.get("removed"), list) or not all(isinstance(v, str) for v in diff["removed"])
                        or not isinstance(diff.get("added"), list) or not all(isinstance(v, str) for v in diff["added"])):
                    raise ValueError("Invalid code_changed event")
            elif kind == "run_result":
                if (not isinstance(payload.get("slug"), str) or not payload["slug"]
                        or not isinstance(payload.get("cases"), list) or not payload["cases"]):
                    raise ValueError("Invalid run_result event")
            elif kind in FOLLOWUP_EVENTS:
                if not isinstance(payload.get("slug"), str) or not payload["slug"]:
                    raise ValueError(f"Invalid {kind} event")
                if kind == "followup_asked" and (not isinstance(payload.get("question"), str)
                        or type(payload.get("complexity")) is not bool):
                    raise ValueError("Invalid followup_asked event")
                if kind == "complexity_postponed" and not isinstance(payload.get("reason"), str):
                    raise ValueError("Invalid complexity_postponed event")
                if kind in {"followup_answered", "reasoning_requested", "followup_skipped", "analysis_resolved"} and type(payload.get("question_id")) is not int:
                    raise ValueError(f"Invalid {kind} event")
                if kind == "analysis_resolved" and payload.get("reason") not in {"no_reasoning", "complexity_stated"}:
                    raise ValueError("Invalid analysis_resolved event")
                if kind == "followup_answered" and (not isinstance(payload.get("text"), str)
                        or type(payload.get("has_reasoning")) is not bool):
                    raise ValueError("Invalid followup_answered event")
            elif kind == "session_ended":
                raise ValueError("Open session contains session_ended")
        if active_since is not None:
            elapsed += (previous - active_since).total_seconds()
        return {"elapsed": elapsed, "problem": problem, "accepted": accepted, "pending_accept": pending_accept,
                "attempt_start": attempt_start, "problem_times": problem_times, "problem_since": None,
                "time_up": time_up, "time_up_shows": time_up_shows, "extensions": extensions, "debrief": debrief}

    def _event(self, db, session_id, ts, kind, payload):
        return db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                          (session_id, ts, kind, json.dumps(payload))).lastrowid

    def _seconds(self, now):
        running = (now - self.active_since).total_seconds() if self.active_since is not None else 0
        return int(self.elapsed + running)

    def _problem_seconds(self, now):
        """Active seconds on the current problem in this session, including its current active segment."""
        if self.problem is None:
            return None
        seconds = self.problem_times.get(self.problem["slug"], 0.0)
        if self.state == "active" and self.problem_since is not None:
            seconds += (now - self.problem_since).total_seconds()
        return seconds

    def _close_problem(self, now):
        if self.problem is not None and self.problem_since is not None:
            slug = self.problem["slug"]
            self.problem_times[slug] = self.problem_times.get(slug, 0.0) + (now - self.problem_since).total_seconds()
        self.problem_since = None

    def _cap_minutes(self):
        if self.problem is None:
            return None
        problem = load_catalog().by_slug(self.problem["slug"])
        return TIME_CAP_MINUTES.get(problem["difficulty"]) if problem else None

    def _cap_seconds(self):
        cap = self._cap_minutes()
        if cap is None:
            return None
        return cap * 60 + (EXTENSION_MINUTES * 60 if self.problem["slug"] in self.extensions else 0)

    def _mode(self):
        if self.state == "active":
            return "debrief" if self.debrief else "watching"
        return "paused" if self.state == "paused" else "idle"

    def _state(self, now):
        reference_status = None
        if self.problem:
            with open_store(self.db_path) as db:
                row = db.execute('SELECT status FROM "references" WHERE problem_key = ?',
                                 (self.problem["slug"],)).fetchone()
            reference_status = row[0] if row and row[0] in {"checked", "resolved_by_you"} else "not_checked_yet"
        state = {"type": "session_state", "ts": now.isoformat(), "state": self.state,
                "paused_reason": self.paused_reason, "check_message": self.check_message,
                "active_seconds": self._seconds(now), "running": self.state == "active",
                "problem": self.problem, "accepted": self.accepted, "mode": self._mode(),
                "cap_minutes": None, "cap_seconds_left": None, "time_up": None}
        if self.problem:
            cap = self._cap_minutes()
            left = self._cap_seconds()
            state["cap_minutes"] = cap
            state["cap_seconds_left"] = None if left is None else max(0, int(left - (self._problem_seconds(now) or 0)))
            state["time_up"] = self.time_up
            state["reference_status"] = reference_status
            if reference_status == "not_checked_yet":
                state["reference_message"] = "Reference not checked yet"
        return state

    def _pause(self, db, now, reason, message=None):
        self._close_problem(now)
        self.elapsed += (now - self.active_since).total_seconds()
        self.active_since = None
        self.state = "paused"
        self.paused_reason = reason
        self.check_message = message if reason == "check" else None
        self._event(db, self.session_id, now.isoformat(), "session_paused", {"reason": reason})

    def _resume(self, db, now):
        reason = self.paused_reason
        self.state = "active"
        self.active_since = now
        if self.problem is not None:
            self.problem_since = now
        self.paused_reason = None
        self.check_message = None
        self._event(db, self.session_id, now.isoformat(), "session_resumed", {"reason": reason})
        if self.pending_accept and self.problem and not self.accepted:
            self.accepted = True
            self.time_up = None
            self._event(db, self.session_id, now.isoformat(), "accept_counted", {"slug": self.problem["slug"]})
        self.pending_accept = False

    def _end(self, db, now):
        self._close_problem(now)
        if self.state == "active":
            self.elapsed += (now - self.active_since).total_seconds()
        self.active_since = None
        if self.problem:
            self._event(db, self.session_id, now.isoformat(), "problem_left", {"slug": self.problem["slug"], "accepted": self.accepted})
        seconds = self._seconds(now)
        self._event(db, self.session_id, now.isoformat(), "session_ended", {"active_seconds": seconds})
        db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now.isoformat(), self.session_id))
        self.state = "ended"
        self.paused_reason = None
        self.check_message = None
        self.pending_accept = False
        self.time_up = None
        self.debrief = False

    def handle(self, message: dict) -> list[dict]:
        now = self.clock()
        kind = message.get("type")
        payload = message.get("payload", {})
        if kind == "session_get_state":
            return [self._state(now)]
        if kind == "session_start" and (self.state not in ("idle", "ended") or not isinstance(payload, dict) or payload):
            return [{"type": "error", "message": "Invalid session_start"}]
        if kind == "session_pause" and self.state != "active":
            return [{"type": "error", "message": "session_pause requires an active session"}]
        if kind == "session_resume" and self.state != "paused":
            return [{"type": "error", "message": "session_resume requires a paused session"}]
        if kind == "session_end" and self.state not in ("active", "paused"):
            return [{"type": "error", "message": "session_end requires a session"}]
        if kind == "session_time_up" and (self.state != "active" or self.problem is None or self.time_up is None
                or not isinstance(payload, dict) or payload.get("choice") not in ("extend", "debrief")
                or (payload.get("choice") == "extend" and (not self.time_up["can_extend"]
                    or self.problem["slug"] in self.extensions))):
            return [{"type": "error", "message": "Invalid session_time_up"}]
        if kind == "session_time_up" and payload["choice"] == "debrief" and self.turn is not None:
            return [{"type": "error", "message": "The Tutor is still answering. Wait for that reply, then choose the debrief."}]
        if kind not in READER_TYPES | {"session_start", "session_pause", "session_resume", "session_end", "session_time_up", "session_done", "session_skip", "tab_closed"}:
            return [{"type": "error", "message": f"Unknown message type: {kind}"}]
        if kind in ("session_done", "session_skip") and (self.state != "active" or self.problem is None or
                (kind == "session_done" and not (self.accepted or self.debrief))):
            return [{"type": "error", "message": f"{kind} requires an appropriate active problem"}]
        if kind == "tab_closed" and self.state != "active":
            return [self._state(now)]
        if kind in READER_TYPES and (self.state in ("idle", "ended") or
            (self.state == "paused" and kind not in ("focus_changed", "reader_check", "submit_result")
             and not (kind == "problem_opened" and self.paused_reason == "check"))):
            return [self._state(now)]
        with open_store(self.db_path) as db:
            if (kind == "problem_opened" and self.state == "paused" and self.paused_reason == "check"):
                catalog_problem = load_catalog().by_slug(payload["slug"])
                if catalog_problem is None or catalog_problem["locked"]:
                    return [self._state(now)]
                self._resume(db, now)
            if kind == "session_start":
                self._reset(now)
                row = db.execute("INSERT INTO sessions (started_at) VALUES (?)", (now.isoformat(),))
                self.session_id = row.lastrowid
                self._event(db, self.session_id, now.isoformat(), "session_started", {})
            elif kind == "session_pause":
                self._pause(db, now, "button")
            elif kind == "tab_closed":
                self._pause(db, now, "closed")
            elif kind == "session_resume":
                self._resume(db, now)
            elif kind == "session_end":
                self._end(db, now)
            elif kind in ("session_done", "session_skip"):
                self._close_problem(now)
                self._event(db, self.session_id, now.isoformat(), "problem_left", {"slug": self.problem["slug"], "accepted": self.accepted})
                self.problem = None
                self.accepted = False
                self.pending_accept = False
                self.attempt_start = None
                self.time_up = None
                self.debrief = False
            elif kind == "session_time_up":
                slug = self.problem["slug"]
                choice_id = self._event(db, self.session_id, now.isoformat(), "time_up_choice", {"slug": slug, "choice": payload["choice"]})
                if payload["choice"] == "extend":
                    self.extensions.add(slug)
                else:
                    write_missed(db, session_id=self.session_id, slug=slug, event_id=choice_id, attempt_start=self.attempt_start)
                    self.debrief = True
                self.time_up = None
            else:
                event_id = self._event(db, self.session_id, now.isoformat(), kind, payload)
                if kind == "focus_changed":
                    if self.state == "active" and (not payload["tab_active"] or not payload["window_focused"]):
                        self._pause(db, now, "focus")
                    elif self.state == "paused" and self.paused_reason == "focus" and payload["tab_active"] and payload["window_focused"]:
                        self._resume(db, now)
                elif kind == "reader_check":
                    if self.state == "active" and payload["ok"] is False:
                        self._pause(db, now, "check", payload.get("message"))
                    elif (self.state == "paused" and self.paused_reason == "check" and payload["ok"] is True
                          and not (self.problem and (catalog := load_catalog().by_slug(self.problem["slug"]))
                                   and catalog["locked"])):
                        self._resume(db, now)
                elif kind == "problem_opened":
                    if self.problem and self.problem["slug"] != payload["slug"]:
                        self._close_problem(now)
                        self._event(db, self.session_id, now.isoformat(), "problem_left", {"slug": self.problem["slug"], "accepted": self.accepted})
                        self.accepted = False
                        self.pending_accept = False
                        self.time_up = None
                        self.debrief = False
                    if self.problem is None or self.problem["slug"] != payload["slug"]:
                        self.attempt_start = event_id
                        self.problem_since = now if self.state == "active" else None
                    self.problem = {"slug": payload["slug"], "title": payload["title"]}
                    self.problem_times.setdefault(payload["slug"], 0.0)
                    catalog_problem = load_catalog().by_slug(payload["slug"])
                    if catalog_problem and catalog_problem["locked"] and self.state == "active":
                        self._pause(db, now, "check", "This problem is locked on NeetCode.")
                elif kind == "submit_result" and payload["accepted"] and self.problem and payload["slug"] == self.problem["slug"]:
                    if self.state == "paused":
                        self.pending_accept = True
                    elif not self.accepted:
                        self.accepted = True
                        self.time_up = None
                        self._event(db, self.session_id, now.isoformat(), "accept_counted", {"slug": payload["slug"]})
        return [self._state(now)]

    def _reset(self, now):
        self.state = "active"
        self.elapsed = 0.0
        self.active_since = now
        self.paused_reason = None
        self.check_message = None
        self.problem = None
        self.accepted = False
        self.pending_accept = False
        self.attempt_start = None
        self.problem_times = {}
        self.problem_since = None
        self.time_up = None
        self.time_up_shows = {}
        self.extensions = set()
        self.debrief = False

    def begin_turn(self, kind, text=None) -> tuple[list[dict], Turn | None]:
        """Start a Tutor turn for a press of "I need help" (`help`), a chat message (`chat`, with its text), or
        the debrief (`debrief`, entered from the time-up card).

        Accepted only while the session is active, on a problem, with no other turn in flight; the debrief also
        requires debrief mode. A refusal is a `tutor_error` with no turn id and logs nothing. An accepted request
        is logged first, then its prompt is built from this attempt's events; if that fails, the turn is logged
        failed and nothing is sent. Every Tutor turn in the debrief is fed the code-built timeline.
        """
        now = self.clock()
        if self.state == "paused":
            return [self._tutor_error(now, None, "The session is paused. Resume it to get help.")], None
        if self.state != "active":
            return [self._tutor_error(now, None, "Help is available only during a session.")], None
        if self.problem is None:
            return [self._tutor_error(now, None, "Open a problem first, then ask.")], None
        if kind not in ("help", "chat", "debrief"):
            return [self._tutor_error(now, None, f"Unknown Tutor request: {kind}")], None
        if kind == "debrief" and not self.debrief:
            return [self._tutor_error(now, None, "The debrief begins from the time-up card.")], None
        if self.turn is not None:
            return [self._tutor_error(now, None, "The Tutor is still answering. Wait for that reply, then ask again.")], None
        slug = self.problem["slug"]
        with open_store(self.db_path) as db:
            events = [(kind_, payload) for _, kind_, payload in self._problem_events(db)]
            if kind == "help":
                request = TutorRequest("help", answered_presses(events) + 1, None)
                turn_id = self._event(db, self.session_id, now.isoformat(), "help_request", {"slug": slug, "press_index": request.press_index})
            elif kind == "debrief":
                request = TutorRequest("debrief", None, None)
                turn_id = self._event(db, self.session_id, now.isoformat(), "debrief_request", {"slug": slug})
            else:
                request = TutorRequest("chat", None, text)
                turn_id = self._event(db, self.session_id, now.isoformat(), "chat_message", {"slug": slug, "text": text})
            try:
                prior_solve = db.execute("SELECT 1 FROM events WHERE type = 'prior_solve' AND json_extract(payload, '$.slug') = ?",
                                         (slug,)).fetchone() is not None
                problem_seconds = self._problem_seconds(now) or 0.0
                cap_seconds = self._cap_seconds()
                seconds_left = max(0, cap_seconds - int(problem_seconds)) if cap_seconds is not None else 0
                timeline = self._timeline(db) if self.debrief else None
                messages = build_messages(self.problem, events, reference=load_reference(db, slug), prior_solve=prior_solve,
                                          problem_seconds=int(problem_seconds), seconds_left=seconds_left,
                                          request=request, timeline=timeline)
            except Exception as exc:
                logger.exception("Cannot build the Tutor's context for %s", slug)
                message = f"Couldn't prepare the Tutor's context: {exc}"
                self._event(db, self.session_id, now.isoformat(), "help_failed",
                            {"turn_id": turn_id, "slug": slug, "reason": "context", "message": message})
                return [self._tutor_error(now, turn_id, message)], None
        self.turn = turn_id
        turn = Turn(turn_id, self.session_id, slug, request, messages, reply_schema(request))
        return [{"type": "tutor_start", "ts": now.isoformat(), "turn_id": turn_id, "request": kind,
                 "press_index": request.press_index}], turn

    def _problem_events(self, db):
        """(id, type, payload) of this session's events about the current problem, oldest first: every event
        naming its slug (help, chat, statements, results), so help continues when you come back to it, plus the
        code events since it was last opened (they name no slug)."""
        rows = db.execute("SELECT id, type, payload FROM events WHERE session_id = ? ORDER BY id", (self.session_id,)).fetchall()
        events = []
        for event_id, kind, raw in rows:
            payload = json.loads(raw)
            if (payload.get("slug") == self.problem["slug"] if kind not in CODE_EVENTS else event_id >= self.attempt_start):
                events.append((event_id, kind, payload))
        return events

    @staticmethod
    def _apply_code_diff(code, diff):
        if code is None:
            return None
        lines = code.split("\n")
        start = diff["start_line"] - 1
        end = start + len(diff["removed"])
        if start > len(lines) or lines[start:end] != diff["removed"]:
            return code
        return "\n".join(lines[:start] + diff["added"] + lines[end:])

    @staticmethod
    def _short_diff(old, new):
        changed = [line for line in difflib.ndiff(old.splitlines(), new.splitlines())
                   if line.startswith(("+ ", "- "))]
        return "\n".join(changed[:40])

    def _timeline(self, db) -> list[dict]:
        """The current problem's compact, code-built timeline for the debrief, oldest first, at most 10 entries."""
        if self.problem is None:
            return []
        slug = self.problem["slug"]
        rows = db.execute("SELECT ts, type, payload FROM events WHERE session_id = ? ORDER BY id",
                          (self.session_id,)).fetchall()
        active = False
        current = None
        previous = None
        target_started = False
        times = {}
        code = None
        code_at = 0
        first_code = None
        previous_attempt_code = None
        last_result = None
        last_result_code = None
        entries = []
        for ts, kind, raw in rows:
            at = datetime.fromisoformat(ts)
            payload = json.loads(raw)
            if previous is None:
                previous = at
            if kind != "session_restored" and active and current is not None:
                times[current] = times.get(current, 0.0) + (at - previous).total_seconds()
            previous = at
            if kind == "session_started":
                active = True
                continue
            if kind == "session_restored":
                active = False
                continue
            if kind == "session_paused":
                active = False
                continue
            if kind == "session_resumed":
                active = True
                continue
            if kind == "session_ended":
                active = False
                continue
            if kind == "problem_opened":
                current = payload["slug"]
                times.setdefault(current, 0.0)
                target_started = target_started or current == slug
                continue
            if kind == "problem_left":
                if payload.get("slug") == current:
                    current = None
                continue
            if not target_started or current != slug:
                continue
            if kind == "code_snapshot":
                code = payload["code"]
                code_at = int(times[slug])
                continue
            if kind == "code_changed":
                code = self._apply_code_diff(code, payload["diff"])
                code_at = int(times[slug])
                continue
            if kind in ("run_result", "submit_result"):
                if payload.get("slug") != slug:
                    continue
                if kind == "run_result":
                    passed = sum(1 for case in payload["cases"] if case["passed"])
                    total = len(payload["cases"])
                else:
                    passed, total = payload["passed"], payload["total"]
                entry = {"kind": "run" if kind == "run_result" else "submit",
                         "at": int(times.get(slug, 0.0)), "passed": passed, "total": total}
                if first_code is None and code is not None:
                    entry["code"] = code
                    first_code = previous_attempt_code = code
                else:
                    if previous_attempt_code is not None and code is not None and code != previous_attempt_code:
                        entry["diff"] = self._short_diff(previous_attempt_code, code)
                    if code is not None:
                        previous_attempt_code = code
                entries.append(entry)
                last_result, last_result_code = entry, code
            elif kind == "help_request" and payload.get("slug") == slug:
                entries.append({"kind": "help", "at": int(times.get(slug, 0.0)), "text": "pressed I need help"})
            elif kind == "chat_message" and payload.get("slug") == slug:
                entries.append({"kind": "chat", "at": int(times.get(slug, 0.0)), "text": payload["text"]})
        if code is not None:
            if last_result is not None and last_result_code == code:
                last_result["code"] = code
            elif first_code != code:
                entries.append({"kind": "final_code", "at": code_at, "code": code})
        if len(entries) > TIMELINE_LIMIT:
            critical = [entry for entry in entries if entry["kind"] in ("run", "submit", "final_code")]
            rest = [entry for entry in entries if entry["kind"] not in ("run", "submit", "final_code")]
            if rest:
                merged = {"kind": "merged", "at": min(entry["at"] for entry in rest),
                          "gist": [f"{entry['at']}s: {entry.get('text', entry['kind'])}" for entry in rest]}
                critical.append(merged)
            entries = critical
        return sorted(entries, key=lambda entry: entry["at"])

    def debrief_timeline(self) -> list[dict]:
        with open_store(self.db_path) as db:
            return self._timeline(db)

    def chat_state(self) -> dict:
        """The conversation on the current problem in this session, rebuilt from stored events: the same events
        the Tutor's context uses. Empty outside an active or paused session on a problem. `streaming` is the turn in flight."""
        now = self.clock()
        turns = []
        if self.state in ("active", "paused") and self.problem is not None:
            with open_store(self.db_path) as db:
                events = self._problem_events(db)
            for event_id, kind, payload in events:
                if kind == "help_request":
                    turns.append({"turn_id": event_id, "kind": "press", "press_index": payload["press_index"]})
                elif kind == "debrief_request":
                    turns.append({"turn_id": event_id, "kind": "debrief"})
                elif kind == "chat_message":
                    turns.append({"turn_id": event_id, "kind": "chat", "text": payload["text"]})
                elif kind == "help_turn":
                    turns.append({"turn_id": payload["turn_id"], "kind": "reply", "text": payload["reply_text"],
                                  "gap": payload["gap"], "gap_note": payload["gap_note"], "help_kind": payload["help_kind"]})
                elif kind == "help_failed":
                    turns.append({"turn_id": payload["turn_id"], "kind": "failed", "reason": payload["reason"],
                                  "message": payload["message"]})
        return {"type": "chat_state", "ts": now.isoformat(), "turns": turns, "streaming": self.turn}

    def finish_turn(self, turn: Turn, reply, model: str) -> list[dict]:
        """Log a validated reply and send it; it is stored whatever the session's state is now."""
        now = self.clock()
        labels = {"gap": reply.gap, "gap_note": reply.gap_note, "help_kind": reply.help_kind}
        try:
            with open_store(self.db_path) as db:
                help_event_id = self._event(db, turn.session_id, now.isoformat(), "help_turn", {
                    "turn_id": turn.turn_id, "slug": turn.slug, "request": turn.request.kind,
                    "press_index": turn.request.press_index, **labels, "reply_text": reply.reply_text,
                    "follow_up_question": reply.follow_up_question, "model": model})
                if reply.help_kind == "full_solution":
                    events = db.execute("SELECT id, type, payload FROM events WHERE session_id = ? AND id <= ? ORDER BY id",
                                        (turn.session_id, turn.turn_id)).fetchall()
                    attempt_start = None
                    for event_id, kind, raw in events:
                        payload = json.loads(raw)
                        if kind == "problem_opened" and payload["slug"] == turn.slug and attempt_start is None:
                            attempt_start = event_id
                        elif kind == "problem_left" and payload["slug"] == turn.slug:
                            attempt_start = None
                    accepted_before_reply = attempt_start is not None and any(
                        kind == "submit_result" and json.loads(raw).get("accepted") and json.loads(raw).get("slug") == turn.slug
                        for event_id, kind, raw in db.execute("SELECT id, type, payload FROM events WHERE session_id = ? AND id > ? AND id < ?",
                                                             (turn.session_id, attempt_start, help_event_id)))
            if reply.help_kind == "full_solution" and attempt_start is not None and not accepted_before_reply:
                try:
                    with open_store(self.db_path) as db:
                        write_missed(db, session_id=turn.session_id, slug=turn.slug, event_id=help_event_id,
                                     attempt_start=attempt_start)
                except Exception as error:
                    logger.exception("Could not record full-solution miss for turn %s", turn.turn_id)
                    return [{"type": "tutor_done", "ts": now.isoformat(), "turn_id": turn.turn_id,
                             "reply_text": reply.reply_text, **labels, "follow_up_question": reply.follow_up_question},
                            {"type": "error", "ts": now.isoformat(), "message": f"Could not record the missed attempt: {error}"}]
        finally:
            self._release(turn)
        return [{"type": "tutor_done", "ts": now.isoformat(), "turn_id": turn.turn_id, "reply_text": reply.reply_text,
                 **labels, "follow_up_question": reply.follow_up_question}]

    def fail_turn(self, turn: Turn, reason: str, message: str) -> list[dict]:
        """Log a turn that ended without a valid reply and tell the panel; nothing if it already ended."""
        if self.turn != turn.turn_id:
            return []
        now = self.clock()
        try:
            with open_store(self.db_path) as db:
                self._event(db, turn.session_id, now.isoformat(), "help_failed",
                            {"turn_id": turn.turn_id, "slug": turn.slug, "reason": reason, "message": message})
        finally:
            self._release(turn)
        return [self._tutor_error(now, turn.turn_id, message)]

    def _release(self, turn):
        if self.turn == turn.turn_id:
            self.turn = None

    @staticmethod
    def _tutor_error(now, turn_id, message):
        return {"type": "tutor_error", "ts": now.isoformat(), "turn_id": turn_id, "message": message}

    def tick(self) -> list[dict]:
        now = self.clock()
        if self.state != "active" or self.problem is None or self.accepted or self.debrief or self.time_up is not None:
            return []
        slug = self.problem["slug"]
        cap = self._cap_seconds()
        if cap is None or (self._problem_seconds(now) or 0) < cap:
            return []
        shows = self.time_up_shows.get(slug, 0)
        extended = slug in self.extensions
        if (not extended and shows > 0) or (extended and shows > 1):
            return []
        with open_store(self.db_path) as db:
            self.time_up = {"can_extend": not extended}
            self.time_up_shows[slug] = shows + 1
            self._event(db, self.session_id, now.isoformat(), "time_up_shown",
                        {"slug": slug, "can_extend": self.time_up["can_extend"]})
        return [self._state(now)]

    def on_connect(self) -> list[dict]:
        return [self._state(self.clock())]

    def on_disconnect(self) -> list[dict]:
        if self.state != "active":
            return []
        now = self.clock()
        with open_store(self.db_path) as db:
            self._pause(db, now, "disconnect")
        return [self._state(now)]
