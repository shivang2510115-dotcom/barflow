"""Pure housekeeping tests — no server, no database.

The transition tables and the merge rule are where a wrong answer is invisible: a room
that silently accepts `inspected` from the attendant who cleaned it, or a guest pressing
the button twice and raising two jobs, both look exactly like working software. So they
are decided here, in one place, and asserted here, without a database in the way.
"""
import pytest

from services.housekeeping import (
    CANCELLED, CLEAN, DIRTY, DONE, IN_PROGRESS, INSPECTED, JOB_STATUSES, LIVE_JOB_STATUSES,
    OPEN, OUT_OF_ORDER, PRIORITIES, STATUSES, can_move_job, can_set, is_ready,
    job_is_live, merge_reason, note_required,
)

SETTERS = ("admin", "manager", "housekeeping", "front_desk")


# ------------------------------- is_ready -------------------------------
def test_ready_means_clean_or_inspected():
    assert is_ready(CLEAN) is True
    assert is_ready(INSPECTED) is True


def test_a_dirty_or_broken_room_is_not_ready():
    assert is_ready(DIRTY) is False
    assert is_ready(OUT_OF_ORDER) is False


def test_a_room_that_has_never_been_touched_is_not_ready():
    # A room whose record predates the field reads as None here. It is answered "not
    # ready" rather than "clean": the startup migration seeds `clean`, and until it has
    # run the honest answer to "has anyone said this room is made up" is no.
    assert is_ready(None) is False
    assert is_ready("") is False
    assert is_ready("sparkling") is False


# ------------------------------- can_set --------------------------------
def test_anyone_on_the_floor_can_dirty_a_room_from_any_status():
    for role in SETTERS:
        for start in (CLEAN, INSPECTED):
            assert can_set(role, start, DIRTY) is True


def test_housekeeping_can_clean_a_dirty_room():
    assert can_set("housekeeping", DIRTY, CLEAN) is True
    assert can_set("manager", DIRTY, CLEAN) is True
    assert can_set("admin", DIRTY, CLEAN) is True


def test_the_front_desk_can_only_dirty():
    # The desk sees the room the guest complained about; making it up is not their job.
    assert can_set("front_desk", DIRTY, CLEAN) is False
    assert can_set("front_desk", CLEAN, INSPECTED) is False
    assert can_set("front_desk", CLEAN, OUT_OF_ORDER) is False
    assert can_set("front_desk", CLEAN, DIRTY) is True


def test_housekeeping_is_refused_inspected():
    # The whole point of an inspection is that somebody else does it.
    assert can_set("housekeeping", CLEAN, INSPECTED) is False
    assert can_set("manager", CLEAN, INSPECTED) is True
    assert can_set("admin", CLEAN, INSPECTED) is True


def test_inspected_follows_a_clean_and_nothing_else():
    for start in (DIRTY, OUT_OF_ORDER):
        assert can_set("manager", start, INSPECTED) is False


def test_clean_follows_a_dirty_and_nothing_else():
    # A room is made up from dirty. `inspected -> clean` is not a move anyone makes:
    # the room gets used, which dirties it, and the cycle starts again.
    assert can_set("manager", INSPECTED, CLEAN) is False


def test_housekeeping_can_report_a_room_broken():
    for start in (CLEAN, DIRTY, INSPECTED):
        assert can_set("housekeeping", start, OUT_OF_ORDER) is True


def test_housekeeping_cannot_take_a_room_back_off_out_of_order():
    # The attendant reports the fault; somebody accountable confirms it is fixed before
    # the room is sold again.
    assert can_set("housekeeping", OUT_OF_ORDER, CLEAN) is False
    assert can_set("housekeeping", OUT_OF_ORDER, DIRTY) is False
    assert can_set("front_desk", OUT_OF_ORDER, DIRTY) is False


def test_a_manager_clears_out_of_order_to_dirty_or_clean():
    for role in ("admin", "manager"):
        assert can_set(role, OUT_OF_ORDER, DIRTY) is True
        assert can_set(role, OUT_OF_ORDER, CLEAN) is True
        # Not straight to inspected: nobody has looked at it since it was broken.
        assert can_set(role, OUT_OF_ORDER, INSPECTED) is False


def test_a_waiter_or_a_cook_sets_nothing():
    for role in ("waiter", "kitchen", "platform_admin", None, ""):
        for target in STATUSES:
            assert can_set(role, DIRTY, target) is False


def test_an_unknown_status_on_either_side_is_refused():
    assert can_set("admin", DIRTY, "sparkling") is False
    assert can_set("admin", "sparkling", CLEAN) is False
    assert can_set("admin", None, CLEAN) is False


def test_setting_the_status_a_room_already_has_is_not_a_transition():
    # Deliberately False, for every role including admin. Nothing moves, so there is
    # nothing to permit — and the route answers a repeat tap before it ever asks this,
    # returning the room unchanged and writing no event. Were this True, the no-op check
    # and this table would be two places that had to agree about a double-tap on a phone.
    for role in SETTERS:
        for status in STATUSES:
            assert can_set(role, status, status) is False


def test_a_note_is_required_only_for_out_of_order():
    assert note_required(OUT_OF_ORDER) is True
    for status in (CLEAN, DIRTY, INSPECTED):
        assert note_required(status) is False


# ----------------------------- job transitions -----------------------------
def test_an_open_job_can_be_picked_up_finished_or_called_off():
    assert can_move_job(OPEN, IN_PROGRESS) is True
    assert can_move_job(OPEN, DONE) is True
    assert can_move_job(OPEN, CANCELLED) is True


def test_a_job_in_progress_finishes_or_is_called_off():
    assert can_move_job(IN_PROGRESS, DONE) is True
    assert can_move_job(IN_PROGRESS, CANCELLED) is True
    # Acknowledging twice is what two attendants tapping at once looks like. It is not a
    # move; the route treats it as a no-op so neither of them sees an error.
    assert can_move_job(IN_PROGRESS, IN_PROGRESS) is False


def test_a_done_job_cannot_be_reopened():
    for target in JOB_STATUSES:
        assert can_move_job(DONE, target) is False


def test_a_cancelled_job_cannot_be_reopened_either():
    # Cancelling is a status rather than a delete precisely so "who asked for this and
    # when" survives. Reopening it would let that record be reused for something else.
    for target in JOB_STATUSES:
        assert can_move_job(CANCELLED, target) is False


def test_nothing_moves_backwards_to_open():
    for start in (IN_PROGRESS, DONE, CANCELLED):
        assert can_move_job(start, OPEN) is False


def test_an_unknown_job_status_is_refused():
    assert can_move_job(OPEN, "escalated") is False
    assert can_move_job("escalated", DONE) is False
    assert can_move_job(None, DONE) is False


def test_a_live_job_is_one_still_waiting_on_somebody():
    assert job_is_live(OPEN) is True
    assert job_is_live(IN_PROGRESS) is True
    assert job_is_live(DONE) is False
    assert job_is_live(CANCELLED) is False
    assert job_is_live(None) is False
    assert LIVE_JOB_STATUSES == (OPEN, IN_PROGRESS)


def test_the_three_priorities():
    assert PRIORITIES == ("low", "normal", "high")


# ------------------------------ the merge rule ------------------------------
def test_a_second_reason_is_appended_to_the_first():
    assert merge_reason("Spill on the carpet", "Also need fresh towels") == (
        "Spill on the carpet\nAlso need fresh towels")


def test_pressing_the_button_again_with_the_same_words_adds_nothing():
    # A guest unsure it worked types the same thing again. Two identical lines on one
    # card tell the attendant nothing and make the card look like two problems.
    assert merge_reason("Spill on the carpet", "Spill on the carpet") == "Spill on the carpet"
    assert merge_reason("Spill on the carpet", "  spill on the CARPET ") == "Spill on the carpet"


def test_a_repeat_of_something_said_earlier_in_the_thread_adds_nothing():
    thread = "Spill on the carpet\nAlso need fresh towels"
    assert merge_reason(thread, "Spill on the carpet") == thread


def test_an_empty_second_press_leaves_the_reason_alone():
    # "Something is wrong in 204" with no words is still worth knowing, but it must not
    # blank out or pad the reason already recorded.
    assert merge_reason("Spill on the carpet", "") == "Spill on the carpet"
    assert merge_reason("Spill on the carpet", "   ") == "Spill on the carpet"
    assert merge_reason("Spill on the carpet", None) == "Spill on the carpet"


def test_the_first_words_on_a_wordless_job_become_the_reason():
    assert merge_reason("", "Spill on the carpet") == "Spill on the carpet"
    assert merge_reason(None, "Spill on the carpet") == "Spill on the carpet"


def test_two_wordless_presses_stay_wordless():
    assert merge_reason(None, None) == ""
    assert merge_reason("", "  ") == ""


@pytest.mark.parametrize("status", STATUSES)
def test_every_status_is_answerable_by_both_predicates(status):
    # A status added to the vocabulary without a rule would fail here rather than in a
    # corridor: is_ready must have an opinion, and some role must be able to reach it.
    assert isinstance(is_ready(status), bool)
    assert any(can_set(role, other, status)
               for role in SETTERS for other in STATUSES if other != status)
