"""The debrief is refused while a Tutor turn is in flight, and changes nothing until it is chosen again."""

from tests.test_grading import reply
from tests.test_time_cap import MEDIUM, at_card, choose, get, reviews_of


def test_the_debrief_waits_for_a_tutor_turn_in_flight(tmp_path):
    coord, _, path = at_card(tmp_path)
    _, turn = coord.begin_turn("chat", "hello?")
    assert turn is not None
    out = choose(coord, "debrief")
    assert out[0]["type"] == "error" and "still answering" in out[0]["message"]
    assert get(coord)["time_up"] == {"can_extend": True} and reviews_of(path) == []
    coord.finish_turn(turn, reply("question"), "x-test-2")
    assert choose(coord, "debrief")[0]["mode"] == "debrief"
    assert reviews_of(path)[0] == ("problem", MEDIUM, "again")
