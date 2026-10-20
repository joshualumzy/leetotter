"""Every test gets its own store unless it sets one, so no test can touch the real database."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "default-store.sqlite3"))
