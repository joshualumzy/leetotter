from collections import Counter
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
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}', reasoning_content=None))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2,
                                  completion_tokens_details=SimpleNamespace(reasoning_tokens=0)),
        )


def test_check_probes_each_model_output_mode_and_three_tutor_streams(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    completions = ProbeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    path = tmp_path / "db"
    assert run_checks(client=client, db_path=path)
    report = capsys.readouterr().out
    models = {model for assigned in load_roles().roles.values() for model in assigned}
    for model in models:
        assert f"{model}: json_schema=supported, json_object=supported, plain=supported" in report
    modes = Counter((request["model"], request.get("response_format", {}).get("type", "plain"))
                    for request in completions.requests)
    for model in models:
        assert modes[(model, "json_schema")] >= 1
        assert modes[(model, "json_object")] >= 1
        assert modes[(model, "plain")] >= 1
    assert sum(request.get("stream") is True for request in completions.requests) == 3
    assert "Tutor median time to first token (3 calls):" in report
    assert "see results above" not in report
    with open_store(path) as db:
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == len(completions.requests)
