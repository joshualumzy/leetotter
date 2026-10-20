"""Breadth first while a category has no evidence (SPEC §8 "Planning", first bullet): code chooses the category."""

import json
import random
from dataclasses import replace
from datetime import timedelta

from companion.catalog import CATEGORIES
from companion.planner import Planner, ask_planner
from companion.store import open_store
from tests.test_planner_flow import RecordedPlanner, reply
from tests.test_planner_rules import (CLIMBING, COIN_CHANGE, HOUSE_ROBBER, INVERT, NOW, attempt, category_review,
                                      imported, review)
from tests.test_planner_steer import focus, steering
from tests.test_session import make, start_msg

DP = "1-D Dynamic Programming"


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


def test_with_evidence_everywhere_breadth_is_none_and_the_old_shortlist_holds(tmp_path):
    _, clock, path = make(tmp_path)
    clock.now = NOW
    with open_store(path) as db:
        for category in CATEGORIES:
            category_review(db, category, "good", NOW - timedelta(days=1))
    request = Planner(path, clock).panel_opened("idle")
    assert request.breadth is None
    client = RecordedPlanner(reply(HOUSE_ROBBER))
    assert ask_planner(client, request).slug == HOUSE_ROBBER
    assert len({c["category"] for c in offered(client)}) > 1
