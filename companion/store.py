"""SQLite store and versioned migrations."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
    ),
    (
        "CREATE TABLE reviews_v2 (id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id), event_id INTEGER REFERENCES events(id), item_type TEXT NOT NULL, item_key TEXT NOT NULL, grade TEXT, meets_expected INTEGER, first_attempt INTEGER NOT NULL, difficulty TEXT, grader_disagreement INTEGER NOT NULL DEFAULT 0, reviewed_at TEXT NOT NULL, failed_grader TEXT)",
        "INSERT INTO reviews_v2 (id, session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, difficulty, grader_disagreement, reviewed_at) SELECT id, session_id, event_id, item_type, item_key, grade, meets_expected, first_attempt, difficulty, grader_disagreement, reviewed_at FROM reviews",
        "DROP TABLE reviews",
        "ALTER TABLE reviews_v2 RENAME TO reviews",
        "CREATE TABLE handled_attempts (start_event_id INTEGER PRIMARY KEY REFERENCES events(id))",
        "CREATE TABLE grader_retries (start_event_id INTEGER PRIMARY KEY REFERENCES events(id), failed_tries INTEGER NOT NULL)",
    ),
    (
        "CREATE TABLE grading_failures (start_event_id INTEGER PRIMARY KEY REFERENCES events(id), message TEXT NOT NULL)",
    ),
    (
        "DELETE FROM reviews WHERE event_id IS NOT NULL AND id IN ("
        "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY event_id, item_type "
        "ORDER BY (grade IS NOT NULL) DESC, id) AS rank FROM reviews WHERE event_id IS NOT NULL) "
        "WHERE rank > 1)",
        "CREATE UNIQUE INDEX reviews_event_item_unique ON reviews(event_id, item_type)",
    ),
    (
        "ALTER TABLE hypotheses ADD COLUMN rejected_note_id INTEGER REFERENCES notes(id)",
        "ALTER TABLE hypotheses ADD COLUMN cited_note_id INTEGER REFERENCES notes(id)",
        "ALTER TABLE teaching ADD COLUMN problem_slug TEXT",
        "ALTER TABLE teaching ADD COLUMN cited_event_ids TEXT",
        "ALTER TABLE teaching ADD COLUMN idea TEXT",
        "ALTER TABLE notes ADD COLUMN session_id INTEGER REFERENCES sessions(id)",
        "ALTER TABLE hypotheses ADD COLUMN rejected_after_event_id INTEGER",
    ),
    (
        "CREATE TABLE reflector_failures (session_id INTEGER PRIMARY KEY REFERENCES sessions(id), message TEXT NOT NULL)",
    ),
    (
        "ALTER TABLE sessions DROP COLUMN budget_minutes",
    ),
    (
        "DELETE FROM events WHERE type IN ('overrun_prompted', 'overrun_choice')",
    ),
    (
        "ALTER TABLE sessions ADD COLUMN recap_seen INTEGER NOT NULL DEFAULT 0",
        "UPDATE sessions SET recap_seen = 1 WHERE recap_bullets IS NOT NULL",
    ),
    (
        "ALTER TABLE sessions ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "ALTER TABLE hypotheses ADD COLUMN recap_session_id INTEGER REFERENCES sessions(id)",
        "CREATE TABLE recap_feedback_failures (session_id INTEGER PRIMARY KEY REFERENCES sessions(id), message TEXT NOT NULL)",
    ),
    (
        "CREATE TABLE recap_feedback_pending (session_id INTEGER PRIMARY KEY REFERENCES sessions(id), note_id INTEGER NOT NULL REFERENCES notes(id))",
    ),
    (
        "ALTER TABLE reviews ADD COLUMN corrected INTEGER NOT NULL DEFAULT 0",
    ),
)

PERSONAL_TABLES = (
    "grading_failures", "grader_retries", "handled_attempts", "reviews", "hypotheses", "teaching",
    "model_calls", "reflector_failures", "recap_feedback_failures", "recap_feedback_pending", "events", "notes",
    "sessions", "settings",
)


def store_path(path: str | Path | None = None) -> Path:
    """Where the store lives: the given path, else LEETOTTER_DB_PATH, else the default."""
    return Path(path or os.environ.get("LEETOTTER_DB_PATH") or DEFAULT_DB_PATH).expanduser()


SNAPSHOT_DAYS = 14


def snapshot_store(path: str | Path | None, reason: str) -> Path | None:
    """Copy the store into a snapshots folder beside it; None if there is no store yet.

    Kept: the newest copy, plus the first copy of each reason on each of the last SNAPSHOT_DAYS days with copies,
    so a bug noticed days later can still be rolled back, and a reset's copy survives the restarts after it."""
    path = store_path(path)
    if not path.exists():
        return None
    folder = path.parent / f"{path.stem}-snapshots"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{stamp}-{reason}.sqlite3"
    source = sqlite3.connect(path)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        copy.close()
        source.close()
    snapshots = sorted(folder.glob("*.sqlite3"))
    days = set(sorted({snapshot.name[:8] for snapshot in snapshots})[-SNAPSHOT_DAYS:])
    keep, firsts = {target}, set()
    for snapshot in snapshots:
        day, kind = snapshot.name[:8], snapshot.stem.split("-", 1)[1]
        if day in days and (day, kind) not in firsts:
            firsts.add((day, kind))
            keep.add(snapshot)
    for old in snapshots:
        if old not in keep:
            old.unlink()
    return target


@contextmanager
def open_store(path: str | Path | None = None):
    """Open the store, creating and migrating it on first use."""
    path = store_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        stored = db.execute("PRAGMA user_version").fetchone()[0]
        snapshot = snapshot_store(path, "before-migration") if 0 < stored < len(MIGRATIONS) else None
        try:
            _migrate(db)
        except Exception as error:
            if snapshot:
                raise RuntimeError(f"Migration failed ({error}); the store before it is saved at {snapshot}") from error
            raise
        yield db
        db.commit()
    finally:
        db.close()


def _migrate(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError(f"Database schema version {version} is newer than this companion supports")
        for index in range(version, len(MIGRATIONS)):
            for statement in MIGRATIONS[index]:
                db.execute(statement)
            db.execute(f"PRAGMA user_version = {index + 1}")


def reset_progress(path: str | Path | None = None):
    """Erase everything personal in one transaction; references, fsrs and the catalog stay (SPEC §2). Snapshots first."""
    snapshot_store(path, "before-reset")
    with open_store(path) as db:
        db.execute("BEGIN IMMEDIATE")
        for table in PERSONAL_TABLES:
            db.execute(f"DELETE FROM {table}")
