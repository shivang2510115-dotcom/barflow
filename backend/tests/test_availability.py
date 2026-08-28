"""Pure availability tests — no server, no database."""
from services.availability import (
    ranges_overlap, room_is_available, count_available, CONSUMING_STATUSES,
    blocking_out_of_order, booking_holding_room,
)

RT = "type-deluxe"


def room(rid, active=True, ooo=None):
    return {"id": rid, "room_type_id": RT, "active": active, "out_of_order": ooo or []}


def booking(check_in, check_out, status="confirmed", rt=RT):
    return {"room_type_id": rt, "check_in": check_in, "check_out": check_out, "status": status}


def holder(bid, check_in, check_out, room_id, status="confirmed"):
    """A booking that holds a specific physical room."""
    return {"id": bid, "reference": f"BF-{bid}", "room_type_id": RT,
            "check_in": check_in, "check_out": check_out, "status": status,
            "assigned_room_id": room_id}


ROOMS = [room("r1"), room("r2"), room("r3")]


def test_touching_ranges_do_not_overlap():
    # Checkout on the 5th, arrival on the 5th — both fit.
    assert ranges_overlap("2026-08-03", "2026-08-05", "2026-08-05", "2026-08-07") is False


def test_partial_overlap():
    assert ranges_overlap("2026-08-03", "2026-08-06", "2026-08-05", "2026-08-08") is True


def test_fully_contained_overlap():
    assert ranges_overlap("2026-08-01", "2026-08-10", "2026-08-04", "2026-08-06") is True


def test_checkout_day_frees_the_room():
    bookings = [booking("2026-08-03", "2026-08-05")]
    assert count_available(RT, "2026-08-05", "2026-08-07", ROOMS, bookings) == 3


def test_overlapping_booking_consumes_one_room():
    bookings = [booking("2026-08-03", "2026-08-06")]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 2


def test_cancelled_and_no_show_do_not_consume():
    bookings = [
        booking("2026-08-03", "2026-08-06", status="cancelled"),
        booking("2026-08-03", "2026-08-06", status="no_show"),
    ]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 3


def test_tentative_and_checked_in_do_consume():
    assert "tentative" in CONSUMING_STATUSES
    assert "checked_in" in CONSUMING_STATUSES
    bookings = [
        booking("2026-08-03", "2026-08-06", status="tentative"),
        booking("2026-08-03", "2026-08-06", status="checked_in"),
    ]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 1


def test_other_room_types_are_ignored():
    bookings = [booking("2026-08-03", "2026-08-06", rt="type-suite")]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 3


def test_inactive_room_never_counts():
    rooms = [room("r1"), room("r2", active=False)]
    assert count_available(RT, "2026-08-04", "2026-08-05", rooms, []) == 1


def test_out_of_order_removes_room_for_those_dates_only():
    rooms = [room("r1", ooo=[{"from": "2026-08-04", "to": "2026-08-06", "reason": "Repaint"}]), room("r2")]
    assert count_available(RT, "2026-08-04", "2026-08-05", rooms, []) == 1
    # Free again from the 6th — the out-of-order range is half-open.
    assert count_available(RT, "2026-08-06", "2026-08-07", rooms, []) == 2


def test_room_is_available_helper():
    r = room("r1", ooo=[{"from": "2026-08-04", "to": "2026-08-06"}])
    assert room_is_available(r, "2026-08-06", "2026-08-08") is True
    assert room_is_available(r, "2026-08-05", "2026-08-08") is False


def test_never_returns_negative():
    bookings = [booking("2026-08-03", "2026-08-06") for _ in range(5)]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 0


# ---------------------- holding one specific room ----------------------
# `count_available` answers "how many rooms of this type are left", which is the wrong
# question for an assignment: a type with two free rooms still cannot give both to the
# same physical door. These two predicates answer the per-room question instead, and are
# what the assign endpoint and check-in both refuse on.


def test_an_overlapping_live_booking_holds_the_room():
    held = [holder("b1", "2026-08-03", "2026-08-06", "r1")]
    clash = booking_holding_room("r1", "2026-08-05", "2026-08-08", held)
    assert clash is not None and clash["id"] == "b1"


def test_adjacent_bookings_can_hold_the_same_room():
    # Departing on the 6th, arriving on the 6th. Half-open, so one door serves both.
    held = [holder("b1", "2026-08-03", "2026-08-06", "r1")]
    assert booking_holding_room("r1", "2026-08-06", "2026-08-09", held) is None
    # And the other way round: the earlier stay may take a room the later one holds.
    later = [holder("b2", "2026-08-06", "2026-08-09", "r1")]
    assert booking_holding_room("r1", "2026-08-03", "2026-08-06", later) is None


def test_a_booking_holding_a_different_room_is_not_a_clash():
    held = [holder("b1", "2026-08-03", "2026-08-06", "r2")]
    assert booking_holding_room("r1", "2026-08-04", "2026-08-05", held) is None


def test_a_booking_holding_no_room_at_all_is_not_a_clash():
    unassigned = [{**holder("b1", "2026-08-03", "2026-08-06", "r1"),
                   "assigned_room_id": None}]
    assert booking_holding_room("r1", "2026-08-04", "2026-08-05", unassigned) is None


def test_released_statuses_do_not_hold_the_room():
    # Same three statuses that release type inventory release the door: a cancelled
    # booking still carrying the room it was once given must not block the next guest.
    for status in ("cancelled", "no_show", "checked_out"):
        held = [holder("b1", "2026-08-03", "2026-08-06", "r1", status=status)]
        assert booking_holding_room("r1", "2026-08-04", "2026-08-05", held) is None


def test_every_consuming_status_holds_the_room():
    for status in sorted(CONSUMING_STATUSES):
        held = [holder("b1", "2026-08-03", "2026-08-06", "r1", status=status)]
        assert booking_holding_room("r1", "2026-08-04", "2026-08-05", held) is not None


def test_a_booking_does_not_clash_with_itself():
    # Reassigning a booking to the room it already holds, and re-checking the room at
    # check-in, both ask this question about a booking that is already the holder.
    held = [holder("b1", "2026-08-03", "2026-08-06", "r1")]
    assert booking_holding_room("r1", "2026-08-03", "2026-08-06", held,
                                exclude_booking_id="b1") is None
    assert booking_holding_room("r1", "2026-08-03", "2026-08-06", held,
                                exclude_booking_id="b2") is not None


def test_the_first_clash_is_named_not_merely_counted():
    # The 409 has to say who holds the room, so the predicate returns the booking.
    held = [holder("b1", "2026-08-01", "2026-08-04", "r1"),
            holder("b2", "2026-08-04", "2026-08-09", "r1")]
    clash = booking_holding_room("r1", "2026-08-05", "2026-08-06", held)
    assert clash["reference"] == "BF-b2"


def test_out_of_order_block_covering_part_of_the_stay_is_named():
    r = room("r1", ooo=[{"from": "2026-08-05", "to": "2026-08-07", "reason": "Repaint"}])
    block = blocking_out_of_order(r, "2026-08-03", "2026-08-06")
    assert block is not None and block["reason"] == "Repaint"


def test_out_of_order_block_ending_on_the_arrival_day_does_not_block():
    r = room("r1", ooo=[{"from": "2026-08-03", "to": "2026-08-06"}])
    assert blocking_out_of_order(r, "2026-08-06", "2026-08-08") is None
    assert blocking_out_of_order(r, "2026-08-05", "2026-08-08") is not None


def test_room_is_available_still_answers_the_same_question():
    # The bool wrapper is now expressed in terms of the named block, so the two cannot
    # drift apart and disagree about the same room.
    r = room("r1", ooo=[{"from": "2026-08-04", "to": "2026-08-06"}])
    assert room_is_available(r, "2026-08-06", "2026-08-08") is True
    assert room_is_available(r, "2026-08-05", "2026-08-08") is False
    assert room_is_available(room("r2", active=False), "2026-08-06", "2026-08-08") is False
