"""One Tutor call through the real ModelClient over a fake provider: streamed text, validated labels, loud failures."""

import json
from types import SimpleNamespace

import pytest
from openai import APITimeoutError

from companion.model import ModelClient
from companion.tutor import FirstHelpReply, ask_tutor, failure

GOOD = {"gap": "stuck_optimising", "gap_note": None, "help_kind": "question",
        "reply_text": "Your loops compare every pair. What could you remember as you go?", "follow_up_question": None}


def chunks(text, size=7):
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text[i:i + size]))], usage=None)
            for i in range(0, len(text), size)]


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return iter(reply)


def client(tmp_path, monkeypatch, *replies):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    completions = FakeCompletions(replies)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return ModelClient(client=fake, db_path=tmp_path / "db.sqlite3"), completions


MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def test_reply_text_streams_by_attempt_and_the_validated_reply_is_returned(tmp_path, monkeypatch):
    models, completions = client(tmp_path, monkeypatch, chunks(json.dumps(GOOD)))
    seen = []
    reply = ask_tutor(models, MESSAGES, FirstHelpReply, lambda attempt, text: seen.append((attempt, text)))
    assert reply.reply_text == GOOD["reply_text"] and reply.gap == "stuck_optimising"
    assert len(seen) > 2 and {a for a, _ in seen} == {0}
    assert "".join(t for _, t in seen) == GOOD["reply_text"]
    assert completions.requests[0]["response_format"]["json_schema"]["name"] == "firsthelpreply"


def test_an_invalid_first_attempt_is_retried_and_the_retry_streams_as_attempt_1(tmp_path, monkeypatch):
    bad = {**GOOD, "help_kind": "full_solution", "reply_text": "Here is the whole solution"}
    models, _ = client(tmp_path, monkeypatch, chunks(json.dumps(bad)), chunks(json.dumps(GOOD)))
    seen = []
    reply = ask_tutor(models, MESSAGES, FirstHelpReply, lambda attempt, text: seen.append((attempt, text)))
    assert reply.help_kind == "question"
    assert "".join(t for a, t in seen if a == 0) == bad["reply_text"]
    assert "".join(t for a, t in seen if a == 1) == GOOD["reply_text"]


def test_other_gap_without_a_note_is_invalid(tmp_path, monkeypatch):
    bad = {**GOOD, "gap": "other"}
    models, _ = client(tmp_path, monkeypatch, chunks(json.dumps(bad)), chunks(json.dumps(bad)))
    with pytest.raises(ValueError) as caught:
        ask_tutor(models, MESSAGES, FirstHelpReply, lambda *_: None)
    assert failure(caught.value)[0] == "invalid_output"


@pytest.mark.parametrize("error, reason, words", [
    (APITimeoutError(request=None), "timeout", "didn't answer in time"),
    (type("RateLimited", (Exception,), {"status_code": 429})("slow down"), "rate_limited", "rate"),
])
def test_provider_failures_are_named(tmp_path, monkeypatch, error, reason, words):
    models, _ = client(tmp_path, monkeypatch, error)
    with pytest.raises(Exception) as caught:
        ask_tutor(models, MESSAGES, FirstHelpReply, lambda *_: None)
    got_reason, message = failure(caught.value)
    assert got_reason == reason and words in message


def test_an_unavailable_model_is_named_with_what_it_affects(tmp_path, monkeypatch):
    error = type("NotFound", (Exception,), {"status_code": 404})("The model x-test-1 does not exist")
    models, _ = client(tmp_path, monkeypatch, error)
    with pytest.raises(Exception) as caught:
        ask_tutor(models, MESSAGES, FirstHelpReply, lambda *_: None)
    reason, message = failure(caught.value)
    assert reason == "model_unavailable" and "GLM 5.3 Flash" in message and "tutoring" in message


@pytest.mark.parametrize("changes", [
    {"gap": "none"},                                   # help given, but labelled as no gap
    {"help_kind": "none"},                             # a gap diagnosed, but no help labelled
    {"gap": "other", "gap_note": None},
    {"gap": "other", "gap_note": "  "},
    {"gap_note": "a note on a listed gap"},
])
def test_labels_must_agree_with_each_other(changes):
    from pydantic import ValidationError
    from companion.model import TutorReply
    with pytest.raises(ValidationError):
        TutorReply.model_validate({**GOOD, **changes})


def test_consistent_labels_are_valid():
    from companion.model import TutorReply
    TutorReply.model_validate({**GOOD, "gap": "none", "help_kind": "none"})
    TutorReply.model_validate({**GOOD, "gap": "other", "gap_note": "mixes up rows and columns"})
