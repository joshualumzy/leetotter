"""A free NeetCode 250 problem while its reference is written in the background."""

import asyncio
import json
import threading
from datetime import datetime, timezone

import pytest

import companion.socket as socket
from companion.catalog import load_catalog
from companion.session import SessionCoordinator
from companion.store import open_store
from tests.test_tutor_socket import wire


PROBLEM = load_catalog().by_slug("concatenation-of-array")
STATEMENT = "Given an integer array nums, return an array with nums followed by nums."
REFERENCE = {"slug": PROBLEM["slug"], "title": PROBLEM["title"], "difficulty": PROBLEM["difficulty"],
             "neetcode_category": PROBLEM["category"], "summary": "Return the input sequence repeated twice.",
             "patterns": ["array"],
             "analysis_concepts": ["linear-time"], "approaches": [{"id": "copy", "name": "Copy twice",
                 "idea": "Place the input twice in a new array", "time": "O(n)", "space": "O(n)",
                 "interview_expected": "usually", "trick": False}]}
CHECK = {"verdicts": [{"approach_id": "copy", "label": label, "agree": True, "reason": "correct"}
                      for label in ("complexity", "interview_expected", "trick")]}
TUTOR = {"gap": "bugs_and_edge_cases", "gap_note": None, "help_kind": "question",
         "reply_text": "How long should the result be?", "follow_up_question": None}


class RecordedModels:
    def __init__(self, gate, grader_down=False):
        self.gate = gate
        self.grader_down = grader_down
        self.writer_started = threading.Event()
        self.calls = []
        self.config = type("Config", (), {"roles": {"grader": ["grader-a", "grader-b"],
                                                   "tutor": ["recorded-tutor"]}})()

    def call_role(self, role, messages, schema, **kwargs):
        self.calls.append((role, messages))
        if role == "reference_writer":
            self.writer_started.set()
            assert self.gate.wait(15)
            value = REFERENCE
        elif role == "checker":
            value = CHECK
        elif role == "tutor":
            assert STATEMENT in messages[-1]["content"]
            assert "no reference" in messages[-1]["content"].lower()
            value = TUTOR
        elif role == "grader":
            if self.grader_down:
                raise RuntimeError("recorded grader outage")
            events = json.loads(messages[-1]["content"])["events"]
            submission = next(event_id for event_id, kind, payload in events
                              if kind == "submit_result" and payload["accepted"])
            value = {"accepts": [{"submission_event_id": submission, "approach_id": "copy",
                                  "meets_expected": True}], "corrected_help_labels": []}
        else:
            raise AssertionError(role)
        return schema.model_validate(value)


class Connection:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, raw):
        item = json.loads(raw)
        self.sent.append(item)
        await self.outgoing.put(item)

    async def request(self, kind, payload=None, response="session_state"):
        await self.incoming.put(wire(kind, payload))
        while True:
            item = await asyncio.wait_for(self.outgoing.get(), 5)
            if item["type"] == response:
                return item


@pytest.mark.parametrize("grader_down", [False, True])
def test_reference_is_checked_after_ungraded_solve_without_blocking_tutor(tmp_path, monkeypatch, grader_down):
    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    gate = threading.Event()
    models = RecordedModels(gate, grader_down)

    async def scenario():
        try:
            socket._coordinator = SessionCoordinator(path, lambda: datetime.now(timezone.utc))
            socket._models = models
            socket._grading_lock = asyncio.Lock()
            socket._reflection_lock = asyncio.Lock()
            connection = Connection()
            handler = asyncio.create_task(socket._handle(connection))
            await connection.request("problem_statement", {"slug": PROBLEM["slug"], "statement": STATEMENT})
            assert not await asyncio.to_thread(models.writer_started.wait, 0.1)
            assert (await connection.request("session_start", {"budget_minutes": 30}))["state"] == "active"
            state = await connection.request("problem_opened", {"site": "neetcode", "slug": PROBLEM["slug"],
                                                                 "title": PROBLEM["title"]})
            assert state["reference_status"] == "not_checked_yet"
            await connection.request("problem_statement", {"slug": PROBLEM["slug"], "statement": STATEMENT})
            assert await asyncio.to_thread(models.writer_started.wait, 2)
            await connection.request("help_request", response="tutor_done")
            await connection.request("submit_result", {"slug": PROBLEM["slug"], "accepted": True,
                "error_type": None, "error_message": "", "runtime": "0.01", "memory": 1000,
                "passed": 2, "total": 2, "failing_case": None})
            assert (await connection.request("session_end"))["state"] == "ended"
            await asyncio.gather(*socket._grading_tasks)
            with open_store(path) as db:
                assert db.execute("SELECT grade FROM reviews WHERE item_type = 'problem'").fetchone()[0] is None
            gate.set()
            while True:
                state = await asyncio.wait_for(connection.outgoing.get(), 5)
                if state["type"] == "session_state" and state["reference_status"] == "checked":
                    break
            with open_store(path) as db:
                assert db.execute('SELECT status FROM "references" WHERE problem_key = ?',
                                  (PROBLEM["slug"],)).fetchone()[0] == "checked"
                assert db.execute("SELECT grade FROM reviews WHERE item_type = 'problem'").fetchone()[0] == (
                    None if grader_down else "hard")
            if grader_down:
                assert any(item["type"] == "error" and "Grading is delayed" in item["message"]
                           for item in connection.sent)
            await connection.incoming.put(None)
            await handler
        finally:
            gate.set()

    asyncio.run(scenario())
