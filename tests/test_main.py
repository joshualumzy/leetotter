from companion.__main__ import main
from companion.store import open_store


def test_running_the_companion_creates_the_store_and_reports_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / "companion.sqlite3"
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(path))

    main()

    assert path.exists()
    assert str(path) in capsys.readouterr().out
    with open_store(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
