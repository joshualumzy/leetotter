from types import SimpleNamespace

import pytest

from companion.model import TutorReply, call_tutor
from companion.store import open_store


class FakeCompletions:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content = next(self.replies)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
        )


def fake_client(*replies):
    completions = FakeCompletions(replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


VALID_REPLY = '{"reply_text":"ok","gap":"weak_intuition","help_kind":"question","follow_up_question":null}'


def test_invalid_reply_is_retried_with_error_and_only_valid_call_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    path = tmp_path / "companion.sqlite3"
    client, completions = fake_client('{"reply_text":42}', VALID_REPLY)

    reply = call_tutor("say ok", db_path=path, client=client)

    assert reply == TutorReply(reply_text="ok", gap="weak_intuition", help_kind="question", follow_up_question=None)
    assert len(completions.requests) == 2
    assert "error" in str(completions.requests[1]["messages"][-1]).lower()
    assert all(request["model"] == "x-test-1" for request in completions.requests)
    assert all("json_schema" in request["response_format"] for request in completions.requests)
    with open_store(path) as db:
        rows = db.execute("SELECT role, model, prompt_tokens, completion_tokens, validation_result FROM model_calls").fetchall()
    assert rows == [("tutor", "x-test-1", 12, 8, "valid")]


def test_two_invalid_replies_fail_without_storing_output(tmp_path, monkeypatch):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    path = tmp_path / "companion.sqlite3"
    client, completions = fake_client("{}", "{}")

    with pytest.raises(ValueError, match="Tutor reply invalid after retry"):
        call_tutor("say ok", db_path=path, client=client)

    assert len(completions.requests) == 2
    with open_store(path) as db:
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0


def test_tutor_reply_rejects_wrong_label_and_extra_fields():
    with pytest.raises(ValueError):
        TutorReply.model_validate_json(
            '{"reply_text":"ok","gap":"guess","help_kind":"question","follow_up_question":null}'
        )
    with pytest.raises(ValueError):
        TutorReply.model_validate_json(
            '{"reply_text":"ok","gap":"weak_intuition","help_kind":"question","follow_up_question":null,"surprise":true}'
        )


@pytest.mark.parametrize("missing", ["NUS_SOCLAAS_BASE_URL", "NUS_SOCLAAS_API_KEY"])
def test_missing_configuration_fails_before_call_or_store(tmp_path, monkeypatch, missing):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    monkeypatch.delenv(missing)
    path = tmp_path / "companion.sqlite3"
    client, completions = fake_client(VALID_REPLY)

    with pytest.raises(RuntimeError, match=missing):
        call_tutor("say ok", db_path=path, client=client)

    assert completions.requests == []
    assert not path.exists()
