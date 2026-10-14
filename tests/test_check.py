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
    assert "Observer thinking-off: honored" in report
    assert "Tutor median time to first token (3 calls):" in report
    assert "see results above" not in report
    with open_store(path) as db:
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == len(completions.requests)


def test_check_reports_unknown_thinking_and_hides_provider_body(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")

    class Failing(ProbeCompletions):
        def create(self, **kwargs):
            if kwargs["model"] == load_roles().roles["observer"][0]:
                error = RuntimeError("secret-provider-body=" + "x" * 500)
                error.status_code = 400
                raise error
            response = super().create(**kwargs)
            if not kwargs.get("stream"):
                response.usage.completion_tokens_details = None
            return response

    run_checks(client=SimpleNamespace(chat=SimpleNamespace(completions=Failing())), db_path=tmp_path / "db")
    report = capsys.readouterr().out
    assert "Observer thinking-off: unknown" in report
    assert "ProviderRequestError" in report
    assert "secret-provider-body" not in report


def test_check_reports_reasoning_text_as_not_honored(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUS_SOCLAAS_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("NUS_SOCLAAS_API_KEY", "fake-test-key")
    observer = load_roles().roles["observer"][0]

    class Reasoning(ProbeCompletions):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if kwargs["model"] == observer:
                response.choices[0].message.reasoning_content = "Let me reason through this"
            return response

    run_checks(client=SimpleNamespace(chat=SimpleNamespace(completions=Reasoning())), db_path=tmp_path / "db")
    assert "Observer thinking-off: not honored" in capsys.readouterr().out
