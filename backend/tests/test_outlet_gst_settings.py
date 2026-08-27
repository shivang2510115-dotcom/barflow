"""The outlet's GST rate as a setting: who may change it, and what happens to bills.

Three claims, each of which the design rests on:

* the rate is the *hotel's*, set by its own admin through `PUT /api/property`, and a
  waiter cannot reach it — the person who knows what the registration says is the owner;
* an existing property gets 5% exclusive from the startup migration, idempotently, and a
  business that deliberately charges 0% is not quietly re-registered at 5%;
* **a settled bill is never recomputed.** The guest paid what the printed bill said.

No server: the endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, the same style as test_isolation.py and test_subscription_api.py.
"""
import asyncio
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.orders as orders
import routers.property as property_router
from migrations import backfill_outlet_gst
from mock_db import MockDatabase
from models.property import Property, PropertyFields
from scoped_db import PropertyScopedDatabase
from services.access import DOMAINS, LIVE, SCREEN_KEYS
from services.tax import DEFAULT_OUTLET_GST_RATE


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


# Every module that holds the unscoped handle itself and is reached from here. The
# migration is one of them — `from db import unscoped_db` binds what existed at import,
# so a test that missed it would run the migration against the developer's own database
# and pass without touching the one under test.
_UNSCOPED_HOLDERS = (db_module, property_router, security, backfill_outlet_gst)


@dataclass
class Tenant:
    record: dict
    admin: dict
    waiter: dict
    db: PropertyScopedDatabase


@pytest.fixture
def hotel(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)
    monkeypatch.setattr(orders, "_db_module", db_module)

    record = Property(id="p1", name="The Grand", status=LIVE).model_dump()
    record["id"] = "p1"
    run(handle.properties.insert_one(record))
    admin = {"id": "u-admin", "email": "admin@grand.example.com", "name": "Owner",
             "role": "admin", "domains": list(DOMAINS),
             "permissions": list(SCREEN_KEYS), "active": True, "property_id": "p1"}
    waiter = {"id": "u-waiter", "email": "waiter@grand.example.com", "name": "Riley",
              "role": "waiter", "domains": list(DOMAINS),
              "permissions": list(SCREEN_KEYS), "active": True, "property_id": "p1"}
    run(handle.users.insert_one(admin))
    run(handle.users.insert_one(waiter))
    return Tenant(record=record, admin=admin, waiter=waiter,
                  db=PropertyScopedDatabase("p1"))


def stored() -> dict:
    return run(db_module.unscoped_db.properties.find_one({"id": "p1"}, {"_id": 0}))


def check(dependency, user):
    """Run an authorization dependency as the request would — test_tenancy.py's helper."""
    return run(dependency(user))


# --------------------------- the record and its default ---------------------------
def test_a_new_property_is_five_percent_exclusive():
    p = Property(name="The Grand")
    assert p.outlet_gst_rate == DEFAULT_OUTLET_GST_RATE == 5.0
    assert p.gst_inclusive is False


# ------------------------------ who may change it ---------------------------------
def test_the_hotels_own_admin_sets_the_rate(hotel):
    out = call(property_router.update_property,
               payload=PropertyFields(name="The Grand", outlet_gst_rate=18.0,
                                      gst_inclusive=True),
               user=hotel.admin)
    assert out["outlet_gst_rate"] == 18.0
    assert out["gst_inclusive"] is True
    assert stored()["outlet_gst_rate"] == 18.0


def test_a_waiter_cannot_change_the_rate(hotel):
    """The route names "admin", and the role check runs before the domain bypass. A
    waiter who could set this would be changing what every guest is charged."""
    with pytest.raises(HTTPException) as exc:
        check(property_router.WRITE, hotel.waiter)
    assert exc.value.status_code == 403


def test_the_hotels_own_admin_may_read_it_and_so_may_a_waiter(hotel):
    """Reading is everybody's — the rate is printed on the bill the waiter hands over."""
    assert check(property_router.READ, hotel.waiter)["id"] == "u-waiter"
    assert call(property_router.get_property, user=hotel.admin)["outlet_gst_rate"] == 5.0


def test_a_rate_outside_the_schedule_is_refused_by_name(hotel):
    refusal = refused(property_router.update_property,
                      payload=PropertyFields(name="The Grand", outlet_gst_rate=45.0),
                      user=hotel.admin)
    assert refusal.status_code == 400
    assert "outlet_gst_rate" in refusal.detail
    # And nothing was written: a refused save must not leave half of it behind.
    assert stored()["outlet_gst_rate"] == 5.0


def test_a_negative_rate_is_refused(hotel):
    assert refused(property_router.update_property,
                   payload=PropertyFields(name="The Grand", outlet_gst_rate=-5.0),
                   user=hotel.admin).status_code == 400


def test_zero_is_a_rate_an_unregistered_business_may_set(hotel):
    out = call(property_router.update_property,
               payload=PropertyFields(name="The Grand", outlet_gst_rate=0.0),
               user=hotel.admin)
    assert out["outlet_gst_rate"] == 0.0


# --------------------------------- the migration ----------------------------------
def test_the_migration_stamps_a_property_that_predates_the_field(hotel):
    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"outlet_gst_rate": None, "gst_inclusive": None}}))
    updated, current = run(backfill_outlet_gst.backfill())
    assert (updated, current) == (1, 0)
    assert stored()["outlet_gst_rate"] == 5.0
    assert stored()["gst_inclusive"] is False


def test_the_migration_is_idempotent(hotel):
    run(backfill_outlet_gst.backfill())
    updated, current = run(backfill_outlet_gst.backfill())
    assert (updated, current) == (0, 1)


def test_the_migration_leaves_a_deliberate_zero_rate_alone(hotel):
    """`not record.get(...)` would find 0.0 falsy and re-register an unregistered
    business at 5%, which would have it collect tax it cannot lawfully collect."""
    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"outlet_gst_rate": 0.0}}))
    run(backfill_outlet_gst.backfill())
    assert stored()["outlet_gst_rate"] == 0.0


def test_the_migration_leaves_a_hotels_own_rate_alone(hotel):
    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"outlet_gst_rate": 18.0, "gst_inclusive": True}}))
    run(backfill_outlet_gst.backfill())
    assert stored()["outlet_gst_rate"] == 18.0
    assert stored()["gst_inclusive"] is True


# ----------------------------- what a bill is charged -----------------------------
def open_bill(price: float, quantity: int = 1) -> dict:
    order = {"id": "o1", "table_id": "t1", "table_label": "T01", "status": "open",
             "discount": 0.0, "items": [
                 {"id": "i1", "menu_item_id": "m1", "name": "Paneer Tikka",
                  "price": price, "quantity": quantity, "station": "kitchen",
                  "notes": "", "status": "pending"}]}
    run(hotel_db().orders.insert_one(dict(order)))
    return order


def hotel_db() -> PropertyScopedDatabase:
    return PropertyScopedDatabase("p1")


def set_gst(rate: float, inclusive: bool) -> None:
    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"outlet_gst_rate": rate, "gst_inclusive": inclusive}}))


def test_a_hundred_rupee_dish_at_five_percent_exclusive_bills_one_hundred_and_five(hotel):
    set_gst(5.0, False)
    order = open_bill(100.0)
    out = run(orders.compute_totals_for(hotel_db(), order))
    assert out["subtotal"] == 100.0 and out["tax"] == 5.0 and out["total"] == 105.0


def test_the_same_dish_inclusive_bills_the_price_on_the_card(hotel):
    set_gst(5.0, True)
    order = open_bill(100.0)
    out = run(orders.compute_totals_for(hotel_db(), order))
    assert out["total"] == 100.0
    assert out["tax"] == 4.76
    assert out["taxable_value"] == 95.24


def test_a_bill_records_the_rate_it_was_charged_at(hotel):
    set_gst(18.0, True)
    order = open_bill(100.0)
    out = run(orders.compute_totals_for(hotel_db(), order))
    assert out["gst_rate"] == 18.0 and out["gst_inclusive"] is True


def test_a_property_the_migration_has_not_reached_still_bills_five_percent(hotel):
    run(db_module.unscoped_db.properties.update_one(
        {"id": "p1"}, {"$set": {"outlet_gst_rate": None, "gst_inclusive": None}}))
    order = open_bill(100.0)
    out = run(orders.compute_totals_for(hotel_db(), order))
    assert out["tax"] == 5.0 and out["total"] == 105.0


def test_ten_percent_is_gone(hotel):
    """The regression this piece exists for. Any rate the property did not choose is the
    bug coming back."""
    set_gst(5.0, False)
    order = open_bill(100.0)
    assert run(orders.compute_totals_for(hotel_db(), order))["tax"] != 10.0


# --------------------- a settled bill is never priced again -----------------------
def settle(order_id="o1"):
    return call(orders.settle_order, order_id=order_id,
                payload=orders.SettleIn(payment_method="cash"),
                user=hotel_admin(), db=hotel_db())


_ADMIN = {"id": "u-admin", "role": "admin", "property_id": "p1"}


def hotel_admin() -> dict:
    return dict(_ADMIN)


def test_a_settled_bill_is_untouched_by_a_later_rate_change(hotel):
    """The whole of the migration's promise, in one test.

    ₹100 at 5% exclusive, settled at ₹105. The hotel then moves to 18% inclusive — which
    would make the same basket ₹100 with ₹15.25 of tax — and the settled bill still says
    ₹105 with ₹5.00 of tax, because the guest paid ₹105 and the till says so.
    """
    set_gst(5.0, False)
    open_bill(100.0)
    run(hotel_db().tables.insert_one({"id": "t1", "label": "T01", "status": "occupied",
                                      "current_order_id": "o1"}))
    settled = settle()
    assert settled["total"] == 105.0 and settled["tax"] == 5.0

    set_gst(18.0, True)
    after = run(hotel_db().orders.find_one({"id": "o1"}, {"_id": 0}))
    assert after["total"] == 105.0
    assert after["tax"] == 5.0
    assert after["gst_rate"] == 5.0
    assert after["gst_inclusive"] is False


def test_the_arithmetic_itself_refuses_a_settled_bill(hotel):
    """Not only the routes. A caller written later inherits the refusal."""
    order = {"id": "o9", "status": "settled", "discount": 0.0, "total": 105.0,
             "tax": 5.0, "subtotal": 100.0,
             "items": [{"price": 100.0, "quantity": 1}]}
    out = orders.compute_totals(order, 18.0, True)
    assert out["total"] == 105.0 and out["tax"] == 5.0


def test_a_line_cannot_be_taken_off_a_settled_bill(hotel):
    """The one route that could still have re-priced a paid bill."""
    set_gst(5.0, False)
    open_bill(100.0)
    run(hotel_db().tables.insert_one({"id": "t1", "label": "T01", "status": "occupied",
                                      "current_order_id": "o1"}))
    settle()
    refusal = refused(orders.remove_item, order_id="o1", item_id="i1",
                      user=hotel_admin(), db=hotel_db())
    assert refusal.status_code == 409
    assert run(hotel_db().orders.find_one({"id": "o1"}, {"_id": 0}))["total"] == 105.0


def test_a_settled_bill_cannot_be_settled_a_second_time(hotel):
    set_gst(5.0, False)
    open_bill(100.0)
    run(hotel_db().tables.insert_one({"id": "t1", "label": "T01", "status": "occupied",
                                      "current_order_id": "o1"}))
    settle()
    assert refused(orders.settle_order, order_id="o1",
                   payload=orders.SettleIn(payment_method="cash"),
                   user=hotel_admin(), db=hotel_db()).status_code == 409
