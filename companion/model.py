"""Validated role calls through the configured OpenAI-compatible endpoint."""

import os
import threading
import time
import tomllib
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from companion.store import open_store

ROLES_PATH = Path(__file__).resolve().parent.parent / "roles.toml"
BACKGROUND_ROLES = frozenset({"reference_writer", "checker"})
AFFECTED = {
    "observer": "approach nudges",
    "tutor": "tutoring",
    "reflector": "session reviews",
    "planner": "suggestions",
    "grader": "grading",
    "reference_writer": "reference writing",
    "checker": "reference checking",
}


@dataclass(frozen=True)
class RoleConfig:
    roles: dict
    timeouts: dict
    observer_thinking_extra_body: dict


def load_roles(path: str | Path = ROLES_PATH) -> RoleConfig:
    with open(path, "rb") as file:
        data = tomllib.load(file)
    roles = data["roles"]
    if set(roles) != set(AFFECTED):
        raise ValueError("roles.toml must configure every role exactly once")
    if not isinstance(roles["grader"], list) or len(roles["grader"]) != 2 or roles["grader"][0] == roles["grader"][1]:
        raise ValueError("grader needs two different models")
    return RoleConfig(roles, data["timeouts"], data["provider"]["observer_thinking_extra_body"])


class ModelUnavailable(RuntimeError):
    def __init__(self, model: str, affected_roles: tuple[str, ...]):
        self.model = model
        self.affected_roles = affected_roles
        effects = ", ".join(AFFECTED[role] for role in affected_roles)
        super().__init__(f"{model} isn't responding: {effects} unavailable")


class RateLimiter:
    """Shared sliding-window admission with live callers ahead of background callers."""

    def __init__(self, limit=90, period=60.0, *, clock=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.period = period
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._times = deque()
        self._live_waiters = 0

    def acquire(self, *, background=False):
        if not background:
            with self._lock:
                self._live_waiters += 1
        try:
            while True:
                with self._lock:
                    now = self.clock()
                    while self._times and now - self._times[0] >= self.period:
                        self._times.popleft()
                    if len(self._times) < self.limit and (not background or self._live_waiters == 0):
                        self._times.append(now)
                        return
                    delay = max(0.001, self.period - (now - self._times[0])) if self._times and len(self._times) >= self.limit else 0.05
                self.sleep(min(delay, 0.05) if background else delay)
        finally:
            if not background:
                with self._lock:
                    self._live_waiters -= 1


RATE_LIMITER = RateLimiter()
_FAILURE_LOCK = threading.Lock()
_CONSECUTIVE_FAILURES: dict[str, int] = {}


class TutorReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reply_text: str = Field(min_length=1)
    gap: Literal[
        "misread_problem", "missing_tool", "weak_intuition", "wrong_intuition",
        "stuck_optimising", "cant_write_code", "bugs_and_edge_cases",
    ]
    help_kind: Literal[
        "question", "trace", "analogy", "teach_tool", "syntax", "snippet",
        "partial_logic", "full_solution",
    ]
    follow_up_question: str | None


def _credentials():
    base_url = os.environ.get("NUS_SOCLAAS_BASE_URL")
    api_key = os.environ.get("NUS_SOCLAAS_API_KEY")
    if not base_url:
        raise RuntimeError("NUS_SOCLAAS_BASE_URL is required")
    if not api_key:
        raise RuntimeError("NUS_SOCLAAS_API_KEY is required")
    return base_url, api_key


def _record(path, role, model, latency_ms, usage, result):
    with open_store(path) as db:
        db.execute(
            "INSERT INTO model_calls (role, model, latency_ms, prompt_tokens, completion_tokens, validation_result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (role, model, latency_ms,
             getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None),
             result, datetime.now(timezone.utc).isoformat()),
        )


def _affected(config, model):
    return tuple(role for role, configured in config.roles.items()
                 if model in (configured if isinstance(configured, list) else [configured]))


def _model_not_found(error):
    code = getattr(error, "code", None)
    if code is None:
        body = getattr(error, "body", None)
        code = body.get("code") if isinstance(body, dict) else None
    message = str(error).lower()
    return code in {"model_not_found", "model_not_exist"} or "model not found" in message or "model_not_found" in message or " not found" in message or "does not exist" in message


def call_role(role: str, messages: list[dict], schema_class: type[BaseModel], *,
              model: str | None = None, planner_phase: str = "start", db_path: str | Path | None = None,
              client=None, limiter: RateLimiter | None = None, config: RoleConfig | None = None,
              stream: bool = False, on_first_token=None):
    """Call one configured role and validate its JSON; invalid replies get one corrective retry."""
    config = config or load_roles()
    if role not in config.roles:
        raise ValueError(f"Unknown role: {role}")
    configured = config.roles[role]
    if isinstance(configured, list):
        if model is None or model not in configured:
            raise ValueError("grader calls require one configured model")
    elif model is not None and model != configured:
        raise ValueError(f"{model} is not configured for {role}")
    model = model or configured
    if role == "planner" and planner_phase not in {"start", "between"}:
        raise ValueError("planner_phase must be start or between")
    timeout_key = f"planner_{planner_phase}" if role == "planner" else role
    timeout = config.timeouts[timeout_key]
    base_url, api_key = _credentials()
    if client is None:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
    limiter = limiter or RATE_LIMITER
    request_messages = list(messages)
    response_format = {"type": "json_schema", "json_schema": {
        "name": schema_class.__name__.lower(), "strict": True, "schema": schema_class.model_json_schema(),
    }}
    for attempt in range(2):
        limiter.acquire(background=role in BACKGROUND_ROLES)
        kwargs = {"model": model, "messages": request_messages, "response_format": response_format, "timeout": timeout}
        if role == "observer":
            kwargs["extra_body"] = config.observer_thinking_extra_body
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**kwargs)
            if stream:
                pieces = []
                usage = None
                for chunk in response:
                    usage = getattr(chunk, "usage", None) or usage
                    if chunk.choices:
                        piece = chunk.choices[0].delta.content or ""
                        if piece and not pieces and on_first_token is not None:
                            on_first_token(round((time.perf_counter() - started) * 1000))
                        pieces.append(piece)
                content = "".join(pieces)
            else:
                usage = response.usage
                content = response.choices[0].message.content or ""
        except Exception as error:
            latency_ms = round((time.perf_counter() - started) * 1000)
            with _FAILURE_LOCK:
                failures = _CONSECUTIVE_FAILURES.get(model, 0) + 1
                _CONSECUTIVE_FAILURES[model] = failures
            unavailable = _model_not_found(error) or failures >= 2
            _record(db_path, role, model, latency_ms, None, "unavailable" if unavailable else "error")
            if unavailable:
                raise ModelUnavailable(model, _affected(config, model)) from error
            raise
        latency_ms = round((time.perf_counter() - started) * 1000)
        with _FAILURE_LOCK:
            _CONSECUTIVE_FAILURES[model] = 0
        try:
            reply = schema_class.model_validate_json(content)
        except ValidationError as error:
            _record(db_path, role, model, latency_ms, usage, "invalid")
            if attempt:
                raise ValueError(f"{role} reply invalid after retry: {error}") from error
            request_messages = [*request_messages,
                                {"role": "assistant", "content": content},
                                {"role": "user", "content": f"Your previous reply failed schema validation. Error: {error}. Return corrected JSON only."}]
            continue
        _record(db_path, role, model, latency_ms, usage, "valid")
        return reply
