"""One-time import of NeetCode solves without inventing review grades or dates."""

import json
from datetime import datetime, timezone
from pathlib import Path

from companion.catalog import Catalog, load_catalog, normalize_link
from companion.store import open_store


SETTING = "neetcode_history_imported"


def import_status(path: str | Path | None = None) -> bool:
    with open_store(path) as db:
        return db.execute("SELECT 1 FROM settings WHERE key = ?", (SETTING,)).fetchone() is not None


def import_history(urls, *, path: str | Path | None = None, catalog: Catalog | None = None) -> dict:
    if not isinstance(urls, list) or any(not isinstance(url, str) or normalize_link(url) is None for url in urls):
        raise ValueError("Invalid import_history URLs: expected LeetCode problem URLs")
    catalog = catalog or load_catalog()
    with open_store(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM settings WHERE key = ?", (SETTING,)).fetchone():
            return {"already_imported": True, "matched": 0, "unmatched": []}
        matched = {}
        unmatched = []
        for url in urls:
            problem = catalog.by_link(url)
            if problem is None:
                if url not in unmatched:
                    unmatched.append(url)
            else:
                matched[problem["slug"]] = problem
        now = datetime.now(timezone.utc).isoformat()
        for problem in matched.values():
            db.execute("INSERT INTO events (session_id, ts, type, payload) VALUES (NULL, ?, 'prior_solve', ?)",
                       (now, json.dumps({"slug": problem["slug"], "source": "neetcode"})))
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (SETTING, now))
        return {"already_imported": False, "matched": len(matched), "unmatched": unmatched}
