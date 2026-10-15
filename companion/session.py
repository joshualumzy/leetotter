"""Session state, active-time accounting, and append-only session events."""

import json
import logging
from datetime import datetime
from companion.reference import load_reference
from companion.store import open_store
from companion.tutor import Turn, TutorRequest, answered_presses, build_messages, reply_schema


READER_TYPES = frozenset({"problem_opened", "problem_statement", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"})
# Tutor turn events. A turn's id is the id of its request event.
#   chat_message {slug, text}           help_request {slug, press_index}
#   help_turn {turn_id, slug, request: "help" | "chat", press_index (None for chat), gap, gap_note, help_kind,
#              reply_text, follow_up_question, model}
#   help_failed {turn_id, slug, reason, message}   reason: model_unavailable, rate_limited, timeout,
#              invalid_output, error, context (the prompt couldn't be built), restart, shutdown
TURN_REQUESTS = frozenset({"chat_message", "help_request"})
TURN_EVENTS = TURN_REQUESTS | {"help_turn", "help_failed"}
EVENT_TYPES = READER_TYPES | TURN_EVENTS | {"session_started", "session_paused", "session_resumed", "session_ended", "problem_left", "accept_counted", "overrun_prompted", "overrun_choice", "session_restored"}
RESTART_MESSAGE = "The companion restarted before the Tutor finished."
logger = logging.getLogger(__name__)


class SessionCoordinator:
    def __init__(self, db_path, clock):
        self.db_path = db_path
        self.clock = clock
        self.state = "idle"
        self.session_id = None
        self.budget_minutes = None
        self.elapsed = 0.0
        self.active_since = None
        self.paused_reason = None
        self.check_message = None
        self.overrun_prompt = False
        self.prompted = False
        self.problem = None
        self.accepted = False
        self.pending_accept = False
        self.attempt_start = None  # id of the event that made the current problem current
        self.problem_started = 0.0  # active seconds when the current problem was opened
        self.turn = None  # id of the Tutor turn in flight; one at a time
        with open_store(db_path) as db:
            rows = db.execute("SELECT id, started_at, budget_minutes FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC, id DESC").fetchall()
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
                for session_id, started_at, budget in rows[1:]:
                    self._interrupt(db, session_id, self._last_event_time(history[session_id], started_at))
                session_id, started_at, budget = rows[0]
                try:
                    restored = self._replay(started_at, budget, history[session_id])
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    logger.error("Cannot restore session %s: %s", session_id, exc, exc_info=True)
                    self._interrupt(db, session_id, self._last_event_time(history[session_id], started_at))
                else:
                    self.session_id = session_id
                    self.budget_minutes = budget
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
    def _replay(started_at, budget, history):
        if type(budget) is not int or budget <= 0:
            raise ValueError("Invalid session budget")
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            raise ValueError("Session start has no timezone")
        elapsed = 0.0
        started_seen = False
        active_since = None
        problem = attempt_start = None
        problem_started = 0.0
        accepted = pending_accept = prompted = overrun_prompt = False
        if not history or history[0][2] != "session_started":
            raise ValueError("Missing session_started event")
        previous = started
        for event_id, ts, kind, raw_payload in history:
            at = datetime.fromisoformat(ts)
            if at.tzinfo is None or at < previous:
                raise ValueError("Invalid event timestamp")
            previous = at
            payload = json.loads(raw_payload)
            if kind not in EVENT_TYPES or not isinstance(payload, dict):
                raise ValueError(f"Unexpected {kind} event payload")
            if kind == "session_started":
                if started_seen or at != started or type(payload.get("budget_minutes")) is not int or payload["budget_minutes"] != budget:
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
                    problem_started = elapsed + ((at - active_since).total_seconds() if active_since else 0)
                problem = {"slug": payload["slug"], "title": payload["title"]}
            elif kind == "problem_left":
                if not isinstance(payload.get("slug"), str) or not payload["slug"] or type(payload.get("accepted")) is not bool:
                    raise ValueError("Invalid problem_left event")
                if problem and problem["slug"] == payload["slug"]:
                    problem = None
                    accepted = pending_accept = False
            elif kind == "accept_counted":
                if not isinstance(payload.get("slug"), str) or not payload["slug"]:
                    raise ValueError("Invalid accept_counted event")
                if problem and problem["slug"] == payload["slug"]:
                    accepted = True
                    pending_accept = False
            elif kind == "submit_result":
                if type(payload.get("accepted")) is not bool or not isinstance(payload.get("slug"), str) or not payload["slug"]:
                    raise ValueError("Invalid submit_result event")
                if active_since is None and payload["accepted"] and problem and payload["slug"] == problem["slug"]:
                    pending_accept = True
            elif kind == "overrun_prompted":
                prompted = overrun_prompt = True
            elif kind == "overrun_choice":
                if payload.get("choice") not in ("finish", "stop"):
                    raise ValueError("Invalid overrun_choice event")
                overrun_prompt = False
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
            elif kind == "session_ended":
                raise ValueError("Open session contains session_ended")
        if active_since is not None:
            elapsed += (previous - active_since).total_seconds()
        return {"elapsed": elapsed, "problem": problem, "accepted": accepted, "pending_accept": pending_accept,
                "overrun_prompt": overrun_prompt, "prompted": prompted, "attempt_start": attempt_start,
                "problem_started": problem_started}

    def _event(self, db, session_id, ts, kind, payload):
        return db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                          (session_id, ts, kind, json.dumps(payload))).lastrowid

    def _seconds(self, now):
        running = (now - self.active_since).total_seconds() if self.active_since is not None else 0
        return int(self.elapsed + running)

    def _state(self, now):
        return {"type": "session_state", "ts": now.isoformat(), "state": self.state,
                "paused_reason": self.paused_reason, "check_message": self.check_message,
                "budget_minutes": self.budget_minutes, "active_seconds": self._seconds(now),
                "running": self.state == "active", "overrun_prompt": self.overrun_prompt,
                "problem": self.problem, "accepted": self.accepted,
                "mode": "watching" if self.state == "active" else "paused" if self.state == "paused" else "idle"}

    def _pause(self, db, now, reason, message=None):
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
        self.paused_reason = None
        self.check_message = None
        self._event(db, self.session_id, now.isoformat(), "session_resumed", {"reason": reason})
        if self.pending_accept and self.problem and not self.accepted:
            self.accepted = True
            self._event(db, self.session_id, now.isoformat(), "accept_counted", {"slug": self.problem["slug"]})
        self.pending_accept = False

    def _end(self, db, now):
        if self.state == "active":
            self.elapsed += (now - self.active_since).total_seconds()
        self.active_since = None
        if self.problem:
            self._event(db, self.session_id, now.isoformat(), "problem_left", {"slug": self.problem["slug"], "accepted": self.accepted})
        seconds = self._seconds(now)
        self._event(db, self.session_id, now.isoformat(), "session_ended",
                    {"active_seconds": seconds, "overrun_seconds": max(0, seconds - self.budget_minutes * 60)})
        db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now.isoformat(), self.session_id))
        self.state = "ended"
        self.paused_reason = None
        self.check_message = None
        self.overrun_prompt = False
        self.pending_accept = False

    def handle(self, message: dict) -> list[dict]:
        now = self.clock()
        kind = message.get("type")
        payload = message.get("payload", {})
        if kind == "session_get_state":
            return [self._state(now)]
        if kind == "session_start" and (self.state not in ("idle", "ended") or
            not isinstance(payload, dict) or type(payload.get("budget_minutes")) is not int or payload["budget_minutes"] <= 0):
            return [{"type": "error", "message": "Invalid session_start"}]
        if kind == "session_pause" and self.state != "active":
            return [{"type": "error", "message": "session_pause requires an active session"}]
        if kind == "session_resume" and self.state != "paused":
            return [{"type": "error", "message": "session_resume requires a paused session"}]
        if kind == "session_end" and self.state not in ("active", "paused"):
            return [{"type": "error", "message": "session_end requires a session"}]
        if kind == "session_overrun" and (not self.overrun_prompt or not isinstance(payload, dict) or payload.get("choice") not in ("finish", "stop")):
            return [{"type": "error", "message": "Invalid session_overrun"}]
        if kind not in READER_TYPES | {"session_start", "session_pause", "session_resume", "session_end", "session_overrun", "tab_closed"}:
            return [{"type": "error", "message": f"Unknown message type: {kind}"}]
        if kind == "tab_closed" and self.state != "active":
            return [self._state(now)]
        if kind in READER_TYPES and (self.state in ("idle", "ended") or
            (self.state == "paused" and kind not in ("focus_changed", "reader_check", "submit_result"))):
            return [self._state(now)]
        with open_store(self.db_path) as db:
            if kind == "session_start":
                self._reset(now, payload["budget_minutes"])
                row = db.execute("INSERT INTO sessions (started_at, budget_minutes) VALUES (?, ?)",
                                 (now.isoformat(), self.budget_minutes))
                self.session_id = row.lastrowid
                self._event(db, self.session_id, now.isoformat(), "session_started", {"budget_minutes": self.budget_minutes})
            elif kind == "session_pause":
                self._pause(db, now, "button")
            elif kind == "tab_closed":
                self._pause(db, now, "closed")
            elif kind == "session_resume":
                self._resume(db, now)
            elif kind == "session_end":
                self._end(db, now)
            elif kind == "session_overrun":
                self._event(db, self.session_id, now.isoformat(), "overrun_choice", {"choice": payload["choice"]})
                self.overrun_prompt = False
                if payload["choice"] == "stop":
                    self._end(db, now)
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
                    elif self.state == "paused" and self.paused_reason == "check" and payload["ok"] is True:
                        self._resume(db, now)
                elif kind == "problem_opened":
                    if self.problem and self.problem["slug"] != payload["slug"]:
                        self._event(db, self.session_id, now.isoformat(), "problem_left", {"slug": self.problem["slug"], "accepted": self.accepted})
                        self.accepted = False
                        self.pending_accept = False
                    if self.problem is None or self.problem["slug"] != payload["slug"]:
                        self.attempt_start = event_id
                        self.problem_started = self._seconds(now)
                    self.problem = {"slug": payload["slug"], "title": payload["title"]}
                elif kind == "submit_result" and payload["accepted"] and self.problem and payload["slug"] == self.problem["slug"]:
                    if self.state == "paused":
                        self.pending_accept = True
                    elif not self.accepted:
                        self.accepted = True
                        self._event(db, self.session_id, now.isoformat(), "accept_counted", {"slug": payload["slug"]})
        return [self._state(now)]

    def _reset(self, now, budget):
        self.state = "active"
        self.budget_minutes = budget
        self.elapsed = 0.0
        self.active_since = now
        self.paused_reason = None
        self.check_message = None
        self.overrun_prompt = False
        self.prompted = False
        self.problem = None
        self.accepted = False
        self.pending_accept = False
        self.attempt_start = None
        self.problem_started = 0.0

    def begin_turn(self, kind, text=None) -> tuple[list[dict], Turn | None]:
        """Start a Tutor turn for a press of "I need help" (`help`) or a chat message (`chat`, with its text).

        Accepted only while the session is active, on a problem, with no other turn in flight; a refusal is a
        `tutor_error` with no turn id and logs nothing. An accepted request is logged first, then its prompt is
        built from this attempt's events; if that fails, the turn is logged failed and nothing is sent.
        """
        now = self.clock()
        if self.state == "paused":
            return [self._tutor_error(now, None, "The session is paused. Resume it to get help.")], None
        if self.state != "active":
            return [self._tutor_error(now, None, "Help is available only during a session.")], None
        if self.problem is None:
            return [self._tutor_error(now, None, "Open a problem first, then ask.")], None
        if self.turn is not None:
            return [self._tutor_error(now, None, "The Tutor is still answering. Wait for that reply, then ask again.")], None
        slug = self.problem["slug"]
        with open_store(self.db_path) as db:
            rows = db.execute("SELECT type, payload FROM events WHERE session_id = ? AND id >= ? ORDER BY id",
                              (self.session_id, self.attempt_start)).fetchall()
            events = [(kind_, json.loads(payload)) for kind_, payload in rows]
            if kind == "help":
                request = TutorRequest("help", answered_presses(events) + 1, None)
                turn_id = self._event(db, self.session_id, now.isoformat(), "help_request", {"slug": slug, "press_index": request.press_index})
            else:
                request = TutorRequest("chat", None, text)
                turn_id = self._event(db, self.session_id, now.isoformat(), "chat_message", {"slug": slug, "text": text})
            try:
                prior_solve = db.execute("SELECT 1 FROM events WHERE type = 'prior_solve' AND json_extract(payload, '$.slug') = ?",
                                         (slug,)).fetchone() is not None
                seconds = self._seconds(now)
                messages = build_messages(self.problem, events, reference=load_reference(db, slug), prior_solve=prior_solve,
                                          problem_seconds=seconds - int(self.problem_started),
                                          seconds_left=self.budget_minutes * 60 - seconds, request=request)
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

    def finish_turn(self, turn: Turn, reply, model: str) -> list[dict]:
        """Log a validated reply and send it; it is stored whatever the session's state is now."""
        now = self.clock()
        labels = {"gap": reply.gap, "gap_note": reply.gap_note, "help_kind": reply.help_kind}
        try:
            with open_store(self.db_path) as db:
                self._event(db, turn.session_id, now.isoformat(), "help_turn", {
                    "turn_id": turn.turn_id, "slug": turn.slug, "request": turn.request.kind,
                    "press_index": turn.request.press_index, **labels, "reply_text": reply.reply_text,
                    "follow_up_question": reply.follow_up_question, "model": model})
        finally:
            self._release(turn)
        return [{"type": "tutor_done", "ts": now.isoformat(), "turn_id": turn.turn_id, "reply_text": reply.reply_text,
                 **labels, "follow_up_question": reply.follow_up_question}]

    def fail_turn(self, turn: Turn, reason: str, message: str) -> list[dict]:
        """Log a turn that ended without a valid reply and tell the panel."""
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
        if self.state != "active" or self.prompted or self._seconds(now) < self.budget_minutes * 60:
            return []
        with open_store(self.db_path) as db:
            self.prompted = True
            self.overrun_prompt = True
            self._event(db, self.session_id, now.isoformat(), "overrun_prompted", {})
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
