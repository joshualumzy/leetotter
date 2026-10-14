import json
import asyncio

import pytest

from companion.catalog import Catalog
from companion.history import import_history, import_status
from companion.store import open_store
from companion.socket import _validate, _handle


CATALOG = Catalog([
    {"slug": "duplicate-integer", "link": "https://leetcode.com/problems/contains-duplicate/"},
    {"slug": "two-sum", "link": "https://leetcode.com/problems/two-sum/"},
])


def test_history_import_matches_unique_problems_and_reports_unmatched(tmp_path):
    path = tmp_path / "store.sqlite3"
    result = import_history([
        "https://leetcode.com/problems/contains-duplicate/",
        "https://leetcode.com/problems/contains-duplicate/?x=1",
        "https://leetcode.com/problems/two-sum/",
        "https://leetcode.com/problems/not-in-catalog/",
    ], path=path, catalog=CATALOG)
    assert result == {"already_imported": False, "matched": 2, "unmatched": ["https://leetcode.com/problems/not-in-catalog/"]}
    with open_store(path) as db:
        rows = db.execute("SELECT session_id, type, payload FROM events ORDER BY id").fetchall()
        assert [(session, kind, json.loads(payload)["slug"]) for session, kind, payload in rows] == [(None, "prior_solve", "duplicate-integer"), (None, "prior_solve", "two-sum")]
        assert db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
    assert import_status(path) is True
    assert import_history(["https://leetcode.com/problems/two-sum/"], path=path, catalog=CATALOG) == {"already_imported": True, "matched": 0, "unmatched": []}
    with open_store(path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


@pytest.mark.parametrize("urls", [None, {}, [1], [""], ["http://leetcode.com/problems/two-sum/"], ["https://evil.test/problems/two-sum/"]])
def test_malformed_import_is_rejected_without_marking_run(tmp_path, urls):
    path = tmp_path / "store.sqlite3"
    with pytest.raises(ValueError, match="Invalid import_history"):
        import_history(urls, path=path, catalog=CATALOG)
    assert import_status(path) is False


def test_wire_import_payload_is_validated():
    message = {"type": "import_history", "ts": "2026-10-15T00:00:00Z", "payload": {"urls": ["https://leetcode.com/problems/two-sum/"]}}
    assert _validate(json.dumps(message)) == message
    for payload in ({}, {"urls": "not a list"}, {"urls": [12]}, {"urls": ["https://evil.test/problems/two-sum/"]}):
        with pytest.raises(ValueError, match="import_history"):
            _validate(json.dumps({**message, "payload": payload}))


def test_socket_handler_reports_import_result_and_run_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LEETOTTER_DB_PATH", str(tmp_path / "store.sqlite3"))
    class FakeConnection:
        def __init__(self):
            self.sent = []
        async def send(self, raw):
            self.sent.append(json.loads(raw))
        def __aiter__(self):
            async def messages():
                yield json.dumps({"type": "import_history", "ts": "now", "payload": {"urls": ["https://leetcode.com/problems/contains-duplicate/", "https://leetcode.com/problems/not-in-catalog/"]}})
                yield json.dumps({"type": "import_history", "ts": "now", "payload": {"urls": []}})
                yield json.dumps({"type": "get_import_status", "ts": "now"})
            return messages()
    fake = FakeConnection()
    asyncio.run(_handle(fake))
    assert [message["type"] for message in fake.sent] == ["import_result", "import_result", "import_status"]
    assert fake.sent[0]["result"] == {"already_imported": False, "matched": 1, "unmatched": ["https://leetcode.com/problems/not-in-catalog/"]}
    assert fake.sent[1]["result"]["already_imported"] is True
    assert fake.sent[2]["imported"] is True
