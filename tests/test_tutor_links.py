"""The past-links section, the linked output field, and the rules around both."""

import pytest
from pydantic import ValidationError

from companion.past_links import PastLink
from companion.reference import Approach, Reference, StoredReference
from companion.tutor import SYSTEM_PROMPT, TutorRequest, asks_about_similar, build_messages, reply_schema

PROBLEM = {"slug": "contains-duplicate", "title": "Contains Duplicate"}
REFERENCE = StoredReference(Reference(
    slug="contains-duplicate", title="Contains Duplicate", difficulty="Easy", neetcode_category="Arrays & Hashing",
    summary="Return true when a value repeats.", patterns=["hash-set"], analysis_concepts=[],
    approaches=[Approach(id="set", name="Hash set", idea="Remember seen values", time="O(n)", space="O(n)",
                         interview_expected="usually", trick=False)]),
    "checked", [])
HELP = TutorRequest("help", 1, None)
CHAT = TutorRequest("chat", None, "is a set ok?")
SIMILAR = TutorRequest("chat", None, "have I seen something like this before?")

LINK = PastLink(slug="two-sum", title="Two Sum", summary="Find two values that add to the target.",
                outcome="solved alone", approach="Hash map", idea="Sorting lets two pointers move inward.",
                shared_patterns=("hash-map",), shared_category=True, good=True, order=10)


def reply(**changes):
    return {"gap": "stuck_optimising", "gap_note": None, "help_kind": "question",
            "reply_text": "What could you remember as you go?", "follow_up_question": None,
            "linked": [], **changes}


def user(messages):
    return messages[-1]["content"]


def build(*, request=HELP, past_links=()):
    return user(build_messages(PROBLEM, [], reference=REFERENCE, prior_solve=False, problem_seconds=60,
                               seconds_left=60, request=request, past_links=list(past_links)))


def test_past_links_section_carries_only_the_facts_from_the_list():
    out = build(past_links=[LINK])
    assert "Past problems" in out
    assert "two-sum" in out and "Two Sum" in out and LINK.summary in out
    assert "solved alone" in out and LINK.idea in out
    assert "Hash map" in out


def test_without_history_there_is_no_past_links_section():
    out = build()
    assert "Past problems" not in out


def test_a_similar_question_is_told_it_may_link_every_close_match():
    out = build(request=SIMILAR, past_links=[LINK])
    assert "every close match" in out
    assert "have I seen something like this before?" in out


def test_a_plain_chat_or_press_is_told_to_link_at_most_one():
    assert "at most one" in build(request=CHAT, past_links=[LINK])
    assert "at most one" in build(request=HELP, past_links=[LINK])


def test_the_system_prompt_has_the_link_rules():
    assert "linked" in SYSTEM_PROMPT
    assert "at most one" in SYSTEM_PROMPT and "strongly fits" in SYSTEM_PROMPT
    assert "every close match" in SYSTEM_PROMPT
    assert "only the list" in SYSTEM_PROMPT.lower()
    assert "never make" in SYSTEM_PROMPT.lower() or "never invent" in SYSTEM_PROMPT.lower()
    assert "reminder" in SYSTEM_PROMPT and "go further only when asked" in SYSTEM_PROMPT
    assert "after the solve" in SYSTEM_PROMPT.lower() or "after they solve" in SYSTEM_PROMPT.lower()


def test_a_link_from_the_list_is_valid_and_makes_the_help_an_analogy():
    schema = reply_schema(HELP, allowed=("two-sum",))
    result = schema.model_validate(reply(linked=["two-sum"]))
    assert result.linked == ["two-sum"] and result.help_kind == "analogy"


def test_a_link_that_spells_out_the_idea_keeps_partial_logic():
    schema = reply_schema(HELP, allowed=("two-sum",))
    result = schema.model_validate(reply(linked=["two-sum"], help_kind="partial_logic"))
    assert result.help_kind == "partial_logic"


def test_a_slug_outside_the_list_is_invalid():
    schema = reply_schema(HELP, allowed=("two-sum",))
    with pytest.raises(ValidationError):
        schema.model_validate(reply(linked=["valid-anagram"]))


def test_more_than_one_link_is_invalid_when_the_learner_did_not_ask_what_is_similar():
    schema = reply_schema(HELP, allowed=("two-sum", "valid-anagram"))
    with pytest.raises(ValidationError):
        schema.model_validate(reply(linked=["two-sum", "valid-anagram"]))
    asked = reply_schema(SIMILAR, allowed=("two-sum", "valid-anagram"), asked=True)
    assert asked.model_validate(reply(linked=["two-sum", "valid-anagram"])).linked == ["two-sum", "valid-anagram"]


def test_a_link_in_a_none_reply_is_invalid():
    schema = reply_schema(CHAT, allowed=("two-sum",))
    with pytest.raises(ValidationError):
        schema.model_validate(reply(gap="none", help_kind="none", linked=["two-sum"]))


def test_with_no_list_any_link_is_invalid():
    schema = reply_schema(HELP, allowed=())
    with pytest.raises(ValidationError):
        schema.model_validate(reply(linked=["two-sum"]))


def test_asking_about_similar_is_recognised_from_plain_words():
    assert asks_about_similar("have I seen something like this?")
    assert asks_about_similar("what did we do before that's similar?")
    assert not asks_about_similar("is a set ok?")
