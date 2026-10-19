"""Steering in plain words (SPEC §8 "Steering", §11 notes, §13). The Planner reads the words; code validates
its reading and enforces the limits around it. Replies are recorded, so the same words can carry different
readings: what code does follows the reading, never keywords in the text.

Interface (companion/planner.py) used by this file, beyond tests/test_planner_rules.py and
tests/test_planner_flow.py:

    A chat message takes two small model calls with code between them (ask_planner):
      1. the Reader: Reading (pydantic, strict, flat, every field always present): reading ("ask" | "steer" |
         "clear" | "cannot" | "unrelated"), reply (the otter's line, always), more_categories,
         more_difficulties, fewer_categories, fewer_difficulties, horizon, label, note, named_title. Only the
         schema is checked (catalog categories, levels, a non-empty reply); code normalises the rest and never
         rejects on how fields agree: no focus and no note on a steer is "cannot"; an empty label gets one.
         It never sees the problem list, so its line cannot name the pick.
      2. code filters the eligible problems to the focus (a named_title is resolved by code against catalog
         titles, no Picker needed; a near miss asks "did you mean"; nothing fitting adds one plain sentence),
         then the Picker (Pick: slug, reason) chooses from that pool. A slug outside the allowed set is retried
         once; if the Picker still fails after a good reading, code ranks the pool and says so.
      Other triggers (start, between, swap, clear) are the Picker alone.
    ask_planner returns PlannerReply (a plain dataclass assembled by code): reading, understood, slug, steer.
    SteerReading: {focus: [Focus, ...], horizon: "session" | "until_cleared", label: str (non-empty),
            note: str}; a steer needs a focus or a note. Focus: {categories, patterns (always empty now; old
            stored steers may have them), difficulties, direction: "more" | "fewer"}.

    active_steer(db, *, session_id) -> dict | None
        The one active steer: {"label", "focus" (Focus dicts), "horizon", ...}. A "session" steer is active
        only in the session it was set in; "until_cleared" in every session until cleared. Stored in `notes`
        (kind "steer"), never in reviews.

    Planner.answer on a chat request: "steer" stores the reading (replacing any active steer), "clear"
    clears it, "ask" stores nothing and shows a card whose message is the understood line. "cannot" and
    "unrelated" pick no problem: they re-send the current card with the understood line as its message. Every card carries the active steer's label.
    Planner.clear_steer(session_id): the card's Clear; removes the steer and plans an unsteered suggestion,
    not logged as a swap. Planner.fail on a chat request says the steer wasn't saved.
"""

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from companion.planner import (Pick, Planner, _pick_messages, active_steer, allowed_next, ask_planner, code_ranked,
                               learner_summary, shortlist)
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


# --- code validates the reading ---------------------------------------------------------------------

def rejected(shown, text, bad):
    """The model's decision is refused: the Reader's reply fails its schema, or no pick is ever eligible."""
    request = shown.planner.chat(shown.session_id, text)
    with pytest.raises(ValueError):
        ask_planner(RecordedPlanner(bad, bad), request)


def test_a_focus_must_name_catalog_categories(tmp_path):
    shown = Shown(tmp_path)
    rejected(shown, "more DP", reply(COIN_CHANGE, "steer", understood="More dynamic programming.",
                                     steer=steering("more dynamic programming", focus("Dynamic Programming"))))


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
    out = shown.say("focus on trees", reply(LEVEL_ORDER, "steer", understood="More trees.",
                                            steer=steering("more trees", focus("Trees"))), reply(LEVEL_ORDER))
    assert card(out)["slug"] != LEVEL_ORDER
    assert "couldn't choose myself" in card(out)["message"]
    assert shown.steer()["label"] == "more trees"


def test_an_ineligible_pick_is_retried_once_with_the_error(tmp_path):
    shown = Shown(tmp_path)
    request = shown.planner.chat(shown.session_id, "more DP")
    steer = reply(COIN_CHANGE, "steer", understood="More dynamic programming.", steer=MORE_DP, picks=[LOCKED_SLUG, COIN_CHANGE])
    client = RecordedPlanner(steer)
    assert ask_planner(client, request).slug == COIN_CHANGE
    assert [role for role, _ in client.calls] == ["planner"] * 3  # the Reader, then the Picker twice
    assert "not in allowed" in client.calls[2][1][-1]["content"]


def test_a_reply_that_fails_its_schema_fails_the_request_loudly(tmp_path):
    shown = Shown(tmp_path)
    request = shown.planner.chat(shown.session_id, "more DP")
    bad = reply(COIN_CHANGE, "steer", understood="More DP.", steer=steering("more DP", focus("Dynamic Programming")))
    with pytest.raises(ValidationError):
        ask_planner(RecordedPlanner(bad), request)


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
    (reply(CLIMBING, "cannot", understood="Sorry, I didn't catch that. What would you like more of?"), None),
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


def talk(reading, line):
    return {**reply(None, reading, understood=line), "reason": None}


def test_a_message_that_is_not_about_practice_gets_a_line_and_keeps_the_card(tmp_path):
    shown = Shown(tmp_path)
    before = dict(shown.card)
    out = shown.say("Hey, what's up?", talk("unrelated", "Just paddling about, you?"))
    assert out == [{**before, "message": "Just paddling about, you?"}]
    assert (shown.planner.card["slug"], shown.planner.card["title"]) == (before["slug"], before["title"])
    assert shown.steer() is None


def reading_of(shown, text, decided):
    request = shown.planner.chat(shown.session_id, text)
    return request, ask_planner(RecordedPlanner(decided), request)


def test_a_steer_with_only_a_note_is_still_saved_and_keeps_the_learners_words(tmp_path):
    shown = Shown(tmp_path)
    out = shown.say("shorter problems please", reply(COIN_CHANGE, "steer", understood="Shorter ones, got it.",
                                                     steer=steering("Shorter problems", note="shorter problems please")))
    assert shown.steer()["note"] == "shorter problems please" and shown.steer()["focus"] == []
    assert card(out)["steer"] == "Shorter problems"


def test_a_steer_with_no_focus_and_no_note_is_read_as_cannot_and_not_saved(tmp_path):
    shown = Shown(tmp_path)
    _, result = reading_of(shown, "hmm", reply(COIN_CHANGE, "steer", understood="Which kind did you mean?", steer=steering("x")))
    assert (result.reading, result.understood, result.slug, result.steer) == ("cannot", "Which kind did you mean?", None, None)


def test_a_difficulty_only_steer_without_a_label_gets_one(tmp_path):
    shown = Shown(tmp_path)
    shown.say("no more hards", reply(COIN_CHANGE, "steer", understood="No more hards.",
                                     steer=steering("", focus(difficulties=["Hard"], direction="fewer"))))
    assert shown.steer()["label"] == "Your focus"


def test_one_steer_can_ask_for_more_of_one_thing_and_fewer_of_another(tmp_path):
    shown = Shown(tmp_path)
    both = steering("trees over arrays", focus("Trees"), focus("Arrays & Hashing", direction="fewer"))
    shown.say("more trees, fewer arrays", reply(ISLANDS, "steer", steer=both, understood="Trees over arrays."))
    assert shown.steer()["focus"] == both["focus"]


def test_an_ask_with_no_focus_is_just_the_next_problem_and_keeps_the_active_steer(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more DP", reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More DP."))
    request, result = reading_of(shown, "another one", reply(COIN_CHANGE, "ask", ask=[], understood="Another one coming."))
    assert result.reading == "ask" and result.steer is None
    assert json.loads(_pick_messages(request, request.allowed, request.steer)[-1]["content"])["steer"]["label"] == MORE_DP["label"]


def test_the_pick_after_a_valid_reading_falls_back_to_code_when_the_picker_crashes(tmp_path):
    shown = Shown(tmp_path)
    decided = reply(COIN_CHANGE, "steer", steer=MORE_DP, understood="More DP.")
    request = shown.planner.chat(shown.session_id, "more DP")

    class Crashing(RecordedPlanner):
        def call_role(self, role, messages, schema_class, **kwargs):
            if schema_class is Pick:
                raise TimeoutError("provider stalled")
            return super().call_role(role, messages, schema_class, **kwargs)

    result = ask_planner(Crashing(decided), request)
    assert CATALOG.by_slug(result.slug)["category"] == DP and result.steer.label == MORE_DP["label"]
    assert "couldn't choose myself" in result.understood


def test_a_dfs_ask_offers_the_picker_only_graph_and_tree_problems(tmp_path):
    shown = Shown(tmp_path)
    request = shown.planner.chat(shown.session_id, "a DFS problem")
    client = RecordedPlanner(reply(ISLANDS, "ask", ask=[focus("Graphs", "Trees")], understood="A DFS one coming."))
    assert ask_planner(client, request).slug == ISLANDS
    offered = json.loads(client.calls[1][1][-1]["content"])["allowed"]
    assert offered and {c["category"] for c in offered} <= {"Graphs", "Trees"}


def test_cannot_words_ask_without_picking_a_new_problem(tmp_path):
    shown = Shown(tmp_path)
    slug = shown.card["slug"]
    out = shown.say("more of the thingy", talk("cannot", "Which thingy do you mean?"))
    assert (card(out)["slug"], card(out)["message"]) == (slug, "Which thingy do you mean?")


def test_cannot_words_store_no_steer_and_say_so(tmp_path):
    shown = Shown(tmp_path)
    shown.say("more of the thingy", reply(CLIMBING, "cannot", understood="I didn't catch which kind you meant."))
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
    out = shown.say("a DP one next", reply(ISLANDS, "ask", ask=[focus(DP)], understood="A dynamic programming one."))
    assert CATALOG.by_slug(card(out)["slug"])["category"] == DP  # the model's pick missed the ask; code ranks within it
    assert "couldn't choose myself" in card(out)["message"]


def all_tries_done(db):
    for slug in TRIES:
        review(db, slug, "good", NOW - timedelta(days=20), first=True)
    steady(db, "Tries")


def test_an_ask_nothing_eligible_meets_is_answered_plainly_with_the_nearest_alternative(tmp_path):
    shown = Shown(tmp_path, all_tries_done)
    line = "Every trie problem is done on your own and none is new, so here's a graph one instead."
    out = shown.say("a trie one next", reply(ISLANDS, "ask", ask=[focus("Tries")], understood=line))
    assert card(out)["slug"] == ISLANDS and card(out)["message"].startswith(line)
    assert "Nothing eligible fits that right now" in card(out)["message"]
    assert shown.steer() is None


def test_a_steer_nothing_eligible_meets_is_kept_and_stays_on_the_card(tmp_path):
    shown = Shown(tmp_path, all_tries_done)
    tries = steering("more tries", focus("Tries"))
    line = "Nothing new or due in tries right now, so I'll suggest others until there is."
    out = shown.say("more tries", reply(ISLANDS, "steer", steer=tries, understood=line))
    assert (card(out)["slug"], card(out)["steer"]) == (ISLANDS, "more tries")
    assert card(out)["message"].startswith(line) and "Nothing eligible fits that right now" in card(out)["message"]
    _, between = shown.between(HOUSE_ROBBER)
    assert between["steer"] == "more tries"


def test_a_problem_you_name_yourself_is_always_allowed(tmp_path):
    shown = Shown(tmp_path, lambda db: (review(db, LEVEL_ORDER, "good", NOW - timedelta(days=1), first=True),
                                        steady(db, "Trees")))
    request = shown.planner.chat(shown.session_id, "the level order one from yesterday")
    assert LEVEL_ORDER not in {c["slug"] for c in request.allowed}
    named = reply(LEVEL_ORDER, "ask", named_title="Binary Tree Level Order Traversal", understood="That one again.")
    client = RecordedPlanner(named)
    out = shown.planner.answer(request, ask_planner(client, request))
    assert card(out)["slug"] == LEVEL_ORDER
    assert len(client.calls) == 1  # a named problem needs no Picker
    assert shown.steer() is None


def test_a_named_problem_that_is_locked_or_missing_is_asked_about_and_the_card_stays(tmp_path):
    shown = Shown(tmp_path)
    locked = CATALOG.by_slug(LOCKED_SLUG)["title"]
    out = shown.say("the locked one", reply(LOCKED_SLUG, "ask", named_title=locked, understood="That one."))
    assert card(out)["slug"] == shown.card["slug"] and "couldn't find a problem called" in card(out)["message"]
    out = shown.say("level order", reply(LEVEL_ORDER, "ask", named_title="Binary Tree Level Order Traversel", understood="That one."))
    assert "Did you mean Binary Tree Level Order Traversal?" in card(out)["message"]


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


