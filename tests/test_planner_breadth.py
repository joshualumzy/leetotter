"""Breadth first while a category has no evidence (SPEC §8 "Planning", first bullet): code chooses the category."""

import json
import random
from dataclasses import replace
from datetime import timedelta

from companion.catalog import CATEGORIES
from companion.planner import Planner, _read, ask_planner
from companion.store import open_store
from tests.test_planner_flow import RecordedPlanner, reply
from tests.test_planner_rules import (CATALOG, CLIMBING, CLONE_GRAPH, COIN_CHANGE, GOOD_NODES, HOUSE_ROBBER, INVERT,
                                      ISLANDS, LEVEL_ORDER, LONGEST_SUB, NOW, RIGHT_VIEW, SAME_TREE, TRIES, attempt,
                                      category_review, entry, imported, new_session, review)
from tests.test_planner_steer import focus, steering
from tests.test_session import make, start_msg

DP = "1-D Dynamic Programming"
MIN_SUBARRAY = "minimum-size-subarray-sum"  # Sliding Window, a 250 extra
MIN_RECOLORS = "minimum-recolors-to-get-k-consecutive-black-blocks"  # Sliding Window, beyond the 250
PERMUTATION = "permutation-string"  # Sliding Window, 150


def offered(client):
    return json.loads(client.calls[-1][1][-1]["content"])["allowed"]


def test_start_requests_draw_several_no_evidence_categories(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    planner = Planner(path, clock, rng=random.Random(7))
    seen = set()
    for _ in range(40):
        request = planner.panel_opened("idle")
        assert request.breadth is not None and request.summary[request.breadth]["solve"] is None
        seen.add(request.breadth)
        planner.session_ended()
    assert len(seen) > 1


def test_a_breadth_category_with_imported_solves_offers_only_imported_problems(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        imported(db, INVERT)
    request = replace(Planner(path, clock).panel_opened("idle"), breadth="Trees")
    client = RecordedPlanner(reply(INVERT))
    assert ask_planner(client, request).slug == INVERT
    assert {c["slug"] for c in offered(client)} == {INVERT}


def test_a_category_tried_this_session_is_not_chosen_while_another_is_left(tmp_path):
    coordinator, clock, path = make(tmp_path)
    clock.now = NOW
    coordinator.handle(start_msg())
    with open_store(path) as db:
        attempt(db, coordinator.session_id, CLIMBING, accepted=True)
    picked = {Planner(path, clock, rng=random.Random(seed)).resume(coordinator.session_id).breadth
              for seed in range(40)}
    assert DP not in picked
    assert len(picked) > 1


def test_a_due_retry_stays_in_the_breadth_pool(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        review(db, HOUSE_ROBBER, "again", NOW - timedelta(days=10), meets=False)
    request = replace(Planner(path, clock).panel_opened("idle"), breadth="Trees")
    client = RecordedPlanner(reply(HOUSE_ROBBER))
    assert ask_planner(client, request).slug == HOUSE_ROBBER
    assert HOUSE_ROBBER in {c["slug"] for c in offered(client)}


def test_a_steer_focus_overrides_breadth(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    request = replace(Planner(path, clock).panel_opened("idle"), breadth="Trees",
                      steer=steering("more DP", focus(DP)))
    client = RecordedPlanner(reply(COIN_CHANGE))
    assert ask_planner(client, request).slug == COIN_CHANGE
    assert {c["category"] for c in offered(client)} == {DP}


def test_a_plain_chat_ask_stays_in_the_breadth_category(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    request = replace(Planner(path, clock).panel_opened("idle"), breadth="Trees")
    client = RecordedPlanner(reply(INVERT, "ask", ask=[], understood="Another one coming."))
    assert ask_planner(client, request).slug == INVERT
    assert {c["category"] for c in offered(client)} == {"Trees"}


def test_a_note_only_steer_stays_in_the_breadth_category(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    request = replace(Planner(path, clock).panel_opened("idle"), breadth="Trees")
    client = RecordedPlanner(reply(INVERT, "steer", steer=steering("something short", note="something short"),
                                   understood="A short one coming."))
    assert ask_planner(client, request).slug == INVERT
    assert {c["category"] for c in offered(client)} == {"Trees"}


def test_with_evidence_everywhere_breadth_is_none_and_the_old_shortlist_holds(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            review(db, next(e["slug"] for e in CATALOG.entries if e["category"] == category and not e["locked"]),
                   "good", NOW - timedelta(days=1))
            category_review(db, category, "good", NOW - timedelta(days=1))
    request = Planner(path, clock).panel_opened("idle")
    assert request.breadth is None
    client = RecordedPlanner(reply(HOUSE_ROBBER))
    assert ask_planner(client, request).slug == HOUSE_ROBBER
    assert len({c["category"] for c in offered(client)}) > 1


def touch(db, category):
    """Give a category a problem review and solve evidence, so neither breadth pass chooses it."""
    review(db, next(e["slug"] for e in CATALOG.entries if e["category"] == category and not e["locked"]),
           "good", NOW - timedelta(days=1))
    category_review(db, category, "good", NOW - timedelta(days=1))


def test_a_category_with_imports_is_chosen_in_pass_one_then_only_unseen_problems_in_pass_two(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category != "Trees":
                touch(db, category)
        imported(db, INVERT)
        imported(db, SAME_TREE)
    first = Planner(path, clock).panel_opened("idle")
    assert (first.breadth, first.breadth_unseen) == ("Trees", False)
    client = RecordedPlanner(reply(INVERT))
    assert ask_planner(client, first).slug == INVERT
    assert {c["slug"] for c in offered(client)} == {INVERT, SAME_TREE}
    with open_store(path) as db:
        review(db, INVERT, "good", NOW)
    second = Planner(path, clock).panel_opened("idle")
    assert (second.breadth, second.breadth_unseen) == ("Trees", True)
    client = RecordedPlanner(reply(LEVEL_ORDER))
    assert ask_planner(client, second).slug == LEVEL_ORDER
    slugs = {c["slug"] for c in offered(client)}
    assert LEVEL_ORDER in slugs and SAME_TREE not in slugs and INVERT not in slugs


def test_pass_two_skips_a_category_with_no_unseen_problem(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category not in ("Graphs", "Tries"):
                touch(db, category)
        for slug in TRIES:
            imported(db, slug)
        review(db, TRIES[0], "good", NOW - timedelta(days=1))
        review(db, ISLANDS, "good", NOW - timedelta(days=1))
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == ("Graphs", True)


def test_pass_two_offers_the_150_before_the_250_extras_and_the_wider_pool(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category != "Sliding Window":
                touch(db, category)
        review(db, LONGEST_SUB, "good", NOW - timedelta(days=1), first=True)
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == ("Sliding Window", True)
    client = RecordedPlanner(reply(PERMUTATION))
    assert ask_planner(client, request).slug == PERMUTATION
    entries = {e["slug"]: e for e in CATALOG.entries}
    assert offered(client) and all(entries[c["slug"]]["neetcode150"] for c in offered(client))


def test_pass_two_moves_to_the_250_extras_when_the_150_has_none_unseen(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category != "Sliding Window":
                touch(db, category)
        for e in CATALOG.entries:
            if e["category"] == "Sliding Window" and e["neetcode150"] and not e["locked"]:
                review(db, e["slug"], "good", NOW - timedelta(days=1), first=True)
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == ("Sliding Window", True)
    client = RecordedPlanner(reply(MIN_SUBARRAY))
    assert ask_planner(client, request).slug == MIN_SUBARRAY
    entries = {e["slug"]: e for e in CATALOG.entries}
    assert offered(client)
    assert all(entries[c["slug"]]["neetcode250"] and not entries[c["slug"]]["neetcode150"] for c in offered(client))


def test_pass_two_moves_to_the_wider_pool_when_the_250_extras_have_none_unseen(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    rest = MIN_RECOLORS
    with open_store(path) as db:
        for category in CATEGORIES:
            if category != "Sliding Window":
                touch(db, category)
        for e in CATALOG.entries:
            if e["category"] == "Sliding Window" and not e["locked"] and (e["neetcode150"] or e["neetcode250"]):
                review(db, e["slug"], "good", NOW - timedelta(days=1), first=True)
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == ("Sliding Window", True)
    client = RecordedPlanner(reply(rest))
    assert ask_planner(client, request).slug == rest
    entries = {e["slug"]: e for e in CATALOG.entries}
    assert offered(client)
    assert all(not entries[c["slug"]]["neetcode250"] and not entries[c["slug"]]["neetcode150"] for c in offered(client))


def test_pass_one_beats_pass_two(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category not in ("Graphs", "Trees"):
                touch(db, category)
        review(db, ISLANDS, "good", NOW - timedelta(days=1))
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == ("Trees", False)


def test_a_third_pass_takes_the_rustiest_category_with_an_unseen_problem(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category not in ("Trees", DP):
                touch(db, category)
        for category, slug, days in (("Trees", INVERT, 30), (DP, HOUSE_ROBBER, 45)):
            review(db, slug, "good", NOW - timedelta(days=1), first=True)
            category_review(db, category, "good", NOW - timedelta(days=days))
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == (DP, True)
    client = RecordedPlanner(reply(COIN_CHANGE))
    assert ask_planner(client, request).slug == COIN_CHANGE
    assert {c["category"] for c in offered(client)} == {DP}


def test_third_pass_ties_are_alphabetical(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            if category not in ("Graphs", "Trees"):
                touch(db, category)
        for category, slug in (("Trees", INVERT), ("Graphs", ISLANDS)):
            review(db, slug, "good", NOW - timedelta(days=1), first=True)
            category_review(db, category, "good", NOW - timedelta(days=30))
    request = Planner(path, clock).panel_opened("idle")
    assert (request.breadth, request.breadth_unseen) == ("Graphs", True)


def test_the_third_pass_does_not_reuse_a_rusty_category_tried_this_session(tmp_path):
    coordinator, clock, path = make(tmp_path)
    clock.now = NOW
    coordinator.handle(start_msg())
    with open_store(path) as db:
        for category in CATEGORIES:
            if category not in ("Trees", DP):
                touch(db, category)
        for category, slug, days in (("Trees", INVERT, 30), (DP, HOUSE_ROBBER, 45)):
            review(db, slug, "good", NOW - timedelta(days=1), first=True)
            category_review(db, category, "good", NOW - timedelta(days=days))
        attempt(db, coordinator.session_id, SAME_TREE, accepted=True)
        attempt(db, coordinator.session_id, CLIMBING, accepted=True)
    request = Planner(path, clock).resume(coordinator.session_id)
    assert request.breadth is None


def test_a_pending_accept_counts_as_an_attempt_in_the_first_breadth_pass(tmp_path):
    coordinator, clock, path = make(tmp_path)
    clock.now = NOW
    coordinator.handle(start_msg())
    with open_store(path) as db:
        for category in CATEGORIES:
            if category != DP:
                touch(db, category)
        category_review(db, DP, "good", NOW - timedelta(days=1))
        attempt(db, coordinator.session_id, CLIMBING, accepted=True)
    request = Planner(path, clock).resume(coordinator.session_id)
    assert request.breadth is None


def test_pass_two_avoids_a_category_tried_this_session_while_another_is_left(tmp_path):
    coordinator, clock, path = make(tmp_path)
    clock.now = NOW
    coordinator.handle(start_msg())
    with open_store(path) as db:
        for category in CATEGORIES:
            if category not in ("Graphs", "Trees"):
                touch(db, category)
        review(db, ISLANDS, "good", NOW - timedelta(days=1))
        review(db, SAME_TREE, "good", NOW - timedelta(days=1))
        attempt(db, coordinator.session_id, CLONE_GRAPH, accepted=True)
    request = Planner(path, clock).resume(coordinator.session_id)
    assert (request.breadth, request.breadth_unseen) == ("Trees", True)


def test_recent_problems_cover_the_current_and_two_previous_sessions_with_outcomes(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW + timedelta(hours=1)
    with open_store(path) as db:
        old = new_session(db, NOW - timedelta(days=6))
        left = attempt(db, old, LEVEL_ORDER, NOW - timedelta(days=6), accepted=True)
        review(db, LEVEL_ORDER, "good", NOW - timedelta(days=6), event_id=left)
        previous_two = new_session(db, NOW - timedelta(days=5))
        for slug, grade, meets in ((INVERT, "good", True), (HOUSE_ROBBER, "hard", False), (COIN_CHANGE, "again", False)):
            left = attempt(db, previous_two, slug, NOW - timedelta(days=5), accepted=True)
            review(db, slug, grade, NOW - timedelta(days=5), meets=meets, event_id=left)
        previous_one = new_session(db, NOW - timedelta(days=4))
        left = attempt(db, previous_one, SAME_TREE, NOW - timedelta(days=4), accepted=True)
        review(db, SAME_TREE, None, NOW - timedelta(days=4), event_id=left)
        attempt(db, previous_one, RIGHT_VIEW, NOW - timedelta(days=4), accepted=True)
        attempt(db, previous_one, GOOD_NODES, NOW - timedelta(days=4), accepted=False)
        current = new_session(db, NOW)
        attempt(db, current, CLIMBING, NOW, accepted=True)
    request = Planner(path, clock).resume(current)
    expected = [
        (entry(INVERT)["title"], (NOW - timedelta(days=5)).date().isoformat(), "solid"),
        (entry(HOUSE_ROBBER)["title"], (NOW - timedelta(days=5)).date().isoformat(), "shaky"),
        (entry(COIN_CHANGE)["title"], (NOW - timedelta(days=5)).date().isoformat(), "missed"),
        (entry(SAME_TREE)["title"], (NOW - timedelta(days=4)).date().isoformat(), "grade pending"),
        (entry(RIGHT_VIEW)["title"], (NOW - timedelta(days=4)).date().isoformat(), "grade pending"),
        (entry(GOOD_NODES)["title"], (NOW - timedelta(days=4)).date().isoformat(), "unfinished"),
        (entry(CLIMBING)["title"], NOW.date().isoformat(), "grade pending"),
    ]
    assert [(p["title"], p["date"], p["outcome"]) for p in request.recent_problems] == expected
    client = RecordedPlanner(reply(CLIMBING, "cannot", understood="I didn't catch that."))
    _read(client, request)
    sent = json.loads(client.calls[0][1][-1]["content"])
    assert sent["recent_problems"] == request.recent_problems


def test_recent_problems_skip_sessions_with_no_attempts_before_a_session_starts(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        first = new_session(db, NOW - timedelta(days=3))
        attempt(db, first, INVERT, NOW - timedelta(days=3), accepted=True)
        review(db, INVERT, "good", NOW - timedelta(days=3))
        new_session(db, NOW - timedelta(days=2))
        latest = new_session(db, NOW - timedelta(days=1))
        attempt(db, latest, HOUSE_ROBBER, NOW - timedelta(days=1), accepted=True)
        review(db, HOUSE_ROBBER, "hard", NOW - timedelta(days=1), meets=False)
    request = Planner(path, clock).panel_opened("idle")
    assert [p["title"] for p in request.recent_problems] == [entry(INVERT)["title"], entry(HOUSE_ROBBER)["title"]]
    assert request.session_id is None


def test_a_recent_problem_named_in_chat_resolves_by_its_title(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        earlier = new_session(db, NOW - timedelta(days=1))
        left = attempt(db, earlier, HOUSE_ROBBER, NOW - timedelta(days=1), accepted=True)
        review(db, HOUSE_ROBBER, "again", NOW - timedelta(days=1), meets=False, event_id=left)
    base = Planner(path, clock).panel_opened("idle")
    request = replace(base, trigger="chat", message="the one I missed yesterday",
                      card={"title": "Anything", "difficulty": "Easy"})
    client = RecordedPlanner(reply(HOUSE_ROBBER, "ask", named_title=entry(HOUSE_ROBBER)["title"],
                                   understood="That one again."))
    assert ask_planner(client, request).slug == HOUSE_ROBBER
    assert len(client.calls) == 1
    sent = json.loads(client.calls[0][1][-1]["content"])
    assert {"title": entry(HOUSE_ROBBER)["title"], "outcome": "missed"}.items() <= sent["recent_problems"][0].items()
