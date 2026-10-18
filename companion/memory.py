"""Rebuild item memory from dated reviews using pinned FSRS settings."""

from dataclasses import dataclass
from datetime import datetime
import json

from fsrs import Card, Rating, Scheduler
from importlib.metadata import version


PINNED_VERSION = "6.3.2"
RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good}


@dataclass(frozen=True)
class ItemMemory:
    stability: float
    recall: float
    last_grade: str


def item_memory(db, item_type, item_key, now):
    """Read the log every time, so corrections and exclusions change the result."""
    if version("fsrs") != PINNED_VERSION:
        raise RuntimeError("Installed FSRS version differs from the pinned version")
    row = db.execute("SELECT library_version, parameters, desired_retention FROM fsrs WHERE id = 1").fetchone()
    if row is None:
        defaults = Scheduler()
        db.execute("INSERT INTO fsrs (id, library_version, parameters) VALUES (1, ?, ?)",
                   (PINNED_VERSION, json.dumps(defaults.parameters)))
        row = db.execute("SELECT library_version, parameters, desired_retention FROM fsrs WHERE id = 1").fetchone()
    library_version, parameters, retention = row
    if library_version != PINNED_VERSION:
        raise RuntimeError("Stored FSRS version differs from the pinned version")
    scheduler = Scheduler(parameters=json.loads(parameters), desired_retention=retention,
                          learning_steps=(), relearning_steps=(), enable_fuzzing=False)
    rows = db.execute("SELECT grade, reviewed_at FROM reviews WHERE item_type = ? AND item_key = ? AND grade IS NOT NULL ORDER BY reviewed_at, id",
                      (item_type, item_key)).fetchall()
    if not rows:
        return None
    card = Card()
    last_grade = None
    for grade, at in rows:
        if grade not in RATINGS:
            raise ValueError(f"Unknown review grade: {grade}")
        card, _ = scheduler.review_card(card, RATINGS[grade], datetime.fromisoformat(at))
        last_grade = grade
    return ItemMemory(card.stability, scheduler.get_card_retrievability(card, now), last_grade)
