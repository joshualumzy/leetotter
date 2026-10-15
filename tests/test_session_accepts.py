from tests.test_session import SLUG_A, make, opened, start_msg, submit
from companion.store import open_store


def test_a_second_accepted_submission_on_the_same_problem_is_not_counted_again(tmp_path):
    coordinator, clock, path = make(tmp_path)
    coordinator.handle(start_msg())
    coordinator.handle(opened())
    coordinator.handle(submit())
    clock.advance(30)
    coordinator.handle(submit())

    with open_store(path) as db:
        counted = db.execute("SELECT payload FROM events WHERE type = 'accept_counted'").fetchall()
        stored = db.execute("SELECT COUNT(*) FROM events WHERE type = 'submit_result'").fetchone()[0]
    assert len(counted) == 1 and SLUG_A in counted[0][0]
    assert stored == 2
