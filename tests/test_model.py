from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from companion.model import ModelUnavailable, RateLimiter, call_role, load_roles
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
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=result))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
        )


def fake_client(*replies):
    completions = FakeCompletions(replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def rows(path):
    with open_store(path) as db:
        return db.execute(
            "SELECT role, model, prompt_tokens, completion_tokens, validation_result FROM model_calls ORDER BY id"
        ).fetchall()


def configured(monkeypatch):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")


def test_roles_file_matches_spec_and_configures_timeouts():
    config = load_roles()
    assert config.roles == {
        "observer": "qwen3.8:27b", "tutor": "x-test-1", "reflector": "x-test-1",
        "planner": "x-test-1", "grader": ["x-test-1", "x-test-2"],
        "reference_writer": "x-test-1", "checker": "x-test-2",
    }
    assert config.timeouts == {
        "observer": 20, "tutor": 60, "grader": 60, "planner_start": 180,
        "planner_between": 30, "reflector": 180,
        "reference_writer": 180, "checker": 180,
    }


def test_invalid_reply_is_retried_with_error_and_every_attempt_logged(tmp_path, monkeypatch):
    configured(monkeypatch)
    path = tmp_path / "db.sqlite3"
    client, completions = fake_client('{"answer":42}', '{"answer":"ok"}')
    reply = call_role("tutor", [{"role": "user", "content": "say ok"}], Reply, db_path=path, client=client)
    assert reply == Reply(answer="ok")
    assert completions.requests[1]["messages"][-2] == {"role": "assistant", "content": '{"answer":42}'}
    assert "answer" in completions.requests[1]["messages"][-1]["content"]
    assert rows(path) == [
        ("tutor", "x-test-1", 12, 8, "invalid"),
        ("tutor", "x-test-1", 12, 8, "valid"),
    ]


def test_final_invalid_reply_is_loud_and_logged(tmp_path, monkeypatch):
    configured(monkeypatch)
    path = tmp_path / "db.sqlite3"
    client, _ = fake_client("{}", "{}")
    with pytest.raises(ValueError, match="answer"):
        call_role("tutor", [{"role": "user", "content": "say ok"}], Reply, db_path=path, client=client)
    assert [row[-1] for row in rows(path)] == ["invalid", "invalid"]


def test_model_not_found_reports_all_affected_roles_and_logs(tmp_path, monkeypatch):
    configured(monkeypatch)
    path = tmp_path / "db.sqlite3"
    client, _ = fake_client(Exception("model x-test-1 not found"))
    with pytest.raises(ModelUnavailable) as caught:
        call_role("tutor", [{"role": "user", "content": "hi"}], Reply, db_path=path, client=client)
    assert caught.value.model == "x-test-1"
    assert set(caught.value.affected_roles) == {"tutor", "reflector", "planner", "grader", "reference_writer"}
    assert "tutoring" in str(caught.value)
    assert rows(path)[0][-1] == "unavailable"


def test_two_provider_failures_raise_unavailable_and_log_both(tmp_path, monkeypatch):
    configured(monkeypatch)
    path = tmp_path / "db.sqlite3"
    client, _ = fake_client(RuntimeError("offline"), RuntimeError("offline"))
    for _ in range(2):
        try:
            call_role("checker", [{"role": "user", "content": "hi"}], Reply, db_path=path, client=client)
        except (RuntimeError, ModelUnavailable):
            pass
    assert [row[-1] for row in rows(path)] == ["error", "unavailable"]


def test_grader_can_select_both_models_and_planner_phase_selects_timeout(tmp_path, monkeypatch):
    configured(monkeypatch)
    client, completions = fake_client('{"answer":"ok"}', '{"answer":"ok"}', '{"answer":"ok"}')
    message = [{"role": "user", "content": "hi"}]
    call_role("grader", message, Reply, model="x-test-2", client=client, db_path=tmp_path / "db")
    call_role("planner", message, Reply, planner_phase="between", client=client, db_path=tmp_path / "db")
    call_role("observer", message, Reply, client=client, db_path=tmp_path / "db")
    assert completions.requests[0]["model"] == "x-test-2"
    assert completions.requests[1]["timeout"] == 30
    assert completions.requests[2]["timeout"] == 20
    assert completions.requests[2]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_rate_limiter_sliding_window_with_fake_clock():
    class Clock:
        now = 0.0
        def time(self):
            return self.now
        def sleep(self, duration):
            self.now += duration
    clock = Clock()
    limiter = RateLimiter(limit=2, period=60, clock=clock.time, sleep=clock.sleep)
    limiter.acquire()
    clock.now = 30
    limiter.acquire()
    clock.now = 59
    limiter.acquire()
    assert clock.now >= 60
    limiter.acquire()
    assert clock.now >= 90


def test_background_backs_off_when_a_live_call_is_waiting():
    class Clock:
        now = 0.0
        def time(self):
            return self.now
    clock = Clock()
    arrivals = []
    def sleep(duration):
        clock.now += duration
        if not arrivals:
            arrivals.append("background waiting")
            limiter.acquire()
            arrivals.append("live admitted")
    limiter = RateLimiter(limit=1, period=60, clock=clock.time, sleep=sleep)
    limiter.acquire()
    limiter.acquire(background=True)
    assert arrivals == ["background waiting", "live admitted"]
    assert clock.now >= 120


def test_missing_credentials_fail_before_provider_or_store(tmp_path, monkeypatch):
    configured(monkeypatch)
    monkeypatch.delenv("NUS_SOCLAAS_API_KEY")
    client, completions = fake_client('{"answer":"ok"}')
    path = tmp_path / "db"
    with pytest.raises(RuntimeError, match="NUS_SOCLAAS_API_KEY"):
        call_role("tutor", [{"role": "user", "content": "hi"}], Reply, client=client, db_path=path)
    assert completions.requests == []
    assert not path.exists()
