import json

import pytest
from pydantic import ValidationError

from companion.reference import Reference, write_and_check
from companion.catalog import load_catalog
from companion.store import open_store


def approach(id, *, expected="usually", trick=False, time="O(n)", space="O(n)"):
    return {"id": id, "name": id, "idea": "one line", "time": time, "space": space,
            "interview_expected": expected, "trick": trick}


def reference(*approaches):
    return {"slug": "two-sum", "title": "Two Sum", "difficulty": "easy", "neetcode_category": "Arrays & Hashing",
            "patterns": ["hash-map"], "analysis_concepts": ["hash-lookup"],
            "approaches": list(approaches) or [approach("hash"), approach("brute", expected="rarely", time="O(n^2)", space="O(1)")]}


def verdicts(*disagreements):
    """A checker reply covering hash and brute; disagreements are (approach_id, label) pairs."""
    return {"verdicts": [{"approach_id": a, "label": label, "agree": (a, label) not in disagreements,
                          "reason": "because"} for a in ("hash", "brute")
                         for label in ("complexity", "interview_expected", "trick")]}


class FakeModels:
    """Stands in for ModelClient: queued JSON replies per role, validated like the real client."""

    def __init__(self, writer=(), checker=()):
        self.replies = {"reference_writer": list(writer), "checker": list(checker)}
        self.calls = []

    def call_role(self, role, messages, schema_class, **kwargs):
        self.calls.append((role, messages, schema_class))
        try:
            return schema_class.model_validate_json(json.dumps(self.replies[role].pop(0)))
        except ValidationError as error:
            raise ValueError(f"{role} reply invalid after retry: {error}") from error

    def roles(self):
        return [role for role, _, _ in self.calls]


def stored(path):
    with open_store(path) as db:
        return db.execute('SELECT problem_key, content, status FROM "references"').fetchall()


@pytest.mark.parametrize("approaches", [
    [approach("a", expected="sometimes")],
    [approach("a"), approach("b", expected="usually", trick=True)],
    [approach("a"), approach("a", expected="rarely")],
    [approach("a", time="")],
    [approach("a", space="")],
    [],
])
def test_validator_rejects(approaches):
    data = reference()
    data["approaches"] = approaches
    with pytest.raises(ValidationError):
        Reference.model_validate(data)


def test_validator_accepts_a_trick_that_is_not_usually():
    Reference.model_validate(reference(approach("a"), approach("b", expected="sometimes", trick=True)))


def test_agreeing_checker_stores_checked(tmp_path):
    path = tmp_path / "db"
    models = FakeModels([reference()], [verdicts()])
    result = write_and_check("two-sum", "Two Sum", models, db_path=path)
    assert result.status == "checked" and result.uncertain == []
    assert models.roles() == ["reference_writer", "checker"]
    assert "two-sum" in models.calls[0][1][-1]["content"] and "Two Sum" in models.calls[0][1][-1]["content"]
    [(key, content, status)] = stored(path)
    assert (key, status) == ("two-sum", "checked")
    assert json.loads(content)["approaches"][0]["id"] == "hash"


def test_on_demand_reference_uses_statement_for_real_250_problem(tmp_path):
    problem = load_catalog().by_slug("concatenation-of-array")
    statement = "Given an integer array nums, return an array with nums followed by nums."
    reply = {"slug": problem["slug"], "title": problem["title"], "difficulty": problem["difficulty"],
             "neetcode_category": problem["category"], "summary": "Return the input sequence repeated twice.",
             "patterns": ["array"],
             "analysis_concepts": ["linear-time"], "approaches": [approach("copy", space="O(n)")]}
    checks = {"verdicts": [{"approach_id": "copy", "label": label, "agree": True, "reason": "correct"}
                           for label in ("complexity", "interview_expected", "trick")]}
    models = FakeModels([reply], [checks])
    result = write_and_check(problem["slug"], problem["title"], models,
                             statement=statement, db_path=tmp_path / "db")
    assert result.status == "checked"
    assert statement in models.calls[0][1][-1]["content"]
    assert problem["category"] in models.calls[0][1][-1]["content"]
    assert problem["difficulty"] in models.calls[0][1][-1]["content"]


def test_on_demand_reference_requires_a_plain_summary(tmp_path):
    problem = load_catalog().by_slug("concatenation-of-array")
    reply = {"slug": problem["slug"], "title": problem["title"], "difficulty": problem["difficulty"],
             "neetcode_category": problem["category"], "patterns": ["array"],
             "analysis_concepts": ["linear-time"], "approaches": [approach("copy")]}
    with pytest.raises(ValueError, match="summary"):
        write_and_check(problem["slug"], problem["title"], FakeModels([reply]),
                        statement="Return an array with nums repeated twice.", db_path=tmp_path / "db")


def test_disagreement_regenerates_only_that_part_then_agrees(tmp_path):
    path = tmp_path / "db"
    revised = approach("brute", expected="sometimes", time="O(n^2)", space="O(1)")
    revised["name"] = "should not be applied"
    models = FakeModels([reference(), revised], [verdicts(("brute", "interview_expected")), verdicts()])
    result = write_and_check("two-sum", "Two Sum", models, db_path=path)
    assert result.status == "checked"
    brute = next(a for a in result.reference.approaches if a.id == "brute")
    assert brute.interview_expected == "sometimes" and brute.name == "brute"
    assert models.roles() == ["reference_writer", "checker", "reference_writer", "checker"]
    assert stored(path)[0][2] == "checked"


def test_persistent_disagreement_is_needs_review_with_uncertain_labels(tmp_path):
    path = tmp_path / "db"
    bad = verdicts(("hash", "trick"))
    models = FakeModels([reference(), approach("hash")], [bad, bad])
    result = write_and_check("two-sum", "Two Sum", models, db_path=path)
    assert result.status == "needs_review" and result.uncertain == ["hash.trick"]
    assert models.roles().count("reference_writer") == 2 and models.roles().count("checker") == 2
    [(_, content, status)] = stored(path)
    assert status == "needs_review" and json.loads(content)["uncertain"] == ["hash.trick"]


def test_regeneration_that_breaks_validity_keeps_the_original_as_needs_review(tmp_path):
    path = tmp_path / "db"
    broken = approach("hash", expected="rarely")  # leaves no `usually` approach
    models = FakeModels([reference(approach("hash")), broken], [{"verdicts": [
        {"approach_id": "hash", "label": "complexity", "agree": True, "reason": "ok"},
        {"approach_id": "hash", "label": "interview_expected", "agree": False, "reason": "no"},
        {"approach_id": "hash", "label": "trick", "agree": True, "reason": "ok"}]}])
    result = write_and_check("two-sum", "Two Sum", models, db_path=path)
    assert result.status == "needs_review" and result.uncertain == ["hash.interview_expected"]
    assert result.reference.approaches[0].interview_expected == "usually"


def test_invalid_writer_reply_is_never_stored(tmp_path):
    path = tmp_path / "db"
    no_usually = reference(approach("a", expected="rarely"))
    with pytest.raises(ValueError):
        write_and_check("two-sum", "Two Sum", FakeModels([no_usually]), db_path=path)
    assert stored(path) == []


def test_checker_missing_a_label_twice_is_loud_and_nothing_is_stored(tmp_path):
    path = tmp_path / "db"
    partial = {"verdicts": verdicts()["verdicts"][:-1]}
    with pytest.raises(ValueError, match="checker reply invalid after retry.*brute"):
        write_and_check("two-sum", "Two Sum", FakeModels([reference()], [partial, partial]), db_path=path)
    assert stored(path) == []


def test_checker_missing_a_label_once_is_retried_with_the_gap_named(tmp_path):
    path = tmp_path / "db"
    partial = {"verdicts": verdicts()["verdicts"][:-1]}
    result = write_and_check("two-sum", "Two Sum", FakeModels([reference()], [partial, verdicts()]), db_path=path)
    assert result.status == "checked"


def test_rerun_replaces_unchecked_only_when_it_now_passes(tmp_path):
    path = tmp_path / "db"
    bad = verdicts(("hash", "trick"))
    first = FakeModels([reference(), approach("hash")], [bad, bad])
    assert write_and_check("two-sum", "Two Sum", first, db_path=path).status == "needs_review"

    still = write_and_check("two-sum", "Two Sum", FakeModels([reference(), approach("hash")], [bad, bad]), db_path=path)
    assert still.status == "needs_review" and stored(path)[0][2] == "needs_review"

    passing = write_and_check("two-sum", "Two Sum", FakeModels([reference()], [verdicts()]), db_path=path)
    assert passing.status == "checked" and len(stored(path)) == 1 and stored(path)[0][2] == "checked"

    untouched = FakeModels()
    assert write_and_check("two-sum", "Two Sum", untouched, db_path=path).status == "checked"
    assert untouched.calls == []
