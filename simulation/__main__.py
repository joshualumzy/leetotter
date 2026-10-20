"""Run every scenario of SPEC §16 and print the report tables as Markdown: `uv run python -m simulation`."""

import argparse
import statistics
from dataclasses import replace

from simulation.catalog import CATEGORIES
from simulation.runner import SEEDS, run_all
from simulation.simulate import Result, Scenario

PACES = (5, 7, 10, 15)
CLEANS = (0.5, 0.7, 0.9)
WIDE = {"extra_pool": 300, "extras_cap": 1.0}  # every free NeetCode problem, no cap on extras
BREAK_AT = 56  # day 56: week 8, during the first stretch of practice
LATE_BREAK_AT = 150


def weeks(days: float | None) -> str:
    return "-" if days is None else f"{days / 7:.0f}"


def median(values: list[int]) -> float | None:
    return statistics.median(values) if values else None


def summary_row(label: str, results: list[Result]) -> str:
    reached = [r for r in results if r.day_reached is not None]
    days = sorted(r.day_reached for r in reached)
    spread = f"{weeks(days[0])}-{weeks(days[-1])}" if days else "-"
    def real(r: Result) -> int:
        return max(1, sum(v for k, v in r.kinds.items() if k != "filler"))

    work = [sum(v for k, v in r.kinds.items() if k != "filler") for r in results]
    retry = statistics.median(r.kinds["retry"] / real(r) for r in results)
    review = statistics.median(r.kinds["review"] / real(r) for r in results)
    early = statistics.median(r.early_returns / max(1, r.early_attempts) for r in results)
    return (
        f"| {label} | {len(reached)}/{len(results)} | {weeks(median(days))} | {spread} "
        f"| {weeks(median([r.day_coverage for r in results if r.day_coverage]))} "
        f"| {weeks(median([r.day_transfer for r in results if r.day_transfer]))} "
        f"| {statistics.median(r.max_strong for r in results):.0f} "
        f"| {statistics.median(work):.0f} | {retry:.0%} | {review:.0%} | {early:.0%} "
        f"| {statistics.median(r.kinds['extra'] for r in results):.0f} "
        f"| {statistics.median(r.kinds['filler'] for r in results):.0f} |"
    )


HEADER = (
    "| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong "
    "| Max cats strong at once | Real attempts | Retry share | Review share | Retry+review share wk 1-26 "
    "| Extras | Idle slots |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def table(title: str, scenarios: dict[str, Scenario]) -> None:
    grouped = run_all(scenarios)
    print(f"\n### {title}\n\n{HEADER}")
    for label in scenarios:
        print(summary_row(label, grouped[label]))


def category_table(title: str, scenarios: dict[str, Scenario]) -> None:
    grouped = run_all(scenarios)
    print(f"\n### {title}\n\nShare of runs in which the category was strong for both skills at some point.\n")
    print("| Category | Size | " + " | ".join(scenarios) + " |\n|---|---|" + "---|" * len(scenarios))
    for name, mix in CATEGORIES.items():
        cells = [
            f"{sum(r.first_strong[name]['both'] is not None for r in grouped[label]) / SEEDS:.0%}"
            for label in scenarios
        ]
        print(f"| {name} | {sum(mix)} | " + " | ".join(cells) + " |")
    for skill in ("solve", "analysis"):
        cells = [f"{sum(r.first_strong['Tries'][skill] is not None for r in grouped[label]) / SEEDS:.0%}" for label in scenarios]
        print(f"| Tries, {skill} only | 3 | " + " | ".join(cells) + " |")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m simulation")
    parser.add_argument("--only-split", action="store_true", help="print only table I")
    only_split = parser.parse_args().only_split

    base = Scenario()
    print(f"Seeds per scenario: {SEEDS}. Weeks are rounded. Horizon: {base.horizon_days} days.")

    table(
        "I. Split memory (done problems return when due), 7/wk",
        {
            f"{c:.0%} clean, {pool}, {variant}": replace(base, pace=7, clean=c, **kw, **policy)
            for c in (0.9, 0.7)
            for pool, kw in (("spec pool", {}), ("wide pool", WIDE))
            for variant, policy in (
                ("current", {}),
                ("split 0.9", {"policy": "split", "done_retention": 0.9}),
                ("split 0.8", {"policy": "split", "done_retention": 0.8}),
                ("split 0.9, sticky done", {"policy": "split", "sticky_done": True}),
                ("split 0.9, reviews last", {"policy": "split", "reviews_last": True}),
                ("split 0.9, reviews last, sticky done", {"policy": "split", "reviews_last": True, "sticky_done": True}),
            )
        },
    )
    if only_split:
        return

    table(
        "A. Pace x clean-solve rate, SPEC as written (100 extras, capped at 15% of attempts)",
        {f"{p}/wk, {c:.0%} clean": replace(base, pace=p, clean=c) for p in PACES for c in CLEANS},
    )
    table(
        "B. Pace x clean-solve rate, wide problem pool (300 extras, uncapped)",
        {f"{p}/wk, {c:.0%} clean": replace(base, pace=p, clean=c, **WIDE) for p in PACES for c in CLEANS},
    )
    table(
        "C. Breaks at 7/wk, wide pool",
        {
            f"{c:.0%} clean, {name}": replace(base, clean=c, break_start=start, break_days=days, **WIDE)
            for c in (0.7, 0.9)
            for name, start, days in (
                ("no break", None, 0),
                ("2 weeks at day 56", BREAK_AT, 14),
                ("3 months at day 56", BREAK_AT, 91),
                ("3 months at day 150", LATE_BREAK_AT, 91),
            )
        },
    )
    table(
        "D. Learner who improves (clean rate rises linearly over the first 150 attempts), 7/wk",
        {
            f"{c:.0%} to {e:.0%}, {name}": replace(base, clean=c, clean_end=e, **kw)
            for c, e in ((0.5, 0.9), (0.7, 0.9))
            for name, kw in (("spec pool", {}), ("wide pool", WIDE))
        },
    )
    table(
        "E. Strong threshold, 7/wk, wide pool",
        {
            f"{days}d, {label}": replace(base, threshold=days, clean=c, clean_end=e, **WIDE)
            for days in (14, 21, 28, 35)
            for label, c, e in (("90% clean", 0.9, None), ("70% to 90%", 0.7, 0.9), ("70% clean", 0.7, None))
        },
    )
    table(
        "F. Complexity-question rate, 7/wk, 90% clean, wide pool",
        {
            f"asked {a:.0%} when strong, {w:.0%} when weak": replace(base, clean=0.9, analysis_rate=a, weak_analysis_rate=w, **WIDE)
            for a, w in ((0.3, 0.5), (0.6, 0.9), (1.0, 1.0))
        },
    )
    table(
        "G. Policy variants, 7/wk, 90% clean",
        {
            "SPEC as written": replace(base, clean=0.9),
            "strong categories not refreshed": replace(base, clean=0.9, refresh_strong=False),
            "imported solves tested by same problem first": replace(base, clean=0.9, imported_first=1.0),
            "no import (0 of 150 seen)": replace(base, clean=0.9, imported=0),
        },
    )
    category_table(
        "H. Every category, 7/wk, 90% clean, by pool of extra problems",
        {f"{pool} extras": replace(base, clean=0.9, extra_pool=pool, extras_cap=1.0) for pool in (0, 100, 300)},
    )


if __name__ == "__main__":
    main()
