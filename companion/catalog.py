"""Stable lookups over the checked-in NeetCode catalog."""

import json
from pathlib import Path
from urllib.parse import urlsplit


CATALOG_PATH = Path(__file__).parent / "data" / "catalog.json"
CATEGORIES = ("Arrays & Hashing", "Two Pointers", "Sliding Window", "Stack", "Binary Search",
              "Linked List", "Trees", "Heap / Priority Queue", "Backtracking", "Tries", "Graphs",
              "Advanced Graphs", "1-D Dynamic Programming", "2-D Dynamic Programming", "Greedy",
              "Intervals", "Math & Geometry", "Bit Manipulation")


def normalize_link(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc.lower() not in {"leetcode.com", "www.leetcode.com"}:
        return None
    path = parts.path.strip("/").split("/")
    if len(path) != 2 or path[0] != "problems" or not path[1]:
        return None
    return f"https://leetcode.com/problems/{path[1]}/"


class Catalog:
    def __init__(self, entries: list[dict]):
        self.entries = entries
        self._slugs = {entry["slug"]: entry for entry in entries}
        self._links = {entry["link"]: entry for entry in entries}

    def by_slug(self, slug: str) -> dict | None:
        return self._slugs.get(slug)

    def by_link(self, url: str) -> dict | None:
        return self._links.get(normalize_link(url))

    def neetcode150(self) -> list[dict]:
        return sorted((entry for entry in self.entries if entry["neetcode150"]),
                      key=lambda entry: CATEGORIES.index(entry["category"]))

    def free_extras(self, category: str) -> list[dict]:
        return [entry for entry in self.entries if entry["category"] == category
                and not entry["neetcode150"] and not entry["locked"]]


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    return Catalog(json.loads(path.read_text()))
