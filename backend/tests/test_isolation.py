"""Isolation: two hotels in one database, and every place one could see the other.

The test that does not count is "hotel A sees its own three bookings" — it passes when
the filter is missing and hotel B happens to have none. So every test here creates the
same shape of data in **both** properties and asserts against B's ids specifically: that
none of them appear in A's response, and that naming one directly from A's session is a
**404, not a 403** — a 403 confirms the record exists, which is itself the leak.

No server. The endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, so both are called as what they are — the same style as test_tenancy.py.
What is exercised is the real router code with the real handle bound to a real
(file-backed) mock database; only the transport is absent.
"""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import db as db_module
import scoped_db
import security
import routers.analytics as analytics
import routers.auth as auth_router
import routers.bookings as bookings
import routers.folios as folios
import routers.frontdesk as frontdesk
import routers.guests as guests
import routers.housekeeping as housekeeping
import routers.inventory as inventory
import routers.menu as menu
import routers.orders as orders
import routers.payments as payments
import routers.planner as planner
import routers.rates as rates
import routers.reports as reports
import routers.rooms as rooms
import routers.staff as staff
import routers.tables as tables
from mock_db import MockDatabase
from models.folio import ChargeIn, PaymentIn, VoidIn
from models.planner import CalendarEventIn
from models.hotel import (
    BookingIn, BookingUpdateIn, CancelIn, GuestIn, GuestRequestIn, HousekeepingStatusIn,
    MealPlanIn, RoomAssignmentIn, RoomIn, TaxSlab)
from scoped_db import PropertyScopedDatabase, UnscopedCollectionError, tenant_db
from services.access import DOMAINS, LIVE, PENDING, SCREEN_KEYS, SUSPENDED
from services.clock import today as local_today
from services.planner import seed_categories as seed_planner_categories


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    """Call an endpoint coroutine directly, as test_tenancy.py calls the dependency."""
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    """Call an endpoint that must refuse, and hand back the refusal to be inspected."""
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


# --------------------------------- the world ---------------------------------
# `scoped_db` reads `db.unscoped_db` at attribute-access time rather than binding it at
# import, so patching the `db` module swaps the database under every router's scoped
# handle at once — the point of binding the scope in one place. The rest of this tuple is
# the whole list of modules that hold the unscoped handle themselves, which is short
# enough to name and is meant to stay that way.
_UNSCOPED_HOLDERS = (db_module, security, auth_router, staff, payments, reports)


@dataclass
class Hotel:
    """One tenant, and the handle its routers would receive."""
    tag: str
    record: dict
    admin: dict
    db: PropertyScopedDatabase

    def ids(self, collection: str) -> set:
        rows = run(db_module.unscoped_db[collection].find({}, {"_id": 0}).to_list(10000))
        return {r["id"] for r in rows if r.get("property_id") == self.record["id"]}


def make_property(tag: str, name: str, status: str = LIVE) -> Hotel:
    """A second hotel, indistinguishable from the first as far as any router can tell.

    Signup does not exist yet — it is a later task — so the tenant is written the way the
    startup migration writes the first one: a `properties` row, and an admin user
    carrying its `property_id`. Everything after that goes through the ordinary code
    path, including `tenant_db`, which is the dependency the application itself uses to
    turn that user into a scoped handle. Nothing here reaches around the mechanism under
    test; it only stands where the signup form will.
    """
    unscoped = db_module.unscoped_db
    record = {"id": f"{tag}-property", "name": name, "status": status,
              "created_at": f"2026-0{1 if tag == 'a' else 2}-01T00:00:00+00:00"}
    run(unscoped.properties.insert_one(record))
    admin = {"id": f"{tag}-admin", "email": f"admin@{tag}.example.com", "name": f"Admin {name}",
             "role": "admin", "domains": list(DOMAINS), "permissions": list(SCREEN_KEYS),
             "active": True, "property_id": record["id"]}
    run(unscoped.users.insert_one(admin))
    return Hotel(tag=tag, record=record, admin=admin, db=run(tenant_db(admin)))


def stock_hotel(hotel: Hotel, money: float) -> None:
    """One of everything, written through the hotel's own scoped handle.

    Both hotels get the *same* human-facing identifiers — room "101", phone
    "9990000001", booking reference "BF-2608-0001", table "T01" — because two hotels
    genuinely do share those, and a test that gave them different ones would pass on a
    global unique index that must not exist.
    """
    t, db = hotel.tag, hotel.db
    day = local_today()
    soon = (date.fromisoformat(day) + timedelta(days=3)).isoformat()
    later = (date.fromisoformat(day) + timedelta(days=5)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    run(db.room_types.insert_one({
        "id": f"{t}-rt", "name": "Deluxe", "code": "DLX", "base_occupancy": 2,
        "max_occupancy": 3, "max_extra_beds": 1, "amenities": [], "images": [],
        "active": True}))
    run(db.rooms.insert_one({
        "id": f"{t}-room", "number": "101", "room_type_id": f"{t}-rt", "floor": "1",
        "active": True, "out_of_order": []}))
    run(db.meal_plans.insert_one({
        "id": f"{t}-mp", "code": "EP", "name": "Room only",
        "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0,
        "active": True}))
    run(db.tax_slabs.insert_one({
        "id": f"{t}-slab", "min_tariff": 0.0, "max_tariff": None, "rate_percent": 12.0,
        "active": True}))
    run(db.rates.insert_one({
        "id": f"{t}-rate", "room_type_id": f"{t}-rt", "period_id": None,
        "base_rate": 1000.0, "extra_adult_rate": 0.0, "extra_child_rate": 0.0}))
    run(db.rate_periods.insert_one({
        "id": f"{t}-period", "name": "Peak", "start_date": soon, "end_date": later,
        "priority": 0, "active": True}))
    run(db.guests.insert_one({
        "id": f"{t}-guest", "name": f"Guest {t.upper()}", "phone": "9990000001",
        "created_at": now}))
    run(db.bookings.insert_one({
        "id": f"{t}-booking", "reference": "BF-2608-0001", "guest_id": f"{t}-guest",
        "room_type_id": f"{t}-rt", "meal_plan_id": f"{t}-mp", "check_in": soon,
        "check_out": later, "adults": 2, "children": 0, "extra_beds": 0,
        "status": "confirmed", "source": "front_desk", "quote": {"nights": []},
        "created_at": now}))
    run(db.folios.insert_one({
        "id": f"{t}-folio", "booking_id": f"{t}-booking", "guest_id": f"{t}-guest",
        "status": "open", "balance": money, "opened_at": now}))
    run(db.folio_entries.insert_one({
        "id": f"{t}-entry", "folio_id": f"{t}-folio", "kind": "misc_charge",
        "direction": "debit", "amount": money, "description": "Laundry",
        "posted_at": now, "posted_by": "system", "charge_date": None}))
    run(db.tables.insert_one({
        "id": f"{t}-table", "label": "T01", "capacity": 4, "zone": "Bar",
        "status": "free", "current_order_id": None}))
    run(db.menu.insert_one({
        "id": f"{t}-menu", "name": f"House Special {t.upper()}", "category": "Cocktails",
        "price": money, "description": "", "image": "", "station": "bar",
        "available": True}))
    run(db.inventory.insert_one({
        "id": f"{t}-inv", "name": "Gin 750ml", "unit": "bottle", "stock": 1.0,
        "threshold": 5.0, "cost_per_unit": 10.0, "category": "spirits"}))
    run(db.orders.insert_one({
        "id": f"{t}-order", "table_id": f"{t}-table", "table_label": "T01",
        "items": [{"id": f"{t}-item", "menu_item_id": f"{t}-menu", "name": "House Special",
                   "price": money, "quantity": 1, "station": "bar", "notes": "",
                   "status": "pending", "created_at": now}],
        "status": "settled", "subtotal": money, "tax": 0.0, "discount": 0.0,
        "total": money, "payment_method": "cash", "customer_name": f"Walk-in {t}",
        "customer_phone": "9990000009", "source": "pos", "created_at": now,
        "settled_at": now}))
    run(db.reservations.insert_one({
        "id": f"{t}-res", "guest_name": "Diner", "phone": "9990000002", "party_size": 2,
        "date": local_today(), "time": "19:00", "table_id": f"{t}-table",
        "table_label": "T01", "status": "booked", "created_at": now}))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two live hotels, A and B, each holding one of everything.

    B's money is 999 and A's is 50, so a total that has silently swallowed the other
    hotel is wrong by an amount no rounding could produce.
    """
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    a = make_property("a", "The Grand")
    b = make_property("b", "The Regent")
    stock_hotel(a, 50.0)
    stock_hotel(b, 999.0)
    return a, b


# ----------------------------- the handle itself -----------------------------
def test_a_scoped_handle_refuses_the_collections_that_stand_outside_tenancy(world):
    a, _b = world
    # Not "returns them unfiltered" — a router that believed `users` was scoped would be
    # reading every hotel's staff while looking exactly like code that was not.
    for name in ("users", "properties", "payment_transactions"):
        with pytest.raises(UnscopedCollectionError):
            getattr(a.db, name)


def test_a_collection_nobody_has_scoped_yet_is_scoped_rather_than_open(world):
    a, b = world
    run(a.db.housekeeping_tasks.insert_one({"id": "a-task"}))
    run(b.db.housekeeping_tasks.insert_one({"id": "b-task"}))
    assert [r["id"] for r in run(a.db.housekeeping_tasks.find({}).to_list(10))] == ["a-task"]


def test_an_insert_cannot_claim_another_hotel(world):
    a, b = world
    run(a.db.guests.insert_one({"id": "smuggled", "name": "X", "phone": "1",
                                "property_id": b.record["id"]}))
    assert run(b.db.guests.find_one({"id": "smuggled"})) is None
    assert run(a.db.guests.find_one({"id": "smuggled"}))["property_id"] == a.record["id"]


def test_a_query_cannot_ask_for_another_hotel(world):
    a, b = world
    # The property is forced, not defaulted. A filter that names B is answered about A
    # anyway, so the worst a mistake — or a property_id arriving in a request body — can
    # do is return the caller's own rows.
    answered = run(a.db.bookings.find_one({"property_id": b.record["id"]}))
    assert answered["id"] == "a-booking"
    assert answered["property_id"] == a.record["id"]


# --------------------------------- bookings ---------------------------------
def test_a_booking_list_holds_none_of_bs_bookings(world):
    a, b = world
    rows = call(bookings.list_bookings, user=a.admin, db=a.db)
    assert {r["id"] for r in rows} == {"a-booking"}
    assert "b-booking" not in str(rows)


def test_bs_booking_fetched_from_as_session_is_404_not_403(world):
    a, _b = world
    # 403 would confirm the record exists, which is the leak this test exists to stop.
    assert refused(bookings.get_booking, booking_id="b-booking",
                   user=a.admin, db=a.db).status_code == 404


def test_updating_bs_booking_from_as_session_is_404(world):
    a, b = world
    assert refused(bookings.update_booking, booking_id="b-booking",
                   payload=BookingUpdateIn(adults=4), user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.bookings.find_one({"id": "b-booking"}))["adults"] == 2


def test_cancelling_bs_booking_from_as_session_is_404(world):
    a, b = world
    assert refused(bookings.cancel_booking, booking_id="b-booking",
                   payload=CancelIn(reason="mine now"), user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.bookings.find_one({"id": "b-booking"}))["status"] == "confirmed"


def test_assigning_a_room_to_bs_booking_from_as_session_is_404(world):
    a, b = world
    assert refused(bookings.set_booking_room, booking_id="b-booking",
                   payload=RoomAssignmentIn(room_id="b-room"), user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.bookings.find_one({"id": "b-booking"})).get("assigned_room_id") is None


def test_bs_room_cannot_be_assigned_to_as_booking(world):
    a, _b = world
    # 404, not 409: the room is not this hotel's to be told anything about, and a 409
    # naming a clash would confirm both that B's room exists and who is in it.
    assert refused(bookings.set_booking_room, booking_id="a-booking",
                   payload=RoomAssignmentIn(room_id="b-room"), user=a.admin,
                   db=a.db).status_code == 404
    assert run(a.db.bookings.find_one({"id": "a-booking"})).get("assigned_room_id") is None


def test_the_other_hotels_booking_cannot_block_this_hotels_room(world):
    a, b = world
    # Both hotels seeded a room "101" and a booking over the same dates. Were the clash
    # query unscoped, B holding its own 101 would refuse A the use of A's — a hotel
    # losing rooms to a stranger's bookings, with a 409 naming a reference it has never
    # seen.
    run(b.db.bookings.update_one({"id": "b-booking"},
                                 {"$set": {"assigned_room_id": "b-room"}}))
    assigned = call(bookings.set_booking_room, booking_id="a-booking",
                    payload=RoomAssignmentIn(room_id="a-room"), user=a.admin, db=a.db)
    assert assigned["assigned_room_id"] == "a-room"
    assert assigned["room"]["number"] == "101"


def test_availability_counts_only_this_hotels_rooms(world):
    a, _b = world
    day = local_today()
    end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    rows = call(bookings.availability, check_in=day, check_out=end, adults=2, children=0,
                user=a.admin, db=a.db)
    assert [r["room_type"]["id"] for r in rows] == ["a-rt"]
    assert rows[0]["available"] == 1  # A's single room, not A's and B's


def test_a_new_booking_is_stamped_with_the_hotel_that_took_it(world):
    a, _b = world
    day = (date.fromisoformat(local_today()) + timedelta(days=10)).isoformat()
    out = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    made = call(bookings.create_booking, payload=BookingIn(
        guest_id="a-guest", room_type_id="a-rt", meal_plan_id="a-mp",
        check_in=day, check_out=out), user=a.admin, db=a.db)
    assert run(a.db.bookings.find_one({"id": made["id"]}))["property_id"] == "a-property"


def test_a_booking_cannot_be_made_against_bs_room_type(world):
    a, _b = world
    day = (date.fromisoformat(local_today()) + timedelta(days=10)).isoformat()
    out = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    assert refused(bookings.create_booking, payload=BookingIn(
        guest_id="a-guest", room_type_id="b-rt", meal_plan_id="a-mp",
        check_in=day, check_out=out), user=a.admin, db=a.db).status_code == 400


# ---------------------------------- guests ----------------------------------
def test_a_guest_list_holds_none_of_bs_guests(world):
    a, _b = world
    rows = call(guests.list_guests, q="", limit=50, user=a.admin, db=a.db)
    assert {g["id"] for g in rows} == {"a-guest"}


def test_searching_by_a_phone_both_hotels_use_finds_only_this_hotels_guest(world):
    a, _b = world
    rows = call(guests.list_guests, q="9990000001", limit=50, user=a.admin, db=a.db)
    assert {g["id"] for g in rows} == {"a-guest"}


def test_bs_guest_fetched_from_as_session_is_404(world):
    a, _b = world
    assert refused(guests.get_guest, guest_id="b-guest", user=a.admin,
                   db=a.db).status_code == 404


def test_updating_bs_guest_from_as_session_is_404(world):
    a, b = world
    assert refused(guests.update_guest, guest_id="b-guest",
                   payload=GuestIn(name="Renamed", phone="9990000077"),
                   user=a.admin, db=a.db).status_code == 404
    assert run(b.db.guests.find_one({"id": "b-guest"}))["name"] == "Guest B"


def test_two_hotels_can_hold_the_same_guest_phone(world):
    a, _b = world
    # The duplicate check is scoped, so B's guest does not block A's — the same person
    # eats in two hotels, and one of them registering them must not 409 the other.
    made = call(guests.create_guest, payload=GuestIn(name="New", phone="9990000042"),
                user=a.admin, db=a.db)
    assert made["property_id"] == "a-property"


# ----------------------------- rooms & room types -----------------------------
def test_room_and_room_type_lists_hold_none_of_bs(world):
    a, _b = world
    assert {r["id"] for r in call(rooms.list_rooms, user=a.admin, db=a.db)} == {"a-room"}
    assert {r["id"] for r in call(rooms.list_room_types, user=a.admin, db=a.db)} == {"a-rt"}


def test_updating_and_deleting_bs_room_from_as_session_are_404(world):
    a, b = world
    assert refused(rooms.update_room, room_id="b-room",
                   payload=RoomIn(number="909", room_type_id="a-rt"),
                   user=a.admin, db=a.db).status_code in (400, 404)
    assert refused(rooms.delete_room, room_id="b-room", user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.rooms.find_one({"id": "b-room"}))["number"] == "101"


def test_both_hotels_can_have_a_room_of_the_same_number(world):
    a, b = world
    # Both were seeded with a 101 already, which the scoped insert allowed. The clash
    # check on the create route is scoped for the same reason: B's 202 must not stop A
    # entering its own, and the unique index is (property_id, number) to match.
    run(b.db.rooms.insert_one({"id": "b-room-202", "number": "202",
                               "room_type_id": "b-rt", "active": True,
                               "out_of_order": []}))
    made = call(rooms.create_room, payload=RoomIn(number="202", room_type_id="a-rt"),
                user=a.admin, db=a.db)
    assert made["number"] == "202" and made["property_id"] == "a-property"
    # A's own duplicate is still refused — scoping narrows the check, it does not drop it.
    assert refused(rooms.create_room, payload=RoomIn(number="202", room_type_id="a-rt"),
                   user=a.admin, db=a.db).status_code == 409


def test_deleting_bs_room_type_from_as_session_is_404(world):
    a, b = world
    assert refused(rooms.delete_room_type, type_id="b-rt", user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.room_types.find_one({"id": "b-rt"})) is not None


# -------------------------------- rates & co --------------------------------
def test_rate_meal_plan_and_slab_lists_hold_none_of_bs(world):
    a, _b = world
    assert {r["id"] for r in call(rates.list_rates, user=a.admin, db=a.db)} == {"a-rate"}
    assert {r["id"] for r in call(rates.list_meal_plans, user=a.admin, db=a.db)} == {"a-mp"}
    assert {r["id"] for r in call(rates.list_tax_slabs, user=a.admin, db=a.db)} == {"a-slab"}
    assert {r["id"] for r in call(rates.list_rate_periods, user=a.admin,
                                  db=a.db)} == {"a-period"}


def test_replacing_the_tax_table_leaves_the_other_hotels_bands_alone(world):
    a, b = world
    call(rates.replace_tax_slabs,
         slabs=[TaxSlab(min_tariff=0.0, max_tariff=None, rate_percent=5.0)],
         user=a.admin, db=a.db)
    assert run(b.db.tax_slabs.find_one({"id": "b-slab"}))["rate_percent"] == 12.0


def test_deleting_bs_rate_from_as_session_leaves_it_standing(world):
    a, b = world
    call(rates.delete_rate, rate_id="b-rate", user=a.admin, db=a.db)
    assert run(b.db.rates.find_one({"id": "b-rate"})) is not None


def test_updating_bs_meal_plan_from_as_session_is_404(world):
    a, b = world
    assert refused(rates.update_meal_plan, plan_id="b-mp",
                   payload=MealPlanIn(code="XX", name="Hijacked"),
                   user=a.admin, db=a.db).status_code == 404
    assert run(b.db.meal_plans.find_one({"id": "b-mp"}))["name"] == "Room only"


# --------------------------------- folios ---------------------------------
def test_a_folio_list_holds_none_of_bs_folios(world):
    a, _b = world
    rows = call(folios.list_folios, status="", user=a.admin, db=a.db)
    assert {f["id"] for f in rows} == {"a-folio"}
    assert "b-guest" not in str(rows)


def test_bs_folio_fetched_from_as_session_is_404(world):
    a, _b = world
    assert refused(folios.get_folio, folio_id="b-folio", user=a.admin,
                   db=a.db).status_code == 404


def test_charging_and_paying_bs_folio_from_as_session_are_404(world):
    a, b = world
    assert refused(folios.add_charge, folio_id="b-folio",
                   payload=ChargeIn(amount=10.0, description="Not mine"),
                   user=a.admin, db=a.db).status_code == 404
    assert refused(folios.add_payment, folio_id="b-folio",
                   payload=PaymentIn(amount=10.0), user=a.admin,
                   db=a.db).status_code == 404
    assert len(run(b.db.folio_entries.find({"folio_id": "b-folio"}).to_list(50))) == 1


def test_voiding_an_entry_on_bs_folio_from_as_session_is_404(world):
    a, _b = world
    assert refused(folios.void_entry, folio_id="b-folio", entry_id="b-entry",
                   payload=VoidIn(reason="testing"), user=a.admin,
                   db=a.db).status_code == 404


# --------------------------------- front desk ---------------------------------
def test_the_front_desk_board_holds_none_of_bs_bookings(world):
    a, b = world
    for hotel in (a, b):
        run(hotel.db.bookings.update_one({"id": f"{hotel.tag}-booking"},
                                         {"$set": {"check_in": local_today()}}))
    board = call(frontdesk.front_desk, user=a.admin, db=a.db)
    assert {row["id"] for row in board["arrivals"]} == {"a-booking"}
    assert "b-booking" not in str(board)


def test_checking_in_bs_booking_from_as_session_is_404(world):
    a, _b = world
    from models.folio import CheckInIn
    assert refused(frontdesk.check_in, booking_id="b-booking",
                   payload=CheckInIn(room_id="b-room", id_proof_type="passport",
                                     id_proof_number="X1"),
                   user=a.admin, db=a.db).status_code == 404


def test_the_pos_in_house_search_holds_none_of_bs_guests(world):
    a, b = world
    for hotel in (a, b):
        run(hotel.db.bookings.update_one({"id": f"{hotel.tag}-booking"},
                                         {"$set": {"status": "checked_in"}}))
    rows = call(frontdesk.in_house, q="", user=a.admin, db=a.db)
    assert [r["booking"]["id"] for r in rows] == ["a-booking"]


# ---------------------------------- orders ----------------------------------
def test_bs_order_fetched_from_as_session_is_404(world):
    a, _b = world
    assert refused(orders.get_order, order_id="b-order", user=a.admin,
                   db=a.db).status_code == 404


def test_settling_and_stripping_bs_order_from_as_session_are_404(world):
    a, b = world
    run(b.db.orders.update_one({"id": "b-order"}, {"$set": {"status": "open"}}))
    assert refused(orders.settle_order, order_id="b-order",
                   payload=orders.SettleIn(), user=a.admin, db=a.db).status_code == 404
    assert refused(orders.remove_item, order_id="b-order", item_id="b-item",
                   user=a.admin, db=a.db).status_code == 404
    assert run(b.db.orders.find_one({"id": "b-order"}))["status"] == "open"


def test_the_kitchen_board_holds_none_of_bs_tickets(world):
    a, b = world
    for hotel in (a, b):
        run(hotel.db.orders.update_one({"id": f"{hotel.tag}-order"},
                                       {"$set": {"status": "open"}}))
    tickets = call(orders.list_kot, user=a.admin, db=a.db)
    assert [t["order_id"] for t in tickets] == ["a-order"]


def test_a_bar_bill_cannot_be_charged_to_the_other_hotels_room(world):
    a, _b = world
    run(a.db.orders.update_one({"id": "a-order"}, {"$set": {"status": "open"}}))
    assert refused(orders.settle_order, order_id="a-order",
                   payload=orders.SettleIn(payment_method="room", folio_id="b-folio"),
                   user=a.admin, db=a.db).status_code == 404


# ------------------------------ the QR self-order ------------------------------
def test_the_qr_link_orders_against_the_hotel_that_owns_the_table(world):
    _a, b = world
    order = call(orders.add_items, table_id="b-table",
                 payload=orders.AddItemsIn(
                     items=[orders.OrderItemIn(menu_item_id="b-menu", quantity=2)],
                     source="qr"))
    # Resolved from the table, because a guest holding a QR code has no token — and the
    # order lands in B, not in whichever hotel happened to be first.
    assert run(b.db.orders.find_one({"id": order["id"]}))["property_id"] == "b-property"
    assert order["items"][0]["name"] == "House Special B"


def test_the_qr_link_cannot_order_another_hotels_dish_onto_this_table(world):
    _a, b = world
    order = call(orders.add_items, table_id="b-table",
                 payload=orders.AddItemsIn(
                     items=[orders.OrderItemIn(menu_item_id="a-menu", quantity=1)],
                     source="qr"))
    assert order["items"] == []


def test_a_pending_hotels_qr_link_does_not_work(world, monkeypatch):
    _a, b = world
    run(db_module.unscoped_db.properties.update_one(
        {"id": "b-property"}, {"$set": {"status": PENDING}}))
    assert refused(orders.add_items, table_id="b-table",
                   payload=orders.AddItemsIn(items=[])).status_code == 404
    assert refused(orders.current_order, table_id="b-table").status_code == 404
    assert refused(tables.get_table_public, table_id="b-table").status_code == 404


def test_a_suspended_hotels_qr_link_does_not_work(world):
    _a, b = world
    run(db_module.unscoped_db.properties.update_one(
        {"id": "b-property"}, {"$set": {"status": SUSPENDED}}))
    assert refused(orders.add_items, table_id="b-table",
                   payload=orders.AddItemsIn(items=[])).status_code == 404


# ------------------------------ housekeeping ------------------------------
def test_the_housekeeping_board_holds_none_of_bs_rooms(world):
    a, _b = world
    board = call(housekeeping.housekeeping_board, user=a.admin, db=a.db)
    assert {r["id"] for r in board["rooms"]} == {"a-room"}
    assert "b-room" not in str(board)


def test_setting_the_status_of_bs_room_from_as_session_is_404(world):
    a, b = world
    assert refused(housekeeping.set_housekeeping_status, room_id="b-room",
                   payload=HousekeepingStatusIn(status="dirty"), user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.rooms.find_one({"id": "b-room"})).get("housekeeping_status") is None


def test_the_log_of_bs_room_is_not_readable_from_as_session(world):
    a, b = world
    call(housekeeping.set_housekeeping_status, room_id="b-room",
         payload=HousekeepingStatusIn(status="dirty"), user=b.admin, db=b.db)
    assert refused(housekeeping.housekeeping_events, room_id="b-room", user=a.admin,
                   db=a.db).status_code == 404
    assert len(call(housekeeping.housekeeping_events, room_id="b-room", user=b.admin,
                    db=b.db)) == 1


def test_a_request_list_holds_none_of_bs_requests(world):
    a, b = world
    made = call(housekeeping.raise_job, payload=housekeeping.HousekeepingJobIn(
        room_id="b-room", reason="Spill"), user=b.admin, db=b.db)
    assert call(housekeeping.list_jobs, status="", user=a.admin, db=a.db) == []
    assert refused(housekeeping.acknowledge_job, job_id=made["id"], user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.housekeeping_jobs.find_one({"id": made["id"]}))["status"] == "open"


def test_the_alert_holds_none_of_bs_requests(world):
    a, b = world
    # Both hotels have a room "101" and an outstanding request against it. A poll that
    # was not scoped would put the other hotel's guest's words on this hotel's screen —
    # four times a minute, on every screen anybody has open.
    call(housekeeping.guest_request, room_id="a-room",
         payload=GuestRequestIn(reason="A wants towels"))
    call(housekeeping.guest_request, room_id="b-room",
         payload=GuestRequestIn(reason="B wants towels"))

    seen = call(housekeeping.housekeeping_alerts, user=a.admin, db=a.db)
    assert seen["count"] == 1
    assert seen["jobs"][0]["reason"] == "A wants towels"
    assert "B wants towels" not in str(seen)
    assert call(housekeeping.housekeeping_alerts, user=b.admin,
                db=b.db)["jobs"][0]["reason"] == "B wants towels"


def test_a_request_cannot_be_raised_against_the_other_hotels_room(world):
    a, _b = world
    assert refused(housekeeping.raise_job, payload=housekeeping.HousekeepingJobIn(
        room_id="b-room"), user=a.admin, db=a.db).status_code == 404


# ------------------------------ the planner ------------------------------
# Both hotels plan the same week, with the same words: a "Meeting" category and a
# "Staff briefing" on the same day. A test that gave them different ones would pass
# against a missing filter whenever the other hotel happened to have planned nothing.
def _plan(hotel: Hotel, title: str, day: str) -> dict:
    run(seed_planner_categories(hotel.db))
    meeting = run(hotel.db.calendar_categories.find_one({"name": "Meeting"}, {"_id": 0}))
    return call(planner.create_event, payload=CalendarEventIn(
        title=title, date=day, start_time="16:00", category_id=meeting["id"]),
        user=hotel.admin, db=hotel.db)


def test_a_month_holds_none_of_the_other_hotels_events(world):
    a, b = world
    _plan(a, "Staff briefing", "2026-08-04")
    b_event = _plan(b, "Staff briefing", "2026-08-04")

    seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                user=a.admin, db=a.db)
    assert len(seen["events"]) == 1
    assert seen["events"][0]["id"] != b_event["id"]
    assert b_event["id"] not in {e["id"] for e in seen["events"]}


def test_the_other_hotels_event_cannot_be_edited_or_deleted(world):
    a, b = world
    b_event = _plan(b, "Fire drill", "2026-08-06")
    a_event = _plan(a, "Fire drill", "2026-08-06")
    meeting = run(a.db.calendar_categories.find_one({"name": "Meeting"}, {"_id": 0}))
    assert a_event["id"] != b_event["id"]
    # 404, not 403: a 403 would confirm that the event exists.
    assert refused(planner.update_event, event_id=b_event["id"],
                   payload=CalendarEventIn(title="Mine now", date="2026-08-06",
                                           category_id=meeting["id"]),
                   user=a.admin, db=a.db).status_code == 404
    assert refused(planner.delete_event, event_id=b_event["id"], user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.calendar_events.find_one({"id": b_event["id"]}))["title"] == "Fire drill"


def test_a_categorys_colour_is_the_hotels_own(world):
    a, b = world
    _plan(a, "Staff briefing", "2026-08-04")
    _plan(b, "Staff briefing", "2026-08-04")
    b_meeting = run(b.db.calendar_categories.find_one({"name": "Meeting"}, {"_id": 0}))

    seen = call(planner.list_categories, user=a.admin, db=a.db)
    assert b_meeting["id"] not in {c["id"] for c in seen["categories"]}
    # And an event cannot be filed under the other hotel's category — the scoped handle
    # does not find it, so it reads as "no such category" rather than as a leak.
    assert refused(planner.create_event, payload=CalendarEventIn(
        title="Borrowed", date="2026-08-07", category_id=b_meeting["id"]),
        user=a.admin, db=a.db).status_code == 400
    assert refused(planner.update_category, category_id=b_meeting["id"],
                   payload=planner.CalendarCategoryIn(name="Taken", colour="#ffffff"),
                   user=a.admin, db=a.db).status_code == 404


# ------------------------------ the in-room QR ------------------------------
def test_the_room_qr_raises_the_request_against_the_hotel_that_owns_the_room(world):
    a, b = world
    out = call(housekeeping.guest_request, room_id="b-room",
               payload=GuestRequestIn(reason="Fresh towels"))
    assert out["received"] is True and out["room_number"] == "101"
    # Resolved from the room, because a guest holding a printed card has no token — and
    # the request lands in B, not in whichever hotel happened to be first.
    raised = run(b.db.housekeeping_jobs.find_one({"reason": "Fresh towels"}))
    assert raised["property_id"] == "b-property"
    assert raised["source"] == "guest" and raised["raised_by"] is None
    assert run(a.db.housekeeping_jobs.find({}).to_list(10)) == []


def test_the_room_card_names_the_hotel_that_owns_the_room_and_nothing_else(world):
    _a, b = world
    run(b.db.rooms.update_one({"id": "b-room"}, {"$set": {
        "housekeeping_status": "dirty", "housekeeping_note": "Guest complained"}}))
    card = call(housekeeping.guest_room_card, room_id="b-room")
    assert card == {"property_name": "The Regent", "room_number": "101"}


def test_a_pending_hotels_room_qr_does_not_work(world):
    _a, b = world
    run(db_module.unscoped_db.properties.update_one(
        {"id": "b-property"}, {"$set": {"status": PENDING}}))
    assert refused(housekeeping.guest_request, room_id="b-room",
                   payload=GuestRequestIn()).status_code == 404
    assert refused(housekeeping.guest_room_card, room_id="b-room").status_code == 404


def test_a_suspended_hotels_room_qr_does_not_work(world):
    _a, b = world
    run(db_module.unscoped_db.properties.update_one(
        {"id": "b-property"}, {"$set": {"status": SUSPENDED}}))
    assert refused(housekeeping.guest_request, room_id="b-room",
                   payload=GuestRequestIn()).status_code == 404


def test_a_staff_token_from_one_hotel_cannot_reach_the_others_room_card(world):
    a, _b = world
    # The QR route's openness is for the guest in that room, not a way around the
    # caller's own scope. Same 404 an unknown room gets.
    with pytest.raises(HTTPException) as exc:
        run(scoped_db.db_for_room("b-room", caller=a.admin))
    assert exc.value.status_code == 404
    # ...and A's own room still resolves, to A.
    scoped, room = run(scoped_db.db_for_room("a-room", caller=a.admin))
    assert scoped.property_id == "a-property" and room["number"] == "101"


# ----------------------------- tables & reservations -----------------------------
def test_the_floor_plan_holds_none_of_bs_tables(world):
    a, _b = world
    assert {t["id"] for t in call(tables.list_tables, user=a.admin,
                                  db=a.db)} == {"a-table"}


def test_deleting_bs_table_from_as_session_leaves_it_standing(world):
    a, b = world
    call(tables.delete_table, table_id="b-table", user=a.admin, db=a.db)
    assert run(b.db.tables.find_one({"id": "b-table"})) is not None


def test_the_reservation_book_holds_none_of_bs_reservations(world):
    a, b = world
    rows = call(tables.list_reservations, date=None, user=a.admin, db=a.db)
    assert {r["id"] for r in rows} == {"a-res"}
    assert refused(tables.set_reservation_status, reservation_id="b-res",
                   payload=tables.StatusIn(status="cancelled"), user=a.admin,
                   db=a.db).status_code == 404
    assert run(b.db.reservations.find_one({"id": "b-res"}))["status"] == "booked"


# ----------------------------------- menu -----------------------------------
def test_the_menu_holds_none_of_bs_dishes(world):
    a, _b = world
    items = call(menu.list_menu, db=a.db)
    assert {m["id"] for m in items} == {"a-menu"}


def test_the_qr_menu_shows_the_menu_of_the_hotel_whose_table_was_scanned(world):
    _a, b = world
    scoped = run(scoped_db.public_db(request=None, table_id="b-table"))
    assert {m["id"] for m in call(menu.list_menu, db=scoped)} == {"b-menu"}


def test_editing_and_deleting_bs_dish_from_as_session_change_nothing(world):
    a, b = world
    call(menu.update_menu_item, item_id="b-menu",
         payload=menu.MenuItemIn(name="Hijacked", category="X", price=1.0),
         user=a.admin, db=a.db)
    call(menu.delete_menu_item, item_id="b-menu", user=a.admin, db=a.db)
    survivor = run(b.db.menu.find_one({"id": "b-menu"}))
    assert survivor is not None and survivor["name"] == "House Special B"


# --------------------------------- inventory ---------------------------------
def test_the_store_room_holds_none_of_bs_stock(world):
    a, _b = world
    assert {i["id"] for i in call(inventory.list_inventory, user=a.admin,
                                  db=a.db)} == {"a-inv"}


def test_a_stock_upload_matches_against_its_own_hotels_shelves_only(world):
    a, b = world
    # Both hotels hold an item called "Gin 750ml" — the same product, two store rooms.
    # A's upload of that name must read as *A's* Gin, and applying it must leave B's
    # stock at the 1.0 it was seeded with. Were the match unscoped, one hotel's opening
    # stock take would silently overwrite another's.
    import io

    from fastapi import UploadFile
    csv = b"name,unit,stock\nGin 750ml,bottle,42\n"
    report = call(inventory.preview_inventory_import,
                  file=UploadFile(file=io.BytesIO(csv), filename="stock.csv",
                                  size=len(csv)),
                  user=a.admin, db=a.db)
    assert report["rows"][0]["item_id"] == "a-inv"
    assert report["rows"][0]["existing"]["stock"] == 1.0

    call(inventory.apply_inventory_import,
         payload=inventory.InventoryImportApplyIn(rows=[
             inventory.InventoryImportRowIn(row=2, name="Gin 750ml", unit="bottle",
                                            stock=42, action="update",
                                            item_id="a-inv")]),
         user=a.admin, db=a.db)
    assert run(a.db.inventory.find_one({"id": "a-inv"}))["stock"] == 42.0
    assert run(b.db.inventory.find_one({"id": "b-inv"}))["stock"] == 1.0


def test_an_upload_cannot_update_the_other_hotels_stock_by_naming_its_id(world):
    a, b = world
    # The item id arrives from a browser, so it is client input. A row pointing at B's
    # item is refused — the whole apply is — and nothing is written to either hotel.
    refusal = refused(inventory.apply_inventory_import,
                      payload=inventory.InventoryImportApplyIn(rows=[
                          inventory.InventoryImportRowIn(
                              row=2, name="Gin 750ml", unit="bottle", stock=99,
                              action="update", item_id="b-inv")]),
                      user=a.admin, db=a.db)
    assert refusal.status_code == 400
    assert run(b.db.inventory.find_one({"id": "b-inv"}))["stock"] == 1.0
    assert run(a.db.inventory.find_one({"id": "a-inv"}))["stock"] == 1.0


def test_adjusting_and_deleting_bs_stock_from_as_session_change_nothing(world):
    a, b = world
    assert refused(inventory.adjust_inventory, item_id="b-inv",
                   payload=inventory.InventoryAdjustIn(delta=-1),
                   user=a.admin, db=a.db).status_code == 404
    call(inventory.delete_inventory, item_id="b-inv", user=a.admin, db=a.db)
    assert run(b.db.inventory.find_one({"id": "b-inv"}))["stock"] == 1.0


# --------------------------------- the money ---------------------------------
def test_revenue_analytics_exclude_the_other_hotels_orders_and_folio_entries(world):
    a, _b = world
    day = local_today()
    report = call(analytics.revenue, start=day, end=day, domains=None,
                  user=a.admin, db=a.db)
    # A's own: one 50 folio charge and one 50 settled order. B's 999s are the tell —
    # any of them present would move a total that cannot otherwise reach that number.
    assert report["hotel"]["total"] == 50.0
    assert report["outlets"]["total"] == 50.0
    assert report["outlets"]["orders"] == 1
    assert report["total"] == 100.0
    assert "999" not in str(report)


def test_the_outlet_report_excludes_the_other_hotels_takings(world):
    a, _b = world
    summary = call(reports.report_summary, user=a.admin, db=a.db)
    assert summary["revenue_total"] == 50.0
    assert summary["orders_total"] == 1
    assert summary["tables_total"] == 1
    assert summary["low_stock_count"] == 1
    assert [i["name"] for i in summary["top_items"]] == ["House Special"]


def test_recent_orders_hold_none_of_bs_bills(world):
    a, _b = world
    rows = call(reports.recent_orders, user=a.admin, db=a.db)
    assert {o["id"] for o in rows} == {"a-order"}


def test_the_daily_brief_is_one_hotels_day(world):
    a, _b = world
    brief = run(reports.build_daily_brief(a.db, local_today()))
    assert brief["revenue"] == 50.0 and brief["bills"] == 1
    assert "999" not in brief["message"]


# ---------------------------------- staff ----------------------------------
def test_the_staff_roster_holds_none_of_bs_people(world):
    a, _b = world
    # `users` stands outside tenancy so that a login can be found by email before we
    # know its hotel — which makes the roster the one query that has to say so in the
    # open. It is still the hotel's roster, not the platform's.
    assert {u["id"] for u in call(staff.list_staff, user=a.admin)} == {"a-admin"}


def test_editing_bs_staff_from_as_session_is_404(world):
    a, _b = world
    assert refused(staff.update_staff, staff_id="b-admin",
                   payload=staff.StaffUpdateIn(name="Hijacked", role="waiter",
                                               domains=["bar"]),
                   user=a.admin).status_code == 404
    assert refused(staff.set_active, staff_id="b-admin",
                   payload=staff.ActiveIn(active=False), user=a.admin).status_code == 404
    assert refused(staff.reset_password, staff_id="b-admin",
                   payload=staff.PasswordIn(password="hijacked1"),
                   user=a.admin).status_code == 404


def test_a_new_staff_member_joins_the_hotel_that_hired_them(world):
    a, _b = world
    made = call(staff.create_staff, payload=staff.StaffIn(
        name="Nina", email="nina@grand.example.com", password="desk12345",
        role="front_desk",
        domains=["hotel"]), user=a.admin)
    row = run(db_module.unscoped_db.users.find_one({"id": made["id"]}, {"_id": 0}))
    assert row["property_id"] == "a-property"


def test_the_last_admin_rule_counts_only_this_hotels_admins(world):
    a, b = world
    # B has an admin too. If the count were global, A could demote its own sole admin
    # and be left with nobody who can administer it.
    call(staff.create_staff, payload=staff.StaffIn(
        name="Second", email="second@grand.example.com", password="admin12345",
        role="admin",
        domains=["hotel"]), user=a.admin)
    second = run(db_module.unscoped_db.users.find_one(
        {"email": "second@grand.example.com"}, {"_id": 0}))
    refusal = refused(staff.set_active, staff_id=second["id"],
                      payload=staff.ActiveIn(active=False), user=b.admin)
    assert refusal.status_code == 404  # not B's to deactivate


# ------------------------- nothing is left unscoped -------------------------
# Every route that reaches hotel data takes the bound handle. These are the ones that do
# not, each for a reason that has to be stated rather than discovered:
SCOPE_FREE = {
    ("GET", "/api/"): "the health check reads nothing",
    ("POST", "/api/auth/login"): "finds a login by its identifier before its hotel is known",
    ("GET", "/api/auth/me"): "the caller's own user record",
    # Same reasoning as /auth/me, and it writes rather than reads: the row is found by
    # the id in the caller's own token, so there is no property to scope to and no id in
    # the request that could name another tenant's user. It is also the one route the
    # platform operator needs that touches `users`, and they belong to no property.
    ("POST", "/api/auth/password"): "the caller's own user record, found by their token",
    ("GET", "/api/permissions"): "the screen catalogue is a constant in code",
    ("GET", "/api/property"): "the caller's own property, resolved from their token",
    ("PUT", "/api/property"): "the caller's own property, resolved from their token",
    ("GET", "/api/staff"): "users stand outside tenancy; filtered explicitly",
    # These two also read the caller's own property record, to bound the domains they may
    # grant by the ones the property has — resolved from the token like GET /api/property
    # above, never from the request, so there is no id here to point at another tenant.
    ("POST", "/api/staff"): "users stand outside tenancy; stamped explicitly",
    ("PUT", "/api/staff/{staff_id}"): "users stand outside tenancy; filtered explicitly",
    ("POST", "/api/staff/{staff_id}/active"): "users; filtered explicitly",
    ("POST", "/api/staff/{staff_id}/password"): "users; filtered explicitly",
    ("POST", "/api/orders/table/{table_id}/items"): "QR: scope comes from the table",
    ("GET", "/api/orders/table/{table_id}/current"): "QR: scope comes from the table",
    ("GET", "/api/tables/public/{table_id}"): "QR: scope comes from the table",
    # The card printed and placed in each guest room. Listed for exactly the reason the
    # three table routes above are, and by the same mechanism: they take no token — a
    # guest scans and asks for towels without an account — so they resolve the property
    # from the room in the URL through `scoped_db.db_for_room`, which is a scoped handle
    # bound from a record rather than from the caller. The guard below recognises two
    # dependency functions by identity, and these call theirs inside the handler instead,
    # like `orders.add_items` does. Nothing here reads a collection unscoped: everything
    # after `db_for_room` goes through the handle it returns.
    ("GET", "/api/housekeeping/room/{room_id}/public"): "QR: scope comes from the room",
    ("POST", "/api/housekeeping/room/{room_id}/requests"): "QR: scope comes from the room",
    ("POST", "/api/payments/checkout/session"): "scope comes from the order",
    ("GET", "/api/payments/checkout/status/{session_id}"): "scope comes from the order",
    ("POST", "/api/webhook/stripe"): "Stripe delivers this; scope comes from the order",
    # Signup is what CREATES a tenant, so there is none to bind yet.
    ("POST", "/api/signup"): "creates the property; tenancy has not begun",
    # Environment variables and Meta's API. No collection is read either way, so there
    # is nothing here a property could scope.
    # The operator's money routes. `properties` and `subscription_payments` both stand
    # outside tenancy — what a business pays the platform is not the business's own data,
    # and the operator belongs to no property to be scoped to.
    ("PUT", "/api/platform/properties/{property_id}/subscription"): "the operator's price",
    ("POST", "/api/platform/properties/{property_id}/payments"): "the platform's ledger",
    ("GET", "/api/platform/properties/{property_id}/payments"): "the platform's ledger",
    ("POST", "/api/platform/properties/{property_id}/type"): "properties, and every user of one",
    # The platform's own registration, and the tax documents issued under it. Both
    # collections stand outside tenancy for the reason `subscription_payments` does — an
    # invoice from the platform to a hotel is the platform's record of its own supply,
    # not the hotel's data — and the operator who writes them belongs to no property to
    # be scoped to. `properties` is read here only to name the customer on the document.
    ("GET", "/api/platform/settings"): "the platform's own registration, not a tenant's",
    ("PUT", "/api/platform/settings"): "the platform's own registration, not a tenant's",
    ("POST", "/api/platform/properties/{property_id}/payments/{payment_id}/invoice"):
        "the platform's own tax document; properties is read to name the customer",
    ("GET", "/api/platform/properties/{property_id}/invoices"): "the platform's own series",
    ("GET", "/api/platform/invoices/{invoice_id}"): "the platform's own series",
    ("POST", "/api/platform/invoices/{invoice_id}/credit-note"): "the platform's own series",
    # The blank stock spreadsheet an owner downloads to fill in. It is a constant in
    # code — the same six column headings and one example row for every property — so
    # there is no collection to read and nothing a tenant could scope. The two endpoints
    # that *do* read stock, /inventory/import/preview and /inventory/import/apply, both
    # take the bound handle and are not listed here.
    ("GET", "/api/inventory/import/template"): "the blank template is a constant in code",
    ("GET", "/api/whatsapp/status"): "reads configuration, not data",
    ("POST", "/api/whatsapp/test"): "sends one message; touches no collection",
    # The operator works across tenants by definition. These read the properties
    # collection, which stands outside tenancy, and counts through a handle bound to the
    # property being inspected — never a guest, booking or folio record.
    ("GET", "/api/platform/properties"): "the operator's cross-tenant list",
    ("GET", "/api/platform/properties/{property_id}"): "counts only, bound per property",
    ("POST", "/api/platform/properties/{property_id}/status"): "the properties collection",
}


def test_every_route_either_binds_a_property_or_says_why_not():
    """The guard that outlives this file.

    Every assertion above tests a route that exists today. This one fails when somebody
    adds a route tomorrow that reads hotel data from an unscoped handle — the failure
    this whole design exists to make impossible to commit quietly.
    """
    import inspect
    from fastapi.params import Depends as DependsMarker
    from fastapi.routing import APIRoute
    from server import app

    bound = {scoped_db.tenant_db, scoped_db.public_db}
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        takes_scope = any(
            isinstance(p.default, DependsMarker) and p.default.dependency in bound
            for p in inspect.signature(route.endpoint).parameters.values())
        for method in route.methods:
            if takes_scope or (method, route.path) in SCOPE_FREE:
                continue
            missing.append(f"{method} {route.path}")
    assert not missing, (
        "these routes reach the database without a property-scoped handle, and are not "
        f"listed as deliberate exceptions: {sorted(missing)}")
