from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from companion.model import ModelClient, ModelUnavailable, ProviderRequestError, RateLimiter, TutorReply, load_roles
from companion.store import open_store


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str


class FakeCompletions:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        if kwargs.get("stream"):
            if isinstance(result, str):
                result = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=result))],
                                          usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8))]
            return iter(result)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))],
                               usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8))


def fake_client(*replies):
    completions = FakeCompletions(replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def model_client(tmp_path, monkeypatch, *replies):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    client, calls = fake_client(*replies)
    path = tmp_path / "db.sqlite3"
    return ModelClient(client=client, db_path=path), calls, path


def rows(path):
    with open_store(path) as db:
        return db.execute("SELECT role, model, prompt_tokens, completion_tokens, validation_result FROM model_calls ORDER BY id").fetchall()


def message():
    return [{"role": "user", "content": "say ok"}]


def test_every_spec_role_has_model_and_timeout():
    config = load_roles()
    assert set(config.roles) == {"tutor", "grader", "planner", "reflector", "reference_writer", "checker"}
    for role, models in config.roles.items():
        assert isinstance(models, list) and models and all(isinstance(model, str) and model for model in models)
        keys = ("planner_start", "planner_between") if role == "planner" else (role,)
        assert all(config.timeouts[key] > 0 for key in keys)
    assert len(config.roles["grader"]) == 2
    assert config.streaming["tutor"] is True


def test_missing_timeout_is_named_at_load(tmp_path):
    source = __import__("pathlib").Path("roles.toml").read_text().replace("planner_between = 30\n", "")
    path = tmp_path / "roles.toml"
    path.write_text(source)
    with pytest.raises(ValueError, match="planner_between"):
        load_roles(path)


def test_invalid_and_empty_reply_retry_with_error_and_log_every_attempt(tmp_path, monkeypatch):
    for first in ('{"answer":42}', ''):
        instance, calls, path = model_client(tmp_path, monkeypatch, first, '{"answer":"ok"}')
        assert instance.call_role("tutor", message(), Reply) == Reply(answer="ok")
        assert calls.requests[1]["messages"][-2] == {"role": "assistant", "content": first}
        assert "answer" in calls.requests[1]["messages"][-1]["content"] or first == ""
        assert [row[-1] for row in rows(path)][-2:] == ["invalid", "valid"]


def test_final_invalid_reply_is_loud_and_logged(tmp_path, monkeypatch):
    instance, _, path = model_client(tmp_path, monkeypatch, "{}", "{}")
    with pytest.raises(ValueError, match="answer"):
        instance.call_role("tutor", message(), Reply)
    assert [row[-1] for row in rows(path)] == ["invalid", "invalid"]


@pytest.mark.parametrize("data", [
    {"reply_text": "ok", "gap": "bad", "help_kind": "question", "follow_up_question": None},
    {"reply_text": "ok", "gap": "weak_intuition", "help_kind": "question", "follow_up_question": None, "extra": 1},
])
def test_tutor_reply_rejects_wrong_labels_and_extra_fields(data):
    with pytest.raises(ValidationError):
        TutorReply.model_validate(data)


def test_model_not_found_uses_provider_signal_and_display_name(tmp_path, monkeypatch):
    error = RuntimeError("The model x-test-2 does not exist")
    error.status_code = 404
    instance, _, path = model_client(tmp_path, monkeypatch, error)
    with pytest.raises(ModelUnavailable) as caught:
        instance.call_role("tutor", message(), Reply)
    assert caught.value.model == "x-test-2"
    assert "DeepSeek V4.1 Flash" in str(caught.value)
    assert "tutoring" in str(caught.value) and "grading continues with one grader" in str(caught.value)
    assert rows(path)[0][-1] == "unavailable"


def test_bare_404_is_a_wrong_route_not_a_missing_model(tmp_path, monkeypatch):
    error = RuntimeError("no such route")
    error.status_code = 404
    instance, _, path = model_client(tmp_path, monkeypatch, error)
    with pytest.raises(ProviderRequestError, match="HTTP 404"):
        instance.call_role("checker", message(), Reply)
    assert rows(path)[0][-1] == "error"


def test_unrelated_not_found_does_not_disable_model(tmp_path, monkeypatch):
    instance, _, path = model_client(tmp_path, monkeypatch, RuntimeError("route not found"))
    with pytest.raises(RuntimeError, match="route not found"):
        instance.call_role("checker", message(), Reply)
    assert rows(path)[0][-1] == "error"


def test_model_not_found_code_must_name_the_model(tmp_path, monkeypatch):
    error = RuntimeError("another model is unavailable")
    error.code = "model_not_found"
    error.body = {"code": "model_not_found", "message": "other-model missing"}
    instance, _, path = model_client(tmp_path, monkeypatch, error)
    with pytest.raises(RuntimeError, match="another model"):
        instance.call_role("checker", message(), Reply)
    assert rows(path)[0][-1] == "error"


@pytest.mark.parametrize("status, name", [(400, "ProviderRequestError"), (401, "ProviderAuthenticationError"),
                                            (403, "ProviderAuthenticationError"), (429, "ProviderRateLimitError")])
def test_other_4xx_have_named_errors(tmp_path, monkeypatch, status, name):
    error = RuntimeError("provider body")
    error.status_code = status
    instance, _, path = model_client(tmp_path, monkeypatch, error)
    with pytest.raises(Exception) as caught:
        instance.call_role("checker", message(), Reply)
    assert type(caught.value).__name__ == name
    assert rows(path)[0][-1] == "error"


def test_two_consecutive_provider_failures_raise_unavailable(tmp_path, monkeypatch):
    instance, _, path = model_client(tmp_path, monkeypatch, TimeoutError("slow"), TimeoutError("slow again"))
    with pytest.raises(TimeoutError):
        instance.call_role("checker", message(), Reply)
    with pytest.raises(ModelUnavailable) as caught:
        instance.call_role("checker", message(), Reply)
    assert set(caught.value.affected_roles) == {"checker", "grader", "tutor"}
    assert [row[-1] for row in rows(path)] == ["error", "unavailable"]


def test_success_resets_failure_count(tmp_path, monkeypatch):
    instance, _, path = model_client(tmp_path, monkeypatch, TimeoutError("slow"), '{"answer":"ok"}', TimeoutError("slow"))
    with pytest.raises(TimeoutError):
        instance.call_role("checker", message(), Reply)
    instance.call_role("checker", message(), Reply)
    with pytest.raises(TimeoutError):
        instance.call_role("checker", message(), Reply)
    assert [row[-1] for row in rows(path)] == ["error", "valid", "error"]


def test_429_has_named_error_and_does_not_count(tmp_path, monkeypatch):
    error = RuntimeError("secret provider body")
    error.status_code = 429
    instance, _, path = model_client(tmp_path, monkeypatch, TimeoutError("slow"), error, TimeoutError("slow"))
    with pytest.raises(TimeoutError):
        instance.call_role("checker", message(), Reply)
    with pytest.raises(Exception) as caught:
        instance.call_role("checker", message(), Reply)
    assert type(caught.value).__name__ == "ProviderRateLimitError"
    with pytest.raises(ModelUnavailable):
        instance.call_role("checker", message(), Reply)
    assert [row[-1] for row in rows(path)] == ["error", "error", "unavailable"]


def test_role_selection_and_timeout(tmp_path, monkeypatch):
    instance, calls, _ = model_client(tmp_path, monkeypatch, *(['{"answer":"ok"}'] * 2))
    instance.call_role("grader", message(), Reply, model="x-test-2")
    instance.call_role("planner", message(), Reply, planner_phase="between")
    assert calls.requests[0]["model"] == "x-test-2"
    assert calls.requests[1]["timeout"] == 30
    assert "extra_body" not in calls.requests[0]


def test_invalid_role_and_phase_raise_value_error(tmp_path, monkeypatch):
    instance, calls, _ = model_client(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Unknown role"):
        instance.call_role("missing", message(), Reply)
    with pytest.raises(ValueError, match="planner_phase"):
        instance.call_role("planner", message(), Reply, planner_phase="wrong")
    assert calls.requests == []


def test_tutor_streams_and_records_first_token_and_final_usage(tmp_path, monkeypatch):
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=None),
              SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""))], usage=None),
              SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"answer":'))], usage=None),
              SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='"ok"}'))], usage=None),
              SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))]
    instance, calls, path = model_client(tmp_path, monkeypatch, chunks)
    first = []
    assert instance.call_role("tutor", message(), Reply, on_first_token=first.append) == Reply(answer="ok")
    assert calls.requests[0]["stream"] is True
    assert len(first) == 1 and first[0] >= 0
    assert rows(path)[0][2:4] == (3, 2)


def test_rate_limiter_uses_exact_fake_clock_and_reserves_live_slots():
    class Clock:
        now = 0.0
        sleeps = []
        def time(self): return self.now
        def sleep(self, duration):
            self.sleeps.append(duration)
            self.now += duration
    clock = Clock()
    limiter = RateLimiter(clock=clock.time, sleep=clock.sleep)
    for _ in range(80):
        limiter.acquire(background=True)
    limiter.acquire()
    assert clock.now == 0
    limiter.acquire(background=True)
    assert clock.sleeps == [60]
    assert clock.now == 60


def test_live_waiter_is_admitted_before_background_waiter():
    class Clock:
        now = 0.0
        def time(self): return self.now
    clock = Clock()
    order = []
    def sleep(duration):
        clock.now += duration
        if not order:
            order.append("background waiting")
            limiter.acquire()
            order.append("live admitted")
    limiter = RateLimiter(limit=1, period=60, clock=clock.time, sleep=sleep)
    limiter.acquire()
    limiter.acquire(background=True)
    assert order == ["background waiting", "live admitted"]


@pytest.mark.parametrize("missing", ["NUS_SOCLAAS_BASE_URL", "NUS_SOCLAAS_API_KEY"])
def test_missing_credentials_fail_before_provider_or_store(tmp_path, monkeypatch, missing):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    monkeypatch.delenv(missing)
    client, calls = fake_client('{"answer":"ok"}')
    path = tmp_path / "db"
    with pytest.raises(RuntimeError, match=missing):
        ModelClient(client=client, db_path=path)
    assert calls.requests == [] and not path.exists()


def test_a_connection_reset_is_retried_once_and_only_a_second_failure_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("companion.model.TRANSIENT_RETRY_DELAY", 0)
    instance, calls, path = model_client(tmp_path, monkeypatch, ConnectionError("reset by peer"), '{"answer": "ok"}')
    assert instance.call_role("checker", message(), Reply).answer == "ok"
    assert len(calls.requests) == 2 and [row[-1] for row in rows(path)] == ["valid"]
    instance, calls, path = model_client(tmp_path, monkeypatch, ConnectionError("reset"), ConnectionError("reset again"))
    with pytest.raises(ConnectionError, match="reset again"):
        instance.call_role("checker", message(), Reply)
    assert len(calls.requests) == 2 and [row[-1] for row in rows(path)] == ["valid", "error"]  # the same database: one row per call that ended


def test_a_5xx_is_retried_once_but_a_timeout_or_a_4xx_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr("companion.model.TRANSIENT_RETRY_DELAY", 0)
    server = RuntimeError("API key authentication failed")
    server.status_code = 503
    instance, calls, _ = model_client(tmp_path, monkeypatch, server, '{"answer": "ok"}')
    assert instance.call_role("checker", message(), Reply).answer == "ok" and len(calls.requests) == 2
    instance, calls, _ = model_client(tmp_path, monkeypatch, TimeoutError("slow"), '{"answer": "ok"}')
    with pytest.raises(TimeoutError):
        instance.call_role("checker", message(), Reply)
    assert len(calls.requests) == 1
    client_error = RuntimeError("bad")
    client_error.status_code = 400
    instance, calls, _ = model_client(tmp_path, monkeypatch, client_error, '{"answer": "ok"}')
    with pytest.raises(Exception):
        instance.call_role("checker", message(), Reply)
    assert len(calls.requests) == 1


def test_a_streamed_call_is_retried_when_nothing_had_streamed_and_only_the_retry_reaches_on_delta(tmp_path, monkeypatch):
    monkeypatch.setattr("companion.model.TRANSIENT_RETRY_DELAY", 0)
    instance, calls, path = model_client(tmp_path, monkeypatch, ConnectionError("reset"), '{"answer": "ok"}')
    seen = []
    assert instance.call_role("tutor", message(), Reply, on_delta=lambda attempt, piece: seen.append(piece)).answer == "ok"
    assert len(calls.requests) == 2 and "".join(seen) == '{"answer": "ok"}'
    assert [row[-1] for row in rows(path)] == ["valid"]
    assert set(instance._failures.values()) == {0}   # a recovered call leaves no failure on the books


def test_a_stream_that_fails_after_text_was_sent_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("companion.model.TRANSIENT_RETRY_DELAY", 0)

    def broken_stream():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"ans'))], usage=None)
        raise ConnectionError("dropped mid-stream")

    instance, calls, path = model_client(tmp_path, monkeypatch, broken_stream(), '{"answer": "ok"}')
    seen = []
    with pytest.raises(ConnectionError, match="dropped"):
        instance.call_role("tutor", message(), Reply, on_delta=lambda attempt, piece: seen.append(piece))
    assert len(calls.requests) == 1 and seen == ['{"ans']
    assert [row[-1] for row in rows(path)] == ["error"]
