"""End-to-end user-journey harness over the companion WebSocket."""

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from websockets.asyncio.client import connect

from companion.grading import ANALYSIS_PROMPT, SOLVE_PROMPT
from companion.model import ModelClient
from companion.planner import PICKER_PROMPT, READER_PROMPT
from companion.reference import CHECKER as CHECKER_PROMPT
from companion.reference import FORMAT as REFERENCE_PROMPT
from companion.reflector import FEEDBACK_PROMPT, PROMPT as REFLECTOR_PROMPT
from companion.socket import ALLOWED_ORIGIN, serve_companion
from companion.store import DEFAULT_DB_PATH, PERSONAL_TABLES, open_store
from companion.tutor import SYSTEM_PROMPT as TUTOR_PROMPT


OUT_DIR = Path(__file__).parent / "out"
STEP_TIMEOUT = 60
LONG_TIMEOUT = 200
PROBLEMS = (
    ("duplicate-integer", "Contains Duplicate"),
    ("is-anagram", "Valid Anagram"),
    ("two-integer-sum", "Two Sum"),
    ("is-palindrome", "Valid Palindrome"),
    ("anagram-groups", "Group Anagrams"),
)
CODE_OLD = "class Solution:\n    def hasDuplicate(self, nums: List[int]) -> bool:\n        return False"
STATEMENT = "Return true if any value appears more than once in the input."
TUTOR_REPLIES = (
    {
        "gap": "bugs_and_edge_cases",
        "gap_note": None,
        "help_kind": "question",
        "reply_text": "Try the smallest input, `[1]`, and trace what your loop returns. What changes on the second pass?",
        "follow_up_question": None,
        "linked": [],
    },
    {
        "gap": "bugs_and_edge_cases",
        "gap_note": None,
        "help_kind": "snippet",
        "reply_text": "Keep track of the numbers you have already seen:\n\n```python\nseen = set()\n```\n\nWhat should you check before adding a number?",
        "follow_up_question": None,
        "linked": [],
    },
    {
        "gap": "bugs_and_edge_cases",
        "gap_note": None,
        "help_kind": "snippet",
        "reply_text": "Put the check and the add next to each other:\n\n```python\nfor n in nums:\n    if n in seen:\n        return True\n    seen.add(n)\n```\n\nWhere does your loop return?",
        "follow_up_question": None,
        "linked": [],
    },
    {
        "gap": "none",
        "gap_note": None,
        "help_kind": "none",
        "reply_text": "Go for it.",
        "follow_up_question": None,
        "linked": [],
    },
)


class FakeHTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"fake provider HTTP {status_code}")
        self.status_code = status_code


SOLVE_REPLY = {
    "gap": "none", "gap_note": None, "help_kind": "none", "reply_text": "That works.",
    "follow_up_question": "What are the time and space complexity of your solution?",
    "follow_up_complexity": True, "postpone_reason": None, "linked": [],
}
ANSWER_REPLY = {
    "gap": "none", "gap_note": None, "help_kind": "none", "reply_text": "Right, one pass with a set.",
    "has_reasoning": True, "follow_up_question": None, "linked": [],
}
FOLLOWUP_ANSWER = "O(n) time and O(n) space, because one pass with a set stores each value once."


def _completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


def _chunks(text, size=23):
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text[i:i + size]))], usage=None)
            for i in range(0, len(text), size)]


class FakeProvider:
    def __init__(self, mode="normal"):
        self.mode = mode

    def create(self, **kwargs):
        if self.mode == "down":
            raise FakeHTTPError(502)
        messages = kwargs["messages"]
        system = messages[0]["content"]
        if system == PICKER_PROMPT:
            user = json.loads(messages[1]["content"])
            allowed = user.get("allowed") or []
            if not allowed:
                raise RuntimeError("fake Planner has nothing allowed to pick")
            return _completion(json.dumps({"slug": allowed[0]["slug"], "reason": "scripted"}))
        if system == READER_PROMPT:
            return _completion(json.dumps({
                "reading": "unrelated", "reply": "OK.", "more_categories": [], "more_difficulties": [],
                "fewer_categories": [], "fewer_difficulties": [], "horizon": "session", "label": "",
                "note": "", "named_title": "",
            }))
        if system == SOLVE_PROMPT:
            user = json.loads(messages[1]["content"])
            accepts = [{"submission_event_id": event[0], "approach_id": "new", "meets_expected": True}
                       for event in user["events"]
                       if event[1] == "submit_result" and event[2].get("accepted")]
            return _completion(json.dumps({"accepts": accepts, "corrected_help_labels": []}))
        if system == ANALYSIS_PROMPT:
            return _completion(json.dumps({"complexity_verdict": "right", "complexity_stated_earlier": False}))
        if system in (REFLECTOR_PROMPT, FEEDBACK_PROMPT):
            return _completion(json.dumps({"summary": "Session completed.", "changes": [], "teaching": [], "bullets": []}))
        if system == REFERENCE_PROMPT:
            fields = dict(line.split(": ", 1) for line in messages[1]["content"].splitlines() if ": " in line)
            slug, title = fields.get("Problem slug", "problem"), fields.get("Title", "Problem")
            return _completion(json.dumps({
                "slug": slug, "title": title, "difficulty": "Easy", "neetcode_category": "Arrays & Hashing",
                "summary": "Check whether any value repeats.", "patterns": [], "analysis_concepts": [],
                "approaches": [{"id": "scan", "name": "Scan", "idea": "Check every pair.", "time": "O(n^2)",
                                "space": "O(1)", "interview_expected": "usually", "trick": False}],
            }))
        if system == CHECKER_PROMPT:
            return _completion(json.dumps({"verdicts": []}))
        if system == TUTOR_PROMPT:
            user = messages[1]["content"]
            if "## After the accept" in user:
                reply = SOLVE_REPLY
            elif "answering the follow-up question" in user:
                reply = ANSWER_REPLY
            elif "The learner wrote:" in user:
                reply = TUTOR_REPLIES[3]
            elif "press 3 on this problem" in user:
                reply = TUTOR_REPLIES[2]
            elif "press 2 on this problem" in user:
                reply = TUTOR_REPLIES[1]
            else:
                reply = TUTOR_REPLIES[0]
            return iter(_chunks(json.dumps(reply)))
        raise RuntimeError(f"Fake provider received an unsupported prompt: {system[:80]}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _message(kind, payload=None):
    message = {"type": kind, "ts": _now()}
    if payload is not None:
        message["payload"] = payload
    return message


def _code_block_lines(text):
    longest = 0
    inside = False
    lines = 0
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if inside:
                longest = max(longest, lines)
                inside, lines = False, 0
            else:
                inside, lines = True, 0
            continue
        if inside:
            lines += 1
    if inside:
        longest = max(longest, lines)
    return longest


class JourneyFailure(RuntimeError):
    pass


class Journey:
    def __init__(self, name, db_path, model_factory):
        self.name = name
        self.db_path = db_path
        self.model_factory = model_factory
        self.conn = None
        self.server = None
        self.port = None
        self._server_cm = None
        self.messages = []
        self.steps = []
        self.seq = 0
        self.protected_event_ids = []

    def record(self, direction, value):
        self.seq += 1
        self.messages.append({"seq": self.seq, "at": _now(), "dir": direction, "message": value})

    async def start_server(self):
        import companion.socket as socket_module
        socket_module._clients.clear()
        self._server_cm = serve_companion(port=0, models=self.model_factory())
        self.server = await self._server_cm.__aenter__()
        self.port = self.server.sockets[0].getsockname()[1]
        self.record("server", "started")

    async def connect(self):
        self.conn = await connect(f"ws://127.0.0.1:{self.port}", origin=ALLOWED_ORIGIN, proxy=None)
        self.record("connection", "open")

    async def close_connection(self):
        if self.conn is not None:
            conn, self.conn = self.conn, None
            await conn.close()
            self.record("connection", "closed")

    async def stop_server(self):
        if self._server_cm is not None:
            cm, self._server_cm = self._server_cm, None
            await cm.__aexit__(None, None, None)
            self.server = None
            self.record("server", "stopped")

    async def crash_server(self):
        cm, self._server_cm = self._server_cm, None
        await cm.__aexit__(None, None, None)
        self.server = None
        self.conn = None
        self.record("server", "crashed")

    async def finish(self):
        try:
            await self.close_connection()
        finally:
            await self.stop_server()

    async def send(self, kind, payload=None):
        message = _message(kind, payload)
        await self.conn.send(json.dumps(message))
        self.record("send", message)
        return message

    async def send_raw(self, raw):
        await self.conn.send(raw)
        self.record("send_raw", raw)

    async def expect(self, kind, timeout=STEP_TIMEOUT, **fields):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {kind} {fields or ''}".strip())
            try:
                raw = await asyncio.wait_for(self.conn.recv(), remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"timed out waiting for {kind} {fields or ''}".strip()) from exc
            try:
                message = json.loads(raw)
            except ValueError:
                message = raw
            self.record("recv", message)
            if isinstance(message, dict) and message.get("type") == kind and all(
                    message.get(key) == value for key, value in fields.items()):
                return message

    async def step(self, label, expected, action):
        started = time.perf_counter()
        try:
            result = await action
        except Exception as exc:
            elapsed = time.perf_counter() - started
            self.steps.append({"label": label, "expected": expected, "seconds": elapsed, "error": str(exc)})
            print(f"[{self.name}] {label} -> FAILED {elapsed:.1f}s: {exc}", flush=True)
            raise
        elapsed = time.perf_counter() - started
        self.steps.append({"label": label, "expected": expected, "seconds": elapsed})
        print(f"[{self.name}] {label} -> {expected} {elapsed:.1f}s", flush=True)
        return result

    def check_invariants(self):
        with open_store(self.db_path) as db:
            events = db.execute("SELECT id, session_id, ts, type, payload FROM events ORDER BY id").fetchall()
            reviews = db.execute("SELECT event_id FROM reviews WHERE event_id IS NOT NULL").fetchall()
            grading_failures = db.execute("SELECT start_event_id FROM grading_failures").fetchall()
            grader_retries = db.execute("SELECT start_event_id FROM grader_retries").fetchall()
            ended = db.execute("SELECT id, summary, recap_bullets FROM sessions WHERE ended_at IS NOT NULL").fetchall()
            reflector_failures = db.execute("SELECT session_id FROM reflector_failures").fetchall()
        parsed = [(id_, session, ts, kind, json.loads(raw)) for id_, session, ts, kind, raw in events]
        problems = []
        previous = None
        for id_, _, ts, kind, _ in parsed:
            at = datetime.fromisoformat(ts)
            if previous is not None and at < previous:
                problems.append(f"event {id_} ({kind}) is before the previous event's timestamp")
            previous = at
        event_ids = {event[0] for event in parsed}
        for missing in sorted(set(self.protected_event_ids) - event_ids):
            problems.append(f"event {missing} was lost across the restart")
        accepted = set(reviewed for (reviewed,) in reviews)
        recorded = {start for (start,) in grading_failures} | {start for (start,) in grader_retries}
        for attempt in _closed_attempts(parsed):
            if not attempt["accepted"]:
                continue
            if attempt["left_id"] not in accepted and attempt["start"] not in recorded:
                problems.append(
                    f"accepted submission in attempt {attempt['start']}..{attempt['left_id']} has no review or grading failure")
        closed = {payload["question_id"] for _, _, _, kind, payload in parsed
                  if kind in ("followup_skipped",) or (kind == "followup_answered" and payload["has_reasoning"])}
        closed |= {payload["question_id"] for _, _, _, kind, payload in parsed
                   if kind == "followup_answered" and not payload["has_reasoning"]}
        for id_, _, _, kind, _ in parsed:
            if kind == "followup_asked" and id_ not in closed:
                problems.append(f"follow-up question {id_} was never answered or skipped")
        reflected = {session for (session,) in reflector_failures}
        for session, summary, bullets in ended:
            if summary is None and bullets is None and session not in reflected:
                problems.append(f"ended session {session} has neither a recap nor a reflector failure")
        turns = {payload["turn_id"] for _, _, _, kind, payload in parsed if kind == "help_turn"}
        failed = {payload["turn_id"] for _, _, _, kind, payload in parsed if kind == "help_failed"}
        for turn_id in sorted(turns & failed):
            problems.append(f"help turn {turn_id} is stored as both a reply and a failure")
        if problems:
            raise JourneyFailure("DB invariants failed: " + "; ".join(problems))


def _closed_attempts(events):
    active = {}
    for id_, session, _, kind, payload in events:
        if kind == "problem_opened":
            current = active.get(session)
            if current is None or current["slug"] != payload["slug"]:
                active[session] = {"start": id_, "slug": payload["slug"], "accepted": False}
        elif kind == "submit_result" and payload.get("accepted"):
            current = active.get(session)
            if current is not None and current["slug"] == payload["slug"]:
                current["accepted"] = True
        elif kind == "problem_left":
            current = active.pop(session, None)
            if current is not None and current["slug"] == payload["slug"]:
                current["accepted"] = bool(payload.get("accepted")) or current["accepted"]
                current["left_id"] = id_
                yield current


async def _start(j):
    await j.send("session_start")
    state = await j.expect("session_state")
    assert state["state"] == "active", state
    return state


async def _open(j, slug, title):
    await j.send("problem_opened", {"site": "neetcode", "slug": slug, "title": title})
    state = await j.expect("session_state")
    assert state["state"] == "active", state
    assert state["problem"] == {"slug": slug, "title": title}, state
    return state


async def _statement(j, slug, text=STATEMENT):
    await j.send("problem_statement", {"slug": slug, "statement": text})
    return await j.expect("session_state")


async def _snapshot(j, code):
    await j.send("code_snapshot", {"code": code})
    return await j.expect("session_state")


async def _run_wrong(j, slug):
    await j.send("run_result", {"slug": slug, "cases": [{
        "passed": False, "error_type": "wrong_answer", "error_message": "expected true",
        "runtime": "0.020", "failing_case": {"input": "nums=[1,1]", "expected": "true", "actual": "false"}}]})
    return await j.expect("session_state")


async def _run_ok(j, slug):
    await j.send("run_result", {"slug": slug, "cases": [{
        "passed": True, "error_type": None, "error_message": "", "runtime": "0.020", "failing_case": None}]})
    return await j.expect("session_state")


async def _submit(j, slug, accepted):
    payload = {
        "slug": slug, "accepted": accepted,
        "error_type": None if accepted else "wrong_answer",
        "error_message": "" if accepted else "3 / 34 testcases passed",
        "runtime": "0.032", "memory": 14184, "passed": 34 if accepted else 3, "total": 34,
        "failing_case": None if accepted else {"input": "nums=[1,1]", "expected": "true", "actual": "false"},
    }
    await j.send("submit_result", payload)
    state = await j.expect("session_state")
    assert state["accepted"] is accepted, state
    return state


async def _press(j):
    await j.send("help_request")
    start = await j.expect("tutor_start")
    done = await j.expect("tutor_done")
    assert done["turn_id"] == start["turn_id"], (start, done)
    return start, done


async def _chat(j, text):
    await j.send("chat_message", {"text": text})
    start = await j.expect("tutor_start")
    done = await j.expect("tutor_done")
    assert done["turn_id"] == start["turn_id"], (start, done)
    return start, done


async def _done_problem(j):
    await j.send("session_done")
    while True:  # a closing follow-up question can broadcast one more state first
        state = await j.expect("session_state")
        if state["problem"] is None:
            return state


async def _end(j):
    await j.send("session_end")
    while True:  # an open follow-up question or a late solve turn can broadcast a state first
        state = await j.expect("session_state")
        if state["state"] == "ended":
            return state


async def _recap(j, status="ready"):
    return await j.expect("recap_state", timeout=LONG_TIMEOUT, status=status)


async def _suggestion(j):
    return await j.expect("suggestion", timeout=LONG_TIMEOUT)


async def _go(j):
    await j.send("planner_go")
    opened = await j.expect("planner_open")
    return opened


async def solve_clean(j):
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[0]))
    await j.step("problem_statement", "session_state", _statement(j, PROBLEMS[0][0]))
    await j.step("code_snapshot", "session_state", _snapshot(j, CODE_OLD))
    await j.step("code_changed", "session_state", _changed(j))
    await j.step("run_result(wrong)", "session_state", _run_wrong(j, PROBLEMS[0][0]))
    await j.step("submit_result(accepted)", "session_state(accepted)", _submit(j, PROBLEMS[0][0], True))
    await j.step("session_done", "session_state(between)", _done_problem(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def _changed(j):
    await j.send("code_changed", {"diff": {"start_line": 3, "removed": ["        return False"],
                                          "added": ["        return len(set(nums)) != len(nums)"]}})
    return await j.expect("session_state")


async def help_ladder(j):
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[0]))
    await j.step("problem_statement", "session_state", _statement(j, PROBLEMS[0][0]))
    replies = []
    for index in range(1, 4):
        start, done = await j.step(f"help_request({index})", "tutor_done", _press(j))
        assert start["request"] == "help", start
        assert start["press_index"] == index, start
        replies.append(done["reply_text"])
    assert _code_block_lines(replies[0]) <= 6, replies[0]
    assert _code_block_lines(replies[1]) <= 6, replies[1]
    start, done = await j.step("chat_message", "tutor_done", _chat(j, "Why does my loop miss duplicates?"))
    assert start["request"] == "chat", start
    assert done["reply_text"], done
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def wrong_then_accept(j):
    slug = PROBLEMS[1][0]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[1]))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.step("code_snapshot", "session_state", _snapshot(j, CODE_OLD))
    await j.step("run_result(wrong)", "session_state", _run_wrong(j, slug))
    await j.step("submit_result(wrong)", "session_state(not accepted)", _submit(j, slug, False))
    await j.step("code_changed", "session_state", _changed(j))
    await j.step("run_result(ok)", "session_state", _run_ok(j, slug))
    await j.step("submit_result(accepted)", "session_state(accepted)", _submit(j, slug, True))
    await j.step("session_done", "session_state(between)", _done_problem(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def give_up(j):
    slug = PROBLEMS[2][0]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[2]))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    for index in range(1, 4):
        await j.step(f"help_request({index})", "tutor_done", _press(j))
    await j.step("session_skip", "session_state(between)", _skip(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def _skip(j):
    await j.send("session_skip")
    state = await j.expect("session_state")
    assert state["problem"] is None, state
    return state


async def abandon(j):
    slug = PROBLEMS[3][0]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[3]))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.step("code_snapshot", "session_state", _snapshot(j, CODE_OLD))
    await j.step("session_end(mid-problem)", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def multi_problem(j, count=3):
    await j.step("session_start", "session_state(active)", _start(j))
    for index in range(count):
        suggestion = await j.step(f"suggestion({index + 1})", "suggestion", _suggestion(j))
        assert suggestion["slug"] and suggestion["title"], suggestion
        opened = await j.step(f"planner_go({index + 1})", "planner_open", _go(j))
        slug, title = opened["slug"], opened["title"]
        await j.step(f"problem_opened({slug})", "session_state(active)", _open(j, slug, title))
        await j.step(f"problem_statement({index + 1})", "session_state", _statement(j, slug))
        await j.step(f"submit_result({index + 1}, accepted)", "session_state(accepted)", _submit(j, slug, True))
        await j.step(f"session_done({index + 1})", "session_state(between)", _done_problem(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def long_session(j):
    await multi_problem(j, count=8)
    with open_store(j.db_path) as db:
        rows = db.execute(
            "SELECT role, COUNT(*), AVG(latency_ms), MAX(latency_ms), SUM(prompt_tokens), SUM(completion_tokens) "
            "FROM model_calls GROUP BY role ORDER BY role"
        ).fetchall()
    for role, calls, mean, worst, prompt, completion in rows:
        print(f"[long-session] {role}: {calls} calls, mean {mean / 1000:.1f}s, max {worst / 1000:.1f}s, tokens {prompt}/{completion}")


async def _accept_and_followup(j, slug, title):
    """Accept a problem and return the open follow-up question, or None when the Tutor postponed or didn't ask."""
    await j.step("problem_opened", "session_state(active)", _open(j, slug, title))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.send("submit_result", {"slug": slug, "accepted": True, "error_type": None, "error_message": "",
                                   "runtime": "0.032", "memory": 14184, "passed": 34, "total": 34, "failing_case": None})
    start = await j.expect("tutor_start", timeout=LONG_TIMEOUT)
    assert start["request"] == "solve", start
    done = await j.expect("tutor_done", timeout=LONG_TIMEOUT)
    if not done["follow_up_question"]:
        return done, None
    while True:
        state = await j.expect("session_state", timeout=LONG_TIMEOUT)
        if state.get("followup"):
            return done, state["followup"]


async def followup_answer(j):
    await j.step("session_start", "session_state(active)", _start(j))
    done, question = await j.step("accept", "tutor_done(solve)", _accept_and_followup(j, *PROBLEMS[0]))
    if question is not None:
        _, answer = await j.step("followup_answer", "tutor_done", _chat(j, FOLLOWUP_ANSWER))
        assert answer["reply_text"], answer
    await j.step("session_done", "session_state(between)", _done_problem(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def followup_skip(j):
    await j.step("session_start", "session_state(active)", _start(j))
    done, question = await j.step("accept", "tutor_done(solve)", _accept_and_followup(j, *PROBLEMS[0]))
    if question is not None:
        await j.send("followup_skip", {"question_id": question["question_id"]})
        state = await j.expect("session_state")
        assert state["followup"] is None, state
    await j.step("session_done", "session_state(between)", _done_problem(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def pause_resume(j):
    slug, title = PROBLEMS[0]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, slug, title))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.step("session_pause", "session_state(paused)", _pause(j))
    await j.step("session_resume", "session_state(active)", _resume(j, slug, title))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def _pause(j):
    await j.send("session_pause")
    state = await j.expect("session_state")
    assert state["state"] == "paused" and state["paused_reason"] == "button", state
    return state


async def _resume(j, slug=None, title=None):
    await j.send("session_resume")
    state = await j.expect("session_state")
    assert state["state"] == "active", state
    if slug is not None:
        assert state["problem"] == {"slug": slug, "title": title}, state
    return state


async def reset_mid_session(j):
    slug, title = PROBLEMS[0]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, slug, title))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.step("submit_result(accepted)", "session_state(accepted)", _submit(j, slug, True))
    await j.step("reset_progress(RESET)", "reset_done", _reset(j))
    state = await j.step("session_get_state", "session_state(idle)", _get_state(j))
    assert state["state"] == "idle", state
    with open_store(j.db_path) as db:
        for table in PERSONAL_TABLES:
            count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            assert count == 0, f"{table} still has {count} rows after reset"


async def _reset(j):
    await j.send("reset_progress", {"confirm": "RESET"})
    done = await j.expect("reset_done")
    await j.expect("settings_state")
    return done


async def _get_state(j):
    await j.send("session_get_state")
    return await j.expect("session_state")


async def reconnect(j):
    slug, title = PROBLEMS[4]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, slug, title))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.step("drop_websocket", "disconnect processed", _drop(j))
    await j.step("reconnect", "status(connected)", _reconnect(j))
    state = await j.step("hello/session_get_state", "session_state(paused)", _get_state(j))
    assert state["paused_reason"] == "disconnect", state
    assert state["problem"] == {"slug": slug, "title": title}, state
    await j.step("session_resume", "session_state(active)", _resume(j, slug, title))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def _drop(j):
    await j.close_connection()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with open_store(j.db_path) as db:
            row = db.execute("SELECT payload FROM events WHERE type = 'session_paused' ORDER BY id DESC LIMIT 1").fetchone()
        if row is not None and json.loads(row[0]).get("reason") == "disconnect":
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("the companion did not pause the session after the disconnect")

async def _reconnect(j):
    await j.connect()
    await j.send("hello")
    await j.expect("status", state="connected")
    return await j.expect("session_state")


async def restart(j):
    slug, title = PROBLEMS[1]
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, slug, title))
    await j.step("problem_statement", "session_state", _statement(j, slug))
    await j.step("code_snapshot", "session_state", _snapshot(j, CODE_OLD))
    with open_store(j.db_path) as db:
        j.protected_event_ids = [id_ for (id_,) in db.execute("SELECT id FROM events ORDER BY id")]
    await j.step("stop_server(mid-session)", "server stopped", _crash(j))
    await j.step("start_server(same DB)", "server started", _restart_server(j))
    await j.step("hello", "session_state(paused/restart)", _hello(j))
    state = await j.step("session_get_state", "session_state(paused/restart)", _get_state(j))
    assert state["state"] == "paused" and state["paused_reason"] == "restart", state
    assert state["problem"] == {"slug": slug, "title": title}, state
    await j.step("session_resume", "session_state(active)", _resume(j, slug, title))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def _crash(j):
    await j.crash_server()


async def _restart_server(j):
    await j.start_server()
    await j.connect()


async def _hello(j):
    await j.send("hello")
    await j.expect("status", state="connected")
    state = await j.expect("session_state")
    assert state["state"] == "paused" and state["paused_reason"] == "restart", state
    return state


async def malformed_input(j):
    await j.step("garbage JSON", "error", _bad_json(j))
    await j.step("unknown type", "error", _bad_type(j))
    await j.step("oversized payload", "error", _oversized(j))
    await j.step("session_start(after errors)", "session_state(active)", _start(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[0]))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(ready)", _recap(j))


async def _bad_json(j):
    await j.send_raw("{not json")
    error = await j.expect("error")
    assert "Invalid JSON" in error["message"], error
    return error


async def _bad_type(j):
    await j.send("made_up_type")
    error = await j.expect("error")
    assert "Unknown message type" in error["message"], error
    return error


async def _oversized(j):
    await j.send("chat_message", {"text": "x" * 5000})
    error = await j.expect("error")
    assert "chat_message" in error["message"] and "4000" in error["message"], error
    return error


async def model_down(j):
    await j.step("session_start", "session_state(active)", _start(j))
    await j.step("planner fallback", "suggestion", _typed_suggestion(j))
    await j.step("problem_opened", "session_state(active)", _open(j, *PROBLEMS[0]))
    await j.step("problem_statement", "session_state", _statement(j, PROBLEMS[0][0]))
    await j.step("help_request", "tutor_error(named)", _tutor_down(j))
    await j.step("session_end", "session_state(ended)", _end(j))
    await j.step("recap", "recap_state(failed)", _recap(j, status="failed"))
    with open_store(j.db_path) as db:
        help_turns = db.execute("SELECT count(*) FROM events WHERE type = 'help_turn'").fetchone()[0]
        help_failed = db.execute("SELECT count(*) FROM events WHERE type = 'help_failed'").fetchone()[0]
        failures = db.execute("SELECT count(*) FROM reflector_failures").fetchone()[0]
    assert help_turns == 0 and help_failed == 1, (help_turns, help_failed)
    assert failures == 1, failures


async def _typed_suggestion(j):
    message = await j.expect("suggestion", timeout=LONG_TIMEOUT)
    assert message["source"] == "fallback", message
    return message


async def _tutor_down(j):
    await j.send("help_request")
    error = await j.expect("tutor_error", timeout=LONG_TIMEOUT)
    assert "isn't responding" in error["message"], error
    assert "tutoring" in error["message"], error
    assert error["turn_id"] is not None, error
    return error


JOURNEYS = {
    "solve-clean": solve_clean,
    "help-ladder": help_ladder,
    "wrong-then-accept": wrong_then_accept,
    "give-up": give_up,
    "abandon": abandon,
    "multi-problem": multi_problem,
    "long-session": long_session,
    "followup-answer": followup_answer,
    "followup-skip": followup_skip,
    "pause-resume": pause_resume,
    "reset-mid-session": reset_mid_session,
    "reconnect": reconnect,
    "restart": restart,
    "malformed-input": malformed_input,
    "model-down": model_down,
}
FAKE_ONLY = {"model-down"}
FAKE_MODES = {"model-down": "down"}


async def _run_journey(name, number, fn, fake, fake_mode):
    with tempfile.TemporaryDirectory(prefix=f"leetotter-journey-{name}-") as directory:
        db_path = Path(directory) / "companion.sqlite3"
        os.environ["LEETOTTER_DB_PATH"] = str(db_path)

        def model_factory():
            if fake:
                return ModelClient(client=SimpleNamespace(chat=SimpleNamespace(completions=FakeProvider(fake_mode))), db_path=db_path)
            return ModelClient()

        journey = Journey(name, db_path, model_factory)
        error = None
        started = time.perf_counter()
        try:
            await journey.start_server()
            await journey.connect()
            await fn(journey)
        except Exception as exc:
            error = exc
        finally:
            try:
                await journey.finish()
            except Exception as exc:
                if error is None:
                    error = exc
        try:
            journey.check_invariants()
        except Exception as exc:
            error = exc if error is None else JourneyFailure(f"{error}; {exc}")
        elapsed = time.perf_counter() - started
        path = OUT_DIR / f"{name}-{number}.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "journey": name, "run": number, "mode": "fake" if fake else "real",
            "seconds": elapsed, "passed": error is None,
            "error": None if error is None else str(error), "steps": journey.steps,
            "messages": journey.messages,
        }, indent=2))
        return {
            "name": name, "number": number, "seconds": elapsed, "passed": error is None,
            "error": None if error is None else str(error),
            "traceback": None if error is None else "".join(traceback.format_exception(error)),
            "path": path, "messages": journey.messages,
        }


def _check_real_mode():
    configured = os.environ.get("LEETOTTER_DB_PATH")
    if not configured:
        sys.exit("Refusing to run: set LEETOTTER_DB_PATH to a throwaway database (the caller must provide it).")
    given = Path(configured).expanduser().resolve()
    default = DEFAULT_DB_PATH.expanduser().resolve()
    if given == default:
        sys.exit(f"Refusing to run: LEETOTTER_DB_PATH points at the real store ({default}).")


async def _main_async(args):
    selected = list(JOURNEYS)
    if args.only:
        missing = [name for name in args.only if name not in JOURNEYS]
        if missing:
            sys.exit(f"Unknown journey: {', '.join(missing)}. Choose from: {', '.join(JOURNEYS)}")
        selected = [name for name in JOURNEYS if name in args.only]
    if not args.fake:
        skipped = [name for name in selected if name in FAKE_ONLY]
        for name in skipped:
            print(f"[{name}] skipped in real mode (fake-only journey)", flush=True)
        selected = [name for name in selected if name not in FAKE_ONLY]
    if not selected:
        sys.exit("No journeys selected.")

    if args.fake:
        os.environ.setdefault("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
        os.environ.setdefault("NUS_SOCLAAS_API_KEY", "fake-key")

    results = []
    for name in selected:
        fn = JOURNEYS[name]
        for number in range(1, args.repeat + 1):
            print(f"[{name}] run {number}/{args.repeat}", flush=True)
            results.append(await _run_journey(name, number, fn, args.fake, FAKE_MODES.get(name, "normal")))

    print("", flush=True)
    print(f"{'Journey':20} {'Runs':>4} {'Pass rate':>10} {'Median':>8} {'Max':>8}", flush=True)
    print("-" * 55, flush=True)
    for name in selected:
        runs = [result for result in results if result["name"] == name]
        elapsed = [result["seconds"] for result in runs]
        passed = sum(1 for result in runs if result["passed"])
        rate = f"{100 * passed / len(runs):.0f}%"
        print(f"{name:20} {len(runs):>4} {rate:>10} {statistics.median(elapsed):>7.1f}s {max(elapsed):>7.1f}s", flush=True)

    failures = [result for result in results if not result["passed"]]
    if failures:
        print("\nFAILURES", flush=True)
        for failure in failures:
            print(f"\n=== {failure['name']} run {failure['number']} ({failure['seconds']:.1f}s) ===", flush=True)
            print(failure["error"], flush=True)
            print(failure["traceback"], flush=True)
            print(f"transcript: {failure['path']}", flush=True)
            print(json.dumps(failure["messages"], indent=2), flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(prog="python -m journeys.run", description="Run end-to-end user journeys.")
    parser.add_argument("--fake", action="store_true", help="use a scripted model client instead of the provider")
    parser.add_argument("--only", action="append", metavar="NAME", help="run only this journey (repeatable)")
    parser.add_argument("--repeat", type=int, default=1, metavar="N", help="run every selected journey N times")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if not args.fake:
        _check_real_mode()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
