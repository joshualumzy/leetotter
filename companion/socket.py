"""Local extension transport and typed import requests."""

import json
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from companion.catalog import normalize_link
from companion.history import import_history, import_status
from companion.model import ModelClient
from companion.session import SessionCoordinator
from companion.store import open_store, store_path
from companion.tutor import ask_tutor, failure


EXTENSION_ID = "oohbhndckkkkdngkglilcbpcoekapmgp"
ALLOWED_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
READER_TYPES = frozenset({"problem_opened", "problem_statement", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"})
SESSION_TYPES = frozenset({"session_start", "session_pause", "session_resume", "session_end", "session_overrun", "session_get_state", "tab_closed"})
# Tutor messages. Client: help_request (a press of "I need help", no payload) and chat_message {text}.
# Server, each with the turn's id: tutor_start {request, press_index}; tutor_chunk {attempt, text}, reply text
# as it streams (append chunks of the same attempt; a higher attempt is a retry after an invalid reply and
# replaces what was shown); tutor_done {reply_text, gap, gap_note, help_kind, follow_up_question}, the
# validated reply, which replaces the streamed text; tutor_error {message}, the turn failed and anything
# streamed for it is withdrawn. A refused request gets tutor_error with turn_id null, to the sender only.
TUTOR_TYPES = frozenset({"help_request", "chat_message"})
MESSAGE_TYPES = frozenset({"hello", "ping", "status", "error", "get_import_status", "import_status", "import_history", "import_result", "session_state", "settings_get", "settings_set", "settings_state", "tutor_start", "tutor_chunk", "tutor_done", "tutor_error"}) | READER_TYPES | SESSION_TYPES | TUTOR_TYPES
CHAT_LIMIT = 4000  # characters; a few minutes of dictation
logger = logging.getLogger(__name__)
_coordinator = None
_models = None
_clients = set()
_turns = set()


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _settings_state():
    with open_store() as db:
        values = dict(db.execute("SELECT key, value FROM settings WHERE key IN ('name', 'theme')"))
    return {"type": "settings_state", "ts": _timestamp(), "name": values.get("name"), "theme": values.get("theme", "match")}


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
    elif kind == "problem_statement":
        valid = _nonempty(payload.get("slug")) and _nonempty(payload.get("statement"))
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
    elif kind == "reader_check":
        valid = type(payload.get("ok")) is bool and ("message" not in payload or _string(payload["message"]))
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
    if kind == "settings_set":
        payload = message.get("payload")
        if not isinstance(payload, dict) or payload.get("key") not in ("name", "theme") or not isinstance(payload.get("value"), str):
            raise ValueError("Invalid settings_set payload")
        value = payload["value"]
        if (payload["key"] == "name" and len(value.strip()) > 40) or (payload["key"] == "theme" and value not in ("match", "light", "dark")):
            raise ValueError("Invalid settings_set value")
    if kind in READER_TYPES:
        _reader_payload(kind, message.get("payload"))
    if kind == "session_start":
        payload = message.get("payload")
        if not isinstance(payload, dict) or not _integer(payload.get("budget_minutes")) or payload["budget_minutes"] <= 0:
            raise ValueError("Invalid session_start payload")
    if kind == "session_overrun":
        payload = message.get("payload")
        if not isinstance(payload, dict) or payload.get("choice") not in ("finish", "stop"):
            raise ValueError("Invalid session_overrun payload")
    if kind in ("tab_closed", "help_request") and "payload" in message:
        raise ValueError(f"Invalid {kind} payload")
    if kind == "chat_message":
        payload = message.get("payload")
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > CHAT_LIMIT:
            raise ValueError(f"Invalid chat_message payload: text must be 1 to {CHAT_LIMIT} characters")
    if kind == "import_history":
        payload = message.get("payload")
        urls = payload.get("urls") if isinstance(payload, dict) else None
        if not isinstance(urls, list) or any(not isinstance(url, str) or normalize_link(url) is None for url in urls):
            raise ValueError("Invalid import_history URLs: expected LeetCode problem URLs")
    if kind == "import_status" and type(message.get("imported")) is not bool:
        raise ValueError("Invalid import_status payload")
    if kind == "import_result":
        result = message.get("result")
        if not isinstance(result, dict) or type(result.get("already_imported")) is not bool or type(result.get("matched")) is not int or not isinstance(result.get("unmatched"), list) or any(not isinstance(url, str) for url in result["unmatched"]):
            raise ValueError("Invalid import_result payload")
    return message


async def _handle(connection):
    _clients.add(connection)
    try:
        async for raw in connection:
            try:
                message = _validate(raw)
                kind = message["type"]
                if kind not in {"hello", "ping", "get_import_status", "import_history", "settings_get", "settings_set"} | READER_TYPES | SESSION_TYPES | TUTOR_TYPES:
                    raise ValueError(f"Unexpected client message type: {kind}")
                if kind == "hello":
                    await connection.send(json.dumps({"type": "status", "ts": _timestamp(), "state": "connected"}))
                    await _broadcast(_coordinator.on_connect(), connection)
                elif kind == "get_import_status":
                    await connection.send(json.dumps({"type": "import_status", "ts": _timestamp(), "imported": import_status()}))
                elif kind == "settings_get":
                    await connection.send(json.dumps(_settings_state()))
                elif kind == "settings_set":
                    payload = message["payload"]
                    with open_store() as db:
                        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                                   (payload["key"], payload["value"].strip() if payload["key"] == "name" else payload["value"]))
                    await _broadcast([_settings_state()], connection)
                elif kind == "import_history":
                    result = import_history(message["payload"]["urls"])
                    await connection.send(json.dumps({"type": "import_result", "ts": _timestamp(), "result": result}))
                elif kind in TUTOR_TYPES:
                    text = message["payload"]["text"].strip() if kind == "chat_message" else None
                    outgoing, turn = _coordinator.begin_turn("chat" if kind == "chat_message" else "help", text)
                    await _broadcast(outgoing, connection)
                    if turn is not None:
                        task = asyncio.create_task(_run_turn(turn))
                        _turns.add(task)
                        task.add_done_callback(_turns.discard)
                elif kind != "ping":
                    outgoing = _coordinator.handle(message)
                    await _broadcast(outgoing, connection, kind != "session_get_state")
            except Exception as exc:
                logger.exception("WebSocket request failed: %s", exc)
                await connection.send(json.dumps({"type": "error", "ts": _timestamp(), "message": str(exc)}))
    finally:
        _clients.discard(connection)
        if not _clients and _coordinator is not None:
            await _broadcast(_coordinator.on_disconnect(), connection)


async def _broadcast(outgoing, sender, broadcast=True):
    for message in outgoing:
        if message["type"] == "error":
            logger.error("Session request rejected: %s", message["message"])
            message = {**message, "ts": _timestamp()}
        turn_message = message["type"].startswith("tutor_") and message["turn_id"] is not None
        targets = tuple(_clients) if turn_message or (broadcast and message["type"] in ("session_state", "settings_state")) else (sender,)
        for target in targets:
            try:
                await target.send(json.dumps(message))
            except ConnectionClosed:
                logger.warning("Connection closed before %s could be delivered", message["type"])


async def _run_turn(turn):
    """Run one Tutor call in a worker thread, so the event loop (the timer, other clients) never waits on it,
    and relay its streamed text to every panel in order. The turn always ends logged: done or failed."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def post(item):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass  # the loop has closed: the companion stopped, and the turn was logged as failed (shutdown)

    def work():
        global _models
        try:
            if _models is None:
                _models = ModelClient()
            reply = ask_tutor(_models, turn.messages, turn.schema, lambda attempt, text: post(("chunk", attempt, text)))
            post(("done", reply, _models.config.roles["tutor"][0]))
        except Exception as exc:
            post(("failed", exc))

    loop.run_in_executor(None, work)
    try:
        while True:
            item = await queue.get()
            if item[0] == "chunk":
                await _broadcast([{"type": "tutor_chunk", "ts": _timestamp(), "turn_id": turn.turn_id,
                                   "attempt": item[1], "text": item[2]}], None)
                continue
            if item[0] == "done":
                outgoing = _coordinator.finish_turn(turn, item[1], item[2])
            else:
                logger.error("Tutor turn %s failed", turn.turn_id, exc_info=item[1])
                outgoing = _coordinator.fail_turn(turn, *failure(item[1]))
            await _broadcast(outgoing, None)
            return
    except asyncio.CancelledError:
        _coordinator.fail_turn(turn, "shutdown", "The companion stopped before the Tutor finished.")
        raise
    except Exception as exc:
        logger.exception("Tutor turn %s could not be completed", turn.turn_id)
        await _broadcast([{"type": "tutor_error", "ts": _timestamp(), "turn_id": turn.turn_id,
                           "message": f"The Tutor's reply couldn't be saved: {exc}"}], None)


async def _ticker():
    while True:
        await asyncio.sleep(1)
        try:
            await _broadcast(_coordinator.tick(), None)
        except Exception as exc:
            logger.exception("Session timer failed")
            for client in tuple(_clients):
                try:
                    await client.send(json.dumps({"type": "error", "ts": _timestamp(), "message": f"Session timer failed: {exc}"}))
                except Exception:
                    logger.exception("Could not report session timer failure to client")


@asynccontextmanager
async def serve_companion(port=8765, models=None):
    """Create the loopback-only WebSocket server for use in main and tests. `models` is the ModelClient for the
    Tutor; by default one is made on the first Tutor request, so a missing key fails that request loudly."""
    global _coordinator, _models
    _coordinator = SessionCoordinator(store_path(), lambda: datetime.now(timezone.utc))
    _models = models
    async with serve(_handle, "127.0.0.1", port, origins=[ALLOWED_ORIGIN]) as server:
        tick_task = asyncio.create_task(_ticker())
        try:
            yield server
        finally:
            for task in tuple(_turns):
                task.cancel()
            await asyncio.gather(*_turns, return_exceptions=True)
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass
            _coordinator = None
