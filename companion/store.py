"""SQLite store and versioned migrations."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DB_PATH = Path.home() / "Library/Application Support/Leetotter/companion.sqlite3"

MIGRATIONS = (
    (
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, budget_minutes INTEGER, summary TEXT, recap_bullets TEXT)",
        "CREATE TABLE events (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id), ts TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL)",
        "CREATE TABLE reviews (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id), event_id INTEGER REFERENCES events(id), item_type TEXT NOT NULL, item_key TEXT NOT NULL, grade TEXT NOT NULL, meets_expected INTEGER, first_attempt INTEGER NOT NULL, difficulty TEXT, grader_disagreement INTEGER NOT NULL DEFAULT 0, reviewed_at TEXT NOT NULL)",
        "CREATE TABLE fsrs (id INTEGER PRIMARY KEY CHECK (id = 1), library_version TEXT NOT NULL, parameters TEXT NOT NULL, strong_threshold_days INTEGER NOT NULL DEFAULT 21, desired_retention REAL NOT NULL DEFAULT 0.9)",
        "CREATE TABLE hypotheses (id INTEGER PRIMARY KEY, text TEXT NOT NULL, category TEXT, skill TEXT, status TEXT NOT NULL, cited_event_ids TEXT NOT NULL)",
        "CREATE TABLE teaching (id INTEGER PRIMARY KEY, category TEXT NOT NULL, framing TEXT NOT NULL, help_kind TEXT NOT NULL, outcome TEXT, next_review_grade TEXT)",
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL)",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        'CREATE TABLE "references" (id INTEGER PRIMARY KEY, problem_key TEXT NOT NULL UNIQUE, content TEXT NOT NULL, status TEXT NOT NULL)',
        "CREATE TABLE model_calls (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id), role TEXT NOT NULL, model TEXT NOT NULL, latency_ms INTEGER NOT NULL, prompt_tokens INTEGER, completion_tokens INTEGER, validation_result TEXT NOT NULL, prompt_text TEXT, created_at TEXT NOT NULL)",
        "INSERT INTO settings (key, value) VALUES ('independent_solving_minutes', '15'), ('quiet_and_gap_minutes', '5'), ('chime', 'true')",
    ),
)


@contextmanager
def open_store(path: str | Path | None = None):
    """Open the store, creating and migrating it on first use."""
    path = Path(path or os.environ.get("LEETOTTER_DB_PATH") or DEFAULT_DB_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError(f"Database schema version {version} is newer than this companion supports")
        for index in range(version, len(MIGRATIONS)):
            db.execute("BEGIN IMMEDIATE")
            with db:
                for statement in MIGRATIONS[index]:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {index + 1}")
        yield db
        db.commit()
    finally:
        db.close()
