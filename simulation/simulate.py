"""One pretend learner working through the NeetCode 150 under a code stand-in for the Planner (SPEC §8, §9)."""

import random
from dataclasses import dataclass, field

from simulation.catalog import CATEGORIES, Problem, build_problems
from simulation.memory import RETENTION, Item, Rating

# How much a difficulty scales the chance of failing: easy fails less, hard more.
FAIL_SCALE = {"E": 0.6, "M": 1.0, "H": 1.6}
IMPROVE_ATTEMPTS = 150
KINDS = ("new", "test", "retry", "refresh", "transfer", "extra", "filler")


@dataclass(frozen=True)
class Scenario:
    pace: float = 7  # problems a week
    clean: float = 0.7  # clean solves on a new medium problem
    clean_end: float | None = None  # if set, the learner improves linearly to this over the first IMPROVE_ATTEMPTS attempts
    brute_share: float = 0.2  # of the non-clean solves: brute force only (Hard, not done)
    questions_share: float = 0.2  # of the non-clean solves: solved after questions (Hard, done); the rest need help
    analysis_good: float | None = None  # right complexity with right reasoning, on a medium problem; None means the same as `clean`
    analysis_rate: float = 0.6  # share of first attempts that get a graded complexity question
    weak_analysis_rate: float = 0.9  # the same where the category's complexity skill is not yet strong (the Planner asks)
    refresh_strong: bool = True  # SPEC §8 refreshes any rusty category; False leaves strong ones alone
    imported: int = 75
    break_start: int | None = None  # day the break begins
    break_days: int = 0
    threshold: float = 21.0  # strong needs this much stability
    extras: bool = True
    extra_pool: int = 100  # problems available beyond the 150
    imported_first: float = 0.5  # when a category has imported and unseen problems left, chance the imported one is tested next
    extras_cap: float = 0.15  # extras as a share of attempts so far (plus 2)
    min_category_gap: int = 5  # days between first attempts in one category (mixing)
    retry_days: int = 3  # a problem needing help or brute force returns "a few days" later
    horizon_days: int = 730


@dataclass
class Category:
    solve: Item = field(default_factory=Item)
    analysis: Item = field(default_factory=Item)
    good_solve_hard: bool = False  # a Good solve review on a medium or hard problem
    good_analysis_hard: bool = False
    last_solve_day: float | None = None
    first_strong: dict = field(default_factory=lambda: {"solve": None, "analysis": None, "both": None})


@dataclass(frozen=True)
class Result:
    day_reached: int | None
    attempts: int
    day_coverage: int | None
    day_transfer: int | None
    day_all_attempted: int | None
    max_strong: int  # most categories strong at once
    kinds: dict
    first_strong: dict  # category -> {"solve", "analysis", "both"} -> day or None


class _Run:
    def __init__(self, sc: Scenario, seed: int) -> None:
        self.sc = sc
        self.rng = random.Random(seed)
        self.problems = build_problems(self.rng, sc.imported, sc.extra_pool)
        self.core = [p for p in self.problems if not p.extra]
        self.cats = {name: Category() for name in CATEGORIES}
        self.pools = {
            (name, extra): [p for p in self.problems if p.category == name and p.extra == extra]
            for name in CATEGORIES for extra in (False, True)
        }
        self.max_strong = 0
        self.day = 0
        self.attempts = 0
        self.kinds = dict.fromkeys(KINDS, 0)
        self.day_coverage = self.day_transfer = self.day_all_attempted = None

    # --- state ---------------------------------------------------------
    def strong(self, name: str, skill: str) -> bool:
        cat = self.cats[name]
        item, good_hard = (cat.solve, cat.good_solve_hard) if skill == "solve" else (cat.analysis, cat.good_analysis_hard)
        return item.reviewed and item.stability >= self.sc.threshold and item.last_grade != Rating.Again and good_hard

    def in_cat(self, name: str, extra: bool = False) -> list[Problem]:
        return self.pools[name, extra]

    def solid(self, name: str) -> bool:
        return all(p.done for p in self.in_cat(name))

    def eligible(self, name: str) -> bool:
        last = self.cats[name].last_solve_day
        return last is None or self.day - last >= self.sc.min_category_gap

    def extras_open(self) -> bool:
        used = sum(1 for p in self.problems if p.extra and p.graded)
        return self.sc.extras and used < self.sc.extras_cap * self.attempts + 2

    def unseen(self, name: str) -> list[Problem]:
        found = [p for p in self.in_cat(name) if p.unseen]
        return sorted(found, key=lambda p: p.difficulty == "E")  # medium and hard first; sort is stable

    def unseen_extra(self, name: str) -> list[Problem]:
        return [p for p in self.in_cat(name, extra=True) if p.unseen] if self.extras_open() else []

    # --- the Planner stand-in (SPEC §8) ---------------------------------
    def pick(self) -> tuple[Problem, str] | None:
        retries = [p for p in self.core if p.graded and not p.done and self.day - p.last_day >= self.sc.retry_days]
        if retries:
            return min(retries, key=lambda p: p.last_day), "retry"

        due = [n for n in self.cats if self.due(n) and (self.unseen(n) or self.unseen_extra(n))]
        for name in sorted(due, key=lambda n: (self.both_strong(n), self.cats[n].solve.recall(self.day))):
            checking = self.solid(name) and not self.both_strong(name)
            if self.unseen(name):
                return self.unseen(name)[0], "transfer" if checking else "refresh"
            return self.unseen_extra(name)[0], "extra"

        for name in self.cats:  # solid but never tested on a new problem: imported solves only
            if not self.cats[name].solve.reviewed and self.solid(name):
                if self.unseen(name):
                    return self.unseen(name)[0], "transfer"
                if self.unseen_extra(name):
                    return self.unseen_extra(name)[0], "extra"

        open_cats = [n for n in self.cats if self.eligible(n) and any(p.graded == 0 for p in self.in_cat(n))]
        if open_cats:
            name = min(open_cats, key=lambda n: (sum(p.done for p in self.in_cat(n)) / len(self.in_cat(n)), self.rng.random()))
            ungraded = [p for p in self.in_cat(name) if p.graded == 0]
            imported = [p for p in ungraded if p.imported]
            fresh = [p for p in ungraded if not p.imported]
            if imported and (not fresh or self.rng.random() < self.sc.imported_first):
                return self.rng.choice(imported), "test"
            return self.rng.choice(fresh), "new"

        filler = [p for p in self.core if p.done and p.last_day != self.day]
        if filler:
            return min(filler, key=lambda p: p.memory.recall(self.day)), "filler"
        return None

    def due(self, name: str) -> bool:
        """Rusty (SPEC §9), or its complexity skill is weak and has slipped below 0.9 too."""
        cat = self.cats[name]
        if not cat.solve.reviewed or cat.last_solve_day >= self.day:
            return False
        if self.both_strong(name) and not self.sc.refresh_strong:
            return False
        weak_analysis = cat.analysis.reviewed and not self.strong(name, "analysis")
        return cat.solve.recall(self.day) < RETENTION or (weak_analysis and cat.analysis.recall(self.day) < RETENTION)

    def both_strong(self, name: str) -> bool:
        return self.strong(name, "solve") and self.strong(name, "analysis")

    # --- one attempt (SPEC §8 grades) -----------------------------------
    def fail_chance(self, difficulty: str, success: float) -> float:
        return min(1.0, (1 - success) * FAIL_SCALE[difficulty])

    def clean_now(self) -> float:
        if self.sc.clean_end is None:
            return self.sc.clean
        progress = min(1.0, self.attempts / IMPROVE_ATTEMPTS)
        return self.sc.clean + (self.sc.clean_end - self.sc.clean) * progress

    def solve_outcome(self, p: Problem) -> tuple[Rating, bool]:
        success = p.memory.recall(self.day) if p.graded else self.clean_now()
        if self.rng.random() >= self.fail_chance(p.difficulty, success):
            return Rating.Good, True
        roll = self.rng.random()
        if roll < self.sc.brute_share:
            return Rating.Hard, False
        if roll < self.sc.brute_share + self.sc.questions_share:
            return Rating.Hard, True
        return Rating.Again, False

    def analysis_outcome(self, p: Problem) -> Rating:
        miss = self.fail_chance(p.difficulty, self.clean_now() if self.sc.analysis_good is None else self.sc.analysis_good)
        roll = self.rng.random()
        if roll >= miss:
            return Rating.Good
        return Rating.Hard if roll < miss * 2 / 3 else Rating.Again

    def attempt(self, p: Problem, kind: str) -> None:
        first = p.unseen
        grade, done = self.solve_outcome(p)
        p.memory.review(grade, self.day)
        p.graded += 1
        p.last_day = self.day
        p.done = done and not p.extra
        self.attempts += 1
        self.kinds[kind] += 1
        if not first:
            return
        cat = self.cats[p.category]
        category_grade = Rating.Hard if grade == Rating.Good and p.difficulty == "E" else grade
        cat.solve.review(category_grade, self.day)
        cat.last_solve_day = self.day
        cat.good_solve_hard |= category_grade == Rating.Good and p.difficulty != "E"
        rate = self.sc.analysis_rate if self.strong(p.category, "analysis") else self.sc.weak_analysis_rate
        if grade != Rating.Again and self.rng.random() < rate:
            analysis_grade = self.analysis_outcome(p)
            cat.analysis.review(analysis_grade, self.day)
            cat.good_analysis_hard |= analysis_grade == Rating.Good and p.difficulty != "E"
        for skill in ("solve", "analysis"):
            if cat.first_strong[skill] is None and self.strong(p.category, skill):
                cat.first_strong[skill] = self.day
        if cat.first_strong["both"] is None and self.both_strong(p.category):
            cat.first_strong["both"] = self.day

    def bar_met(self) -> bool:
        coverage = all(p.done for p in self.core)
        strong = sum(self.both_strong(n) for n in self.cats)
        self.max_strong = max(self.max_strong, strong)
        transfer = strong == len(self.cats)
        if self.day_coverage is None and coverage:
            self.day_coverage = self.day
        if self.day_transfer is None and transfer:
            self.day_transfer = self.day
        if self.day_all_attempted is None and all(p.graded for p in self.core):
            self.day_all_attempted = self.day
        return coverage and transfer

    # --- the calendar ---------------------------------------------------
    def run(self) -> Result:
        sc = self.sc
        budget, broke, reached = 0.0, False, None
        while reached is None and self.day <= sc.horizon_days:
            gap = self.rng.randint(2, 7)
            budget += sc.pace * gap / 7
            count, budget = int(budget), budget - int(budget)
            for _ in range(count):
                choice = self.pick()
                if choice is None:
                    break
                self.attempt(*choice)
                if self.bar_met():
                    reached = self.day
                    break
            self.day += gap
            if sc.break_start is not None and not broke and self.day >= sc.break_start:
                self.day += sc.break_days
                broke = True
        return Result(
            reached, self.attempts, self.day_coverage, self.day_transfer, self.day_all_attempted,
            self.max_strong, self.kinds, {n: c.first_strong for n, c in self.cats.items()},
        )


def simulate(sc: Scenario, seed: int) -> Result:
    return _Run(sc, seed).run()
