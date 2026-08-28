"""Extending a stay: two more nights, after the booking was made.

The guest at the desk asks to stay on. That is not "edit the booking" — check-in never
moves for someone already in the room, the extra nights have to be free, the nights
already quoted must keep their price, and if the guest is in-house the new nights have to
reach the folio they will be billed from.

Five claims, each a way this goes wrong if it is written as a date edit:

* **check-out only.** The window grows at the far end; `check_in` is not in the payload
  and cannot move.
* **the room must be free for the extra nights** — the pre-assigned room if there is one,
  the type's inventory if there is not — and a clash is a 409 that *names* what blocks it,
  so the desk can go and move it rather than only being told no.
* **only the added nights are priced.** A rate rise since the booking was taken must not
  reach back and change what the guest was told the first three nights cost.
* **an in-house guest's new nights post through `post_due_nights`**, the lazy poster that
  already exists, rather than through a second mechanism that can disagree with it.
* **a dead booking is refused**, the same three statuses `update_booking` refuses.

No server: the endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, the same style as test_room_assignment.py.
"""
import asyncio
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.auth as auth_router
import routers.bookings as bookings
import routers.folios as folios
import routers.payments as payments
import routers.reports as reports
import routers.staff as staff
from models.folio import Folio
from models.hotel import BookingIn, ExtendStayIn, RoomAssignmentIn
from mock_db import MockDatabase
from scoped_db import PropertyScopedDatabase
from services.access import DOMAINS, LIVE, SCREEN_KEYS

_UNSCOPED_HOLDERS = (db_module, security, auth_router, staff, payments, reports)

# A two-night stay, and the two nights after it.
STAY_IN, STAY_OUT = "2029-11-04", "2029-11-06"
EXTENDED_OUT = "2029-11-08"
ADDED = ["2029-11-06", "2029-11-07"]

# ₹4,000 a night at 12% is ₹4,480 on the folio.
NIGHT_TARIFF = 4000.0
NIGHT_TOTAL = 4480.0


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


@dataclass
class Hotel:
    admin: dict
    desk: dict
    db: PropertyScopedDatabase


@pytest.fixture
def hotel(tmp_path, monkeypatch):
    """One property, two Deluxe rooms, a flat ₹4,000 rate and one GST band.

    Meal plans are off — the property setting's default — so a booking is taken at one
    price and the arithmetic in these tests is the room rate and nothing else. The
    plan-carrying case has its own test at the bottom.
    """
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    run(handle.properties.insert_one({
        "id": "p1", "name": "The Grand", "status": LIVE,
        "meal_plans_enabled": False, "created_at": "2029-01-01T00:00:00+00:00"}))

    def person(uid, role):
        return {"id": uid, "email": f"{uid}@grand.example.com", "name": role,
                "role": role, "domains": list(DOMAINS), "permissions": list(SCREEN_KEYS),
                "active": True, "property_id": "p1"}

    admin, desk = person("u-admin", "admin"), person("u-desk", "front_desk")
    run(handle.users.insert_one(admin))
    run(handle.users.insert_one(desk))

    db = PropertyScopedDatabase("p1")
    run(db.room_types.insert_one({
        "id": "rt-dlx", "name": "Deluxe", "code": "DLX", "base_occupancy": 2,
        "max_occupancy": 3, "max_extra_beds": 1, "amenities": [], "images": [],
        "active": True}))
    for room_id, number in (("room-101", "101"), ("room-102", "102")):
        run(db.rooms.insert_one({
            "id": room_id, "number": number, "room_type_id": "rt-dlx", "floor": "1",
            "active": True, "out_of_order": []}))
    for gid, name in (("g1", "Guest One"), ("g2", "Guest Two")):
        run(db.guests.insert_one({
            "id": gid, "name": name, "phone": f"999000000{gid[-1]}",
            "created_at": "2029-01-01T00:00:00+00:00"}))
    run(db.rates.insert_one({
        "id": "rate-dlx", "room_type_id": "rt-dlx", "period_id": None,
        "base_rate": NIGHT_TARIFF, "extra_adult_rate": 0.0, "extra_child_rate": 0.0}))
    run(db.tax_slabs.insert_one({
        "id": "slab", "min_tariff": 0.0, "max_tariff": 7500.0, "rate_percent": 12.0,
        "active": True}))
    return Hotel(admin=admin, desk=desk, db=db)


def book(hotel, *, guest_id="g1", check_in=STAY_IN, check_out=STAY_OUT) -> dict:
    return call(bookings.create_booking, payload=BookingIn(
        guest_id=guest_id, room_type_id="rt-dlx",
        check_in=check_in, check_out=check_out), user=hotel.admin, db=hotel.db)


def assign(hotel, booking_id, room_id) -> dict:
    return call(bookings.set_booking_room, booking_id=booking_id,
                payload=RoomAssignmentIn(room_id=room_id),
                user=hotel.admin, db=hotel.db)


def extend(hotel, booking_id, check_out=EXTENDED_OUT, user=None) -> dict:
    return call(bookings.extend_stay, booking_id=booking_id,
                payload=ExtendStayIn(check_out=check_out),
                user=user or hotel.admin, db=hotel.db)


def refuse_extension(hotel, booking_id, check_out=EXTENDED_OUT,
                     user=None) -> HTTPException:
    return refused(bookings.extend_stay, booking_id=booking_id,
                   payload=ExtendStayIn(check_out=check_out),
                   user=user or hotel.admin, db=hotel.db)


def stored(hotel, booking_id) -> dict:
    return run(hotel.db.bookings.find_one({"id": booking_id}, {"_id": 0}))


def set_status(hotel, booking_id, status) -> None:
    run(hotel.db.bookings.update_one({"id": booking_id}, {"$set": {"status": status}}))


# ------------------------------ what it moves ------------------------------
def test_an_extension_moves_check_out_and_nothing_else(hotel):
    made = book(hotel)
    result = extend(hotel, made["id"])

    after = stored(hotel, made["id"])
    assert after["check_in"] == STAY_IN, "check-in never moves"
    assert after["check_out"] == EXTENDED_OUT
    assert after["status"] == made["status"]
    assert result["check_out"] == EXTENDED_OUT


def test_check_out_has_to_move_later(hotel):
    made = book(hotel)
    for attempt in (STAY_OUT, STAY_IN, "2029-11-05"):
        exc = refuse_extension(hotel, made["id"], check_out=attempt)
        assert exc.status_code == 400
    assert stored(hotel, made["id"])["check_out"] == STAY_OUT


def test_a_checked_in_booking_can_be_extended(hotel):
    made = book(hotel)
    assign(hotel, made["id"], "room-101")
    set_status(hotel, made["id"], "checked_in")

    extend(hotel, made["id"])
    assert stored(hotel, made["id"])["check_out"] == EXTENDED_OUT


@pytest.mark.parametrize("status", ["cancelled", "checked_out", "no_show"])
def test_a_dead_booking_cannot_be_extended(hotel, status):
    """The same three `update_booking` refuses."""
    made = book(hotel)
    set_status(hotel, made["id"], status)

    exc = refuse_extension(hotel, made["id"])
    assert exc.status_code == 409
    assert status in str(exc.detail)
    assert stored(hotel, made["id"])["check_out"] == STAY_OUT


def test_an_unknown_booking_is_a_404(hotel):
    assert refuse_extension(hotel, "no-such-booking").status_code == 404


def test_the_front_desk_may_extend_a_stay(hotel):
    """Extending is operational — it is the desk the request arrives at."""
    made = book(hotel)
    extend(hotel, made["id"], user=hotel.desk)
    assert stored(hotel, made["id"])["check_out"] == EXTENDED_OUT


# --------------------------- the room must be free ---------------------------
def test_an_extension_onto_a_night_the_room_is_held_for_is_refused(hotel):
    """The pre-assigned room. The 409 names the booking in the way, because the desk's
    next move is to go and look at it."""
    made = book(hotel)
    assign(hotel, made["id"], "room-101")

    blocker = book(hotel, guest_id="g2", check_in="2029-11-07", check_out="2029-11-09")
    assign(hotel, blocker["id"], "room-101")

    exc = refuse_extension(hotel, made["id"])
    assert exc.status_code == 409
    assert exc.detail["reference"] == blocker["reference"]
    assert exc.detail["booking_id"] == blocker["id"]
    assert "101" in exc.detail["message"]
    assert stored(hotel, made["id"])["check_out"] == STAY_OUT


def test_an_extension_over_an_out_of_order_block_is_refused(hotel):
    made = book(hotel)
    assign(hotel, made["id"], "room-101")
    run(hotel.db.rooms.update_one({"id": "room-101"}, {"$set": {"out_of_order": [
        {"from": "2029-11-07", "to": "2029-11-09", "reason": "burst pipe"}]}}))

    exc = refuse_extension(hotel, made["id"])
    assert exc.status_code == 409
    assert "burst pipe" in exc.detail["message"]
    assert stored(hotel, made["id"])["check_out"] == STAY_OUT


def test_an_extension_with_no_room_of_the_type_free_is_refused(hotel):
    """No room assigned, so the question is the type's inventory: two Deluxe rooms, and
    two other bookings holding them across the added nights."""
    made = book(hotel)
    for guest in ("g1", "g2"):
        book(hotel, guest_id=guest, check_in="2029-11-06", check_out="2029-11-08")

    exc = refuse_extension(hotel, made["id"])
    assert exc.status_code == 409
    assert "Deluxe" in exc.detail["message"]
    assert exc.detail["check_out"] == EXTENDED_OUT
    assert stored(hotel, made["id"])["check_out"] == STAY_OUT


def test_the_booking_does_not_block_its_own_extension(hotel):
    """One Deluxe booked out of two, extended: its own nights are not counted against it,
    and the nights being added are the only ones asked about."""
    made = book(hotel)
    book(hotel, guest_id="g2", check_in=STAY_IN, check_out=STAY_OUT)

    extend(hotel, made["id"])
    assert stored(hotel, made["id"])["check_out"] == EXTENDED_OUT


def test_a_room_free_only_for_part_of_the_added_nights_is_refused(hotel):
    """The block covers the second added night only. A check that looked at the first
    night alone, or at the new check-out date alone, would let this through."""
    made = book(hotel)
    assign(hotel, made["id"], "room-101")
    blocker = book(hotel, guest_id="g2", check_in="2029-11-07", check_out="2029-11-08")
    assign(hotel, blocker["id"], "room-101")

    assert refuse_extension(hotel, made["id"]).status_code == 409


# --------------------------- only the new nights ---------------------------
def test_only_the_added_nights_are_priced_at_the_current_rate(hotel):
    """The rate went up after the booking was taken. The first two nights keep the
    number the guest was quoted."""
    made = book(hotel)
    assert [n["tariff"] for n in made["quote"]["nights"]] == [4000.0, 4000.0]

    run(hotel.db.rates.update_one({"id": "rate-dlx"}, {"$set": {"base_rate": 6000.0}}))
    result = extend(hotel, made["id"])

    nights = stored(hotel, made["id"])["quote"]["nights"]
    assert [n["date"] for n in nights] == [STAY_IN, "2029-11-05"] + ADDED
    assert [n["tariff"] for n in nights] == [4000.0, 4000.0, 6000.0, 6000.0]
    assert stored(hotel, made["id"])["quote"]["room_subtotal"] == 20000.0
    assert stored(hotel, made["id"])["quote"]["tax_total"] == 2400.0
    assert stored(hotel, made["id"])["quote"]["total"] == 22400.0

    # And the desk is told what the extension itself costs, to quote to the guest.
    assert result["added"]["room_subtotal"] == 12000.0
    assert result["added"]["total"] == 13440.0
    assert [n["date"] for n in result["added"]["nights"]] == ADDED


def test_added_nights_with_no_rate_are_refused_not_priced_at_zero(hotel):
    made = book(hotel)
    run(hotel.db.rates.delete_many({}))

    exc = refuse_extension(hotel, made["id"])
    assert exc.status_code == 422
    assert exc.detail["dates"] == ADDED

    after = stored(hotel, made["id"])
    assert after["check_out"] == STAY_OUT
    assert after["quote"]["total"] == 8960.0


def test_a_refused_extension_leaves_the_stored_quote_untouched(hotel):
    made = book(hotel)
    assign(hotel, made["id"], "room-101")
    blocker = book(hotel, guest_id="g2", check_in="2029-11-06", check_out="2029-11-09")
    assign(hotel, blocker["id"], "room-101")

    refuse_extension(hotel, made["id"])
    assert stored(hotel, made["id"])["quote"] == made["quote"]


def test_an_extension_carries_the_bookings_own_meal_plan(hotel):
    """A booking taken on a plan keeps being priced on it — the plan on the booking is
    what governs that booking, not what the property's setting says today."""
    run(hotel.db.meal_plans.insert_one({
        "id": "mp-cp", "code": "CP", "name": "With breakfast",
        "price_per_adult_per_night": 500.0, "price_per_child_per_night": 250.0,
        "active": True}))
    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"meal_plans_enabled": True}}))

    made = call(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", meal_plan_id="mp-cp",
        check_in=STAY_IN, check_out=STAY_OUT), user=hotel.admin, db=hotel.db)
    assert made["quote"]["nights"][0]["tariff"] == 5000.0

    result = extend(hotel, made["id"])
    assert [n["tariff"] for n in result["added"]["nights"]] == [5000.0, 5000.0]


# ------------------------- and it reaches the folio -------------------------
def test_an_in_house_guests_extension_posts_exactly_the_added_nights(hotel, monkeypatch):
    """No second posting mechanism: the extension moves `check_out` and grows the stored
    quote, and `post_due_nights` — which derives what is due from exactly those two —
    picks the new nights up on the next folio read."""
    monkeypatch.setattr(folios, "_today", lambda: "2029-11-20")

    made = book(hotel)
    assign(hotel, made["id"], "room-101")
    set_status(hotel, made["id"], "checked_in")
    folio = Folio(booking_id=made["id"], guest_id="g1").model_dump()
    run(hotel.db.folios.insert_one(folio))

    first = call(folios.get_folio, folio_id=folio["id"], user=hotel.admin, db=hotel.db)
    nights = [e for e in first["entries"] if e["kind"] == "room_night"]
    assert [e["charge_date"] for e in nights] == [STAY_IN, "2029-11-05"]
    assert first["balance"] == 2 * NIGHT_TOTAL

    extend(hotel, made["id"])

    after = call(folios.get_folio, folio_id=folio["id"], user=hotel.admin, db=hotel.db)
    nights = sorted((e for e in after["entries"] if e["kind"] == "room_night"),
                    key=lambda e: e["charge_date"])
    assert [e["charge_date"] for e in nights] == [STAY_IN, "2029-11-05"] + ADDED
    assert [e["amount"] for e in nights] == [NIGHT_TOTAL] * 4
    assert after["balance"] == 4 * NIGHT_TOTAL

    # Read again: nothing new. The added nights are posted once, like every other night.
    again = call(folios.get_folio, folio_id=folio["id"], user=hotel.admin, db=hotel.db)
    assert len([e for e in again["entries"] if e["kind"] == "room_night"]) == 4
    assert again["balance"] == 4 * NIGHT_TOTAL


def test_the_added_nights_post_at_the_price_the_extension_quoted(hotel, monkeypatch):
    """A rate change between the extension and the folio read must not move the figure
    either: the folio bills the stored quote, and the extension is what wrote it."""
    monkeypatch.setattr(folios, "_today", lambda: "2029-11-20")

    made = book(hotel)
    assign(hotel, made["id"], "room-101")
    set_status(hotel, made["id"], "checked_in")
    folio = Folio(booking_id=made["id"], guest_id="g1").model_dump()
    run(hotel.db.folios.insert_one(folio))

    run(hotel.db.rates.update_one({"id": "rate-dlx"}, {"$set": {"base_rate": 6000.0}}))
    extend(hotel, made["id"])
    run(hotel.db.rates.update_one({"id": "rate-dlx"}, {"$set": {"base_rate": 9000.0}}))

    after = call(folios.get_folio, folio_id=folio["id"], user=hotel.admin, db=hotel.db)
    by_date = {e["charge_date"]: e["amount"]
               for e in after["entries"] if e["kind"] == "room_night"}
    assert by_date[STAY_IN] == NIGHT_TOTAL
    assert by_date["2029-11-05"] == NIGHT_TOTAL
    assert by_date["2029-11-06"] == 6720.0   # 6000 + 12%
    assert by_date["2029-11-07"] == 6720.0
