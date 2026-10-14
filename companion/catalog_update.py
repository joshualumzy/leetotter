"""Extract NeetCode catalog literals as text; never load the downloaded JavaScript."""

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen


CATALOG_PATH = Path(__file__).parent / "data" / "catalog.json"
OBJECT = re.compile(r"\{problem:(?:[^{}]|\{[^{}]*\})*\}")
STRING = re.compile(r'(\w+):("(?:\\.|[^"\\])*")')
EXPECTED = {"all": 973, "editor": 588, "150": 150, "250": 250, "blind75": 75, "pro_field": 156, "locked": 149, "free_extra": 289}


def script_url(html: str) -> str:
    scripts = re.findall(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', html, re.I)
    matches = [urljoin("https://neetcode.io", src) for src in scripts if re.search(r"(?:^|/)main\.[\w-]+\.js(?:\?|$)", src)]
    if len(matches) != 1:
        raise ValueError("Expected exactly one main.*.js script in NeetCode HTML")
    if urlsplit(matches[0]).hostname != "neetcode.io":
        raise ValueError("Expected main.*.js script on neetcode.io")
    return matches[0]


def extract_entries(script: str, *, check_counts: bool = True) -> list[dict]:
    blocks = OBJECT.findall(script)
    if not blocks:
        raise ValueError("No problem entries found in NeetCode script")
    entries = []
    counts = {"all": len(blocks), "editor": 0, "150": 0, "250": 0, "blind75": 0, "pro_field": 0, "locked": 0, "free_extra": 0}
    for block in blocks:
        fields = {key: json.loads(value) for key, value in STRING.findall(block)}
        counts["pro_field"] += bool(re.search(r"\bpro:!\d\b", block))
        for key, count_key in (("neetcode150", "150"), ("neetcode250", "250"), ("blind75", "blind75"), ("pro", "locked")):
            counts[count_key] += bool(re.search(rf"\b{key}:!0\b", block))
        if "ncLink" not in fields:
            continue
        missing = {"problem", "pattern", "link", "difficulty", "ncLink"} - fields.keys()
        if missing:
            raise ValueError(f"NeetCode entry missing {', '.join(sorted(missing))}: {block[:100]}")
        slug = fields["ncLink"].strip("/")
        link = fields["link"].strip("/")
        if not slug or not link or not all(fields[key] for key in ("problem", "pattern", "difficulty")):
            raise ValueError(f"NeetCode entry has empty fields: {block[:100]}")
        entry = {"slug": slug, "title": fields["problem"], "category": fields["pattern"],
                 "difficulty": fields["difficulty"], "link": f"https://leetcode.com/problems/{link}/",
                 "neetcode150": "neetcode150:!0" in block, "neetcode250": "neetcode250:!0" in block,
                 "blind75": "blind75:!0" in block, "locked": "pro:!0" in block}
        entries.append(entry)
        counts["editor"] += 1
        counts["free_extra"] += not entry["locked"] and not entry["neetcode150"]
    if len({entry["slug"] for entry in entries}) != len(entries):
        raise ValueError("Duplicate NeetCode editor slug")
    if check_counts and any(not expected * 0.9 <= counts[key] <= expected * 1.1 for key, expected in EXPECTED.items()):
        raise ValueError(f"NeetCode script shape/count changed substantially: expected near {EXPECTED}, got {counts}")
    return entries


def _fetch(url: str) -> str:
    with urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def update_catalog(path: Path = CATALOG_PATH, *, fetch=_fetch, check_counts: bool = True) -> None:
    html = fetch("https://neetcode.io")
    entries = extract_entries(fetch(script_url(html)), check_counts=check_counts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    update_catalog()
