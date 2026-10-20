"""Bug report bundles: one folder per report beside the store (SPEC §2, "Bug reports")."""

import asyncio
import json
import re
from datetime import datetime

import pytest
from websockets.asyncio.client import connect

import companion.bug_reports as bug_reports
import companion.socket as socket
from companion.bug_reports import count_bug_reports, save_bug_report
from companion.session import SessionCoordinator
from companion.socket import ALLOWED_ORIGIN, serve_companion
from companion.store import open_store


def seed_store(path, sessions=1, events=0, model_calls=0):
    with open_store(path) as db:
        session_ids = []
        for index in range(sessions):
            session_ids.append(db.execute(
                "INSERT INTO sessions (started_at, ended_at) VALUES (?, ?)",
                (f"2026-10-17T10:00:{index:02d}+00:00", None if index == sessions - 1 else "2026-10-17T10:30:00+00:00"),
            ).lastrowid)
        session_id = session_ids[-1]
        for index in range(events):
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                       (session_id, f"2026-10-17T10:{index // 60:02d}:{index % 60:02d}+00:00", "problem_left",
                        json.dumps({"slug": "two-sum", "index": index})))
        for index in range(model_calls):
            db.execute("INSERT INTO model_calls (session_id, role, model, latency_ms, validation_result, created_at) "
                       "VALUES (?, 'tutor', 'test-model', ?, 'valid', ?)",
                       (session_id, index, f"2026-10-17T11:00:{index:02d}+00:00"))
        return session_ids


def test_a_bundle_holds_the_report_messages_state_events_model_calls_and_log(tmp_path, monkeypatch):
    path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    log = tmp_path / "companion.log"
    log.write_text("".join(f"line {index}\n" for index in range(350)))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(log))
    session_id = seed_store(path, events=60, model_calls=8)[0]
    messages = [{"direction": "in", "time": "2026-10-17T10:00:00+00:00", "message": {"type": "ping", "ts": "now"}}]
    state = {"type": "session_state", "ts": "now", "session_id": session_id, "state": "active"}

    folder = save_bug_report("The otter ate my homework", messages, state)

    assert folder.parent == tmp_path / "bugs"
    assert re.fullmatch(r"\d{8}-\d{6}", folder.name)
    report = (folder / "report.md").read_text()
    assert "The otter ate my homework" in report and "T" in report
    trace = json.loads((folder / "trace.json").read_text())
    assert trace["extension_messages"] == messages
    assert trace["session_state"] == state
    assert [row["id"] for row in trace["events"]] == list(range(11, 61))
    assert [row["payload"] for row in trace["events"]][-1] == json.dumps({"slug": "two-sum", "index": 59})
    assert [row["id"] for row in trace["model_calls"]] == [4, 5, 6, 7, 8]
    assert (folder / "companion.log").read_text() == "".join(f"line {index}\n" for index in range(50, 350))


def test_the_most_recent_session_is_used_between_sessions(tmp_path, monkeypatch):
    path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(tmp_path / "companion.log"))
    (tmp_path / "companion.log").write_text("log\n")
    session_ids = seed_store(path, sessions=2, events=3)

    folder = save_bug_report("Between sessions", [])

    trace = json.loads((folder / "trace.json").read_text())
    assert {row["session_id"] for row in trace["events"]} == {session_ids[-1]}


def test_a_folder_name_collision_gets_a_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(tmp_path / "companion.log"))
    (tmp_path / "companion.log").write_text("log\n")
    monkeypatch.setattr(bug_reports, "_now", lambda: datetime(2026, 10, 20, 9, 8, 7))

    first = save_bug_report("one", [])
    second = save_bug_report("two", [])
    third = save_bug_report("three", [])

    assert [folder.name for folder in (first, second, third)] == ["20261020-090807", "20261020-090807-2", "20261020-090807-3"]
    assert "two" in (second / "report.md").read_text()


def test_a_part_that_cannot_be_read_is_an_error_and_the_rest_is_saved(tmp_path, monkeypatch):
    broken = tmp_path / "not-a-store"
    broken.mkdir()
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(broken))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(tmp_path / "companion.log"))
    (tmp_path / "companion.log").write_text("log\n")
    state = {"type": "session_state", "ts": "now", "session_id": 1, "state": "active"}

    folder = save_bug_report("Half broken", [{"direction": "in"}], state)

    trace = json.loads((folder / "trace.json").read_text())
    assert trace["extension_messages"] == [{"direction": "in"}]
    assert trace["session_state"] == state
    assert "error" in trace["events"] and trace["events"]["error"]
    assert "error" in trace["model_calls"] and trace["model_calls"]["error"]
    assert (folder / "report.md").exists() and (folder / "companion.log").read_text() == "log\n"


def test_an_unset_log_path_is_recorded_in_companion_log(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    monkeypatch.delenv("LEETOTTER_LOG_PATH", raising=False)

    folder = save_bug_report("No log", [])

    assert (folder / "companion.log").read_text() == "LEETOTTER_LOG_PATH is not set"


def test_count_bug_reports_counts_folders_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    assert count_bug_reports() == 0
    (tmp_path / "bugs").mkdir()
    (tmp_path / "bugs" / "20261020-090807").mkdir()
    (tmp_path / "bugs" / "20261020-090808").mkdir()
    (tmp_path / "bugs" / "stray.txt").write_text("not a report")
    assert count_bug_reports() == 2


def test_socket_saves_a_report_and_broadcasts_the_new_count(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(tmp_path / "companion.log"))
    (tmp_path / "companion.log").write_text("log\n")

    async def check():
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as a, connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as b:
                await a.send(json.dumps({"type": "bug_report", "ts": "now",
                                         "payload": {"note": "  It froze  ", "trace": [{"direction": "in"}]}}))
                first = json.loads(await asyncio.wait_for(a.recv(), 2))
                second = json.loads(await asyncio.wait_for(a.recv(), 2))
                assert first["type"] == "bug_saved" and first["ts"]
                assert second["type"] == "settings_state" and second["bug_reports"] == 1
                assert json.loads(await asyncio.wait_for(b.recv(), 2))["type"] == "bug_saved"
                assert json.loads(await asyncio.wait_for(b.recv(), 2))["bug_reports"] == 1
                folders = list((tmp_path / "bugs").iterdir())
                assert len(folders) == 1
                assert "It froze" in (folders[0] / "report.md").read_text()

    asyncio.run(check())


def test_an_unreadable_session_state_is_recorded_and_the_report_still_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(tmp_path / "companion.log"))
    (tmp_path / "companion.log").write_text("log\n")
    monkeypatch.setattr(SessionCoordinator, "_state", lambda self, now: (_ for _ in ()).throw(RuntimeError("state broke")))

    async def check():
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"type": "bug_report", "ts": "now", "payload": {"note": "It froze", "trace": []}}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "bug_saved"

    asyncio.run(check())
    trace = json.loads((next((tmp_path / "bugs").iterdir()) / "trace.json").read_text())
    assert trace["session_state"] == {"error": "Session state could not be read: state broke"}


def test_socket_rejects_invalid_bug_report_payloads(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))

    async def check():
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as ws:
                for payload in ({"note": "   ", "trace": []}, {"note": "x" * 4001, "trace": []},
                                {"note": "fine", "trace": "no"}, {"trace": []}):
                    await ws.send(json.dumps({"type": "bug_report", "ts": "now", "payload": payload}))
                    assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "error"
                assert count_bug_reports() == 0

    asyncio.run(check())


def test_a_failed_save_reaches_only_the_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    monkeypatch.setattr(socket, "save_bug_report", lambda *args: (_ for _ in ()).throw(OSError("disk full")))

    async def check():
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as a, connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as b:
                await a.send(json.dumps({"type": "bug_report", "ts": "now", "payload": {"note": "Nope", "trace": []}}))
                failed = json.loads(await asyncio.wait_for(a.recv(), 2))
                assert failed["type"] == "bug_failed" and failed["payload"]["error"] == "disk full"
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(b.recv(), 0.2)

    asyncio.run(check())


def test_reset_does_not_touch_bug_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    monkeypatch.setenv("LEETOTTER_LOG_PATH", str(tmp_path / "companion.log"))
    (tmp_path / "companion.log").write_text("log\n")
    folder = save_bug_report("Keep me", [])

    async def check():
        async with serve_companion(port=0) as server:
            uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"type": "reset_progress", "ts": "now", "payload": {"confirm": "RESET"}}))
                replies = [json.loads(await asyncio.wait_for(ws.recv(), 2)) for _ in range(2)]
                assert [reply["type"] for reply in replies] == ["reset_done", "settings_state"]
                assert replies[1]["bug_reports"] == 1

    asyncio.run(check())
    assert folder.exists() and (folder / "report.md").exists()
