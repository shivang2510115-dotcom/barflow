"""One all-inclusive rate, or the three-plan split — as a property setting.

The owner of a small hotel sells a room at one price and bills anything extra as it is
consumed. A resort selling a breakfast-inclusive package needs EP/CP/MAP. Both are right
for their own hotel, so this is `meal_plans_enabled` on the property and not a deletion.

Four claims, and the whole design rests on them:

* **off is the default for a new property**, and with it off a booking is taken without
  the caller supplying a plan, quoted at one price per room type, and the Rates screen is
  not asked for per-plan pricing;
* **on is exactly today's behaviour**, unchanged — a plan is required, and the quote
  carries its per-person supplement;
* **a booking that cannot be priced is refused with a reason.** Plan-less is not the same
  as free: the room rate still has to exist, and a night no rate covers is a 422 naming
  the night, never a silent zero;
* **an existing booking is never repriced.** It keeps the plan it was taken on and the
  quote the guest was given, whichever way the setting is moved afterwards.

No server: the endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, the same style as test_isolation.py and test_room_assignment.py.
"""
import asyncio
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.bookings as bookings
import routers.property as property_router
from migrations import backfill_meal_plans
from mock_db import MockDatabase
from models.hotel import BookingIn
from models.property import Property, PropertyFields
from scoped_db import PropertyScopedDatabase
from services.access import DOMAINS, LIVE, SCREEN_KEYS
from services.pricing import DEFAULT_MEAL_PLANS_ENABLED, meal_plans_enabled

# Every module that holds the unscoped handle itself and is reached from here. The
# migration is one of them — `from db import unscoped_db` binds what existed at import,
# so a test that missed it would run the migration against the developer's own database.
_UNSCOPED_HOLDERS = (db_module, property_router, security, backfill_meal_plans)

STAY_IN, STAY_OUT = "2029-11-04", "2029-11-06"   # two nights


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


@dataclass
class Tenant:
    record: dict
    admin: dict
    manager: dict
    db: PropertyScopedDatabase


def _seed(handle, monkeypatch, *, plans_on: bool) -> Tenant:
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    record = Property(id="p1", name="The Grand", status=LIVE).model_dump()
    record["id"] = "p1"
    record["meal_plans_enabled"] = plans_on
    run(handle.properties.insert_one(record))

    def staff(uid, role):
        return {"id": uid, "email": f"{uid}@grand.example.com", "name": role.title(),
                "role": role, "domains": list(DOMAINS), "permissions": list(SCREEN_KEYS),
                "active": True, "property_id": "p1"}

    admin, manager = staff("u-admin", "admin"), staff("u-manager", "manager")
    run(handle.users.insert_one(admin))
    run(handle.users.insert_one(manager))

    db = PropertyScopedDatabase("p1")
    run(db.room_types.insert_one({
        "id": "rt-dlx", "name": "Deluxe", "code": "DLX", "base_occupancy": 2,
        "max_occupancy": 3, "max_extra_beds": 1, "amenities": [], "images": [],
        "active": True}))
    run(db.rooms.insert_one({
        "id": "room-101", "number": "101", "room_type_id": "rt-dlx", "floor": "1",
        "active": True, "out_of_order": []}))
    run(db.guests.insert_one({
        "id": "g1", "name": "Guest", "phone": "9990000001",
        "created_at": "2029-01-01T00:00:00+00:00"}))
    # ₹4,000 a night flat, and the statutory 12% band that covers it.
    run(db.rates.insert_one({
        "id": "rate-dlx", "room_type_id": "rt-dlx", "period_id": None,
        "base_rate": 4000.0, "extra_adult_rate": 0.0, "extra_child_rate": 0.0}))
    run(db.tax_slabs.insert_one({
        "id": "slab", "min_tariff": 0.0, "max_tariff": 7500.0, "rate_percent": 12.0,
        "active": True}))
    # Both plans exist in both tenants. The setting, not the absence of rows, is what
    # decides whether they are quoted — a hotel that switches plans off keeps its plan
    # records, and switching back on must restore exactly what it had.
    run(db.meal_plans.insert_one({
        "id": "mp-ep", "code": "EP", "name": "Room only",
        "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0,
        "active": True}))
    run(db.meal_plans.insert_one({
        "id": "mp-cp", "code": "CP", "name": "With breakfast",
        "price_per_adult_per_night": 500.0, "price_per_child_per_night": 250.0,
        "active": True}))
    return Tenant(record=record, admin=admin, manager=manager, db=db)


@pytest.fixture
def plans_off(tmp_path, monkeypatch):
    return _seed(MockDatabase(str(tmp_path / "db.json")), monkeypatch, plans_on=False)


@pytest.fixture
def plans_on(tmp_path, monkeypatch):
    return _seed(MockDatabase(str(tmp_path / "db.json")), monkeypatch, plans_on=True)


def stored_booking(tenant, booking_id) -> dict:
    return run(tenant.db.bookings.find_one({"id": booking_id}, {"_id": 0}))


# ------------------------------- the default -------------------------------
def test_a_new_property_has_meal_plans_off():
    """The simpler model is what a hotel gets without being asked."""
    assert DEFAULT_MEAL_PLANS_ENABLED is False
    assert Property(name="New Hotel").meal_plans_enabled is False


def test_an_unstamped_record_reads_as_off():
    """A record with no key at all — the shape a migration has not reached yet."""
    assert meal_plans_enabled({}) is False
    assert meal_plans_enabled(None) is False
    assert meal_plans_enabled({"meal_plans_enabled": True}) is True


# --------------------------- with plans switched off ---------------------------
def test_a_booking_is_taken_without_a_plan(plans_off):
    made = call(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", check_in=STAY_IN, check_out=STAY_OUT),
        user=plans_off.admin, db=plans_off.db)

    assert made["meal_plan_id"] is None
    # Two nights at ₹4,000, 12% GST. One price, and nothing added per head.
    assert made["quote"]["room_subtotal"] == 8000.0
    assert made["quote"]["tax_total"] == 960.0
    assert made["quote"]["total"] == 8960.0
    assert [n["tariff"] for n in made["quote"]["nights"]] == [4000.0, 4000.0]


def test_availability_offers_one_price_per_room_type(plans_off):
    rows = call(bookings.availability, check_in=STAY_IN, check_out=STAY_OUT,
                adults=2, children=0, user=plans_off.admin, db=plans_off.db)

    assert len(rows) == 1
    quotes = rows[0]["quotes"]
    assert len(quotes) == 1, "one all-inclusive price, not one per plan"
    assert quotes[0]["meal_plan"] is None
    assert quotes[0]["total"] == 8960.0


def test_a_plan_sent_anyway_is_not_charged_for(plans_off):
    """The property does not sell plans, so a stale client sending one must not make the
    guest pay for a breakfast this hotel does not price separately."""
    made = call(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", meal_plan_id="mp-cp",
        check_in=STAY_IN, check_out=STAY_OUT), user=plans_off.admin, db=plans_off.db)

    assert made["meal_plan_id"] is None
    assert made["quote"]["total"] == 8960.0  # not 8960 + 2 nights x 2 adults x ₹500


def test_a_night_no_rate_covers_is_refused_not_priced_at_zero(plans_off):
    """Plan-less is not free. The room rate still has to exist."""
    run(plans_off.db.rates.delete_many({}))
    exc = refused(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", check_in=STAY_IN, check_out=STAY_OUT),
        user=plans_off.admin, db=plans_off.db)

    assert exc.status_code == 422
    assert exc.detail["dates"] == [STAY_IN, "2029-11-05"]
    assert run(plans_off.db.bookings.count_documents({})) == 0


def test_availability_reports_unpriced_dates_rather_than_a_free_room(plans_off):
    run(plans_off.db.rates.delete_many({}))
    rows = call(bookings.availability, check_in=STAY_IN, check_out=STAY_OUT,
                adults=2, children=0, user=plans_off.admin, db=plans_off.db)

    assert rows[0]["quotes"] == []
    assert rows[0]["unpriced_dates"] == [STAY_IN, "2029-11-05"]


# ---------------------------- with plans switched on ----------------------------
def test_plans_on_is_exactly_todays_behaviour(plans_on):
    rows = call(bookings.availability, check_in=STAY_IN, check_out=STAY_OUT,
                adults=2, children=0, user=plans_on.admin, db=plans_on.db)

    quotes = {q["meal_plan"]["code"]: q for q in rows[0]["quotes"]}
    assert set(quotes) == {"EP", "CP"}
    assert quotes["EP"]["total"] == 8960.0
    # ₹500 per adult per night on top: (4000 + 1000) x 2 nights, plus 12%.
    assert quotes["CP"]["room_subtotal"] == 10000.0
    assert quotes["CP"]["total"] == 11200.0


def test_a_booking_on_a_plan_carries_the_plans_price(plans_on):
    made = call(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", meal_plan_id="mp-cp",
        check_in=STAY_IN, check_out=STAY_OUT), user=plans_on.admin, db=plans_on.db)

    assert made["meal_plan_id"] == "mp-cp"
    assert made["quote"]["total"] == 11200.0


def test_with_plans_on_a_booking_without_one_is_refused(plans_on):
    """The on-path is not weakened by the field becoming optional: this used to be a
    Pydantic 422 and is now a 400 naming the field, but it is still refused."""
    exc = refused(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", check_in=STAY_IN, check_out=STAY_OUT),
        user=plans_on.admin, db=plans_on.db)

    assert exc.status_code == 400
    assert "meal_plan_id" in str(exc.detail)
    assert run(plans_on.db.bookings.count_documents({})) == 0


def test_with_plans_on_an_unknown_plan_is_still_refused(plans_on):
    exc = refused(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", meal_plan_id="nope",
        check_in=STAY_IN, check_out=STAY_OUT), user=plans_on.admin, db=plans_on.db)
    assert exc.status_code == 400


# --------------------------- moving the setting ---------------------------
def test_switching_plans_off_does_not_reprice_an_existing_booking(plans_on):
    """The guest was quoted a number. Whatever the hotel does to its own pricing model
    afterwards, that booking keeps its plan and its stored quote."""
    made = call(bookings.create_booking, payload=BookingIn(
        guest_id="g1", room_type_id="rt-dlx", meal_plan_id="mp-cp",
        check_in=STAY_IN, check_out=STAY_OUT), user=plans_on.admin, db=plans_on.db)

    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"meal_plans_enabled": False}}))

    after = stored_booking(plans_on, made["id"])
    assert after["meal_plan_id"] == "mp-cp"
    assert after["quote"] == made["quote"]
    assert after["quote"]["total"] == 11200.0


def test_the_admin_moves_the_setting_from_property_settings(plans_off):
    body = PropertyFields(**{k: v for k, v in plans_off.record.items()
                             if k in PropertyFields.model_fields})
    body.meal_plans_enabled = True

    saved = call(property_router.update_property, payload=body, user=plans_off.admin)
    assert saved["meal_plans_enabled"] is True
    assert run(db_module.unscoped_db.properties.find_one(
        {"id": "p1"}, {"_id": 0}))["meal_plans_enabled"] is True


def test_a_body_that_does_not_mention_the_setting_leaves_it_alone(plans_on):
    """`PUT /property` replaces the editable half wholesale, and this field was added
    after every settings form and script that calls it. A body written before it existed
    must not switch a hotel's pricing model off as a side effect of fixing a postcode."""
    body = PropertyFields(name="The Grand", city="Panaji")
    assert body.meal_plans_enabled is None

    saved = call(property_router.update_property, payload=body, user=plans_on.admin)
    assert saved["city"] == "Panaji"
    assert saved["meal_plans_enabled"] is True
    assert run(db_module.unscoped_db.properties.find_one(
        {"id": "p1"}, {"_id": 0}))["meal_plans_enabled"] is True


def test_saying_off_out_loud_still_switches_it_off(plans_on):
    body = PropertyFields(name="The Grand", meal_plans_enabled=False)
    saved = call(property_router.update_property, payload=body, user=plans_on.admin)
    assert saved["meal_plans_enabled"] is False


def test_a_manager_cannot_move_the_setting(plans_off):
    """It sits beside the GST settings and carries the same gate: the owner's, not the
    duty manager's."""
    exc = refused(property_router.WRITE, user=plans_off.manager)
    assert exc.status_code == 403


# ------------------------------ the migration ------------------------------
def test_a_record_that_predates_the_field_keeps_todays_behaviour(plans_off):
    """It has been quoting EP, CP and MAP all along. Stamping the new default onto it
    would drop the breakfast supplement out of every quote the morning this deploys."""
    legacy = Property(id="p-legacy", name="The Old Inn", status=LIVE).model_dump()
    legacy["id"] = "p-legacy"
    legacy.pop("meal_plans_enabled")
    run(db_module.unscoped_db.properties.insert_one(legacy))

    updated, current = run(backfill_meal_plans.backfill())
    assert (updated, current) == (1, 1)
    assert run(db_module.unscoped_db.properties.find_one(
        {"id": "p-legacy"}, {"_id": 0}))["meal_plans_enabled"] is True
    # …and the hotel next door that had already answered the question is untouched.
    assert run(db_module.unscoped_db.properties.find_one(
        {"id": "p1"}, {"_id": 0}))["meal_plans_enabled"] is False


def test_the_migration_leaves_a_deliberate_false_alone(plans_off):
    """Key presence, not truthiness — the trap backfill_domains.py documents. A hotel
    that has deliberately switched plans off must not be switched back on every restart.
    """
    updated, current = run(backfill_meal_plans.backfill())
    assert (updated, current) == (0, 1)
    assert run(db_module.unscoped_db.properties.find_one(
        {"id": "p1"}, {"_id": 0}))["meal_plans_enabled"] is False


def test_the_migration_is_idempotent(plans_on):
    first = run(backfill_meal_plans.backfill())
    second = run(backfill_meal_plans.backfill())
    assert first == (0, 1) and second == (0, 1)
