"""Streaming reply_text out of a Tutor reply's JSON as it arrives."""

import json

import pytest

from companion.tutor import ReplyTextStream

REPLY = {"gap": "stuck_optimising", "gap_note": None, "help_kind": "question",
         "reply_text": "Your nested loops check every pair. What work is repeated \"each time\"?\nTry `seen = {}` → café 😀",
         "follow_up_question": None}


def streamed(pieces):
    stream = ReplyTextStream()
    return [stream.feed(piece) for piece in pieces]


def test_one_chunk_yields_the_decoded_reply_text():
    assert "".join(streamed([json.dumps(REPLY)])) == REPLY["reply_text"]


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_every_single_character_chunk_boundary_yields_the_same_text(ensure_ascii):
    text = json.dumps(REPLY, ensure_ascii=ensure_ascii)
    pieces = streamed(list(text))
    assert "".join(pieces) == REPLY["reply_text"]


def test_text_is_released_as_it_arrives_not_at_the_end():
    pieces = streamed(['{"gap":"weak_intuition","gap_note":null,"help_kind":"question","reply_', 'text":"What does', ' the ', 'output', ' need?"', ',"follow_up_question":null}'])
    assert pieces == ["", "What does", " the ", "output", " need?", ""]


def test_an_escape_split_across_chunks_waits_for_its_end():
    pieces = streamed(['{"reply_text":"a\\', 'nb \\u00', 'e9 \\ud83d', '\\ude00 c"}'])
    assert pieces == ["a", "\nb ", "é ", "😀 c"]


def test_reply_text_after_other_fields_with_tricky_values_is_found():
    reply = {"gap": "other", "gap_note": 'a "reply_text": decoy, with {braces} and [brackets]', "help_kind": "trace",
             "follow_up_question": "\\", "reply_text": "real"}
    assert "".join(streamed(list(json.dumps(reply)))) == "real"


def test_a_reply_without_reply_text_or_not_json_yields_nothing():
    assert "".join(streamed(['{"gap": "none"}'])) == ""
    assert "".join(streamed(["Sure! Here is a hint"])) == ""


def test_malformed_escapes_stop_the_preview_instead_of_raising():
    assert streamed(['{"reply_text":"ok \\uZZZZ more"}']) == ["ok "]
    assert streamed(['{"bad\\x": 1, "reply_text":"x"}']) == [""]
