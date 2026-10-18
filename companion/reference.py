"""Reference writer and Checker: one problem's reference in the SPEC section 10 format."""

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from companion.catalog import load_catalog
from companion.model import ModelClient
from companion.store import open_store

logger = logging.getLogger(__name__)

LABELS = ("complexity", "interview_expected", "trick")
FIELDS_OF = {"complexity": ("time", "space"), "interview_expected": ("interview_expected",), "trick": ("trick",)}

FORMAT = """Write a short reference for a coding-interview problem as JSON with these fields:
slug, title, difficulty, neetcode_category (NeetCode's category name),
summary: one plain sentence saying what the problem asks, without an approach or pattern name,
patterns: reusable interview ideas the problem uses (sliding window, fast and slow pointers, monotonic stack...),
analysis_concepts: finer tags for complexity analysis,
approaches: a list of {id (unique), name, idea (one line), time, space,
  interview_expected: "usually" | "sometimes" | "rarely",
  trick: true only for a one-off trick that only works for this problem}.
Rules: at least one approach is "usually"; a trick is never "usually". Mark an idea as a trick only if it does not come back in other problems.
No solution code."""

CHECKER = """You review a coding-problem reference. For every approach, judge three labels and give one verdict per approach and label:
complexity (are time and space right?), interview_expected (is usually/sometimes/rarely right for how often interviewers expect it?),
trick (is the one-off-trick flag right? a reusable pattern is not a trick). Set agree to false only for a label you believe is wrong, and give the reason."""


class Approach(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    idea: str = Field(min_length=1)
    time: str = Field(min_length=1)
    space: str = Field(min_length=1)
    interview_expected: Literal["usually", "sometimes", "rarely"]
    trick: bool


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    difficulty: str = Field(min_length=1)
    neetcode_category: str = Field(min_length=1)
    summary: str | None = None
    patterns: list[str]
    analysis_concepts: list[str]
    approaches: list[Approach] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self):
        if len({a.id for a in self.approaches}) != len(self.approaches):
            raise ValueError("approach ids must be unique")
        if not any(a.interview_expected == "usually" for a in self.approaches):
            raise ValueError("at least one approach must be `usually`")
        if any(a.trick and a.interview_expected == "usually" for a in self.approaches):
            raise ValueError("a trick is never `usually`")
        return self


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approach_id: str
    label: Literal["complexity", "interview_expected", "trick"]
    agree: bool
    reason: str


class CheckReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    verdicts: list[Verdict]


@dataclass(frozen=True)
class StoredReference:
    reference: Reference
    status: Literal["checked", "needs_review", "resolved_by_you"]
    uncertain: list[str]  # "approach_id.label" for each label the Checker still disputes


def _check(models, reference) -> list[Verdict]:
    """The Checker's disagreements. A reply that skips an approach's label gets one retry with the gap
    named; still incomplete is an error, never a silent pass."""
    messages = [
        {"role": "system", "content": CHECKER},
        {"role": "user", "content": reference.model_dump_json()},
    ]
    for attempt in range(2):
        reply = models.call_role("checker", messages, CheckReply)
        seen = {(v.approach_id, v.label) for v in reply.verdicts}
        missing = [f"{a.id}.{label}" for a in reference.approaches for label in LABELS if (a.id, label) not in seen]
        if not missing:
            return [v for v in reply.verdicts if not v.agree]
        if attempt:
            raise ValueError(f"checker reply invalid after retry: no verdict for: {', '.join(missing)}")
        messages = [*messages, {"role": "assistant", "content": reply.model_dump_json()},
                    {"role": "user", "content": f"Your reply gave no verdict for: {', '.join(missing)}. Return a verdict for every approach and every label."}]


def _regenerate(models, reference, disagreements) -> Reference | None:
    """Rewrite only the disputed fields of each disputed approach; None if the result is not a valid reference."""
    approaches = {a.id: a.model_dump() for a in reference.approaches}
    for approach_id in dict.fromkeys(v.approach_id for v in disagreements):
        mine = [v for v in disagreements if v.approach_id == approach_id]
        revised = models.call_role("reference_writer", [
            {"role": "system", "content": FORMAT},
            {"role": "user", "content": f"Problem: {reference.title} ({reference.slug}).\n"
                f"Reference: {reference.model_dump_json()}\nA reviewer disputes approach `{approach_id}`:\n"
                + "\n".join(f"- {v.label}: {v.reason}" for v in mine)
                + "\nReturn that one approach again as JSON, corrected where the reviewer is right."},
        ], Approach)
        for v in mine:
            for field in FIELDS_OF[v.label]:
                approaches[approach_id][field] = getattr(revised, field)
    try:
        return Reference.model_validate({**reference.model_dump(), "approaches": list(approaches.values())})
    except ValueError:
        return None


def load_reference(db, slug) -> StoredReference | None:
    row = db.execute('SELECT content, status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
    if row is None:
        return None
    content = json.loads(row[0])
    uncertain = content.pop("uncertain")
    return StoredReference(Reference.model_validate(content), row[1], uncertain)


def write_and_check(slug: str, title: str, models: ModelClient, *, db_path: str | Path | None = None,
                    statement: str | None = None, grade_waiting: bool = True) -> StoredReference:
    """Write, check and store one reference. A stored `checked` reference is returned as is; a stored
    `needs_review` one is replaced only by a result that is now `checked`."""
    with open_store(db_path) as db:
        existing = load_reference(db, slug)
    if existing and existing.status in ("checked", "resolved_by_you"):
        return existing

    catalog_problem = load_catalog().by_slug(slug) if statement is not None else None
    if statement is not None and catalog_problem is None:
        raise ValueError(f"Unknown catalog problem: {slug}")

    reference = models.call_role("reference_writer", [
        {"role": "system", "content": FORMAT},
        {"role": "user", "content": f"Problem slug: {slug}\nTitle: {title}"
         + (f"\nDifficulty: {catalog_problem['difficulty']}\nNeetCode category: {catalog_problem['category']}"
            f"\nProblem statement: {statement}" if statement else "")},
    ], Reference)
    if statement is not None:
        if (reference.slug != slug or reference.title != title or
                reference.difficulty != catalog_problem["difficulty"] or
                reference.neetcode_category != catalog_problem["category"]):
            raise ValueError("On-demand reference does not match catalog metadata")
        summary = reference.summary
        if not summary or not summary.strip():
            raise ValueError("On-demand reference requires a summary")
        forbidden = [a.name for a in reference.approaches] + [pattern.replace("-", " ") for pattern in reference.patterns]
        if any(name and name.casefold() in summary.casefold() for name in forbidden):
            raise ValueError("Reference summary names an approach or pattern")
    disagreements = _check(models, reference)
    if disagreements:
        revised = _regenerate(models, reference, disagreements)
        if revised is not None:
            reference = revised
            disagreements = _check(models, reference)
    uncertain = [f"{v.approach_id}.{v.label}" for v in disagreements]
    result = StoredReference(reference, "needs_review" if disagreements else "checked", uncertain)

    if existing and result.status != "checked":
        return existing
    with open_store(db_path) as db:
        current = db.execute('SELECT status FROM "references" WHERE problem_key = ?', (slug,)).fetchone()
        if current and current[0] == "resolved_by_you":
            return load_reference(db, slug)
        open_flag = db.execute("SELECT 1 FROM events f WHERE f.type = 'reference_flagged' "
                               "AND json_extract(f.payload, '$.slug') = ? "
                               "AND f.id > COALESCE((SELECT MAX(r.id) FROM events r WHERE r.type = 'reference_resolved' "
                               "AND json_extract(r.payload, '$.slug') = ?), 0) LIMIT 1", (slug, slug)).fetchone()
        if open_flag:
            result = StoredReference(reference, "needs_review", uncertain)
        if current and current[0] == "needs_review" and open_flag:
            return load_reference(db, slug)
        content = json.dumps({**reference.model_dump(), "uncertain": uncertain})
        db.execute('INSERT INTO "references" (problem_key, content, status) VALUES (?, ?, ?) '
                   "ON CONFLICT(problem_key) DO UPDATE SET content = excluded.content, status = excluded.status",
                   (slug, content, result.status))
    if result.status == "checked" and grade_waiting:
        from companion.grading import grade_pending
        try:
            grade_pending(models, db_path=db_path, only_slug=slug)
        except Exception:
            logger.exception("Reference %s was saved, but pending grading failed", slug)
    return result


def main():
    parser = argparse.ArgumentParser(prog="python -m companion.reference", description="Write and check one problem reference.")
    parser.add_argument("slug")
    parser.add_argument("title")
    args = parser.parse_args()
    result = write_and_check(args.slug, args.title, ModelClient())
    print(f"{args.slug}: {result.status}" + (f" (uncertain: {', '.join(result.uncertain)})" if result.uncertain else ""))
    print(result.reference.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
