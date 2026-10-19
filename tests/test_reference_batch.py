import json
from types import SimpleNamespace

import pytest

from companion.model import ModelUnavailable
from companion.reference_batch import run_batch
from companion.store import open_store


PROBLEMS = [
    {"slug": "one", "title": "One"},
    {"slug": "two", "title": "Two"},
    {"slug": "three", "title": "Three"},
]


def seed(path, slug, status, uncertain=()):
    with open_store(path) as db:
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)',
                   (slug, json.dumps({"uncertain": list(uncertain)}), status))


def test_resume_skips_checked_without_calling_writer(tmp_path, capsys):
    db = tmp_path / "db"
    seed(db, "one", "checked")
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append((slug, title))
        return SimpleNamespace(status="checked", uncertain=[])

    report = run_batch(PROBLEMS, db_path=db, report_path=tmp_path / "report.md",
                       write=write, models=object())
    assert calls == [("two", "Two"), ("three", "Three")]
    assert report.checked == 3 and report.skipped == 1
    assert "1 of 3 one: skipped (checked)" in capsys.readouterr().out


def test_needs_review_is_retried_only_with_flag(tmp_path):
    db = tmp_path / "db"
    seed(db, "one", "needs_review", ["hash.trick"])
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        return SimpleNamespace(status="checked", uncertain=[])

    quiet = run_batch(PROBLEMS[:1], db_path=db, report_path=tmp_path / "report.md",
                      write=write, models=object())
    assert calls == [] and quiet.skipped == 1 and quiet.needs_review == 1
    assert quiet.review_reasons == {"one": "hash.trick"}
    retried = run_batch(PROBLEMS[:1], db_path=db, report_path=tmp_path / "report.md",
                        write=write, models=object(), retry_needs_review=True)
    assert calls == ["one"] and retried.checked == 1
    assert retried.review_reasons == {}


def test_model_unavailable_stops_and_names_model_and_effect(tmp_path, capsys):
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        raise ModelUnavailable("x-test-2", "DeepSeek V4.1 Flash", ("checker",))

    report_path = tmp_path / "report.md"
    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=report_path,
                       write=write, models=object())
    assert calls == ["one"]
    assert report.failed == 1 and report.skipped == 2
    assert "DeepSeek V4.1 Flash" in report.stopped and "reference checking" in report.stopped
    assert "x-test-2" in report.failure_reasons["one"]
    assert "DeepSeek V4.1 Flash" in report_path.read_text()
    assert "two" not in calls and "three" not in calls


def test_one_provider_timeout_fails_that_problem_and_the_batch_continues(tmp_path):
    from openai import APITimeoutError
    import httpx

    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        if slug == "one":
            raise APITimeoutError(httpx.Request("POST", "https://example.test"))
        return SimpleNamespace(status="checked")

    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=tmp_path / "report.md",
                       write=write, models=object())
    assert calls == ["one", "two", "three"]
    assert report.failed == 1 and report.stopped is None
    assert "timeout" in report.failure_reasons["one"]


def test_an_invalid_checker_reply_fails_that_problem_and_the_batch_continues(tmp_path):
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        if slug == "one":
            raise ValueError("checker reply invalid after retry: 1 validation error")
        return SimpleNamespace(status="checked")

    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=tmp_path / "report.md",
                       write=write, models=object())
    assert calls == ["one", "two", "three"]
    assert report.failed == 1 and report.stopped is None
    assert "checker reply invalid" in report.failure_reasons["one"]


def test_an_unexpected_value_error_names_itself_in_the_report(tmp_path):
    def write(slug, title, models, *, db_path):
        raise ValueError("reference missing the approaches field")

    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=tmp_path / "report.md",
                       write=write, models=object())
    assert "reference missing the approaches field" in report.stopped


def test_missing_setup_names_what_is_missing(tmp_path):
    def write(slug, title, models, *, db_path):
        raise RuntimeError("NUS_SOCLAAS_BASE_URL is required")

    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=tmp_path / "report.md",
                       write=write, models=object())
    assert "NUS_SOCLAAS_BASE_URL is required" in report.stopped
    assert "NUS_SOCLAAS_BASE_URL is required" in report.failure_reasons["one"]


def test_single_invalid_writer_reply_continues_and_report_lists_reasons(tmp_path, capsys):
    def write(slug, title, models, *, db_path):
        if slug == "one":
            raise ValueError("reference_writer reply invalid after retry: secret raw details")
        return SimpleNamespace(status="needs_review" if slug == "two" else "checked",
                               uncertain=["hash.trick"] if slug == "two" else [])

    path = tmp_path / "report.md"
    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=path,
                       write=write, models=object())
    assert (report.checked, report.needs_review, report.failed, report.skipped) == (1, 1, 1, 0)
    content = path.read_text()
    assert "`one`: reference_writer reply invalid after retry" in content
    assert "`two`: hash.trick" in content
    assert "secret raw details" not in content + capsys.readouterr().out


def test_five_consecutive_invalid_writer_replies_stop(tmp_path):
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        raise ValueError("reference_writer reply invalid after retry: detail")

    problems = [{"slug": str(i), "title": str(i)} for i in range(7)]
    report = run_batch(problems, db_path=tmp_path / "db", report_path=tmp_path / "report.md",
                       write=write, models=object())
    assert calls == [str(i) for i in range(5)]
    assert report.failed == 5 and report.skipped == 2
    assert "Five consecutive" in report.stopped


def test_limit_counts_only_unfinished_and_slug_selects_one(tmp_path):
    db = tmp_path / "db"
    seed(db, "one", "checked")
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        return SimpleNamespace(status="checked", uncertain=[])

    report = run_batch(PROBLEMS, db_path=db, report_path=tmp_path / "report.md",
                       write=write, models=object(), limit=1)
    assert calls == ["two"] and report.skipped == 2
    calls.clear()
    report = run_batch(PROBLEMS, db_path=db, report_path=tmp_path / "report.md",
                       write=write, models=object(), slug="three")
    assert calls == ["three"] and report.skipped == 2
    with pytest.raises(ValueError, match="Unknown NeetCode 150 slug"):
        run_batch(PROBLEMS, db_path=db, report_path=tmp_path / "report.md", slug="missing")


def test_ctrl_c_prints_and_writes_completed_count(tmp_path, capsys):
    calls = []

    def write(slug, title, models, *, db_path):
        calls.append(slug)
        if slug == "two":
            raise KeyboardInterrupt
        return SimpleNamespace(status="checked", uncertain=[])

    path = tmp_path / "report.md"
    report = run_batch(PROBLEMS, db_path=tmp_path / "db", report_path=path,
                       write=write, models=object())
    assert calls == ["one", "two"]
    assert report.checked == 1 and report.skipped == 2
    assert "Interrupted by Ctrl-C" in report.stopped
    assert "Checked: 1" in path.read_text()
    assert "Checked: 1" in capsys.readouterr().out


def test_real_writer_with_fake_model_client_persists_and_resumes(tmp_path):
    from companion.reference import CheckReply, Reference, WrittenReference

    class FakeModels:
        calls = []

        def call_role(self, role, messages, schema_class):
            self.calls.append((role, messages[-1]["content"]))
            if role == "reference_writer":
                assert schema_class is WrittenReference
                return WrittenReference.model_validate({
                    "slug": "one", "title": "One", "difficulty": "Easy",
                    "neetcode_category": "Arrays & Hashing", "patterns": ["hashing"],
                    "summary": "Decide whether the input has a repeated value.",
                    "analysis_concepts": ["lookup"], "approaches": [{
                        "id": "hash", "name": "Hash map", "idea": "Look up complements",
                        "time": "O(n)", "space": "O(n)", "interview_expected": "usually", "trick": False,
                    }],
                })
            assert role == "checker" and schema_class is CheckReply
            return CheckReply.model_validate({"verdicts": [
                {"approach_id": "hash", "label": label, "agree": True, "reason": "ok"}
                for label in ("complexity", "interview_expected", "trick")
            ]})

    db = tmp_path / "db"
    models = FakeModels()
    first = run_batch(PROBLEMS[:1], db_path=db, report_path=tmp_path / "report.md", models=models)
    second = run_batch(PROBLEMS[:1], db_path=db, report_path=tmp_path / "report.md", models=models)
    assert first.checked == 1 and second.skipped == 1
    assert [role for role, _ in models.calls] == ["reference_writer", "checker"]
    assert models.calls[0][1] == "Problem slug: one\nTitle: One"
    with open_store(db) as connection:
        assert connection.execute('SELECT status FROM "references" WHERE problem_key = ?', ("one",)).fetchone() == ("checked",)


# --- summary backfill -------------------------------------------------------------

def seed_reference(path, slug, *, summary=None, patterns=("hash-map",)):
    content = {"slug": slug, "title": slug.title(), "difficulty": "Easy", "neetcode_category": "Arrays & Hashing",
               "patterns": list(patterns), "analysis_concepts": [],
               "approaches": [{"id": "main", "name": "Hash map", "idea": "One line", "time": "O(n)",
                               "space": "O(n)", "interview_expected": "usually", "trick": False}]}
    if summary is not None:
        content["summary"] = summary
    with open_store(path) as db:
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?)',
                   (slug, json.dumps(content), "checked"))


class SummaryModels:
    def __init__(self, *summaries):
        self.summaries = list(summaries)
        self.calls = []

    def call_role(self, role, messages, schema_class, **kwargs):
        self.calls.append((role, messages[-1]["content"]))
        return schema_class.model_validate({"summary": self.summaries.pop(0)})


def test_summary_backfill_updates_only_references_without_one(tmp_path):
    from companion.reference_batch import run_summary_batch
    db = tmp_path / "db"
    seed_reference(db, "one")
    seed_reference(db, "two", summary="The existing plain sentence.")
    models = SummaryModels("Find two values that add to the target.")
    report = run_summary_batch(PROBLEMS[:2], db_path=db, report_path=tmp_path / "report.md", models=models)
    assert report.updated == 1 and report.skipped == 1
    assert len(models.calls) == 1 and models.calls[0][0] == "reference_writer"
    assert "one" in models.calls[0][1] and "hash-map" in models.calls[0][1]
    with open_store(db) as connection:
        contents = {slug: json.loads(content)
                    for slug, content in connection.execute('SELECT problem_key, content FROM "references"')}
    assert contents["one"]["summary"] == "Find two values that add to the target."
    assert contents["one"]["patterns"] == ["hash-map"] and contents["one"]["approaches"][0]["id"] == "main"
    assert contents["two"]["summary"] == "The existing plain sentence."


def test_summary_backfill_rejects_a_summary_that_names_a_pattern_twice_and_stores_nothing(tmp_path):
    from companion.reference_batch import run_summary_batch
    db = tmp_path / "db"
    seed_reference(db, "one")
    models = SummaryModels("Use a hash map.", "Use a hash map.")
    report = run_summary_batch(PROBLEMS[:1], db_path=db, report_path=tmp_path / "report.md", models=models)
    assert report.failed == 1 and len(models.calls) == 2
    with open_store(db) as connection:
        content = json.loads(connection.execute('SELECT content FROM "references"').fetchone()[0])
    assert "summary" not in content


def test_summary_backfill_skips_a_slug_without_a_reference(tmp_path):
    from companion.reference_batch import run_summary_batch
    report = run_summary_batch(PROBLEMS[:1], db_path=tmp_path / "db", report_path=tmp_path / "report.md",
                               models=SummaryModels())
    assert report.updated == 0 and report.skipped == 1
