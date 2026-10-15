"""Resumable offline reference writing for the NeetCode 150."""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from openai import APIConnectionError, APITimeoutError

from companion.catalog import load_catalog
from companion.model import ModelClient, ModelUnavailable
from companion.reference import write_and_check
from companion.store import open_store

REPORT_PATH = Path("docs/reference-batch-report.md")


@dataclass
class BatchReport:
    checked: int = 0
    needs_review: int = 0
    failed: int = 0
    skipped: int = 0
    review_reasons: dict[str, str] = field(default_factory=dict)
    failure_reasons: dict[str, str] = field(default_factory=dict)
    stopped: str | None = None


def _stored(db_path):
    with open_store(db_path) as db:
        return {slug: (status, json.loads(content).get("uncertain", []))
                for slug, content, status in db.execute('SELECT problem_key, content, status FROM "references"')}


def _render(report, total):
    lines = ["# Reference batch report", "", f"Catalog problems: {total}",
             f"Checked: {report.checked}", f"Needs review: {report.needs_review}",
             f"Failed: {report.failed}", f"Skipped: {report.skipped}",
             "", "Checked and needs review count stored outcomes across the catalog. "
             "Failed and skipped count this run, so skipped can overlap a stored outcome."]
    if report.stopped:
        lines += ["", f"Stopped: {report.stopped}"]
    lines += ["", "## Needs review", ""]
    lines += [f"- `{slug}`: {reason}" for slug, reason in report.review_reasons.items()] or ["None."]
    lines += ["", "## Failed", ""]
    lines += [f"- `{slug}`: {reason}" for slug, reason in report.failure_reasons.items()] or ["None."]
    return "\n".join(lines) + "\n"


def run_batch(problems, *, db_path=None, report_path=REPORT_PATH, limit=None, slug=None,
              retry_needs_review=False, write=write_and_check, models=None) -> BatchReport:
    """Run one sequential batch. `write` and `models` permit offline tests."""
    problems = list(problems)
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive")
    if slug is not None and slug not in {problem["slug"] for problem in problems}:
        raise ValueError(f"Unknown NeetCode 150 slug: {slug}")
    report = BatchReport()
    stored = _stored(db_path)
    final_status = {problem["slug"]: stored[problem["slug"]][0]
                    for problem in problems if problem["slug"] in stored}
    attempted = 0
    consecutive_failures = 0
    total = len(problems)
    index = 0
    try:
        for index, problem in enumerate(problems, 1):
            key = problem["slug"]
            existing = stored.get(key)
            if existing and existing[0] == "needs_review":
                report.review_reasons[key] = ", ".join(existing[1]) or "Checker disagreement"
            if slug is not None and key != slug:
                status = "skipped (not selected)"
            elif existing and existing[0] == "checked":
                status = "skipped (checked)"
            elif existing and existing[0] == "needs_review" and not retry_needs_review:
                status = "skipped (needs_review; use --retry-needs-review)"
            elif limit is not None and attempted >= limit:
                status = "skipped (limit)"
            else:
                attempted += 1
                try:
                    if models is None:
                        models = ModelClient(db_path=db_path)
                    result = write(key, problem["title"], models, db_path=db_path)
                except ModelUnavailable as error:
                    report.failed += 1
                    report.failure_reasons[key] = f"{error.model}: reference writing/checking paused"
                    report.stopped = f"{error.model} ({error}); batch paused at {key}"
                    status = "failed (model unavailable)"
                except (TimeoutError, ConnectionError, APITimeoutError, APIConnectionError):
                    # One slow or dropped call is not worth stopping a multi-hour run. The model client already
                    # raises ModelUnavailable when the same model fails twice in a row, which does pause the batch.
                    report.failed += 1
                    report.failure_reasons[key] = "provider timeout or connection error; it is retried on the next run"
                    status = "failed (provider timeout or connection error)"
                except ValueError as error:
                    if not str(error).startswith("reference_writer reply invalid after retry:"):
                        report.failed += 1
                        report.failure_reasons[key] = "Unexpected ValueError; batch paused"
                        report.stopped = f"Unexpected ValueError at {key}; batch paused"
                        status = "failed (unexpected error)"
                    else:
                        report.failed += 1
                        consecutive_failures += 1
                        report.failure_reasons[key] = "reference_writer reply invalid after retry"
                        status = "failed (invalid writer reply)"
                        if consecutive_failures >= 5:
                            report.stopped = "Five consecutive invalid writer replies; batch paused"
                except Exception as error:
                    # RuntimeError messages come from our own setup checks (e.g. a missing key), so they are safe to show.
                    detail = f": {error}" if type(error) is RuntimeError else ""
                    report.failed += 1
                    report.failure_reasons[key] = f"Unexpected {type(error).__name__}{detail}; batch paused"
                    report.stopped = f"Unexpected {type(error).__name__}{detail} at {key}; batch paused"
                    status = "failed (unexpected error)"
                else:
                    consecutive_failures = 0
                    status = result.status
                    if result.status == "checked":
                        final_status[key] = "checked"
                        report.review_reasons.pop(key, None)
                    elif result.status == "needs_review":
                        final_status[key] = "needs_review"
                        report.review_reasons[key] = ", ".join(result.uncertain) or "Checker disagreement"
                    else:
                        raise ValueError(f"Unexpected reference status: {result.status}")
            if status.startswith("skipped"):
                report.skipped += 1
            print(f"{index} of {total} {key}: {status}", flush=True)
            if report.stopped:
                report.skipped += total - index
                break
    except KeyboardInterrupt:
        report.stopped = "Interrupted by Ctrl-C; completed problems remain stored"
        if index:
            print(f"{index} of {total} {key}: interrupted", flush=True)
        report.skipped += total - index + (1 if index else 0)
    finally:
        report.checked = sum(status == "checked" for status in final_status.values())
        report.needs_review = sum(status == "needs_review" for status in final_status.values())
        rendered = _render(report, total)
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered)
        print(rendered, end="", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write and check NeetCode 150 references using spare capacity.")
    parser.add_argument("--limit", type=int, metavar="N", help="Attempt the first N unfinished problems")
    parser.add_argument("--slug", help="Attempt one NeetCode 150 problem")
    parser.add_argument("--retry-needs-review", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        report = run_batch(load_catalog().neetcode150(), limit=args.limit, slug=args.slug,
                           retry_needs_review=args.retry_needs_review)
    except ValueError as error:
        parser.error(str(error))
    return 1 if report.stopped or report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
