"""Validated role calls through the configured OpenAI-compatible endpoint."""

import logging
import os
import threading
import time
import tomllib
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from companion.store import open_store

logger = logging.getLogger(__name__)
TRANSIENT_RETRY_DELAY = 1.5  # seconds before the one retry of a call that got no answer
ROLES_PATH = Path(__file__).resolve().parent.parent / "roles.toml"
BACKGROUND_ROLES = frozenset({"reference_writer", "checker"})
ROLE_EFFECTS = (
    ("observer", "approach nudges"), ("tutor", "tutoring"),
    ("reflector", "session reviews"), ("planner", "suggestions"),
    ("grader", "grading"), ("reference_writer", "reference writing"),
    ("checker", "reference checking"),
)


@dataclass(frozen=True)
class RoleConfig:
    roles: dict[str, list[str]]
    timeouts: dict[str, int]
    streaming: dict[str, bool]
    display_names: dict[str, str]
    observer_thinking_extra_body: dict


def load_roles(path: str | Path = ROLES_PATH) -> RoleConfig:
    with open(path, "rb") as file:
        data = tomllib.load(file)
    roles = {role: models if isinstance(models, list) else [models]
             for role, models in data["roles"].items()}
    expected = {role for role, _ in ROLE_EFFECTS}
    if set(roles) != expected:
        raise ValueError("roles.toml must configure every role exactly once")
    if any(not models or any(not isinstance(model, str) or not model for model in models)
           for models in roles.values()):
        raise ValueError("every role needs a model")
    if len(roles["grader"]) != 2 or len(set(roles["grader"])) != 2:
        raise ValueError("grader needs two different models")
    timeouts = data.get("timeouts", {})
    for role in roles:
        for key in (("planner_start", "planner_between") if role == "planner" else (role,)):
            if not isinstance(timeouts.get(key), (int, float)) or timeouts[key] <= 0:
                raise ValueError(f"missing or invalid timeout: {key}")
    names = data.get("display_names", {})
    for model in {m for models in roles.values() for m in models}:
        if not names.get(model):
            raise ValueError(f"missing display_name for model {model}")
    return RoleConfig(roles, timeouts, data.get("streaming", {}), names,
                      data["provider"]["observer_thinking_extra_body"])


class ModelUnavailable(RuntimeError):
    def __init__(self, model: str, display_name: str, affected_roles: tuple[str, ...]):
        self.model = model
        self.affected_roles = affected_roles
        effects = ", ".join(effect for role, effect in ROLE_EFFECTS if role in affected_roles and role != "grader")
        if "grader" in affected_roles:
            grading = "grading continues with one grader if the other is up"
            effects = f"{effects} unavailable; {grading}" if effects else grading
        else:
            effects = f"{effects} unavailable"
        super().__init__(f"{display_name} isn't responding: {effects}")


class ProviderRateLimitError(RuntimeError):
    pass


class ProviderAuthenticationError(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
    pass


class RateLimiter:
    """Sliding-window admission; background calls can fill at most 80 of 90 slots."""

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
                    while self._times and now - self._times[0][0] >= self.period:
                        self._times.popleft()
                    background_times = [when for when, was_background in self._times if was_background]
                    background_limit = max(1, self.limit - 10)
                    full = len(self._times) >= self.limit
                    background_full = background and len(background_times) >= background_limit
                    live_waiting = background and self._live_waiters > 0
                    if not (full or background_full or live_waiting):
                        self._times.append((now, background))
                        return
                    if full:
                        delay = self._times[0][0] + self.period - now
                    elif background_full:
                        delay = background_times[0] + self.period - now
                    else:
                        delay = 0.05
                self.sleep(delay)
        finally:
            if not background:
                with self._lock:
                    self._live_waiters -= 1


GAPS = ("misread_problem", "missing_tool", "pattern_not_recognised", "weak_intuition", "wrong_intuition",
        "stuck_optimising", "cant_write_code", "bugs_and_edge_cases", "other", "none")
HELP_KINDS = ("question", "trace", "analogy", "teach_tool", "syntax", "snippet", "partial_logic", "full_solution", "none")


# One Tutor turn. The labels come first so the model diagnoses before it writes; `none` marks a reply
# that gives no help (a remark answered), and `gap_note` names an `other` gap in the Tutor's own words.
class TutorReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gap: Literal[GAPS]
    gap_note: str | None
    help_kind: Literal[HELP_KINDS]
    reply_text: str = Field(min_length=1)
    follow_up_question: str | None

    @model_validator(mode="after")
    def _consistent_labels(self):
        if (self.gap == "none") != (self.help_kind == "none"):
            raise ValueError("gap and help_kind must both be none (no help given) or both name what was given")
        if self.gap == "other" and not (self.gap_note or "").strip():
            raise ValueError("gap_note must describe the gap when gap is other")
        if self.gap != "other" and self.gap_note is not None:
            raise ValueError("gap_note must be null unless gap is other")
        return self


def _credentials():
    base_url = os.environ.get("NUS_SOCLAAS_BASE_URL")
    api_key = os.environ.get("NUS_SOCLAAS_API_KEY")
    if not base_url:
        raise RuntimeError("NUS_SOCLAAS_BASE_URL is required")
    if not api_key:
        raise RuntimeError("NUS_SOCLAAS_API_KEY is required")
    return base_url, api_key


def _status(error):
    return getattr(error, "status_code", None)


def _model_not_found(error, model):
    """A 404 or `model_not_found` code that names this model; a bare 404 is a wrong route, not a missing model."""
    body = getattr(error, "body", None)
    provider_error = body.get("error", body) if isinstance(body, dict) else None
    code = getattr(error, "code", None) or (provider_error.get("code") if isinstance(provider_error, dict) else None)
    names_model = model.lower() in str(body or error).lower()
    return names_model and (_status(error) == 404 or code == "model_not_found")


class ModelClient:
    def __init__(self, *, config: RoleConfig | None = None, limiter: RateLimiter | None = None,
                 db_path: str | Path | None = None, client=None):
        self.config = config or load_roles()
        self.limiter = limiter or RateLimiter()
        self.db_path = db_path
        base_url, api_key = _credentials()
        self.client = client or OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}

    def _record(self, role, model, latency_ms, usage, result):
        with open_store(self.db_path) as db:
            db.execute(
                "INSERT INTO model_calls (role, model, latency_ms, prompt_tokens, completion_tokens, validation_result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (role, model, latency_ms, getattr(usage, "prompt_tokens", None),
                 getattr(usage, "completion_tokens", None), result, datetime.now(timezone.utc).isoformat()),
            )

    def _unavailable(self, model):
        affected = tuple(role for role, models in self.config.roles.items() if model in models)
        return ModelUnavailable(model, self.config.display_names[model], affected)

    def call_role(self, role: str, messages: list[dict], schema_class: type[BaseModel], *,
                  model: str | None = None, planner_phase: Literal["start", "between"] = "start",
                  on_first_token=None, on_response=None, on_delta=None, response_mode: Literal["json_schema", "json_object", "plain"] = "json_schema",
                  thinking_off: bool = True):
        if role not in self.config.roles:
            raise ValueError(f"Unknown role: {role}")
        if role == "planner" and planner_phase not in ("start", "between"):
            raise ValueError("planner_phase must be start or between")
        configured = self.config.roles[role]
        if model is None and len(configured) != 1:
            raise ValueError("grader calls require one configured model")
        model = model or configured[0]
        if model not in configured:
            raise ValueError(f"{model} is not configured for {role}")
        timeout = self.config.timeouts[f"planner_{planner_phase}" if role == "planner" else role]
        request_messages = list(messages)
        retried = False  # one transient retry per call, however many validation attempts there are
        for attempt in range(2):
            self.limiter.acquire(background=role in BACKGROUND_ROLES)
            kwargs = {"model": model, "messages": request_messages, "timeout": timeout}
            if response_mode == "json_schema":
                kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema_class.__name__.lower(), "strict": True, "schema": schema_class.model_json_schema()}}
            elif response_mode == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            if role == "observer" and thinking_off:
                kwargs["extra_body"] = self.config.observer_thinking_extra_body
            streamed = self.config.streaming.get(role, False)
            if streamed:
                kwargs.update(stream=True, stream_options={"include_usage": True})
            while True:
                started = time.perf_counter()
                pieces = []
                try:
                    response = self.client.chat.completions.create(**kwargs)
                    if streamed:
                        usage = None
                        for chunk in response:
                            if on_response:
                                on_response(chunk)
                            usage = getattr(chunk, "usage", None) or usage
                            for choice in getattr(chunk, "choices", ()):
                                piece = getattr(choice.delta, "content", None)
                                if not piece:
                                    continue
                                if not pieces and on_first_token:
                                    on_first_token(round((time.perf_counter() - started) * 1000))
                                pieces.append(piece)
                                if on_delta:
                                    on_delta(attempt, piece)
                        content = "".join(pieces)
                    else:
                        if on_response:
                            on_response(response)
                        usage = response.usage
                        content = response.choices[0].message.content or ""
                    break
                except Exception as error:
                    latency_ms = round((time.perf_counter() - started) * 1000)
                    status = _status(error)
                    # One quick retry when the request never got an answer (connection reset, a 5xx): nothing was sent twice
                    # and no word was streamed. Only the second failure counts and is reported.
                    if (not retried and not (streamed and pieces) and not isinstance(error, (TimeoutError, APITimeoutError))
                            and (isinstance(error, (ConnectionError, APIConnectionError)) or (status is not None and status >= 500))):
                        retried = True
                        logger.warning("%s call to %s failed (%s); retrying once", role, model, error)
                        time.sleep(TRANSIENT_RETRY_DELAY)
                        self.limiter.acquire(background=role in BACKGROUND_ROLES)
                        continue
                    eligible = isinstance(error, (TimeoutError, ConnectionError, APITimeoutError, APIConnectionError)) or (status is not None and status >= 500) or _model_not_found(error, model)
                    with self._lock:
                        if eligible:
                            self._failures[model] = self._failures.get(model, 0) + 1
                        unavailable = _model_not_found(error, model) or (eligible and self._failures[model] >= 2)
                    self._record(role, model, latency_ms, None, "unavailable" if unavailable else "error")
                    if unavailable:
                        raise self._unavailable(model) from error
                    if status == 429:
                        raise ProviderRateLimitError("Provider rate limit (HTTP 429)") from error
                    if status in (401, 403):
                        raise ProviderAuthenticationError(f"Provider authentication failed (HTTP {status})") from error
                    if status is not None and 400 <= status < 500:
                        raise ProviderRequestError(f"Provider request failed (HTTP {status})") from error
                    raise
            latency_ms = round((time.perf_counter() - started) * 1000)
            with self._lock:
                self._failures[model] = 0
            try:
                reply = schema_class.model_validate_json(content)
            except ValidationError as error:
                self._record(role, model, latency_ms, usage, "invalid")
                if attempt:
                    raise ValueError(f"{role} reply invalid after retry: {error}") from error
                request_messages = [*request_messages, {"role": "assistant", "content": content},
                                    {"role": "user", "content": f"Your previous reply failed schema validation. Error: {error}. Return corrected JSON only."}]
                continue
            self._record(role, model, latency_ms, usage, "valid")
            return reply
