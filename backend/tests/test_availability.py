"""Pure availability tests — no server, no database."""
from services.availability import (
    ranges_overlap, room_is_available, count_available, CONSUMING_STATUSES,
)

RT = "type-deluxe"


def room(rid, active=True, ooo=None):
    return {"id": rid, "room_type_id": RT, "active": active, "out_of_order": ooo or []}


def booking(check_in, check_out, status="confirmed", rt=RT):
    return {"room_type_id": rt, "check_in": check_in, "check_out": check_out, "status": status}


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
