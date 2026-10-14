from pathlib import Path
import re

import pytest

from companion.catalog import Catalog, load_catalog
from companion.catalog_update import extract_entries, script_url, update_catalog


SAMPLE = (Path(__file__).parent / "fixtures/neetcode-sample.js").read_text()
BUNDLE = Path("/private/tmp/claude-501/-Users-joshualum-Documents-VSCode-leetotter/645bdcad-8484-4f30-af21-e286018b4d8a/scratchpad/neetcode-bundle/main.js")


def test_extracts_flags_locked_and_escaped_titles_and_skips_missing_editor_link():
    entries = extract_entries(SAMPLE, check_counts=False)
    assert len(entries) == 3
    assert entries[0] == {"slug": "duplicate-integer", "title": "Contains Duplicate", "category": "Arrays & Hashing", "difficulty": "Easy", "link": "https://leetcode.com/problems/contains-duplicate/", "neetcode150": True, "neetcode250": True, "blind75": True, "locked": False}
    assert entries[1]["title"] == 'A "Quoted" Problem'
    assert entries[2]["locked"] is True
    assert entries[2]["neetcode150"] is False


def test_catalog_lookup_and_free_extras():
    catalog = Catalog(extract_entries(SAMPLE, check_counts=False))
    assert catalog.by_slug("duplicate-integer")["title"] == "Contains Duplicate"
    assert catalog.by_link("https://leetcode.com/problems/contains-duplicate/?envType=study-plan-v2") == catalog.by_slug("duplicate-integer")
    assert catalog.by_link("https://leetcode.com/problems/unknown/") is None
    assert [p["slug"] for p in catalog.neetcode150()] == ["duplicate-integer"]
    assert [p["slug"] for p in catalog.free_extras("Trees")] == ["quoted-problem"]
    assert catalog.free_extras("Graphs") == []


def test_updater_finds_script_and_fails_on_shape_change(tmp_path):
    assert script_url('<script src="/assets/main.a1b2.js"></script>') == "https://neetcode.io/assets/main.a1b2.js"
    def fake(url):
        return '<script src="/assets/main.a1b2.js"></script>' if url == "https://neetcode.io" else SAMPLE
    target = tmp_path / "catalog.json"
    update_catalog(target, fetch=fake, check_counts=False)
    assert len(load_catalog(target).entries) == 3
    with pytest.raises(ValueError, match="main.*js"):
        script_url("<html></html>")
    with pytest.raises(ValueError, match="No problem entries"):
        extract_entries("const x=[]", check_counts=False)
    with pytest.raises(ValueError, match="missing difficulty"):
        extract_entries('{problem:"Title",pattern:"Trees",link:"title/",ncLink:"title/"}', check_counts=False)


def test_checked_in_catalog_sizes():
    catalog = load_catalog()
    assert len(catalog.entries) == 588
    assert sum(p["neetcode150"] for p in catalog.entries) == 150
    assert sum(p["neetcode250"] for p in catalog.entries) == 250
    assert sum(p["blind75"] for p in catalog.entries) == 75
    assert sum(p["locked"] for p in catalog.entries) == 149
    assert not any(p["locked"] and (p["neetcode150"] or p["neetcode250"]) for p in catalog.entries)
    assert sum(not p["locked"] and not p["neetcode150"] for p in catalog.entries) == 289
    assert [sum(p["category"] == category for p in catalog.neetcode150()) for category in ("Arrays & Hashing", "Two Pointers", "Sliding Window", "Stack", "Binary Search", "Linked List", "Trees", "Heap / Priority Queue", "Backtracking", "Tries", "Graphs", "Advanced Graphs", "1-D Dynamic Programming", "2-D Dynamic Programming", "Greedy", "Intervals", "Math & Geometry", "Bit Manipulation")] == [9, 5, 6, 6, 7, 11, 15, 7, 10, 3, 13, 6, 12, 11, 8, 6, 8, 7]
    assert [p["category"] for p in catalog.neetcode150()] == sorted(
        [p["category"] for p in catalog.neetcode150()],
        key=("Arrays & Hashing", "Two Pointers", "Sliding Window", "Stack", "Binary Search", "Linked List", "Trees", "Heap / Priority Queue", "Backtracking", "Tries", "Graphs", "Advanced Graphs", "1-D Dynamic Programming", "2-D Dynamic Programming", "Greedy", "Intervals", "Math & Geometry", "Bit Manipulation").index,
    )


def test_real_bundle_counts_and_committed_catalog():
    if not BUNDLE.exists():
        pytest.skip("downloaded NeetCode bundle unavailable")
    entries = extract_entries(BUNDLE.read_text())
    assert len(re.findall(r"\{problem:", BUNDLE.read_text())) == 973
    assert len(re.findall(r"\bpro:!\d\b", BUNDLE.read_text())) == 156
    assert len(entries) == 588
    assert sum(p["neetcode150"] for p in entries) == 150
    assert sum(p["neetcode250"] for p in entries) == 250
    assert sum(p["blind75"] for p in entries) == 75
    assert sum(p["locked"] for p in entries) == 149
    assert sum(not p["locked"] and not p["neetcode150"] for p in entries) == 289
    catalog = load_catalog()
    assert catalog.entries == entries
