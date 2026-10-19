"""When the Planner runs and what reaches the panel (SPEC §5 "Session start" and "Between problems", §7
Planner row, §8 "Steering", §12, §13). The Planner's replies are recorded; the clock is fake.

Interface (companion/planner.py) used by this file, beyond tests/test_planner_rules.py:

    PLANNER_WAIT = 30  # seconds without an answer before the code-ranked pick is shown (start and between)

    PlannerReply (pydantic, strict): {slug, reason, reading, ask, steer, understood, named_by_you}; see
        tests/test_planner_steer.py. `reason` is internal and never sent to the panel.

    ask_planner(client, request) -> PlannerReply
        One blocking Planner call: client.call_role("planner", request.messages, PlannerReply, ...), then
        code's checks against the request (slug allowed or named by you, focus ids in the catalog, never naming
        the suggested problem's category or pattern unless you asked for it). A failed check is retried once
        with the error shown to the model; a second failure raises ValueError.

    class Planner(db_path, clock)
        One per companion; the socket calls it and runs ask_planner in a worker thread.
        card                         the suggestion message on screen, or None
        panel_opened(state) -> PlannerRequest | None
            `state` is the coordinator's state. Starts planning at session start ("idle" or "ended"); nothing
            on a paused session's resume-or-end choice, mid-problem, or while a card or a request is waiting.
        problem_left(session_id) -> PlannerRequest     between problems, as soon as you leave one
        swap(session_id) -> PlannerRequest             logs the card's problem as a preference (notes, kind
                                                       "preference"), takes the card down, plans again
        chat(session_id, text) -> PlannerRequest | None   only while a card is shown; the card stays up
        clear_steer(session_id) -> PlannerRequest      see tests/test_planner_steer.py
        go() -> dict                                   the card's problem ({"slug", "title"}); the card goes
        session_ended()                                the card goes and a waiting answer is discarded
        answer(request, reply) -> list[dict]           [] for a request whose answer is stale
        fail(request, error) -> list[dict]             the code-ranked card at once, the failure logged
        tick() -> list[dict]                           the code-ranked card once PLANNER_WAIT has passed

    PlannerRequest: trigger ("start" | "between" | "swap" | "chat" | "clear"), session_id, started (the clock
        when planning began), allowed (allowed_next's list at that moment), messages (the last one's content
        is JSON with at least "allowed", "session_problems", "learner_summary" and "steer").

    A card is {"type": "suggestion", "ts", "slug", "title", "difficulty", "cap_minutes", "steer" (the active
    steer's label or None), "message" (a line for you, or None), "source" ("planner" | "fallback")}: by title
    only, never the problem's category, patterns or the Planner's reason.
"""

import json
import logging

import pytest

from companion.grading import grade_pending
from companion.planner import PLANNER_WAIT, SHORTLIST_PER_CATEGORY, Planner, allowed_next, ask_planner
from companion.store import open_store
from companion.tutor import SYSTEM_PROMPT
from tests.test_grading import MEDIUM, FakeGraders, Run, put_reference
from tests.test_planner_rules import (CLIMBING, COIN_CHANGE, HOUSE_ROBBER, MAX_PATH, PERMUTATION, entry,
                                      reference)
from tests.test_session import make, msg, opened, start_msg, submit

REASON = "Little evidence on this category yet; sample it."


class RecordedPlanner:
    """Stands in for ModelClient: recorded Planner replies (or failures), validated like the real client."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def call_role(self, role, messages, schema_class, **kwargs):
        self.calls.append((role, messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return schema_class.model_validate_json(json.dumps(reply))


def reply(slug, reading="none", *, ask=None, steer=None, understood=None, named=False):
    return {"slug": slug, "reason": REASON, "reading": reading, "ask": ask, "steer": steer,
            "understood": understood, "named_by_you": named}


def context(request):
    return json.loads(request.messages[-1]["content"])


def cards(out):
    return [message for message in out if message["type"] == "suggestion"]


def card(out):
    found = cards(out)
    assert len(found) == 1, out
    return found[0]


def answered(planner, request, *replies):
    return planner.answer(request, ask_planner(RecordedPlanner(*replies), request))


def setup(tmp_path):
    coord, clock, path = make(tmp_path)
    return coord, clock, path, Planner(path, clock)


def notes(path, kind):
    with open_store(path) as db:
        return [text for (text,) in db.execute("SELECT text FROM notes WHERE kind = ? ORDER BY id", (kind,))]


# --- when it starts -------------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["idle", "ended"])
def test_the_planner_starts_as_soon_as_the_panel_opens_for_a_new_session(tmp_path, state):
    _, clock, _, planner = setup(tmp_path)
    request = planner.panel_opened(state)
    assert request is not None
    assert (request.trigger, request.session_id, request.started) == ("start", None, clock())


@pytest.mark.parametrize("state", ["paused", "active"])
def test_no_planning_on_a_paused_sessions_resume_or_end_choice_or_mid_problem(tmp_path, state):
    _, _, _, planner = setup(tmp_path)
    assert planner.panel_opened(state) is None


def test_reopening_the_panel_while_planning_or_with_a_card_up_starts_nothing_new(tmp_path):
    _, _, _, planner = setup(tmp_path)
    request = planner.panel_opened("idle")
    assert planner.panel_opened("idle") is None
    answered(planner, request, reply(HOUSE_ROBBER))
    assert planner.panel_opened("idle") is None


def test_leaving_a_problem_starts_the_planner_at_once_without_waiting_for_the_graders(tmp_path):
    coord, clock, path, planner = setup(tmp_path)
    coord.handle(start_msg())
    coord.handle(opened(CLIMBING, "Climbing Stairs"))
    clock.advance(600)
    coord.handle(submit(CLIMBING, True))
    clock.advance(30)
    coord.handle(opened(HOUSE_ROBBER, "House Robber"))  # leaves Climbing Stairs, accepted, grade pending
    request = planner.problem_left(coord.session_id)
    assert (request.trigger, request.session_id, request.started) == ("between", coord.session_id, clock())
    slugs = {candidate["slug"] for candidate in request.allowed}
    assert CLIMBING not in slugs
    with open_store(path) as db:
        assert slugs == {c["slug"] for c in allowed_next(db, session_id=coord.session_id, now=clock())}
    sent = context(request)
    offered = {candidate["slug"] for candidate in sent["allowed"]}
    assert offered < slugs
    assert max(sum(c["category"] == cat for c in sent["allowed"]) for cat in {c["category"] for c in sent["allowed"]}) <= SHORTLIST_PER_CATEGORY + 1
    so_far = sent["session_problems"]
    assert [problem["slug"] for problem in so_far][:1] == [CLIMBING]
    assert {"title", "difficulty", "active_seconds", "help", "outcome"} <= set(so_far[0])
    assert sent["steer"] is None


# --- the card ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("slug, cap", [(CLIMBING, 20), (HOUSE_ROBBER, 35), (MAX_PATH, 50)])
def test_the_answer_becomes_a_card_by_title_with_its_cap_and_never_its_reason_or_category(tmp_path, slug, cap):
    _, _, _, planner = setup(tmp_path)
    request = planner.panel_opened("idle")
    shown = card(answered(planner, request, reply(slug)))
    found = entry(slug)
    assert (shown["slug"], shown["title"], shown["difficulty"], shown["cap_minutes"]) == (
        slug, found["title"], found["difficulty"], cap)
    assert (shown["source"], shown["steer"], shown["message"]) == ("planner", None, None)
    assert not {"reason", "category", "patterns"} & set(shown)
    assert REASON not in json.dumps(shown)
    assert found["category"].lower() not in json.dumps(shown).lower()
    assert planner.card == shown


def test_after_30_s_without_an_answer_the_code_ranked_pick_is_shown_and_a_late_answer_is_discarded(tmp_path):
    _, clock, _, planner = setup(tmp_path)
    assert PLANNER_WAIT == 30
    request = planner.panel_opened("idle")
    clock.advance(29)
    assert planner.tick() == []
    clock.advance(1)
    shown = card(planner.tick())
    assert shown["source"] == "fallback" and shown["message"]
    assert shown["slug"] in {candidate["slug"] for candidate in request.allowed}
    assert planner.tick() == []
    assert answered(planner, request, reply(HOUSE_ROBBER)) == []
    assert planner.card == shown


def test_an_answer_in_time_stays_on_the_card(tmp_path):
    _, clock, _, planner = setup(tmp_path)
    request = planner.panel_opened("idle")
    clock.advance(12)
    shown = card(answered(planner, request, reply(HOUSE_ROBBER)))
    clock.advance(60)
    assert planner.tick() == []
    assert planner.card == shown


def test_a_failed_planner_shows_the_code_ranked_pick_at_once_and_logs_it(tmp_path, caplog):
    coord, _, _, planner = setup(tmp_path)
    coord.handle(start_msg())
    request = planner.problem_left(coord.session_id)
    with caplog.at_level(logging.WARNING):
        shown = card(planner.fail(request, ValueError("planner reply invalid after retry")))
    assert shown["source"] == "fallback" and shown["message"]
    assert shown["slug"] in {candidate["slug"] for candidate in request.allowed}
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert answered(planner, request, reply(HOUSE_ROBBER)) == []


def test_a_swap_is_logged_as_a_preference_and_the_answer_it_replaced_is_discarded(tmp_path):
    coord, _, path, planner = setup(tmp_path)
    first = planner.panel_opened("idle")
    coord.handle(start_msg())
    answered(planner, first, reply(HOUSE_ROBBER))
    swapped = planner.swap(coord.session_id)
    assert swapped.trigger == "swap"
    assert planner.card is None
    [preference] = notes(path, "preference")
    assert HOUSE_ROBBER in preference
    assert card(answered(planner, swapped, reply(COIN_CHANGE)))["slug"] == COIN_CHANGE
    again = planner.swap(coord.session_id)
    assert answered(planner, swapped, reply(CLIMBING)) == []  # superseded by the second swap
    assert len(notes(path, "preference")) == 2 and COIN_CHANGE in notes(path, "preference")[1]
    assert card(answered(planner, again, reply(CLIMBING)))["slug"] == CLIMBING


def test_go_takes_the_card_down_and_opens_its_problem(tmp_path):
    coord, _, _, planner = setup(tmp_path)
    request = planner.panel_opened("idle")
    coord.handle(start_msg())
    answered(planner, request, reply(HOUSE_ROBBER))
    assert planner.go()["slug"] == HOUSE_ROBBER
    assert planner.card is None


def test_ending_the_session_takes_the_card_down_and_discards_a_waiting_answer(tmp_path):
    coord, _, _, planner = setup(tmp_path)
    first = planner.panel_opened("idle")
    coord.handle(start_msg())
    answered(planner, first, reply(HOUSE_ROBBER))
    waiting = planner.problem_left(coord.session_id)
    coord.handle(msg("session_end"))
    planner.session_ended()
    assert planner.card is None
    assert answered(planner, waiting, reply(COIN_CHANGE)) == []
    assert planner.panel_opened("ended") is not None


# --- chat -------------------------------------------------------------------------------------------

def test_chat_goes_to_the_planner_while_a_card_is_shown_or_being_chosen(tmp_path):
    coord, _, path, planner = setup(tmp_path)
    coord.handle(start_msg())
    assert planner.chat(coord.session_id, "more dynamic programming") is None  # nothing asked of the Planner yet
    request = planner.problem_left(coord.session_id)
    steered = planner.chat(coord.session_id, "more dynamic programming")  # still choosing: replaces the pending request
    assert steered.trigger == "chat" and steered.session_id == coord.session_id
    assert answered(planner, request, reply(HOUSE_ROBBER)) == []
    shown = card(answered(planner, steered, reply(HOUSE_ROBBER, "ask", ask=[{"categories": ["1-D Dynamic Programming"], "patterns": [], "direction": "more"}], understood="A dynamic programming problem next.")))
    talk = planner.chat(coord.session_id, "more dynamic programming")
    assert talk.trigger == "chat" and talk.session_id == coord.session_id
    assert "more dynamic programming" in talk.messages[-1]["content"]
    assert planner.card == shown  # the card stays up while the Planner reads the words
    planner.go()
    assert planner.chat(coord.session_id, "more dynamic programming") is None
    assert notes(path, "steer") == []


def test_mid_problem_the_tutor_says_it_will_take_steering_at_the_next_suggestion():
    assert "next suggestion" in SYSTEM_PROMPT.lower()


def test_the_planner_never_names_the_suggested_problems_pattern_unless_you_asked_for_it(tmp_path):
    coord, _, path, planner = setup(tmp_path)
    with open_store(path) as db:
        reference(db, PERMUTATION, ["sliding-window"])
    request = planner.panel_opened("idle")
    coord.handle(start_msg())
    answered(planner, request, reply(HOUSE_ROBBER))
    talk = planner.chat(coord.session_id, "something different please")
    blurted = reply(PERMUTATION, "unreadable", understood="Not sure what you meant; here's a sliding window one.")
    with pytest.raises(ValueError):
        ask_planner(RecordedPlanner(blurted, blurted), talk)
    asked = reply(PERMUTATION, "ask", ask=[{"categories": [], "patterns": ["sliding-window"], "direction": "more"}],
                  understood="A sliding window problem next.")
    assert ask_planner(RecordedPlanner(asked), talk).slug == PERMUTATION


# --- across sessions --------------------------------------------------------------------------------

def test_a_second_sessions_suggestions_reflect_the_first(tmp_path):
    run = Run(tmp_path)
    put_reference(run.path, MEDIUM)
    run.start()
    run.open()
    run.accept()
    run.end()
    grade_pending(FakeGraders(), db_path=run.path)  # a clean solve, at least as good as usually expected
    run.advance_days(1)
    planner = Planner(run.path, run.clock)
    with open_store(run.path) as db:
        assert db.execute("SELECT grade FROM reviews WHERE item_type = 'problem'").fetchone()[0] == "good"
    request = planner.panel_opened("ended")
    assert MEDIUM not in {candidate["slug"] for candidate in request.allowed}
    assert context(request)["learner_summary"]["Arrays & Hashing"]["recent"]["Medium"] == ["good"]
