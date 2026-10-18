from companion.store import open_store


def test_first_open_creates_all_spec_tables_and_schema_version(tmp_path):
    path = tmp_path / "companion.sqlite3"

    with open_store(path) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = db.execute("PRAGMA user_version").fetchone()[0]

    assert tables >= {
        "events", "sessions", "reviews", "fsrs", "hypotheses", "teaching",
        "notes", "settings", "references", "model_calls",
    }
    assert version == 8
    assert path.exists()


def test_reopen_preserves_data_and_does_not_reapply_migration(tmp_path):
    path = tmp_path / "companion.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO notes (ts, kind, text) VALUES ('2026-01-01', 'preference', 'quiet')")

    with open_store(path) as db:
        notes = db.execute("SELECT text FROM notes").fetchall()
        version = db.execute("PRAGMA user_version").fetchone()[0]

    assert notes == [("quiet",)]
    assert version == 8


def test_environment_can_set_default_store_path(tmp_path, monkeypatch):
    path = tmp_path / "custom.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    with open_store() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8

    assert path.exists()


def test_retired_overrun_events_are_removed_from_an_existing_store(tmp_path):
    path = tmp_path / "old.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-17T10:00:00+00:00')")
        for kind in ("session_started", "overrun_prompted", "overrun_choice", "focus_changed"):
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (1, '2026-10-17T10:00:00+00:00', ?, '{}')", (kind,))
        db.execute("PRAGMA user_version = 7")
    with open_store(path) as db:
        kinds = [row[0] for row in db.execute("SELECT type FROM events ORDER BY id")]
    assert kinds == ["session_started", "focus_changed"]
