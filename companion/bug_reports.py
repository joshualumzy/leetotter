"""Local bug report bundles, beside the store, for a coding agent to fix later."""

import json
import os
from collections import deque
from datetime import datetime
from pathlib import Path

from companion.store import open_store, store_path


LOG_LINES = 300
EVENT_ROWS = 50
MODEL_CALL_ROWS = 5


def bugs_dir() -> Path:
    return store_path().parent / "bugs"


def count_bug_reports() -> int:
    folder = bugs_dir()
    if not folder.is_dir():
        return 0
    return sum(1 for item in folder.iterdir() if item.is_dir())


def save_bug_report(note, extension_trace, session_state=None):
    """Write one bundle and return its folder. A part that cannot be read is recorded as its error."""
    folder = _new_folder()
    now = _now().astimezone()
    (folder / "report.md").write_text(f"{note}\n\n{now.isoformat()}\n", encoding="utf-8")
    trace = {
        "extension_messages": _part(lambda: _json_safe(extension_trace)),
        "session_state": _part(lambda: _json_safe(session_state) if session_state is not None
                               else {"error": "Session state is unavailable"}),
        "events": _part(_events, session_state),
        "model_calls": _part(_model_calls),
    }
    (folder / "trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "companion.log").write_text(_log_tail(), encoding="utf-8")
    return folder


def _now():
    return datetime.now()


def _new_folder() -> Path:
    base = bugs_dir()
    base.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    suffix = 1
    while True:
        folder = base / (stamp if suffix == 1 else f"{stamp}-{suffix}")
        try:
            folder.mkdir()
        except FileExistsError:
            suffix += 1
            continue
        return folder


def _part(gather, *args):
    try:
        return gather(*args)
    except Exception as error:
        return {"error": str(error)}


def _events(session_state):
    session_id = _session_id(session_state)
    if session_id is None:
        return []
    with open_store() as db:
        cursor = db.execute("SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                            (session_id, EVENT_ROWS))
        return _rows(cursor)


def _model_calls():
    with open_store() as db:
        return _rows(db.execute("SELECT * FROM model_calls ORDER BY id DESC LIMIT ?", (MODEL_CALL_ROWS,)))


def _session_id(session_state):
    if isinstance(session_state, dict):
        session_id = session_state.get("session_id")
        if type(session_id) is int and session_id > 0:
            return session_id
    with open_store() as db:
        row = db.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, (_json_safe(value) for value in row))) for row in cursor.fetchall()]
    return rows[::-1]


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (datetime, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _log_tail() -> str:
    path = os.environ.get("LEETOTTER_LOG_PATH")
    if not path:
        return "LEETOTTER_LOG_PATH is not set"
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=LOG_LINES))
    except Exception as error:
        return str(error)
