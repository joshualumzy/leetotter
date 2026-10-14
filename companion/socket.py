"""Local extension transport. No learner data is handled by this ticket."""

import json
import logging
from datetime import datetime, timezone

from websockets.asyncio.server import serve


EXTENSION_ID = "oohbhndckkkkdngkglilcbpcoekapmgp"
ALLOWED_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
MESSAGE_TYPES = frozenset({"hello", "ping", "status", "error"})
logger = logging.getLogger(__name__)


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


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
    return message


async def _handle(connection):
    async for raw in connection:
        try:
            message = _validate(raw)
            if message["type"] not in {"hello", "ping"}:
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
