"""Session state, active-time accounting, and append-only session events."""

import json
from companion.store import open_store


READER_TYPES = frozenset({"problem_opened", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"})


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
        with open_store(db_path) as db:
            rows = db.execute("SELECT id, started_at FROM sessions WHERE ended_at IS NULL").fetchall()
            for session_id, started_at in rows:
                last = db.execute("SELECT ts FROM events WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
                ended_at = last[0] if last else started_at
                db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (ended_at, session_id))
                self._event(db, session_id, ended_at, "session_interrupted", {})

    def _event(self, db, session_id, ts, kind, payload):
        db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                   (session_id, ts, kind, json.dumps(payload)))

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
        if kind not in READER_TYPES | {"session_start", "session_pause", "session_resume", "session_end", "session_overrun"}:
            return [{"type": "error", "message": f"Unknown message type: {kind}"}]
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
                self._event(db, self.session_id, now.isoformat(), kind, payload)
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
        now = self.clock()
        if self.state == "paused" and self.paused_reason == "disconnect":
            with open_store(self.db_path) as db:
                self._resume(db, now)
        return [self._state(now)]

    def on_disconnect(self) -> list[dict]:
        if self.state != "active":
            return []
        now = self.clock()
        with open_store(self.db_path) as db:
            self._pause(db, now, "disconnect")
        return [self._state(now)]
