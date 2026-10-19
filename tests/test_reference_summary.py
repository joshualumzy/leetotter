"""The reference summary: one plain sentence, never naming an approach or a pattern."""

import json

import pytest
from pydantic import ValidationError

from companion.reference import Reference, WrittenReference, write_and_check
from companion.store import open_store


def approach(id="hash", name="Hash map"):
    return {"id": id, "name": name, "idea": "One line", "time": "O(n)", "space": "O(n)",
            "interview_expected": "usually", "trick": False}


def reference(**changes):
    data = {"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "neetcode_category": "Arrays & Hashing",
            "summary": "Find two values in the list that add up to the target.",
            "patterns": ["hash-map"], "analysis_concepts": ["lookup"],
            "approaches": [approach(), approach("brute", "Compare every pair")]}
    data.update(changes)
    return data


@pytest.mark.parametrize("summary", [
    "",
    "   ",
    "Use a Hash Map to remember what you have seen.",
    "Try the HASH MAP approach.",
])
def test_summary_validator_rejects_empty_or_a_named_pattern_or_approach(summary):
    with pytest.raises(ValidationError):
        Reference.model_validate(reference(summary=summary))


def test_summary_validator_rejects_a_pattern_display_name_case_insensitively():
    with pytest.raises(ValidationError):
        Reference.model_validate(reference(summary="Scan the array with a Sliding Window.",
                                           patterns=["sliding-window"]))


def test_summary_validator_rejects_an_approach_name_case_insensitively():
    with pytest.raises(ValidationError):
        Reference.model_validate(reference(summary="Try the hAsH mAp here."))


def test_a_plain_summary_is_valid():
    Reference.model_validate(reference())


def test_a_written_reference_requires_a_summary():
    data = reference()
    del data["summary"]
    with pytest.raises(ValidationError):
        WrittenReference.model_validate(data)


class FakeModels:
    """Validates each reply like ModelClient, retrying a bad reply once with the error shown."""

    def __init__(self, writer, checker=()):
        self.replies = {"reference_writer": list(writer), "checker": list(checker)}
        self.calls = []

    def call_role(self, role, messages, schema_class, **kwargs):
        self.calls.append((role, messages))
        for attempt in range(2):
            raw = self.replies[role].pop(0)
            try:
                return schema_class.model_validate(raw)
            except ValidationError as error:
                if attempt:
                    raise ValueError(f"{role} reply invalid after retry: {error}") from error
                messages = [*messages, {"role": "assistant", "content": json.dumps(raw)},
                            {"role": "user", "content": f"Invalid summary: {error}. Return corrected JSON only."}]


def verdicts():
    return {"verdicts": [{"approach_id": item["id"], "label": label, "agree": True, "reason": "ok"}
                         for item in reference()["approaches"]
                         for label in ("complexity", "interview_expected", "trick")]}


def stored(path):
    with open_store(path) as db:
        return db.execute('SELECT content FROM "references"').fetchall()


def test_an_invalid_summary_is_retried_once_then_the_corrected_one_is_stored(tmp_path):
    bad = reference(summary="Use a hash map here.")
    good = reference()
    models = FakeModels([bad, good], [verdicts()])
    result = write_and_check("two-sum", "Two Sum", models, db_path=tmp_path / "db")
    assert result.status == "checked"
    assert [role for role, _ in models.calls] == ["reference_writer", "checker"]
    assert json.loads(stored(tmp_path / "db")[0][0])["summary"] == good["summary"]


def test_an_invalid_summary_twice_fails_loudly_and_stores_nothing(tmp_path):
    bad = reference(summary="Use a hash map here.")
    with pytest.raises(ValueError, match="reference_writer reply invalid after retry"):
        write_and_check("two-sum", "Two Sum", FakeModels([bad, bad]), db_path=tmp_path / "db")
    assert stored(tmp_path / "db") == []
