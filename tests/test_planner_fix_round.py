"""Regression checks for Planner eligibility and context."""

from datetime import timedelta

from companion.planner import Planner, active_steer, allowed_next, code_ranked, learner_summary
from companion.store import open_store
from tests.test_planner_rules import (NOW, CLIMBING, COIN_CHANGE, HOUSE_ROBBER, LONGEST_SUB, PERMUTATION,
                                      attempt, imported, new_session, reference, review)
from tests.test_planner_flow import RecordedPlanner, reply
from tests.test_planner_flow import context
from tests.test_planner_steer import Shown, focus, steering
from companion.planner import ask_planner
from tests.test_session import make, msg, start_msg


def test_first_attempt_graded_after_it_opened_does_not_block_due_retry(tmp_path):
    with open_store(tmp_path / "planner.sqlite3") as db:
        review(db, COIN_CHANGE, "again", NOW - timedelta(days=10), meets=False)
        session = new_session(db)
        attempt(db, session, CLIMBING, accepted=True)
        review(db, CLIMBING, "good", NOW + timedelta(minutes=1))
        allowed = {c["slug"] for c in allowed_next(db, session_id=session, now=NOW + timedelta(minutes=2))}
    assert COIN_CHANGE in allowed


def test_skipped_due_retry_is_not_offered_again_this_session(tmp_path):
    with open_store(tmp_path / "planner.sqlite3") as db:
        review(db, HOUSE_ROBBER, "again", NOW - timedelta(days=10), meets=False)
        session = new_session(db)
        attempt(db, session, HOUSE_ROBBER, accepted=False)
        attempt(db, session, CLIMBING, accepted=False)
        allowed = {c["slug"] for c in allowed_next(db, session_id=session, now=NOW + timedelta(minutes=1))}
    assert HOUSE_ROBBER not in allowed


def test_fallback_prefers_due_not_done_retry_to_unseen_in_same_category(tmp_path):
    with open_store(tmp_path / "planner.sqlite3") as db:
        review(db, HOUSE_ROBBER, "again", NOW - timedelta(days=10), meets=False)
        candidates = [c for c in allowed_next(db, session_id=None, now=NOW)
                      if c["slug"] in {HOUSE_ROBBER, CLIMBING}]
        picked = code_ranked(candidates, learner_summary(db, now=NOW))
    assert picked["slug"] == HOUSE_ROBBER


def test_imported_same_pattern_problem_does_not_hold_back_retry(tmp_path):
    with open_store(tmp_path / "planner.sqlite3") as db:
        reference(db, LONGEST_SUB, ["sliding-window"])
        reference(db, PERMUTATION, ["sliding-window"])
        imported(db, PERMUTATION)
        review(db, LONGEST_SUB, "again", NOW - timedelta(days=10), meets=False)
        allowed = {c["slug"] for c in allowed_next(db, session_id=None, now=NOW)}
    assert LONGEST_SUB in allowed


def test_real_pattern_is_valid_focus_when_no_problem_with_it_is_eligible(tmp_path):
    def prepare(db):
        reference(db, PERMUTATION, ["sliding-window"])
        review(db, PERMUTATION, "good", NOW - timedelta(days=10))
    shown = Shown(tmp_path, prepare)
    request = shown.planner.chat(shown.session_id, "more sliding window")
    raw = reply(COIN_CHANGE, "steer", steer=steering("more sliding window", focus(patterns=["sliding-window"])),
                understood="No sliding window problem is eligible now, so here is a nearby one.")
    result = ask_planner(RecordedPlanner(raw), request)
    shown.planner.answer(request, result)
    assert shown.steer()["focus"][0]["patterns"] == ["sliding-window"]


def test_start_card_steer_belongs_to_new_session_after_swap_and_chat(tmp_path):
    coordinator, clock, path = make(tmp_path)
    planner = Planner(path, clock)
    first = planner.panel_opened("idle")
    planner.answer(first, ask_planner(RecordedPlanner(reply(CLIMBING)), first))
    swapped = planner.swap(coordinator.session_id)
    assert swapped.session_id is None
    planner.answer(swapped, ask_planner(RecordedPlanner(reply(HOUSE_ROBBER)), swapped))
    chatted = planner.chat(coordinator.session_id, "more DP")
    steer = steering("more DP", focus("1-D Dynamic Programming"))
    planner.answer(chatted, ask_planner(RecordedPlanner(reply(COIN_CHANGE, "steer", steer=steer,
                                                          understood="More DP this session.")), chatted))
    coordinator.handle(start_msg())
    planner.session_started(coordinator.session_id)
    with open_store(path) as db:
        assert active_steer(db, session_id=coordinator.session_id)["label"] == "more DP"


def test_start_card_after_ended_session_uses_new_session_scope(tmp_path):
    coordinator, clock, path = make(tmp_path)
    coordinator.handle(start_msg())
    old_session_id = coordinator.session_id
    coordinator.handle(msg("session_end"))
    planner = Planner(path, clock)
    first = planner.panel_opened("ended")
    planner.answer(first, ask_planner(RecordedPlanner(reply(CLIMBING)), first))
    swapped = planner.swap(old_session_id)
    assert swapped.session_id is None
    planner.answer(swapped, ask_planner(RecordedPlanner(reply(HOUSE_ROBBER)), swapped))
    chatted = planner.chat(old_session_id, "more DP")
    assert chatted.session_id is None
    steer = steering("more DP", focus("1-D Dynamic Programming"))
    planner.answer(chatted, ask_planner(RecordedPlanner(reply(COIN_CHANGE, "steer", steer=steer,
                                                          understood="More DP this session.")), chatted))
    coordinator.handle(start_msg())
    planner.session_started(coordinator.session_id)
    with open_store(path) as db:
        assert active_steer(db, session_id=coordinator.session_id)["label"] == "more DP"


def test_empty_eligible_list_reports_planner_failure_only_once(tmp_path):
    _, clock, path = make(tmp_path)
    planner = Planner(path, clock)
    request = planner.panel_opened("idle")
    request.allowed.clear()
    out = planner.fail(request, RuntimeError("offline"))
    assert len(out) == 1 and out[0]["type"] == "error"
    clock.advance(31)
    assert planner.tick() == []


def test_planner_context_includes_prior_unfinished_and_dated_recent_results(tmp_path):
    _, clock, path = make(tmp_path)
    with open_store(path) as db:
        earlier = new_session(db, NOW - timedelta(days=4))
        attempt(db, earlier, HOUSE_ROBBER, NOW - timedelta(days=4), accepted=False)
        review(db, CLIMBING, "good", NOW - timedelta(days=2))
    sent = context(Planner(path, clock).panel_opened("idle"))
    assert any(row["slug"] == HOUSE_ROBBER for row in sent["unfinished"])
    assert {"grade": "good", "at": (NOW - timedelta(days=2)).isoformat()} in sent[
        "learner_summary"]["1-D Dynamic Programming"]["recent_dated"]["Easy"]


def test_start_pressed_while_the_panel_start_request_is_out_replaces_it_with_one_for_the_session(tmp_path):
    coordinator, clock, path = make(tmp_path)
    planner = Planner(path, clock)
    old = planner.panel_opened("idle")
    coordinator.handle(start_msg())
    new = planner.session_started(coordinator.session_id)
    assert new is not None and new.session_id == coordinator.session_id
    assert planner.answer(old, ask_planner(RecordedPlanner(reply(CLIMBING)), old)) == []
    shown = planner.answer(new, ask_planner(RecordedPlanner(reply(HOUSE_ROBBER)), new))
    assert [card["type"] for card in shown] == ["suggestion"] and shown[0]["slug"] == HOUSE_ROBBER


def test_the_request_made_at_start_falls_back_after_the_wait(tmp_path):
    coordinator, clock, path = make(tmp_path)
    planner = Planner(path, clock)
    planner.panel_opened("idle")
    coordinator.handle(start_msg())
    planner.session_started(coordinator.session_id)
    clock.advance(31)
    out = planner.tick()
    assert [card["type"] for card in out] == ["suggestion"] and out[0]["source"] == "fallback"


def test_start_with_nothing_out_and_no_card_asks_the_planner(tmp_path):
    coordinator, clock, path = make(tmp_path)
    planner = Planner(path, clock)
    coordinator.handle(start_msg())
    request = planner.session_started(coordinator.session_id)
    assert request is not None and request.trigger == "start" and request.session_id == coordinator.session_id
    shown = planner.answer(request, ask_planner(RecordedPlanner(reply(CLIMBING)), request))
    assert shown[0]["slug"] == CLIMBING


def test_resume_asks_again_only_when_no_card_and_no_request_is_out(tmp_path):
    coordinator, clock, path = make(tmp_path)
    coordinator.handle(start_msg())
    planner = Planner(path, clock)
    request = planner.resume(coordinator.session_id)
    assert request is not None and request.trigger == "start" and request.session_id == coordinator.session_id
    assert planner.resume(coordinator.session_id) is None            # one is out
    planner.answer(request, ask_planner(RecordedPlanner(reply(CLIMBING)), request))
    assert planner.resume(coordinator.session_id) is None            # a card is shown
