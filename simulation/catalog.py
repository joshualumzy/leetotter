"""The NeetCode 150: category sizes and difficulty mix (easy, medium, hard), plus the extra pool beyond it."""

import random
from dataclasses import dataclass, field

from simulation.memory import Item

# Difficulty counts per category, from the NeetCode 150 list as recalled; they total 28 / 101 / 21.
CATEGORIES: dict[str, tuple[int, int, int]] = {
    "Arrays & Hashing": (3, 6, 0),
    "Two Pointers": (1, 3, 1),
    "Sliding Window": (1, 3, 2),
    "Stack": (1, 5, 1),
    "Binary Search": (1, 5, 1),
    "Linked List": (3, 6, 2),
    "Trees": (6, 7, 2),
    "Heap": (2, 4, 1),
    "Backtracking": (0, 8, 1),
    "Tries": (0, 2, 1),
    "Graphs": (0, 12, 1),
    "Advanced Graphs": (0, 3, 3),
    "1-D DP": (2, 10, 0),
    "2-D DP": (0, 7, 4),
    "Greedy": (0, 8, 0),
    "Intervals": (1, 4, 1),
    "Math & Geometry": (2, 6, 0),
    "Bit Manipulation": (5, 2, 0),
}
EXTRA_POOL = 100  # default: the extra 100 problems of NeetCode's 250 list, spread by category size
EXTRA_HARD_SHARE = 0.3  # extras are assumed to be medium or hard only


@dataclass(eq=False)
class Problem:
    category: str
    difficulty: str  # "E", "M" or "H"
    extra: bool = False
    imported: bool = False
    memory: Item = field(default_factory=Item)
    graded: int = 0
    last_day: float | None = None
    done: bool = False

    @property
    def unseen(self) -> bool:
        return not self.imported and self.graded == 0


def _extra_counts(pool: int) -> dict[str, int]:
    sizes = {name: sum(mix) for name, mix in CATEGORIES.items()}
    raw = {name: pool * size / 150 for name, size in sizes.items()}
    counts = {name: int(value) for name, value in raw.items()}
    leftovers = sorted(raw, key=lambda name: raw[name] - counts[name], reverse=True)
    for name in leftovers[: pool - sum(counts.values())]:
        counts[name] += 1
    return counts


def build_problems(rng: random.Random, imported: int, extra_pool: int = EXTRA_POOL) -> list[Problem]:
    problems = [
        Problem(name, difficulty)
        for name, mix in CATEGORIES.items()
        for difficulty, count in zip("EMH", mix)
        for _ in range(count)
    ]
    for problem in rng.sample(problems, imported):
        problem.imported = True
    for name, count in _extra_counts(extra_pool).items():
        for _ in range(count):
            hard = rng.random() < EXTRA_HARD_SHARE
            problems.append(Problem(name, "H" if hard else "M", extra=True))
    return problems
