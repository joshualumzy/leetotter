"""One FSRS item, using py-fsrs the way SPEC §8 pins it: defaults, no learning steps, 0.9 retention."""

from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler

__all__ = ["Item", "Rating", "RETENTION"]

RETENTION = 0.9
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
# Fuzzing only jitters scheduled intervals, which the simulation never uses; off keeps runs repeatable.
_SCHEDULER = Scheduler(
    desired_retention=RETENTION,
    learning_steps=(),
    relearning_steps=(),
    enable_fuzzing=False,
)


def _at(day: float) -> datetime:
    return _EPOCH + timedelta(days=day)


class Item:
    """A problem or a (category, skill). Time is in days from the simulation start."""

    def __init__(self) -> None:
        self.card = Card()
        self.last_grade: Rating | None = None

    @property
    def reviewed(self) -> bool:
        return self.last_grade is not None

    @property
    def stability(self) -> float:
        return self.card.stability or 0.0

    def review(self, grade: Rating, day: float) -> None:
        self.card, _ = _SCHEDULER.review_card(self.card, grade, _at(day))
        self.last_grade = grade

    def recall(self, day: float) -> float:
        return _SCHEDULER.get_card_retrievability(self.card, _at(day))
