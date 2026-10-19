"""Steering in plain words (SPEC §8 "Steering", §11 notes, §13). The Planner reads the words; code validates
its reading and enforces the limits around it. Replies are recorded, so the same words can carry different
readings: what code does follows the reading, never keywords in the text.

Interface (companion/planner.py) used by this file, beyond tests/test_planner_rules.py and
tests/test_planner_flow.py:

    PlannerReply (pydantic, strict, extra fields forbidden):
        slug: str                     the suggested problem
        reason: str                   internal, never shown
        reading: "none" | "ask" | "steer" | "clear" | "unreadable" | "unrelated"
            none: no words to read (start, between, swap, clear); the others answer a chat message:
            ask: a request for the next problem only, never stored; steer: a focus to store; clear: back to
            normal; unreadable: words it couldn't read or that name nothing in the catalog; unrelated: not
            about practice, ignored
        ask: list[Focus] | None       required for "ask" (empty only when named_by_you), else None
        steer: SteerReading | None    required for "steer", else None
        understood: str | None        the one line it says to you; required for ask, steer, clear and
                                      unreadable, None for none and unrelated
        named_by_you: bool            you named this problem yourself; only with reading "ask"
    Focus: {categories: [catalog category names], patterns: [pattern ids from stored references],
            difficulties: ["Easy" | "Medium" | "Hard"], direction: "more" | "fewer"}, naming at least one
        category, pattern or difficulty
    SteerReading: {focus: [Focus, ...] (at least one), horizon: "session" | "until_cleared", label: str,
            note: str}

    Checks in ask_planner beyond the schema: categories are catalog categories and patterns are known
    pattern ids; the slug is in request.allowed unless named_by_you on a chat request (then any unlocked
    catalog problem); a reading other than "none" only on a chat request, "none" never on one; for an ask
    that some allowed problem meets, the slug meets it; the understood line never names the suggested
    problem's category or pattern (an id's hyphens read as spaces) unless the ask or steer names it.

    active_steer(db, *, session_id) -> dict | None
        The one active steer: {"label", "focus" (Focus dicts), "horizon", ...}. A "session" steer is active
        only in the session it was set in; "until_cleared" in every session until cleared. Stored in `notes`
        (kind "steer"), never in reviews.

    Planner.answer on a chat request: "steer" stores the reading (replacing any active steer), "clear"
    clears it, "ask" and "unreadable" store nothing; each shows a card whose message is the understood
    line. "unrelated" re-sends the card as it was, so the panel stops waiting. Every card carries the active steer's label.
    Planner.clear_steer(session_id): the card's Clear; removes the steer and plans an unsteered suggestion,
    not logged as a swap. Planner.fail on a chat request says the steer wasn't saved.
"""

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from companion.planner import Planner, PlannerReply, active_steer, allowed_next, ask_planner, code_ranked, learner_summary, shortlist
from companion.store import open_store
from tests.test_planner_flow import RecordedPlanner, answered, card, context, notes, reply
from tests.test_planner_rules import (CATALOG, CLIMBING, COIN_CHANGE, GOOD_NODES, HOUSE_ROBBER, ISLANDS, LEVEL_ORDER,
                                      NOW, PERMUTATION, RIGHT_VIEW, TRIES, category_review, entry, reference, review,
                                      steady)
from tests.test_session import make, msg, start_msg

DP = "1-D Dynamic Programming"
LOCKED_SLUG = next(e["slug"] for e in CATALOG.entries if e["locked"])


def focus(*categories, patterns=(), difficulties=(), direction="more"):
    return {"categories": list(categories), "patterns": list(patterns), "difficulties": list(difficulties),
            "direction": direction}


def steering(label, *focuses, horizon="session", note=""):
    return {"focus": list(focuses), "horizon": horizon, "label": label, "note": note}


MORE_DP = steering("more dynamic programming", focus(DP))


class Shown:
    """A session at NOW with a suggestion card on screen."""

    def __init__(self, tmp_path, prepare=None):
        self.coord, self.clock, self.path = make(tmp_path)
        self.clock.now = NOW
        if prepare:
            with open_store(self.path) as db:
                prepare(db)
        self.planner = Planner(self.path, self.clock)
        request = self.planner.panel_opened("idle")
        self.coord.handle(start_msg())
        self.session_id = self.coord.session_id
        self.card = card(answered(self.planner, request, reply(CLIMBING)))

    def say(self, text, *replies):
        """Type `text` on the card; the Planner answers with `replies` (one, or two when the first is invalid)."""
        request = self.planner.chat(self.session_id, text)
        assert request is not None
        return self.planner.answer(request, ask_planner(RecordedPlanner(*replies), request))

    def steer(self):
        with open_store(self.path) as db:
            return active_steer(db, session_id=self.session_id)

    def between(self, slug=HOUSE_ROBBER):
        request = self.planner.problem_left(self.session_id)
        return request, card(answered(self.planner, request, reply(slug)))


# --- schema -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    reply(COIN_CHANGE, "steer", understood="More DP."),                                  # steer missing
    reply(COIN_CHANGE, "steer", steer=MORE_DP),                                          # says nothing
    reply(COIN_CHANGE, "none", understood="Here you go."),                               # a line with no words read
    reply(COIN_CHANGE, "ask", understood="A DP one next."),                              # ask missing
    reply(COIN_CHANGE, "ask", ask=[], understood="A DP one next."),                      # empty ask, not named
    reply(COIN_CHANGE, "none", named=True),                                              # named outside an ask
    reply(COIN_CHANGE, "clear", steer=MORE_DP, understood="Back to normal."),            # clear carrying a steer
    reply(COIN_CHANGE, "steer", steer=steering("nothing", focus()), understood="Hm."),   # focus names nothing
    reply(COIN_CHANGE, "steer", steer=steering("x", focus(difficulties=["Expert"])), understood="x"),
    reply(COIN_CHANGE, "steer", steer=steering("x", focus(DP), horizon="week"), understood="x"),
    {**reply(COIN_CHANGE), "extra": 1},
])
def test_the_planners_reply_is_checked_against_its_schema(bad):
    with pytest.raises(ValidationError):
        PlannerReply.model_validate_json(json.dumps(bad))


# --- code validates the reading ---------------------------------------------------------------------

def rejected(shown, text, bad):
    request = shown.planner.chat(shown.session_id, text)
    client = RecordedPlanner(bad, bad)
    with pytest.raises(ValueError):
        ask_planner(client, request)
    assert len(client.calls) == 2  # one retry with the error shown, then a loud failure
    assert len(client.calls[1][1]) > len(client.calls[0][1])


def test_a_focus_must_name_catalog_categories(tmp_path):
    shown = Shown(tmp_path)
    rejected(shown, "more DP", reply(COIN_CHANGE, "steer", understood="More dynamic programming.",
                                     steer=steering("more dynamic programming", focus("Dynamic Programming"))))


def test_a_focus_must_name_known_patterns(tmp_path):
    shown = Shown(tmp_path, lambda db: reference(db, PERMUTATION, ["sliding-window"]))
    rejected(shown, "more memo stuff", reply(COIN_CHANGE, "steer", understood="More memoisation.",
                                             steer=steering("more memoisation", focus(patterns=["memoisation"]))))


def test_a_pattern_the_learner_named_is_not_a_reveal(tmp_path):
    shown = Shown(tmp_path, lambda db: reference(db, COIN_CHANGE, ["dynamic programming"]))
    out = shown.say("can I do harder dynamic programming",
                    reply(COIN_CHANGE, "steer", steer=steering("harder dynamic programming", focus(DP, difficulties=["Hard"])),
                          understood="A dynamic programming problem it is."))
    assert card(out)["message"] == "A dynamic programming problem it is."


def test_a_pattern_the_learner_did_not_name_is_a_reveal_naming_the_word(tmp_path):
    shown = Shown(tmp_path, lambda db: reference(db, COIN_CHANGE, ["dynamic programming"]))
    request = shown.planner.chat(shown.session_id, "can I do harder DP")
    bad = reply(COIN_CHANGE, "steer", steer=steering("harder DP", focus(DP, difficulties=["Hard"])),
                understood="A dynamic programming problem it is.")
    client = RecordedPlanner(bad, bad)
    with pytest.raises(ValueError) as raised:
        ask_planner(client, request)
    assert "Understood line names 'dynamic programming', which the learner did not ask for; rewrite it without that word" in str(raised.value)
    assert len(client.calls) == 2


def test_an_understood_line_that_only_echoes_the_learner_is_rejected(tmp_path):
    shown = Shown(tmp_path)
    request = shown.planner.chat(shown.session_id, "more practice please")
    bad = reply(COIN_CHANGE, "steer", steer=steering("more practice", focus(DP)),
                understood="More practice, please.")
    client = RecordedPlanner(bad, bad)
    with pytest.raises(ValueError) as raised:
        ask_planner(client, request)
    assert "Understood line only repeats the learner's message; reply in your own words" in str(raised.value)
    assert len(client.calls) == 2


def test_a_normal_understood_line_is_accepted(tmp_path):
    shown = Shown(tmp_path)
    out = shown.say("more practice please",
                    reply(COIN_CHANGE, "steer", steer=steering("more practice", focus(DP)),
                          understood="A fresh dynamic programming one is up next."))
    assert card(out)["message"] == "A fresh dynamic programming one is up next."


def test_a_difficulty_only_focus_filters_the_shortlist(tmp_path):
    with open_store(tmp_path / "db.sqlite3") as db:
        candidates = allowed_next(db, session_id=None, now=NOW)
        summary = learner_summary(db, now=NOW)
    assert any(c["difficulty"] == "Hard" for c in candidates)
    hard = steering("more hard", focus(difficulties=["Hard"]))
    pool = shortlist(candidates, summary, hard)
    assert pool and {c["difficulty"] for c in pool} == {"Hard"}


def test_harder_dp_needs_both_the_category_and_the_difficulty(tmp_path):
    with open_store(tmp_path / "db.sqlite3") as db:
        candidates = allowed_next(db, session_id=None, now=NOW)
        summary = learner_summary(db, now=NOW)
    harder_dp = steering("harder dynamic programming", focus(DP, difficulties=["Hard"]))
    pool = shortlist(candidates, summary, harder_dp)
    assert pool and {(c["category"], c["difficulty"]) for c in pool} == {(DP, "Hard")}


def test_fewer_with_difficulties_excludes_them(tmp_path):
    with open_store(tmp_path / "db.sqlite3") as db:
        candidates = allowed_next(db, session_id=None, now=NOW)
        summary = learner_summary(db, now=NOW)
    assert any(c["difficulty"] == "Hard" for c in candidates)
    fewer_hard = steering("fewer hard", focus(difficulties=["Hard"], direction="fewer"))
    pool = shortlist(candidates, summary, fewer_hard)
    assert pool and all(c["difficulty"] != "Hard" for c in pool)


def test_an_old_stored_steer_without_the_new_fields_still_loads(tmp_path):
    _, _, path = make(tmp_path)
    old = {"focus": [{"categories": ["Trees"], "patterns": [], "direction": "more"}],
           "horizon": "until_cleared", "label": "more trees"}
    with open_store(path) as db:
        db.execute("INSERT INTO notes (ts, kind, text) VALUES (?, 'steer', ?)", (NOW.isoformat(), json.dumps(old)))
        steer = active_steer(db, session_id=None)
    assert steer["note"] == "" and steer["focus"][0]["difficulties"] == []


def test_a_steer_never_makes_an_ineligible_problem_eligible(tmp_path):
    def prepare(db):
        review(db, LEVEL_ORDER, "good", NOW - timedelta(days=30), first=True)  # done on your own
        steady(db, "Trees")
    shown = Shown(tmp_path, prepare)
    rejected(shown, "focus on trees", reply(LEVEL_ORDER, "steer", understood="More trees.",
                                            steer=steering("more trees", focus("Trees"))))


def test_steering_words_are_read_only_on_a_chat_message(tmp_path):
    coord, clock, path = make(tmp_path)
    request = Planner(path, clock).panel_opened("idle")
    bad = reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming.")
    with pytest.raises(ValueError):
        ask_planner(RecordedPlanner(bad, bad), request)


def test_a_chat_message_always_gets_a_reading(tmp_path):
    shown = Shown(tmp_path)
    rejected(shown, "more DP please", reply(COIN_CHANGE))


def test_an_invalid_reading_is_retried_once_with_the_error(tmp_path):
    shown = Shown(tmp_path)
    request = shown.planner.chat(shown.session_id, "more DP")
    bad = reply(COIN_CHANGE, "steer", understood="More DP.", steer=steering("more DP", focus("DP")))
    good = reply(COIN_CHANGE, "steer", understood="More dynamic programming, this session.", steer=MORE_DP)
    client = RecordedPlanner(bad, good)
    assert ask_planner(client, request).steer.label == "more dynamic programming"
    assert len(client.calls) == 2


# --- readings: the words don't decide, the reading does ---------------------------------------------

@pytest.mark.parametrize("text", ["more dynamic programming", "let's work on DP for now",
                                  "can I get a bunch of those 1-D DP ones"])
def test_varied_words_with_the_same_reading_store_the_same_steer(tmp_path, text):
    shown = Shown(tmp_path)
    out = shown.say(text, reply(COIN_CHANGE, "steer", steer=MORE_DP,
                                understood="More dynamic programming for this session."))
    steer = shown.steer()
    assert steer["focus"] == [focus(DP)] and steer["horizon"] == "session"
    shown_card = card(out)
    assert (shown_card["slug"], shown_card["steer"]) == (COIN_CHANGE, "more dynamic programming")
    assert shown_card["message"] == "More dynamic programming for this session."
    assert len(notes(shown.path, "steer")) >= 1


@pytest.mark.parametrize("reading, stored", [
    (reply(COIN_CHANGE, "ask", ask=[focus(DP)], understood="A dynamic programming one next."), None),
    (reply(COIN_CHANGE, "steer", steer=steering("more dynamic programming", focus(DP), horizon="until_cleared"),
           understood="More dynamic programming until you clear it."), "until_cleared"),
    (reply(CLIMBING, "unreadable", understood="Sorry, I didn't catch that. What would you like more of?"), None),
])
def test_the_same_words_follow_whatever_the_planner_read(tmp_path, reading, stored):
    shown = Shown(tmp_path)
    out = shown.say("dp", reading)
    steer = shown.steer()
    assert (steer and steer["horizon"]) == stored
    assert any(message.get("message") == reading["understood"] for message in out)


@pytest.mark.parametrize("text, reading", [
    ("I'm confident with arrays, fewer of those",
     steering("fewer arrays", focus("Arrays & Hashing", direction="fewer"))),
    ("focus on trees and graphs", steering("more trees and graphs", focus("Trees", "Graphs"))),
])
def test_different_intents_store_what_was_read(tmp_path, text, reading):
    shown = Shown(tmp_path)
    shown.say(text, reply(ISLANDS, "steer", steer=reading, understood=f"Got it: {reading['label']}."))
    assert shown.steer()["focus"] == reading["focus"]


def test_a_message_that_is_not_about_practice_is_ignored(tmp_path):
    shown = Shown(tmp_path)
    assert shown.say("ha, my cat just walked on the keyboard", reply(COIN_CHANGE, "unrelated")) == [shown.card]
    assert shown.planner.card == shown.card
    assert shown.steer() is None


def test_unreadable_words_store_no_steer_and_say_so(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more of the thingy", reply(CLIMBING, "unreadable", understood="I didn't catch which kind you meant."))
    assert shown.steer() is None
    assert notes(shown.path, "steer") == []


# --- one steer at a time ----------------------------------------------------------------------------

def test_a_new_steer_replaces_the_active_one(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    trees = steering("more trees", focus("Trees"))
    out = shown.say("actually, trees", reply(GOOD_NODES, "steer", steer=trees, understood="Trees instead."))
    assert shown.steer()["focus"] == [focus("Trees")]
    assert card(out)["steer"] == "more trees"


def test_an_addition_is_merged_into_one_steer(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    both = steering("more dynamic programming, fewer arrays", focus(DP), focus("Arrays & Hashing", direction="fewer"))
    out = shown.say("and fewer arrays", reply(HOUSE_ROBBER, "steer", steer=both, understood="Added fewer arrays."))
    assert shown.steer()["focus"] == both["focus"]
    assert card(out)["steer"] == "more dynamic programming, fewer arrays"


def test_back_to_normal_clears_the_steer(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    out = shown.say("back to normal", reply(ISLANDS, "clear", understood="Back to the usual mix."))
    assert shown.steer() is None
    assert card(out)["steer"] is None


def test_clear_on_the_card_replaces_its_suggestion_with_an_unsteered_one_and_is_not_a_swap(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    request = shown.planner.clear_steer(shown.session_id)
    assert request.trigger == "clear"
    assert shown.steer() is None
    assert context(request)["steer"] is None
    out = answered(shown.planner, request, reply(ISLANDS))
    assert card(out)["slug"] == ISLANDS and card(out)["steer"] is None
    assert notes(shown.path, "preference") == []


def test_every_card_shows_the_active_steer_until_it_is_cleared(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    request, between = shown.between(HOUSE_ROBBER)
    assert context(request)["steer"]["label"] == "more dynamic programming"
    assert between["steer"] == "more dynamic programming"
    swap = shown.planner.swap(shown.session_id)
    assert card(answered(shown.planner, swap, reply(COIN_CHANGE)))["steer"] == "more dynamic programming"


# --- how long a steer lasts ---------------------------------------------------------------------------

def next_session(shown):
    shown.coord.handle(msg("session_end"))
    shown.planner.session_ended()
    shown.clock.advance(86400)
    request = shown.planner.panel_opened("ended")
    shown.coord.handle(start_msg())
    return request, shown.coord.session_id


def test_a_session_steer_just_disappears_when_the_session_ends(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    request, session_id = next_session(shown)
    assert context(request)["steer"] is None
    assert card(answered(shown.planner, request, reply(ISLANDS)))["steer"] is None
    with open_store(shown.path) as db:
        assert active_steer(db, session_id=session_id) is None


def test_a_steer_until_cleared_carries_into_the_next_session(tmp_path):
    shown = Shown(tmp_path)
    lasting = steering("more dynamic programming", focus(DP), horizon="until_cleared")
    shown.say("DP until I say otherwise", reply(COIN_CHANGE, "steer", steer=lasting,
                                                understood="More dynamic programming until you clear it."))
    request, session_id = next_session(shown)
    assert context(request)["steer"]["label"] == "more dynamic programming"
    assert card(answered(shown.planner, request, reply(HOUSE_ROBBER)))["steer"] == "more dynamic programming"
    with open_store(shown.path) as db:
        assert active_steer(db, session_id=session_id)["horizon"] == "until_cleared"


# --- a direct ask -------------------------------------------------------------------------------------

def test_a_direct_ask_is_honoured_at_once_and_not_stored(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more trees", reply(GOOD_NODES, "steer", steer=steering("more trees", focus("Trees")),
                                  understood="More trees this session."))
    out = shown.say("give me a DP question next", reply(COIN_CHANGE, "ask", ask=[focus(DP)],
                                                         understood="A dynamic programming one next."))
    assert card(out)["slug"] == COIN_CHANGE
    assert card(out)["steer"] == "more trees"
    assert shown.steer()["focus"] == [focus("Trees")]


def test_an_ask_that_an_allowed_problem_meets_must_be_honoured(tmp_path):
    shown = Shown(tmp_path)
    rejected(shown, "a DP one next", reply(ISLANDS, "ask", ask=[focus(DP)], understood="A dynamic programming one."))


def all_tries_done(db):
    for slug in TRIES:
        review(db, slug, "good", NOW - timedelta(days=20), first=True)
    steady(db, "Tries")


def test_an_ask_nothing_eligible_meets_is_answered_plainly_with_the_nearest_alternative(tmp_path):
    shown = Shown(tmp_path, all_tries_done)
    line = "Every trie problem is done on your own and none is new, so here's a graph one instead."
    out = shown.say("a trie one next", reply(ISLANDS, "ask", ask=[focus("Tries")], understood=line))
    assert (card(out)["slug"], card(out)["message"]) == (ISLANDS, line)
    assert shown.steer() is None


def test_a_steer_nothing_eligible_meets_is_kept_and_stays_on_the_card(tmp_path):
    shown = Shown(tmp_path, all_tries_done)
    tries = steering("more tries", focus("Tries"))
    line = "Nothing new or due in tries right now, so I'll suggest others until there is."
    out = shown.say("more tries", reply(ISLANDS, "steer", steer=tries, understood=line))
    assert (card(out)["slug"], card(out)["steer"], card(out)["message"]) == (ISLANDS, "more tries", line)
    _, between = shown.between(HOUSE_ROBBER)
    assert between["steer"] == "more tries"


def test_a_problem_you_name_yourself_is_always_allowed(tmp_path):
    shown = Shown(tmp_path, lambda db: (review(db, LEVEL_ORDER, "good", NOW - timedelta(days=1), first=True),
                                        steady(db, "Trees")))
    request = shown.planner.chat(shown.session_id, "the level order one from yesterday")
    assert LEVEL_ORDER not in {c["slug"] for c in request.allowed}
    named = reply(LEVEL_ORDER, "ask", ask=[], named=True, understood="Binary Tree Level Order Traversal again.")
    out = shown.planner.answer(request, ask_planner(RecordedPlanner(named), request))
    assert card(out)["slug"] == LEVEL_ORDER
    assert shown.steer() is None


def test_a_named_problem_must_be_free_and_named_in_chat(tmp_path):
    shown = Shown(tmp_path)
    rejected(shown, "the locked one", reply(LOCKED_SLUG, "ask", ask=[], named=True, understood="That one."))
    coord, clock, path = make(tmp_path / "other")
    request = Planner(path, clock).panel_opened("idle")
    bad = reply(LEVEL_ORDER, "ask", ask=[], named=True, understood="That one.")
    with pytest.raises(ValueError):
        ask_planner(RecordedPlanner(bad, bad), request)


# --- a steer shifts suggestions only ------------------------------------------------------------------

def test_a_steer_never_changes_grades_reviews_the_learner_model_or_the_list(tmp_path):
    def prepare(db):
        review(db, RIGHT_VIEW, "again", NOW - timedelta(days=10), meets=False, first=True)
        category_review(db, "Trees", "again", NOW - timedelta(days=10))
    shown = Shown(tmp_path, prepare)
    with open_store(shown.path) as db:
        reviews = db.execute("SELECT * FROM reviews ORDER BY id").fetchall()
        summary = learner_summary(db, now=NOW)
        before = {c["slug"] for c in allowed_next(db, session_id=shown.session_id, now=NOW)}
    confident = steering("fewer trees", focus("Trees", direction="fewer"), horizon="until_cleared")
    shown.say("I'm confident with trees", reply(ISLANDS, "steer", steer=confident, understood="Fewer trees."))
    with open_store(shown.path) as db:
        assert db.execute("SELECT * FROM reviews ORDER BY id").fetchall() == reviews
        assert learner_summary(db, now=NOW)["Trees"]["solve"] == summary["Trees"]["solve"]
        after = {c["slug"] for c in allowed_next(db, session_id=shown.session_id, now=NOW)}
    assert after == before and RIGHT_VIEW in after  # the due retry keeps its place


def test_a_steer_never_holds_back_a_due_retry_outside_its_focus(tmp_path):
    shown = Shown(tmp_path, lambda db: review(db, RIGHT_VIEW, "again", NOW - timedelta(days=10), meets=False, first=True))
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More dynamic programming."))
    request = shown.planner.problem_left(shown.session_id)
    assert RIGHT_VIEW in {c["slug"] for c in request.allowed}
    assert card(answered(shown.planner, request, reply(RIGHT_VIEW)))["slug"] == RIGHT_VIEW


def test_a_steer_overrides_the_balance_preference_in_the_fallback(tmp_path):
    with open_store(tmp_path / "db.sqlite3") as db:
        steady(db, "Trees")  # Graphs, with no evidence, is the weaker
        candidates = [c for c in allowed_next(db, session_id=None, now=NOW) if c["slug"] in {GOOD_NODES, ISLANDS}]
        summary = learner_summary(db, now=NOW)
        assert code_ranked(candidates, summary)["slug"] == ISLANDS
        trees = {"label": "more trees", "focus": [focus("Trees")], "horizon": "session"}
        assert code_ranked(candidates, summary, trees)["slug"] == GOOD_NODES
        fewer_graphs = {"label": "fewer graphs", "focus": [focus("Graphs", direction="fewer")], "horizon": "session"}
        assert code_ranked(candidates, summary, fewer_graphs)["slug"] == GOOD_NODES
        patterns = {"label": "more counting", "focus": [focus(patterns=["counting"])], "horizon": "session"}
        assert code_ranked(candidates, summary, patterns)["slug"] == ISLANDS  # nothing meets it: no filter


# --- failures are loud ----------------------------------------------------------------------------------

def test_a_failed_planner_says_the_steer_was_not_saved(tmp_path):
    shown = Shown(tmp_path)
    request = shown.planner.chat(shown.session_id, "more DP")
    out = shown.planner.fail(request, ValueError("planner reply invalid after retry"))
    assert any("steer wasn't saved" in (message.get("message") or "").lower() for message in out)
    assert shown.steer() is None
    assert notes(shown.path, "steer") == []


def test_the_fallback_applies_the_stored_steer_as_a_filter_within_eligibility(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more trees", reply(GOOD_NODES, "steer", steer=steering("more trees", focus("Trees")),
                                  understood="More trees this session."))
    request = shown.planner.problem_left(shown.session_id)
    fallback = card(shown.planner.fail(request, TimeoutError("planner timed out")))
    assert fallback["source"] == "fallback" and fallback["steer"] == "more trees"
    assert entry(fallback["slug"])["category"] == "Trees"
    assert fallback["slug"] in {c["slug"] for c in request.allowed}


def test_the_fallback_ignores_a_steer_nothing_eligible_meets(tmp_path):
    shown = Shown(tmp_path, all_tries_done)
    shown.say("more tries", reply(ISLANDS, "steer", steer=steering("more tries", focus("Tries")),
                                  understood="Nothing new in tries, so others for now."))
    request = shown.planner.problem_left(shown.session_id)
    fallback = card(shown.planner.fail(request, TimeoutError("planner timed out")))
    assert fallback["slug"] in {c["slug"] for c in request.allowed}
    assert fallback["steer"] == "more tries"


def test_an_understood_line_with_stray_braces_is_rejected(tmp_path):
    shown = Shown(tmp_path)
    rejected(shown, "more easies please", reply(COIN_CHANGE, "steer", steer=steering("Easier", focus(difficulties=["Easy"])), understood="Sure thing.},"))
