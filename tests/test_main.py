import os
import subprocess
import sys

from companion.store import open_store


def test_running_the_companion_creates_the_store_and_keeps_running(tmp_path):
    path = tmp_path / "companion.sqlite3"
    env = {**os.environ, "LEETOTTER_DB_PATH": str(path), "LEETOTTER_PORT": "0"}
    process = subprocess.Popen([sys.executable, "-m", "companion"], env=env, stdout=subprocess.PIPE, text=True)
    try:
        output = process.stdout.readline()
        assert str(path) in output
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert path.exists()
    with open_store(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
