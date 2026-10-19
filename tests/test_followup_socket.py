"""The follow-up flow over the wire: the after-accept solve turn, answering, skipping and grading."""

import asyncio
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect

from companion.grading import ANALYSIS_PROMPT, SOLVE_PROMPT
from companion.memory import item_memory
from companion.model import ModelClient
from companion.socket import ALLOWED_ORIGIN, serve_companion
from companion.store import open_store
from companion.tutor import SYSTEM_PROMPT as TUTOR_PROMPT
from tests.test_followups import SAME_CATEGORY, SAME_TITLE
from tests.test_grading import CATEGORY, MEDIUM, MEDIUM_TITLE, put_reference, reviews
from tests.test_session import of_type, submit
from tests.test_tutor_socket import chunks, until, wire

QUESTION = "What's the time complexity of your approach?"


class Provider:
    """A fake provider: scripted Tutor replies, and fixed grader verdicts for the socket's own grading."""

    def __init__(self, *replies, analysis_verdict="right"):
        self.replies = list(replies)
        self.analysis_verdict = analysis_verdict
        self.requests = []
        self.grader_calls = []

    def _plain(self, content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    def create(self, **kwargs):
        messages = kwargs["messages"]
        system = messages[0]["content"]
        self.requests.append(messages)
        if system == SOLVE_PROMPT:
            events = json.loads(messages[1]["content"])["events"]
            accepted = [event[0] for event in events if event[1] == "submit_result" and event[2]["accepted"]]
            self.grader_calls.append("solve")
            return self._plain(json.dumps({
                "accepts": [{"submission_event_id": event_id, "approach_id": "heap", "meets_expected": True}
                            for event_id in accepted],
                "corrected_help_labels": []}))
        if system == ANALYSIS_PROMPT:
            self.grader_calls.append("analysis")
            return self._plain(json.dumps({"complexity_verdict": self.analysis_verdict,
                                           "complexity_stated_earlier": False}))
        if system != TUTOR_PROMPT:
            raise RuntimeError("Model role not scripted here")
        reply = self.replies.pop(0)
        if isinstance(reply, tuple):
            gate, reply = reply
            assert gate.wait(5)
        if isinstance(reply, Exception):
            raise reply
        return iter(reply)


def models(monkeypatch, path, *replies, analysis_verdict="right"):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    provider = Provider(*replies, analysis_verdict=analysis_verdict)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=provider))
    return ModelClient(client=fake, db_path=path), provider


def run(path, monkeypatch, client, body):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    async def check():
        async with serve_companion(port=0, models=client) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await body(ws)

    asyncio.run(check())


async def in_session(ws, slug=MEDIUM, title=MEDIUM_TITLE):
    await ws.send(wire("session_start"))
    await until(ws, "session_state", state="active")
    await ws.send(wire("problem_opened", {"site": "neetcode", "slug": slug, "title": title}))
    await until(ws, "session_state")


async def accept(ws, slug=MEDIUM, code="def topKFrequent(nums, k):\n    return []"):
    await ws.send(wire("code_snapshot", {"code": code}))
    await until(ws, "session_state")
    await ws.send(json.dumps(submit(slug, True)))
    await until(ws, "session_state", accepted=True)


async def followup_state(ws):
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if message["type"] == "session_state" and message.get("followup"):
            return message["followup"]


def solve_reply(**changes):
    return {"gap": "none", "gap_note": None, "help_kind": "none",
            "reply_text": "That works. Can you make it faster?", "follow_up_question": QUESTION,
            "follow_up_complexity": True, "postpone_reason": None, **changes}


def test_the_first_accept_runs_one_solve_turn_and_opens_the_question(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    client, provider = models(monkeypatch, path, chunks(json.dumps(solve_reply())))
    seen = []

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        start, _ = await until(ws, "tutor_start")
        done, _ = await until(ws, "tutor_done")
        followup = await followup_state(ws)
        seen.append((start, done, followup))
        await ws.send(json.dumps(submit(MEDIUM, True)))  # a second accept in the same visit
        await until(ws, "session_state", accepted=True)
        await asyncio.sleep(0.1)

    run(path, monkeypatch, client, body)
    start, done, followup = seen[0]
    assert start["request"] == "solve"
    assert done["follow_up_question"] == QUESTION
    asked, = of_type(path, "followup_asked")
    assert (asked[1]["question"], asked[1]["complexity"]) == (QUESTION, True)
    assert followup == {"question_id": followup["question_id"], "question": QUESTION, "complexity": True}
    assert isinstance(followup["question_id"], int)
    assert len(of_type(path, "solve_request")) == 1
    solve_turns = [row for row in of_type(path, "help_turn") if row[1]["request"] == "solve"]
    assert len(solve_turns) == 1
    assert len(provider.replies) == 0


def test_a_needs_review_reference_forbids_the_faster_line_and_every_follow_up(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    put_reference(path, status="needs_review")
    client, provider = models(monkeypatch, path, chunks(json.dumps(solve_reply(
        reply_text="That works. Can you make it faster?"))))

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        done, _ = await until(ws, "tutor_done")
        await until(ws, "session_state")
        assert done["follow_up_question"] is None

    run(path, monkeypatch, client, body)
    assert of_type(path, "followup_asked") == []
    user = provider.requests[-1][-1]["content"]
    assert "needs review" in user.lower() and "forbidden" in user.lower()
    assert "make it faster" not in user.lower()


def test_a_flagged_reference_with_no_disputed_label_keeps_follow_ups(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    put_reference(path)
    with open_store(path) as db:
        db.execute('UPDATE "references" SET status = ? WHERE problem_key = ?', ("needs_review", MEDIUM))
    client, _ = models(monkeypatch, path, chunks(json.dumps(solve_reply())))

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        done, _ = await until(ws, "tutor_done")
        assert done["follow_up_question"] == QUESTION
        await followup_state(ws)

    run(path, monkeypatch, client, body)
    assert len(of_type(path, "followup_asked")) == 1


def test_a_postponement_moves_the_complexity_question_to_the_next_first_attempt(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    postponed = solve_reply(reply_text="That works.", follow_up_question=None, follow_up_complexity=False,
                            postpone_reason="Long, hard problem; enough for now.")
    client, _ = models(monkeypatch, path, chunks(json.dumps(postponed)), chunks(json.dumps(solve_reply())))

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        await until(ws, "tutor_done")
        await ws.send(wire("session_done"))
        await until(ws, "session_state")
        await ws.send(wire("problem_opened", {"site": "neetcode", "slug": SAME_CATEGORY, "title": SAME_TITLE}))
        await until(ws, "session_state")
        await accept(ws, SAME_CATEGORY)
        await until(ws, "tutor_start")
        await until(ws, "tutor_done")
        await followup_state(ws)

    run(path, monkeypatch, client, body)
    postponed, = of_type(path, "complexity_postponed")
    asked, = of_type(path, "followup_asked")
    assert postponed[1]["slug"] == MEDIUM and postponed[1]["reason"] == "Long, hard problem; enough for now."
    assert asked[1]["slug"] == SAME_CATEGORY and asked[1]["complexity"] is True
    assert len(of_type(path, "solve_request")) == 2


def test_an_answered_complexity_question_reaches_the_analysis_grader_and_memory(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    put_reference(path)
    answer = {"gap": "none", "gap_note": None, "help_kind": "none",
              "reply_text": "Right: each heap operation costs log k.", "has_reasoning": True}
    client, provider = models(monkeypatch, path, chunks(json.dumps(solve_reply())),
                              chunks(json.dumps(answer)), analysis_verdict="right")

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        await until(ws, "tutor_done")
        await ws.send(wire("chat_message", {"text": "O(n log k), because each push and pop costs log k"}))
        start, _ = await until(ws, "tutor_start")
        assert start["request"] == "chat"
        await until(ws, "tutor_done")
        await ws.send(wire("session_end"))
        await until(ws, "session_state", state="ended")

    run(path, monkeypatch, client, body)
    answered = of_type(path, "followup_answered")
    assert answered and answered[-1][1]["has_reasoning"] is True
    analysis = [row for row in reviews(path) if row["item_type"] == "category:analysis"]
    assert [(row["item_key"], row["grade"]) for row in analysis] == [(CATEGORY, "good")]
    with open_store(path) as db:
        memory = item_memory(db, "category:analysis", CATEGORY, datetime.now(timezone.utc))
    assert memory is not None and memory.last_grade == "good"
    assert provider.grader_calls.count("analysis") == 2


def test_a_skipped_question_never_changes_or_delays_the_solve_grade(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    put_reference(path)
    client, provider = models(monkeypatch, path, chunks(json.dumps(solve_reply())))
    seen = []

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        await until(ws, "tutor_done")
        followup = await followup_state(ws)
        seen.append(followup)
        await ws.send(wire("followup_skip", {"question_id": followup["question_id"]}))
        await until(ws, "session_state")
        await ws.send(wire("session_end"))
        await until(ws, "session_state", state="ended")

    run(path, monkeypatch, client, body)
    assert [row[1]["question_id"] for row in of_type(path, "followup_skipped")] == \
        [seen[0]["question_id"]]
    solve_rows = [row for row in reviews(path) if row["item_type"] != "category:analysis"]
    assert [(row["item_type"], row["grade"]) for row in solve_rows] == [("problem", "good"),
                                                                       ("category:solve", "good")]
    assert [row for row in reviews(path) if row["item_type"] == "category:analysis"] == []
    assert "analysis" not in provider.grader_calls


def test_a_stale_answer_arriving_after_done_is_rejected_loudly(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    gate = threading.Event()
    answer = {"gap": "none", "gap_note": None, "help_kind": "none",
              "reply_text": "Right.", "has_reasoning": True}
    client, _ = models(monkeypatch, path, chunks(json.dumps(solve_reply())),
                       (gate, chunks(json.dumps(answer))))

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        await until(ws, "tutor_done")
        await followup_state(ws)
        await ws.send(wire("chat_message", {"text": "O(n log k), because of the heap"}))
        await until(ws, "tutor_start")
        await ws.send(wire("session_done"))
        await until(ws, "session_state")
        gate.set()
        error, _ = await until(ws, "tutor_error")
        assert "closed" in error["message"].lower()

    run(path, monkeypatch, client, body)
    failed, = [row for row in of_type(path, "help_failed") if row[1]["reason"] == "closed"]
    assert failed[1]["slug"] == MEDIUM
    assert of_type(path, "followup_answered") == []


def test_a_stale_solve_reply_after_done_does_not_open_a_question(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    gate = threading.Event()
    client, _ = models(monkeypatch, path, (gate, chunks(json.dumps(solve_reply()))))

    async def body(ws):
        await in_session(ws)
        await accept(ws)
        await until(ws, "tutor_start")
        await ws.send(wire("session_done"))
        await until(ws, "session_state")
        gate.set()
        done, _ = await until(ws, "tutor_done")
        assert done["follow_up_question"] is None
        await until(ws, "session_state")

    run(path, monkeypatch, client, body)
    assert of_type(path, "followup_asked") == []
    assert of_type(path, "followup_skipped") == []
    assert len(of_type(path, "solve_request")) == 1


@pytest.mark.parametrize("payload", [None, {}, {"question_id": 0}, {"question_id": "1"}, {"question_id": True}])
def test_malformed_followup_skip_payloads_get_a_visible_error(tmp_path, monkeypatch, payload):
    path = tmp_path / "db.sqlite3"

    async def body(ws):
        await ws.send(wire("followup_skip", payload))
        error, _ = await until(ws, "error")
        assert "followup_skip" in error["message"]

    run(path, monkeypatch, None, body)
    assert of_type(path, "session_started") == []


def test_followup_skip_outside_a_session_gets_an_error(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"

    async def body(ws):
        await ws.send(wire("followup_skip", {"question_id": 1}))
        error, _ = await until(ws, "error")
        assert "session" in error["message"].lower()

    run(path, monkeypatch, None, body)
