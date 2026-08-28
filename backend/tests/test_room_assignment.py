"""Pre-assigning a physical room to a booking, and the clash check that is the point.

No server. The endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, so both are called as what they are — the same style as test_isolation.py.

The rule under test is one sentence: a room may be assigned only if, across the
booking's **whole stay window**, no other live booking holds it and no out-of-order
block covers it. An earlier attempt at this feature shipped without it and put two
guests behind one door, so the clash cases are written first and in the most detail.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.auth as auth_router
import routers.bookings as bookings
import routers.frontdesk as frontdesk
import routers.payments as payments
import routers.reports as reports
import routers.staff as staff
from mock_db import MockDatabase
from models.folio import CheckInIn
from models.hotel import RoomAssignmentIn
from scoped_db import tenant_db
from services.access import DOMAINS, LIVE, SCREEN_KEYS

_UNSCOPED_HOLDERS = (db_module, security, auth_router, staff, payments, reports)

# A window and the two windows either side of it, sharing a boundary day each time.
EARLY_IN, EARLY_OUT = "2029-09-01", "2029-09-04"
STAY_IN, STAY_OUT = "2029-09-04", "2029-09-08"
LATE_IN, LATE_OUT = "2029-09-08", "2029-09-11"
# Overlaps STAY by exactly one night, the 7th.
LAP_IN, LAP_OUT = "2029-09-07", "2029-09-10"


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


@pytest.fixture
def hotel(tmp_path, monkeypatch):
    """One property, two rooms of one type, a suite nobody booked, and no bookings yet.

    Written the way test_isolation.py writes a tenant: a `properties` row and an admin
    carrying its `property_id`, then everything else through the scoped handle the
    application itself hands to routers.
    """
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    record = {"id": "p1", "name": "The Grand", "status": LIVE,
              "created_at": "2029-01-01T00:00:00+00:00"}
    run(handle.properties.insert_one(record))
    admin = {"id": "u1", "email": "admin@grand.example.com", "name": "Admin",
             "role": "admin", "domains": list(DOMAINS), "permissions": list(SCREEN_KEYS),
             "active": True, "property_id": "p1"}
    run(handle.users.insert_one(admin))
    db = run(tenant_db(admin))

    for rt_id, name in (("rt-dlx", "Deluxe"), ("rt-suite", "Suite")):
        run(db.room_types.insert_one({
            "id": rt_id, "name": name, "code": name[:3].upper(), "base_occupancy": 2,
            "max_occupancy": 3, "max_extra_beds": 1, "amenities": [], "images": [],
            "active": True}))
    for room_id, number, rt_id in (("room-101", "101", "rt-dlx"),
                                   ("room-102", "102", "rt-dlx"),
                                   ("room-901", "901", "rt-suite")):
        run(db.rooms.insert_one({
            "id": room_id, "number": number, "room_type_id": rt_id, "floor": "1",
            "active": True, "out_of_order": []}))
    run(db.meal_plans.insert_one({
        "id": "mp", "code": "EP", "name": "Room only",
        "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0,
        "active": True}))
    run(db.guests.insert_one({
        "id": "g1", "name": "Guest", "phone": "9990000001",
        "created_at": "2029-01-01T00:00:00+00:00"}))
    # A flat rate and one tax band for each type, so that editing a booking's dates can
    # reprice and reach the checks these tests are about rather than 422 on the way.
    for rt_id in ("rt-dlx", "rt-suite"):
        run(db.rates.insert_one({
            "id": f"rate-{rt_id}", "room_type_id": rt_id, "period_id": None,
            "base_rate": 1000.0, "extra_adult_rate": 0.0, "extra_child_rate": 0.0}))
    run(db.tax_slabs.insert_one({
        "id": "slab", "min_tariff": 0.0, "max_tariff": None, "rate_percent": 12.0,
        "active": True}))
    return admin, db


def make_booking(db, bid, check_in, check_out, room_type_id="rt-dlx",
                 status="confirmed", assigned_room_id=None):
    """A booking written straight in, so these tests exercise assignment rather than
    the pricing and inventory rules that `POST /bookings` also enforces."""
    run(db.bookings.insert_one({
        "id": bid, "reference": f"BF-2909-{bid}", "guest_id": "g1",
        "room_type_id": room_type_id, "meal_plan_id": "mp", "check_in": check_in,
        "check_out": check_out, "adults": 2, "children": 0, "extra_beds": 0,
        "status": status, "source": "front_desk", "quote": {"nights": []},
        "assigned_room_id": assigned_room_id,
        "created_at": datetime.now(timezone.utc).isoformat()}))
    return bid


def assign(admin, db, booking_id, room_id):
    return call(bookings.set_booking_room, booking_id=booking_id,
                payload=RoomAssignmentIn(room_id=room_id), user=admin, db=db)


def assign_refused(admin, db, booking_id, room_id) -> HTTPException:
    return refused(bookings.set_booking_room, booking_id=booking_id,
                   payload=RoomAssignmentIn(room_id=room_id), user=admin, db=db)


def stored(db, booking_id):
    return run(db.bookings.find_one({"id": booking_id}, {"_id": 0}))


# ------------------------------- the clash check -------------------------------
def test_two_overlapping_bookings_cannot_hold_one_room(hotel):
    admin, db = hotel
    make_booking(db, "first", STAY_IN, STAY_OUT)
    make_booking(db, "second", LAP_IN, LAP_OUT)

    assign(admin, db, "first", "room-101")
    refusal = assign_refused(admin, db, "second", "room-101")

    assert refusal.status_code == 409
    # Naming the holder is the point: the receptionist can go and move that booking,
    # rather than being told only that the room is unavailable.
    assert refusal.detail["reference"] == "BF-2909-first"
    assert refusal.detail["booking_id"] == "first"
    assert "BF-2909-first" in refusal.detail["message"]
    assert "101" in refusal.detail["message"]
    # And the refusal changed nothing.
    assert stored(db, "second")["assigned_room_id"] is None


def test_adjacent_bookings_can_hold_one_room(hotel):
    admin, db = hotel
    make_booking(db, "early", EARLY_IN, EARLY_OUT)
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    make_booking(db, "late", LATE_IN, LATE_OUT)

    # Departing on the 4th, arriving on the 4th, departing on the 8th, arriving on the
    # 8th. Half-open, so one door serves all three back to back.
    assign(admin, db, "stay", "room-101")
    assign(admin, db, "early", "room-101")
    assign(admin, db, "late", "room-101")

    assert [stored(db, b)["assigned_room_id"] for b in ("early", "stay", "late")] == \
        ["room-101"] * 3


def test_a_room_out_of_order_for_part_of_the_stay_is_refused(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    run(db.rooms.update_one({"id": "room-101"}, {"$set": {"out_of_order": [
        {"from": "2029-09-06", "to": "2029-09-07", "reason": "Burst pipe"}]}}))

    refusal = assign_refused(admin, db, "stay", "room-101")
    assert refusal.status_code == 409
    assert "Burst pipe" in refusal.detail["message"]
    assert stored(db, "stay")["assigned_room_id"] is None


def test_an_out_of_order_block_ending_on_the_arrival_day_does_not_refuse(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    run(db.rooms.update_one({"id": "room-101"}, {"$set": {"out_of_order": [
        {"from": "2029-09-01", "to": STAY_IN, "reason": "Repaint"}]}}))

    assign(admin, db, "stay", "room-101")
    assert stored(db, "stay")["assigned_room_id"] == "room-101"


def test_a_room_of_the_wrong_type_is_refused(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    # The suite is free for the whole window — it is the wrong type that refuses it,
    # because a suite handed to a standard booking changes what the guest pays for.
    refusal = assign_refused(admin, db, "stay", "room-901")
    assert refusal.status_code == 409
    assert stored(db, "stay")["assigned_room_id"] is None


def test_an_inactive_room_is_refused(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    run(db.rooms.update_one({"id": "room-101"}, {"$set": {"active": False}}))
    assert assign_refused(admin, db, "stay", "room-101").status_code == 409


def test_reassigning_frees_the_old_room_for_someone_else(hotel):
    admin, db = hotel
    make_booking(db, "first", STAY_IN, STAY_OUT)
    make_booking(db, "second", LAP_IN, LAP_OUT)

    assign(admin, db, "first", "room-101")
    assert assign_refused(admin, db, "second", "room-101").status_code == 409

    assign(admin, db, "first", "room-102")
    assign(admin, db, "second", "room-101")

    assert stored(db, "first")["assigned_room_id"] == "room-102"
    assert stored(db, "second")["assigned_room_id"] == "room-101"


def test_clearing_an_assignment_frees_the_room(hotel):
    admin, db = hotel
    make_booking(db, "first", STAY_IN, STAY_OUT)
    make_booking(db, "second", LAP_IN, LAP_OUT)

    assign(admin, db, "first", "room-101")
    assert assign_refused(admin, db, "second", "room-101").status_code == 409

    cleared = call(bookings.set_booking_room, booking_id="first",
                   payload=RoomAssignmentIn(room_id=None), user=admin, db=db)
    assert cleared["assigned_room_id"] is None
    assert cleared["room"] is None

    assign(admin, db, "second", "room-101")
    assert stored(db, "second")["assigned_room_id"] == "room-101"


def test_a_released_booking_does_not_hold_the_room_it_was_given(hotel):
    admin, db = hotel
    make_booking(db, "first", STAY_IN, STAY_OUT)
    make_booking(db, "second", LAP_IN, LAP_OUT)
    assign(admin, db, "first", "room-101")

    # Cancelled through the ordinary route, which does not clear assigned_room_id —
    # so the room is freed by the status, exactly as type inventory is.
    from models.hotel import CancelIn
    call(bookings.cancel_booking, booking_id="first", payload=CancelIn(reason="plans"),
         user=admin, db=db)
    assign(admin, db, "second", "room-101")
    assert stored(db, "second")["assigned_room_id"] == "room-101"


def test_assigning_the_room_a_booking_already_holds_is_not_a_clash_with_itself(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assign(admin, db, "stay", "room-101")
    assign(admin, db, "stay", "room-101")
    assert stored(db, "stay")["assigned_room_id"] == "room-101"


# ------------------------------ what may be assigned ------------------------------
def test_an_unknown_booking_and_an_unknown_room_are_404(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assert assign_refused(admin, db, "nobody", "room-101").status_code == 404
    assert assign_refused(admin, db, "stay", "no-such-room").status_code == 404


def test_a_finished_booking_cannot_be_given_a_room(hotel):
    admin, db = hotel
    for status in ("cancelled", "checked_out", "no_show"):
        make_booking(db, status, STAY_IN, STAY_OUT, status=status)
        assert assign_refused(admin, db, status, "room-101").status_code == 409


def test_a_tentative_hold_can_be_given_a_room(hotel):
    # A held room for a returning guest is exactly the case this feature exists for,
    # and holds are the bookings most likely to need it recorded early.
    admin, db = hotel
    make_booking(db, "hold", STAY_IN, STAY_OUT, status="tentative")
    assign(admin, db, "hold", "room-101")
    assert stored(db, "hold")["assigned_room_id"] == "room-101"


def test_an_in_house_guests_room_can_be_changed_but_not_taken_away(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT, status="checked_in",
                 assigned_room_id="room-101")
    # A guest can be moved after check-in — that is a room move, and the desk does it.
    assign(admin, db, "stay", "room-102")
    assert stored(db, "stay")["assigned_room_id"] == "room-102"
    # Leaving an in-house guest with no room is not a room move; it is a lost guest.
    refusal = refused(bookings.set_booking_room, booking_id="stay",
                      payload=RoomAssignmentIn(room_id=None), user=admin, db=db)
    assert refusal.status_code == 409
    assert stored(db, "stay")["assigned_room_id"] == "room-102"


def test_the_assignment_is_visible_on_the_booking_and_in_the_list(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assign(admin, db, "stay", "room-101")

    one = call(bookings.get_booking, booking_id="stay", user=admin, db=db)
    assert one["room"]["number"] == "101"

    rows = call(bookings.list_bookings, start="", end="", status="", q="",
                user=admin, db=db)
    assert [r["room"]["number"] for r in rows] == ["101"]

    make_booking(db, "unassigned", LATE_IN, LATE_OUT)
    rows = call(bookings.list_bookings, start="", end="", status="", q="",
                user=admin, db=db)
    assert {r["id"]: (r["room"] or {}).get("number") for r in rows} == \
        {"stay": "101", "unassigned": None}


# --------------------------- check-in uses the same rule ---------------------------
# This is the regression that matters most. Before pre-assignment, only a checked-in
# booking could hold a room, so check-in could clash-check against `status: checked_in`
# alone. Now a confirmed booking holds one too, and a check-in that still asked the old
# question would hand tomorrow's held room to today's walk-in and rediscover the bug
# from the other end.
def _proof():
    return {"id_proof_type": "Aadhaar", "id_proof_number": "1234-5678-9012"}


def test_check_in_refuses_a_room_held_by_an_overlapping_future_booking(hotel):
    admin, db = hotel
    make_booking(db, "future", LAP_IN, LAP_OUT)
    assign(admin, db, "future", "room-101")
    make_booking(db, "today", STAY_IN, STAY_OUT)

    refusal = refused(frontdesk.check_in, booking_id="today",
                      payload=CheckInIn(room_id="room-101", **_proof()),
                      user=admin, db=db)
    assert refusal.status_code == 409
    assert refusal.detail["reference"] == "BF-2909-future"
    assert stored(db, "today")["status"] == "confirmed"


def test_check_in_accepts_the_room_the_booking_was_pre_assigned(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assign(admin, db, "stay", "room-101")

    out = call(frontdesk.check_in, booking_id="stay",
               payload=CheckInIn(room_id="room-101", **_proof()), user=admin, db=db)
    assert out["booking"]["status"] == "checked_in"
    assert out["booking"]["assigned_room_id"] == "room-101"


def test_check_in_may_override_the_pre_assigned_room(hotel):
    # The guest at the desk may need a different room, and the desk decides.
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assign(admin, db, "stay", "room-101")

    out = call(frontdesk.check_in, booking_id="stay",
               payload=CheckInIn(room_id="room-102", **_proof()), user=admin, db=db)
    assert out["booking"]["assigned_room_id"] == "room-102"
    # And 101 is free again for the booking that was going to be moved out of it.
    make_booking(db, "other", LAP_IN, LAP_OUT)
    assign(admin, db, "other", "room-101")
    assert stored(db, "other")["assigned_room_id"] == "room-101"


def test_check_in_refuses_a_room_that_is_out_of_order_for_the_stay(hotel):
    admin, db = hotel
    make_booking(db, "stay", STAY_IN, STAY_OUT)
    run(db.rooms.update_one({"id": "room-101"}, {"$set": {"out_of_order": [
        {"from": "2029-09-05", "to": "2029-09-06", "reason": "Burst pipe"}]}}))
    refusal = refused(frontdesk.check_in, booking_id="stay",
                      payload=CheckInIn(room_id="room-101", **_proof()),
                      user=admin, db=db)
    assert refusal.status_code == 409


# --------------------- moving the dates cannot smuggle a clash ---------------------
# The clash check guards the assignment, but the stay window is half of what it checks
# against — so editing the dates of a booking that already holds a room is the other
# door into the same bug, and it has to be shut from this side too.
def test_extending_a_stay_over_a_room_someone_else_holds_is_refused(hotel):
    admin, db = hotel
    from models.hotel import BookingUpdateIn

    make_booking(db, "early", EARLY_IN, EARLY_OUT)
    make_booking(db, "late", LATE_IN, LATE_OUT)
    assign(admin, db, "early", "room-101")
    assign(admin, db, "late", "room-101")  # adjacent, so both may hold it

    # Now stretch the early stay across the late one's nights.
    refusal = refused(bookings.update_booking, booking_id="early",
                      payload=BookingUpdateIn(check_out=LATE_OUT), user=admin, db=db)
    assert refusal.status_code == 409
    assert refusal.detail["reference"] == "BF-2909-late"
    # Nothing moved: the dates and the room are as they were.
    assert stored(db, "early")["check_out"] == EARLY_OUT
    assert stored(db, "early")["assigned_room_id"] == "room-101"


def test_a_booking_holding_no_room_may_still_move_freely(hotel):
    admin, db = hotel
    from models.hotel import BookingUpdateIn

    make_booking(db, "late", LATE_IN, LATE_OUT)
    assign(admin, db, "late", "room-101")
    # room-102 is free, so the type has inventory; this booking holds no door at all.
    make_booking(db, "early", EARLY_IN, EARLY_OUT)

    call(bookings.update_booking, booking_id="early",
         payload=BookingUpdateIn(check_out=LATE_OUT), user=admin, db=db)
    assert stored(db, "early")["check_out"] == LATE_OUT


def test_a_stay_may_still_be_shortened_while_holding_a_room(hotel):
    admin, db = hotel
    from models.hotel import BookingUpdateIn

    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assign(admin, db, "stay", "room-101")
    call(bookings.update_booking, booking_id="stay",
         payload=BookingUpdateIn(check_out="2029-09-06"), user=admin, db=db)
    assert stored(db, "stay")["check_out"] == "2029-09-06"
    assert stored(db, "stay")["assigned_room_id"] == "room-101"


def test_moving_a_stay_onto_an_out_of_order_block_is_refused(hotel):
    admin, db = hotel
    from models.hotel import BookingUpdateIn

    make_booking(db, "stay", STAY_IN, STAY_OUT)
    assign(admin, db, "stay", "room-101")
    run(db.rooms.update_one({"id": "room-101"}, {"$set": {"out_of_order": [
        {"from": "2029-09-09", "to": "2029-09-12", "reason": "Repaint"}]}}))

    refusal = refused(bookings.update_booking, booking_id="stay",
                      payload=BookingUpdateIn(check_in="2029-09-09",
                                              check_out="2029-09-11"),
                      user=admin, db=db)
    assert refusal.status_code == 409
    assert stored(db, "stay")["check_in"] == STAY_IN
