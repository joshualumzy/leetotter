"""Local extension transport. No learner data is handled by this ticket."""

import json
import logging
from datetime import datetime, timezone

from websockets.asyncio.server import serve


EXTENSION_ID = "oohbhndckkkkdngkglilcbpcoekapmgp"
ALLOWED_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
READER_TYPES = frozenset({"problem_opened", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed"})
MESSAGE_TYPES = frozenset({"hello", "ping", "status", "error"}) | READER_TYPES
logger = logging.getLogger(__name__)


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _string(value):
    return isinstance(value, str)


def _nonempty(value):
    return _string(value) and bool(value)


def _integer(value):
    return type(value) is int


def _failure(value):
    return value is None or (isinstance(value, dict) and all(_string(value.get(k)) for k in ("input", "expected", "actual")))


def _result(value):
    return (isinstance(value, dict) and type(value.get("passed")) is bool
            and (value.get("error_type") is None or _nonempty(value.get("error_type")))
            and _string(value.get("error_message")) and _string(value.get("runtime"))
            and _failure(value.get("failing_case")))


def _reader_payload(kind, payload):
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {kind} payload")
    valid = False
    if kind == "problem_opened":
        valid = payload.get("site") == "neetcode" and _nonempty(payload.get("slug")) and _nonempty(payload.get("title"))
    elif kind == "code_snapshot":
        valid = _string(payload.get("code"))
    elif kind == "code_changed":
        diff = payload.get("diff")
        valid = (isinstance(diff, dict) and _integer(diff.get("start_line")) and diff["start_line"] > 0
                 and isinstance(diff.get("removed"), list) and all(_string(v) for v in diff["removed"])
                 and isinstance(diff.get("added"), list) and all(_string(v) for v in diff["added"]))
    elif kind == "run_result":
        cases = payload.get("cases")
        valid = _nonempty(payload.get("slug")) and isinstance(cases, list) and bool(cases) and all(_result(v) for v in cases)
    elif kind == "submit_result":
        valid = (_nonempty(payload.get("slug")) and type(payload.get("accepted")) is bool
                 and (payload.get("error_type") is None or _nonempty(payload.get("error_type")))
                 and _string(payload.get("error_message")) and _string(payload.get("runtime"))
                 and type(payload.get("memory")) in (int, float)
                 and _integer(payload.get("passed")) and _integer(payload.get("total"))
                 and _failure(payload.get("failing_case")))
    elif kind == "focus_changed":
        valid = type(payload.get("tab_active")) is bool and type(payload.get("window_focused")) is bool
    if not valid:
        raise ValueError(f"Invalid {kind} payload")


def _validate(raw):
    try:
        message = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid JSON message") from exc
    if not isinstance(message, dict):
        raise ValueError("Message must be an object")
    kind = message.get("type")
    if not isinstance(kind, str) or kind not in MESSAGE_TYPES:
        raise ValueError(f"Unknown message type: {kind}")
    if not isinstance(message.get("ts"), str) or not message["ts"]:
        raise ValueError("Message requires ts")
    if "session_id" in message and (not isinstance(message["session_id"], str) or not message["session_id"]):
        raise ValueError("Invalid session_id")
    if kind == "status" and message.get("state") != "connected":
        raise ValueError("Invalid status state")
    if kind == "error" and (not isinstance(message.get("message"), str) or not message["message"]):
        raise ValueError("Error requires message")
    if kind in READER_TYPES:
        _reader_payload(kind, message.get("payload"))
    return message


async def _handle(connection):
    async for raw in connection:
        try:
            message = _validate(raw)
            if message["type"] not in {"hello", "ping"} | READER_TYPES:
                raise ValueError(f"Unexpected client message type: {message['type']}")
        except ValueError as exc:
            logger.error("WebSocket protocol error: %s", exc)
            await connection.send(json.dumps({"type": "error", "ts": _timestamp(), "message": str(exc)}))
            continue
        if message["type"] == "hello":
            await connection.send(json.dumps({"type": "status", "ts": _timestamp(), "state": "connected"}))


def serve_companion(port=8765):
    """Create the loopback-only WebSocket server for use in main and tests."""
    return serve(_handle, "127.0.0.1", port, origins=[ALLOWED_ORIGIN])
