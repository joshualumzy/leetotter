"""The learner's current code, rebuilt from a snapshot and the diffs after it."""

from companion.tutor import current_code


def snap(code):
    return ("code_snapshot", {"code": code})


def diff(start_line, removed, added):
    return ("code_changed", {"diff": {"start_line": start_line, "removed": removed, "added": added}})


def test_no_code_seen_yet():
    assert current_code([]) == (None, False)


def test_a_snapshot_then_diffs_give_the_latest_code():
    events = [snap("def f(nums):\n    pass"),
              diff(2, ["    pass"], ["    seen = set()", "    for n in nums:"]),
              diff(4, [], ["        if n in seen: return True"])]
    assert current_code(events) == ("def f(nums):\n    seen = set()\n    for n in nums:\n        if n in seen: return True", False)


def test_a_later_snapshot_replaces_everything():
    assert current_code([snap("a"), diff(1, ["a"], ["b"]), snap("fresh")]) == ("fresh", False)


def test_a_diff_that_does_not_apply_keeps_the_last_good_code_and_flags_it_behind():
    events = [snap("a\nb"), diff(2, ["b"], ["c"]), diff(1, ["zzz"], ["y"]), diff(1, ["a"], ["q"])]
    assert current_code(events) == ("a\nc", True)


def test_a_diff_past_the_end_does_not_apply():
    assert current_code([snap("a"), diff(5, ["x"], [])]) == ("a", True)


def test_a_snapshot_after_a_bad_diff_clears_the_flag():
    assert current_code([snap("a"), diff(3, ["x"], []), snap("b"), diff(1, ["b"], ["c"])]) == ("c", False)


def test_diffs_before_any_snapshot_cannot_be_applied():
    assert current_code([diff(1, [], ["x"])]) == (None, True)


def test_appending_at_the_line_after_the_end_applies():
    assert current_code([snap("a"), diff(2, [], ["b"])]) == ("a\nb", False)
