import logging
import os
import subprocess
import sys
import time

from companion.__main__ import configure_logging
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 13


def test_startup_line_is_written_to_configured_log(tmp_path):
    path = tmp_path / "companion.sqlite3"
    log = tmp_path / "logs/companion.log"
    env = {**os.environ, "LEETOTTER_DB_PATH": str(path), "LEETOTTER_PORT": "0", "LEETOTTER_LOG_PATH": str(log)}
    process = subprocess.Popen([sys.executable, "-m", "companion"], env=env, stdout=subprocess.PIPE, text=True)
    try:
        output = process.stdout.readline()
        assert str(path) in output
        deadline = time.monotonic() + 5
        while not log.exists() or "Leetotter companion: store" not in log.read_text():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert str(path) in log.read_text()


def test_log_handler_rotates_at_size_limit(tmp_path):
    log = tmp_path / "companion.log"
    root = logging.getLogger()
    previous = root.handlers[:]
    try:
        configure_logging(log, max_bytes=200)
        logger = logging.getLogger("test.rotation")
        for i in range(20):
            logger.info("line %s %s", i, "x" * 60)
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in previous:
            root.addHandler(handler)

    assert log.exists()
    assert (tmp_path / "companion.log.1").exists()
