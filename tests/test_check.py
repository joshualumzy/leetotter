from types import SimpleNamespace

from companion.check import run_checks
from companion.model import load_roles
from companion.store import open_store


class ProbeCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream"):
            return iter([
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"answer":'))], usage=None),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='"ok"}'))], usage=None),
                SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2)),
            ])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}'))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )


def test_check_reports_every_role_and_model_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    completions = ProbeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    path = tmp_path / "db"
    run_checks(client=client, db_path=path)
    report = capsys.readouterr().out
    assert len(completions.requests) == 8
    assert {request["model"] for request in completions.requests} == {"qwen3.8:27b", "x-test-1", "x-test-2"}
    assert "x-test-2 reachable with valid structured output: yes" in report
    assert "Observer thinking-disabled request: succeeded" in report
    assert "Observer response time:" in report
    assert "Tutor time to first token:" in report
    assert "JSON/structured-output support" in report
    assert completions.requests[1]["stream"] is True
    with open_store(path) as db:
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 8
