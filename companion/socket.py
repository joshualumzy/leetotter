"""Local extension transport and typed import requests."""

import json
import logging
import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from companion.catalog import load_catalog, normalize_link
from companion.grading import grade_pending
from companion.history import import_history, import_status
from companion.model import ModelClient
from companion.planner import Planner, ask_planner
from companion.reference import write_and_check
from companion.reference_review import apply_verdict, flag_tutor_message, review_state
from companion.reflector import pending_sessions, recap_state, recaps_state, reflect_feedback, reflect_session, submit_feedback
from companion.session import SessionCoordinator
from companion.store import open_store, reset_progress, store_path
from companion.tutor import ask_tutor, failure


EXTENSION_ID = "oohbhndckkkkdngkglilcbpcoekapmgp"
ALLOWED_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
READER_TYPES = frozenset({"problem_opened", "problem_statement", "code_changed", "code_snapshot", "run_result", "submit_result", "focus_changed", "reader_check"})
SESSION_TYPES = frozenset({"session_start", "session_pause", "session_resume", "session_end", "session_time_up", "session_done", "session_skip", "session_get_state", "tab_closed"})
# Tutor messages. Client: help_request (a press of "I need help", no payload) and chat_message {text}.
# Server, each with the turn's id: tutor_start {request, press_index}; tutor_chunk {attempt, text}, reply text
# as it streams (append chunks of the same attempt; a higher attempt is a retry after an invalid reply and
# replaces what was shown); tutor_done {reply_text, gap, gap_note, help_kind, follow_up_question}, the
# validated reply, which replaces the streamed text; tutor_error {message}, the turn failed and anything
# streamed for it is withdrawn. A refused request gets tutor_error with turn_id null, to the sender only.
# chat_get (no payload) answers the sender only with chat_state {turns, streaming}: the current problem's
# conversation in this session, from the store, oldest first, empty outside an active or paused session on a problem; each turn is
#   {turn_id, kind: "press", press_index} | {turn_id, kind: "chat", text}
#   | {turn_id, kind: "reply", text, gap, gap_note, help_kind} | {turn_id, kind: "failed", reason, message}
# and streaming is the id of the turn whose reply is still coming, or null.
TUTOR_TYPES = frozenset({"help_request", "chat_message"})
PLANNER_TYPES = frozenset({"planner_go", "planner_swap", "planner_clear", "planner_chat"})
MESSAGE_TYPES = frozenset({"hello", "ping", "status", "error", "get_import_status", "import_status", "import_history", "import_result", "session_state", "settings_get", "settings_set", "settings_state", "reset_progress", "reset_done", "tutor_start", "tutor_chunk", "tutor_done", "tutor_error", "chat_get", "chat_state", "recap_get", "recap_state", "recaps_get", "recaps_state", "recap_feedback", "recap_noted", "recap_seen", "suggestion", "planner_get", "planner_open"}) | READER_TYPES | SESSION_TYPES | TUTOR_TYPES | PLANNER_TYPES
REFERENCE_TYPES = frozenset({"reference_get", "reference_flag", "reference_apply"})
MESSAGE_TYPES |= REFERENCE_TYPES | {"reference_state", "reference_flagged", "reference_verdict"}
CHAT_LIMIT = 4000  # characters; a few minutes of dictation
logger = logging.getLogger(__name__)
_coordinator = None
_planner = None
_planner_tasks = set()
_models = None
_clients = set()
_turns = set()
_grading_tasks = set()
_reference_tasks = {}
_reference_statements = {}
_grading_lock = None
_reflection_tasks = set()
_reflection_lock = None
_model_lock = threading.Lock()


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _get_models():
    global _models
    with _model_lock:
        if _models is None:
            _models = ModelClient()
        return _models


def _settings_state():
    with open_store() as db:
        values = dict(db.execute("SELECT key, value FROM settings WHERE key IN ('name', 'theme')"))
    return {"type": "settings_state", "ts": _timestamp(), "name": values.get("name"), "theme": values.get("theme", "match")}


def _recaps_message():
    return {"ts": _timestamp(), **recaps_state(store_path())}


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
        payload = message.get("payload", {})
        if not isinstance(payload, dict) or payload:
            raise ValueError("Invalid session_start payload")
    if kind == "session_time_up":
        payload = message.get("payload")
        if not isinstance(payload, dict) or payload.get("choice") not in ("extend", "debrief"):
            raise ValueError("Invalid session_time_up payload")
    if kind in ("tab_closed", "help_request", "chat_get") and "payload" in message:
        raise ValueError(f"Invalid {kind} payload")
    if kind == "chat_message":
        payload = message.get("payload")
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > CHAT_LIMIT:
            raise ValueError(f"Invalid chat_message payload: text must be 1 to {CHAT_LIMIT} characters")
    if kind == "reference_get" and "payload" in message:
        raise ValueError("Invalid reference_get payload")
    if kind == "reference_flag":
        payload = message.get("payload")
        if (not isinstance(payload, dict) or type(payload.get("message_id")) is not int or
                payload["message_id"] <= 0 or not isinstance(payload.get("line", ""), str) or
                "\n" in payload.get("line", "") or len(payload.get("line", "")) > 500):
            raise ValueError("Invalid reference_flag payload")
    if kind == "reference_apply":
        payload = message.get("payload")
        if (not isinstance(payload, dict) or not _nonempty(payload.get("slug")) or
                not _nonempty(payload.get("reply"))):
            raise ValueError("Invalid reference_apply payload")
    if kind == "planner_chat":
        payload = message.get("payload")
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > CHAT_LIMIT:
            raise ValueError(f"Invalid planner_chat payload: text must be 1 to {CHAT_LIMIT} characters")
    if kind in {"planner_go", "planner_swap", "planner_clear", "planner_get", "session_done", "session_skip"} and "payload" in message:
        raise ValueError(f"Invalid {kind} payload")
    if kind in ("recap_get", "recap_feedback", "recap_seen"):
        payload = message.get("payload")
        if not isinstance(payload, dict) or type(payload.get("session_id")) is not int or payload["session_id"] <= 0:
            raise ValueError(f"Invalid {kind} payload")
        if kind == "recap_feedback" and (not isinstance(payload.get("text"), str) or not payload["text"].strip()):
            raise ValueError("Invalid recap_feedback text")
    if kind == "recaps_get" and "payload" in message:
        raise ValueError("Invalid recaps_get payload")
    if kind == "reset_progress":
        payload = message.get("payload")
        if not isinstance(payload, dict) or payload.get("confirm") != "RESET":
            raise ValueError("Invalid reset_progress payload: type RESET to confirm")
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
                if kind not in {"hello", "ping", "get_import_status", "import_history", "settings_get", "settings_set", "reset_progress", "chat_get", "recap_get", "recaps_get", "recap_feedback", "recap_seen", "planner_get"} | READER_TYPES | SESSION_TYPES | TUTOR_TYPES | PLANNER_TYPES | REFERENCE_TYPES:
                    raise ValueError(f"Unexpected client message type: {kind}")
                if kind == "hello":
                    await connection.send(json.dumps({"type": "status", "ts": _timestamp(), "state": "connected"}))
                    await _broadcast(_coordinator.on_connect(), connection)
                    for session_id in pending_sessions(store_path()):
                        await connection.send(json.dumps(recap_state(store_path(), session_id)))
                    with open_store() as db:
                        ready = db.execute("SELECT id FROM sessions WHERE recap_bullets IS NOT NULL AND recap_seen = 0 "
                                           "ORDER BY id DESC LIMIT 1").fetchone()
                    if ready:
                        await connection.send(json.dumps(recap_state(store_path(), ready[0])))
                    if _planner is not None:
                        request = _planner.panel_opened(_coordinator.state)
                        if request is None and _coordinator.state == "active" and _coordinator.problem is None:
                            request = _planner.resume(_coordinator.session_id)
                        if request:
                            _schedule_planner(request)
                        elif _planner.card and _coordinator.state != "paused":
                            await connection.send(json.dumps(_planner.card))
                elif kind == "planner_get":
                    if _planner is not None:
                        request = _planner.panel_opened(_coordinator.state)
                        if request is None and _coordinator.state == "active" and _coordinator.problem is None:
                            request = _planner.resume(_coordinator.session_id)
                        if request:
                            _schedule_planner(request)
                        elif _planner.card and _coordinator.state != "paused":
                            await connection.send(json.dumps(_planner.card))
                elif kind in PLANNER_TYPES:
                    if _planner is None:
                        raise ValueError("Planner unavailable")
                    if kind == "planner_go":
                        if _coordinator.state not in ("idle", "ended", "active") or _coordinator.problem:
                            raise ValueError("No suggestion can be opened now")
                        problem = _planner.go()
                        await connection.send(json.dumps({"type": "planner_open", "ts": _timestamp(), **problem}))
                    elif kind == "planner_swap":
                        _schedule_planner(_planner.swap(_coordinator.session_id))
                    elif kind == "planner_clear":
                        _schedule_planner(_planner.clear_steer(_coordinator.session_id))
                    else:
                        request = _planner.chat(_coordinator.session_id, message["payload"]["text"].strip())
                        if request is None:
                            raise ValueError("Planner chat requires a suggestion card")
                        _schedule_planner(request)
                elif kind == "get_import_status":
                    await connection.send(json.dumps({"type": "import_status", "ts": _timestamp(), "imported": import_status()}))
                elif kind == "reset_progress":
                    if _coordinator.state in ("active", "paused"):
                        _coordinator.handle({"type": "session_end", "ts": _timestamp()})
                    await _cancel_background_tasks()
                    reset_progress(store_path())
                    _reference_statements.clear()
                    _coordinator.reset()
                    if _planner is not None:
                        _planner.session_ended()
                    await _broadcast([{"type": "reset_done", "ts": _timestamp()}, _settings_state()], None)
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
                elif kind == "chat_get":
                    await connection.send(json.dumps(_coordinator.chat_state()))
                elif kind == "reference_get":
                    await connection.send(json.dumps({"type": "reference_state", "ts": _timestamp(), **review_state(store_path())}))
                elif kind == "reference_flag":
                    note_id = flag_tutor_message(store_path(), message["payload"]["message_id"],
                                                 message["payload"].get("line", ""))
                    await connection.send(json.dumps({"type": "reference_flagged", "ts": _timestamp(),
                                                      "message_id": message["payload"]["message_id"], "note_id": note_id}))
                    await _broadcast([{"type": "reference_state", "ts": _timestamp(), **review_state(store_path())}], None)
                elif kind == "reference_apply":
                    slug = message["payload"]["slug"]
                    try:
                        result = apply_verdict(store_path(), slug, message["payload"]["reply"])
                    except ValueError as error:
                        result = {"accepted": False, "reason": str(error), "graded": []}
                    else:
                        outcome = await _run_grading("initial", only_slug=slug)
                        if outcome is not None:
                            result["graded"] = outcome.graded
                        await _broadcast([{"type": "reference_state", "ts": _timestamp(), **review_state(store_path())}], None)
                    await connection.send(json.dumps({"type": "reference_verdict", "ts": _timestamp(),
                                                      "slug": slug, **result}))
                elif kind == "recaps_get":
                    await connection.send(json.dumps(_recaps_message()))
                elif kind == "recap_get":
                    await connection.send(json.dumps(recap_state(store_path(), message["payload"]["session_id"])))
                elif kind == "recap_feedback":
                    session_id = message["payload"]["session_id"]
                    note_id = submit_feedback(None, store_path(), session_id, message["payload"]["text"])
                    await connection.send(json.dumps({"type": "recap_noted", "session_id": session_id, "note_id": note_id,
                                                      "message": "Noted. For a grade correction, use Progress."}))
                    _schedule_feedback(session_id, note_id)
                elif kind == "recap_seen":
                    session_id = message["payload"]["session_id"]
                    with open_store() as db:
                        ended = db.execute("SELECT ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
                        if ended is None or ended[0] is None:
                            raise ValueError("No ended session for recap")
                        db.execute("UPDATE sessions SET recap_seen = 1 WHERE id = ?", (session_id,))
                    await _broadcast([_recaps_message()], None)
                elif kind in TUTOR_TYPES:
                    text = message["payload"]["text"].strip() if kind == "chat_message" else None
                    outgoing, turn = _coordinator.begin_turn("chat" if kind == "chat_message" else "help", text)
                    if turn is None:
                        await _broadcast(outgoing, connection)
                    else:
                        task = asyncio.create_task(_run_turn(turn, outgoing))
                        _turns.add(task)
                        task.add_done_callback(_turns.discard)
                elif kind != "ping":
                    before_state = _coordinator.state
                    outgoing = _coordinator.handle(message)
                    await _broadcast(outgoing, connection, kind != "session_get_state")
                    if not any(item["type"] == "error" for item in outgoing):
                        if kind == "session_time_up" and message["payload"]["choice"] == "debrief":
                            started, turn = _coordinator.begin_turn("debrief")
                            if turn is None:
                                await _broadcast(started, connection)
                            else:
                                task = asyncio.create_task(_run_turn(turn, started))
                                _turns.add(task)
                                task.add_done_callback(_turns.discard)
                        if _planner is not None:
                            if kind == "session_start":
                                request = _planner.session_started(_coordinator.session_id)
                                if request:
                                    _schedule_planner(request)
                            elif kind in ("session_done", "session_skip"):
                                _schedule_planner(_planner.problem_left(_coordinator.session_id))
                                _schedule_grading("next_problem")
                            elif kind == "problem_opened" and _coordinator.problem is not None:
                                _planner.session_ended()
                            elif kind == "session_end":
                                _planner.session_ended()
                        if kind in ("session_start", "session_end"):
                            _reference_statements.clear()
                        if kind == "problem_opened":
                            _schedule_grading("next_problem")
                        elif kind == "problem_statement":
                            if _coordinator.problem and _coordinator.problem["slug"] == message["payload"]["slug"]:
                                _reference_statements[message["payload"]["slug"]] = message["payload"]["statement"]
                            _schedule_reference(message["payload"])
                        if before_state == "paused" and _coordinator.state == "active":
                            _retry_current_reference()
                        elif kind == "session_end":
                            _schedule_grading("initial")
                            await _broadcast([recap_state(store_path(), _coordinator.session_id)], connection)
                            _schedule_reflection(_coordinator.session_id)
            except Exception as exc:
                logger.exception("WebSocket request failed: %s", exc)
                await connection.send(json.dumps({"type": "error", "ts": _timestamp(), "message": str(exc)}))
    finally:
        _clients.discard(connection)
        if not _clients and _coordinator is not None:
            await _broadcast(_coordinator.on_disconnect(), connection)


async def _cancel_background_tasks():
    tasks = set(_turns) | set(_planner_tasks) | set(_grading_tasks) | set(_reflection_tasks) | set(_reference_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _turns.clear()
    _planner_tasks.clear()
    _grading_tasks.clear()
    _reflection_tasks.clear()
    _reference_tasks.clear()


async def _broadcast(outgoing, sender, broadcast=True):
    for message in outgoing:
        if message["type"] == "error":
            logger.error("Session request rejected: %s", message["message"])
            message = {**message, "ts": _timestamp()}
        turn_message = message["type"].startswith("tutor_") and message["turn_id"] is not None
        targets = tuple(_clients) if sender is None or turn_message or (broadcast and message["type"] in ("session_state", "settings_state", "recap_state", "suggestion")) else (sender,)
        for target in targets:
            try:
                await target.send(json.dumps(message))
            except ConnectionClosed:
                logger.warning("Connection closed before %s could be delivered", message["type"])


async def _run_turn(turn, started):
    """Announce a turn (`started`), run its Tutor call in a worker thread, so the event loop (the timer, other
    clients) never waits on it, and relay its streamed text to every panel in order. Whatever goes wrong, the turn
    ends logged, done or failed, so it never blocks later requests."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def post(item):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass  # the loop has closed: the companion stopped, and the turn was logged as failed (shutdown)

    def work():
        try:
            models = _get_models()
            reply = ask_tutor(models, turn.messages, turn.schema, lambda attempt, text: post(("chunk", attempt, text)))
            post(("done", reply, models.config.roles["tutor"][0]))
        except Exception as exc:
            post(("failed", exc))

    try:
        await _broadcast(started, None)
        loop.run_in_executor(None, work)
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
        message = f"The Tutor's turn failed: {exc}"
        try:
            outgoing = _coordinator.fail_turn(turn, "error", message)
        except Exception:
            logger.exception("Could not log Tutor turn %s as failed", turn.turn_id)
            outgoing = [{"type": "tutor_error", "ts": _timestamp(), "turn_id": turn.turn_id, "message": message}]
        try:
            await _broadcast(outgoing, None)
        except Exception:
            logger.exception("Could not report Tutor turn %s failure", turn.turn_id)


async def _ticker():
    while True:
        await asyncio.sleep(1)
        try:
            await _broadcast(_coordinator.tick(), None)
            if _planner is not None:
                await _broadcast(_planner.tick(), None)
        except Exception as exc:
            logger.exception("Session timer failed")
            for client in tuple(_clients):
                try:
                    await client.send(json.dumps({"type": "error", "ts": _timestamp(), "message": f"Session timer failed: {exc}"}))
                except Exception:
                    logger.exception("Could not report session timer failure to client")


def _schedule_planner(request):
    task = asyncio.create_task(_run_planner(request))
    _planner_tasks.add(task)
    task.add_done_callback(_planner_tasks.discard)


async def _run_planner(request):
    try:
        reply = await asyncio.to_thread(ask_planner, _get_models(), request)
        await _broadcast(_planner.answer(request, reply), None)
    except Exception as error:
        logger.exception("Planner request failed")
        await _broadcast(_planner.fail(request, error), None)


def _schedule_grading(trigger="initial"):
    task = asyncio.create_task(_run_grading(trigger))
    _grading_tasks.add(task)
    task.add_done_callback(_grading_tasks.discard)


def _schedule_reference(payload):
    slug = payload["slug"]
    if (_coordinator is None or _coordinator.state != "active" or
            _coordinator.problem is None or _coordinator.problem["slug"] != slug):
        return
    problem = load_catalog().by_slug(slug)
    if problem is None or problem["neetcode150"] or problem["locked"] or slug in _reference_tasks:
        return
    with open_store() as db:
        stored = db.execute('SELECT status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
    if stored is not None:
        return
    task = asyncio.create_task(_run_reference(problem, payload["statement"]))
    _reference_tasks[slug] = task
    task.add_done_callback(lambda _: _reference_tasks.pop(slug, None))


def _retry_current_reference():
    if _coordinator is None or _coordinator.problem is None:
        return
    slug = _coordinator.problem["slug"]
    statement = _reference_statements.get(slug)
    if statement is None:
        with open_store() as db:
            rows = db.execute("SELECT payload FROM events WHERE session_id = ? AND type = 'problem_statement' ORDER BY id DESC",
                              (_coordinator.session_id,))
            statement = next((payload["statement"] for (raw,) in rows
                              if (payload := json.loads(raw))["slug"] == slug), None)
    if statement:
        _schedule_reference({"slug": slug, "statement": statement})


async def _run_reference(problem, statement):
    slug = problem["slug"]
    checked = False
    try:
        result = await asyncio.to_thread(write_and_check, slug, problem["title"], _get_models(),
                                         db_path=store_path(), statement=statement, grade_waiting=False)
        if result.status == "checked":
            checked = True
            await _run_grading("initial")
        await _broadcast([{"type": "reference_state", "ts": _timestamp(), **review_state(store_path())}], None)
        if _coordinator is not None and _coordinator.problem and _coordinator.problem["slug"] == slug:
            await _broadcast([_coordinator._state(_coordinator.clock())], None)
    except Exception as error:
        logger.exception("On-demand %s failed for %s", "grading" if checked else "reference", slug)
        await _broadcast([{"type": "error", "ts": _timestamp(),
                           "message": (f"Grading after checking {problem['title']} failed: {error}" if checked else
                                       f"Reference for {problem['title']} is not checked yet: {error}")}], None)


def _schedule_reflection(session_id):
    task = asyncio.create_task(_run_reflection(session_id))
    _reflection_tasks.add(task)
    task.add_done_callback(_reflection_tasks.discard)


async def _run_reflection(session_id):
    async with _reflection_lock:
        try:
            if _grading_tasks:
                await asyncio.gather(*tuple(_grading_tasks), return_exceptions=True)
            state = await asyncio.to_thread(reflect_session, _get_models(), store_path(), session_id)
            await _broadcast([state], None)
        except Exception as error:
            logger.exception("Reflector failed for session %s", session_id)
            with open_store() as db:
                if db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                    return
                db.execute("INSERT INTO reflector_failures (session_id, message) VALUES (?, ?) "
                           "ON CONFLICT(session_id) DO UPDATE SET message = excluded.message",
                           (session_id, f"Session review is missing: {error}"))
            await _broadcast([recap_state(store_path(), session_id)], None)


def _schedule_feedback(session_id, note_id):
    task = asyncio.create_task(_run_feedback(session_id, note_id))
    _reflection_tasks.add(task)
    task.add_done_callback(_reflection_tasks.discard)


async def _run_feedback(session_id, note_id):
    async with _reflection_lock:
        try:
            state = await asyncio.to_thread(reflect_feedback, _get_models(), store_path(), session_id, note_id)
            await _broadcast([state], None)
        except Exception as error:
            logger.exception("Reflector feedback failed for note %s", note_id)
            message = f"Feedback review failed; your note was saved: {error}"
            with open_store() as db:
                if db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                    return
                db.execute("INSERT INTO recap_feedback_failures (session_id, message) VALUES (?, ?) "
                           "ON CONFLICT(session_id) DO UPDATE SET message = excluded.message",
                           (session_id, message))
                bullets = json.loads(db.execute("SELECT recap_bullets FROM sessions WHERE id = ?", (session_id,)).fetchone()[0])
            await _broadcast([{"type": "recap_state", "session_id": session_id, "status": "feedback_failed",
                               "bullets": bullets, "message": message}], None)


async def _run_grading(trigger="initial", only_slug=None):
    async with _grading_lock:
        def work():
            class GraderClient:
                @property
                def config(self):
                    return _get_models().config

                def call_role(self, *args, **kwargs):
                    return _get_models().call_role(*args, **kwargs)

            return grade_pending(GraderClient(), db_path=store_path(), trigger=trigger, only_slug=only_slug)

        try:
            outcome = await asyncio.to_thread(work)
            if outcome.failed:
                for client in tuple(_clients):
                    for attempt_id in outcome.failed:
                        message = outcome.failures.get(attempt_id, "Grading is delayed; an attempt remains ungraded.")
                        await client.send(json.dumps({"type": "error", "ts": _timestamp(), "message": message}))
            return outcome
        except Exception as error:
            logger.exception("Pending grading failed")
            for client in tuple(_clients):
                await client.send(json.dumps({"type": "error", "ts": _timestamp(), "message": f"Grading failed: {error}"}))


@asynccontextmanager
async def serve_companion(port=8765, models=None):
    """Create the loopback-only WebSocket server for use in main and tests. `models` is the ModelClient for the
    Tutor; by default one is made on the first Tutor request, so a missing key fails that request loudly."""
    global _coordinator, _planner, _models, _grading_lock, _reflection_lock
    _coordinator = SessionCoordinator(store_path(), lambda: datetime.now(timezone.utc))
    _planner = Planner(store_path(), lambda: datetime.now(timezone.utc))
    _models = models
    _grading_lock = asyncio.Lock()
    _reflection_lock = asyncio.Lock()
    async with serve(_handle, "127.0.0.1", port, origins=[ALLOWED_ORIGIN]) as server:
        tick_task = asyncio.create_task(_ticker())
        _schedule_grading("next_start")
        for session_id in pending_sessions(store_path()):
            _schedule_reflection(session_id)
        try:
            yield server
        finally:
            for task in tuple(_turns):
                task.cancel()
            await asyncio.gather(*_turns, return_exceptions=True)
            for task in tuple(_planner_tasks):
                task.cancel()
            await asyncio.gather(*_planner_tasks, return_exceptions=True)
            await asyncio.gather(*_grading_tasks, return_exceptions=True)
            await asyncio.gather(*tuple(_reference_tasks.values()), return_exceptions=True)
            await asyncio.gather(*_reflection_tasks, return_exceptions=True)
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass
            _coordinator = None
            _planner = None
