"""Regression checks at the in-process socket seam."""

import asyncio
from types import SimpleNamespace

import companion.socket as socket
from tests.test_planner_socket import Connection, setup
from tests.test_session import msg, opened
from companion.catalog import load_catalog


def test_off_plan_open_discards_card_and_planner_chat_is_rejected(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, ["house-robber"])
        connection = Connection([msg("hello"), msg("session_start", {"budget_minutes": 60}),
                                 opened("climbing-stairs", "Climbing Stairs"),
                                 msg("planner_chat", {"text": "more DP"})])
        await socket._handle(connection)
        assert socket._planner.card is None
        assert any(m["type"] == "error" and "suggestion card" in m["message"] for m in connection.sent)
    asyncio.run(scenario())


def test_session_start_discards_waiting_start_request(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, [])
        pending = socket._planner.panel_opened("idle")
        connection = Connection([msg("session_start", {"budget_minutes": 60})])
        await socket._handle(connection)
        assert socket._planner.answer(pending, None) == []
    asyncio.run(scenario())


def test_grading_failure_after_reference_check_reports_grading_not_reference(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, [])
        async def checked(*args, **kwargs):
            return SimpleNamespace(status="checked")
        async def grading(*args):
            raise RuntimeError("grader offline")
        sent = []
        async def broadcast(messages, sender):
            sent.extend(messages)
        monkeypatch.setattr(socket.asyncio, "to_thread", checked)
        monkeypatch.setattr(socket, "_run_grading", grading)
        monkeypatch.setattr(socket, "_broadcast", broadcast)
        problem = load_catalog().by_slug("minimum-size-subarray-sum")
        await socket._run_reference(problem, "Statement")
        assert sent and "Grading" in sent[-1]["message"]
        assert "not checked" not in sent[-1]["message"]
    asyncio.run(scenario())


def test_reference_statement_is_retried_when_session_resumes(tmp_path, monkeypatch):
    async def scenario():
        setup(tmp_path, monkeypatch, [])
        seen = []
        monkeypatch.setattr(socket, "_schedule_reference", seen.append)
        slug = "minimum-size-subarray-sum"
        connection = Connection([msg("session_start", {"budget_minutes": 60}),
                                 opened(slug, "Minimum Size Subarray Sum"),
                                 msg("problem_statement", {"slug": slug, "statement": "Find the shortest subarray."}),
                                 msg("session_pause"), msg("session_resume")])
        await socket._handle(connection)
        assert len(seen) == 2
        assert seen[-1]["statement"] == "Find the shortest subarray."
    asyncio.run(scenario())


def test_statement_received_while_paused_starts_reference_on_resume(tmp_path, monkeypatch):
    async def scenario():
        real_schedule = socket._schedule_reference
        setup(tmp_path, monkeypatch, [])
        monkeypatch.setattr(socket, "_schedule_reference", real_schedule)
        started = []
        async def write(problem, statement):
            started.append((problem["slug"], statement))
        monkeypatch.setattr(socket, "_run_reference", write)
        slug = "minimum-size-subarray-sum"
        connection = Connection([msg("session_start", {"budget_minutes": 60}),
                                 opened(slug, "Minimum Size Subarray Sum"), msg("session_pause"),
                                 msg("problem_statement", {"slug": slug, "statement": "Shortest subarray."}),
                                 msg("session_resume")])
        await socket._handle(connection)
        await asyncio.gather(*tuple(socket._reference_tasks.values()))
        assert started == [(slug, "Shortest subarray.")]
    asyncio.run(scenario())
