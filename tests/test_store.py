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
    assert version == 9
    assert path.exists()


def test_reopen_preserves_data_and_does_not_reapply_migration(tmp_path):
    path = tmp_path / "companion.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO notes (ts, kind, text) VALUES ('2026-01-01', 'preference', 'quiet')")

    with open_store(path) as db:
        notes = db.execute("SELECT text FROM notes").fetchall()
        version = db.execute("PRAGMA user_version").fetchone()[0]

    assert notes == [("quiet",)]
    assert version == 9


def test_environment_can_set_default_store_path(tmp_path, monkeypatch):
    path = tmp_path / "custom.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    with open_store() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9

    assert path.exists()


def test_recap_seen_migration_marks_existing_ready_recaps_as_seen(tmp_path):
    path = tmp_path / "old.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets) VALUES ('2026-10-17T10:00:00+00:00', '2026-10-17T10:30:00+00:00', 'Done.', '[\"A bullet\"]')")
        db.execute("INSERT INTO sessions (started_at, ended_at) VALUES ('2026-10-17T11:00:00+00:00', '2026-10-17T11:30:00+00:00')")
        db.execute("INSERT INTO sessions (started_at, ended_at, summary, recap_bullets) VALUES ('2026-10-17T12:00:00+00:00', '2026-10-17T12:30:00+00:00', 'Smooth.', '[]')")
        db.execute("ALTER TABLE sessions DROP COLUMN recap_seen")
        db.execute("PRAGMA user_version = 8")

    with open_store(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("SELECT id, recap_seen FROM sessions ORDER BY id").fetchall() == [(1, 1), (2, 0), (3, 1)]
        db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-17T13:00:00+00:00')")
        assert db.execute("SELECT recap_seen FROM sessions WHERE id = 4").fetchone()[0] == 0


def test_retired_overrun_events_are_removed_from_an_existing_store(tmp_path):
    path = tmp_path / "old.sqlite3"
    with open_store(path) as db:
        db.execute("INSERT INTO sessions (started_at) VALUES ('2026-10-17T10:00:00+00:00')")
        for kind in ("session_started", "overrun_prompted", "overrun_choice", "focus_changed"):
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (1, '2026-10-17T10:00:00+00:00', ?, '{}')", (kind,))
        db.execute("ALTER TABLE sessions DROP COLUMN recap_seen")
        db.execute("PRAGMA user_version = 7")
    with open_store(path) as db:
        kinds = [row[0] for row in db.execute("SELECT type FROM events ORDER BY id")]
    assert kinds == ["session_started", "focus_changed"]
