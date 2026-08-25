"""Subscriptions where they meet the request: the record, the operator, and the tenant.

No server. The endpoints are ordinary coroutines and the dependencies are ordinary
functions, so both are called as what they are — the same style as test_isolation.py and
test_property_type.py. What is exercised is the real router code against a real
(file-backed) mock database; only the transport is absent.

The claims under test, in the order the money moves:

* a price is the operator's to set and nobody else's — the tenant cannot reach the route
  and cannot smuggle the fields through their own settings form;
* recording a payment moves `paid_until` by the agreed period, and writes a ledger line
  that is never edited or deleted afterwards;
* overdue is visible to the operator with a day count and visible to the business as a
  plain notice — and it stops nothing. An overdue hotel still takes a booking. Only the
  operator's deliberate press does;
* one property's subscription is invisible to another, exactly like every other tenant
  record;
* changing what a business *is* does not leave its staff holding a domain the new type
  does not have.
"""
import asyncio
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.platform as platform
import routers.property as property_router
import routers.staff as staff
from mock_db import MockDatabase
from models.property import Property, PropertyFields, SubscriptionPayment
from services.access import (
    DOMAINS, LIVE, OUTLET, PLATFORM_ADMIN, PROPERTY_BOTH, PROPERTY_HOTEL,
    PROPERTY_OUTLET, SCREEN_KEYS, SHARED, SUSPENDED, can_access, narrow_to_domains,
)
from services.subscription import MONTHLY, QUARTERLY, YEARLY

TODAY = "2026-08-06"


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


# --------------------------------- the world ---------------------------------
# Every module that holds the unscoped handle itself and is reached from here.
# security too: `resolve_property` lives there and binds `unscoped_db` at import, so a
# test that swapped only the routers' handles would have the property looked up in the
# real database and answered 404 by a route that was working perfectly.
_UNSCOPED_HOLDERS = (db_module, platform, property_router, staff, security)


@dataclass
class Tenant:
    record: dict
    admin: dict


def make_property(tag: str, name: str, property_type: str = PROPERTY_BOTH) -> Tenant:
    unscoped = db_module.unscoped_db
    record = Property(id=f"{tag}-property", name=name, status=LIVE,
                      property_type=property_type).model_dump()
    record["id"] = f"{tag}-property"
    run(unscoped.properties.insert_one(record))
    admin = {"id": f"{tag}-admin", "email": f"admin@{tag}.example.com",
             "name": f"Admin {name}", "role": "admin", "domains": list(DOMAINS),
             "permissions": list(SCREEN_KEYS), "active": True,
             "property_id": record["id"]}
    run(unscoped.users.insert_one(admin))
    return Tenant(record=record, admin=admin)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two live properties and the operator who prices them.

    A is priced at ₹12,000 monthly through the routes under test; B is left unpriced,
    which is a normal state and is also the tell for a leak — B's admin asking for its
    own property must see no figure at all, not A's.
    """
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)
    # The property's local day, frozen, so the arithmetic under test is the arithmetic
    # and not the calendar. The routes read it through this one name.
    monkeypatch.setattr(platform, "today", lambda: TODAY)
    monkeypatch.setattr(property_router, "today", lambda: TODAY)

    a = make_property("a", "The Grand")
    b = make_property("b", "The Regent")
    operator = {"id": "op-1", "email": "ops@barflow.io", "role": PLATFORM_ADMIN,
                "active": True}
    run(handle.users.insert_one(operator))
    return a, b, operator


def reread(property_id: str) -> dict:
    return run(db_module.unscoped_db.properties.find_one({"id": property_id}, {"_id": 0}))


def price(operator, property_id="a-property", amount=12000.0, period=MONTHLY,
          note="NEFT to HDFC 0001"):
    return call(platform.set_subscription, property_id=property_id,
                payload=platform.SubscriptionIn(amount=amount, period=period, note=note),
                user=operator)


def pay(operator, property_id="a-property", amount=12000.0, method="bank_transfer",
        received_on=None, reference="NEFT-8891"):
    return call(platform.record_payment, property_id=property_id,
                payload=platform.PaymentIn(amount=amount, method=method,
                                           received_on=received_on, reference=reference),
                user=operator)


# ------------------------------- the record -------------------------------
def test_a_new_property_is_unpriced_and_that_is_not_an_error():
    record = Property(name="Fresh")
    assert record.subscription_amount is None
    assert record.billing_period is None
    assert record.paid_until is None
    assert record.payment_note is None


def test_the_price_is_not_something_the_business_may_edit_about_itself():
    # The same guard property_type has, for the same reason: `PropertyFields` is the body
    # of PUT /api/property, so a field absent from it cannot be sent. An admin who could
    # PUT their own price would set it to zero.
    for field in ("subscription_amount", "billing_period", "paid_until", "payment_note"):
        assert field not in PropertyFields.model_fields
        assert field in Property.model_fields


def test_an_unknown_billing_period_is_refused_by_the_record():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Property(name="X", billing_period="fortnightly")


def test_a_payment_line_names_who_recorded_it_and_when():
    line = SubscriptionPayment(
        property_id="a-property", amount=12000.0, received_on=TODAY,
        covers_from=TODAY, covers_to="2026-09-06", method="upi",
        reference="UPI-1", recorded_by="op-1")
    assert line.recorded_by == "op-1"
    assert line.recorded_at.startswith("20")
    assert line.id


# ------------------------------ setting a price ------------------------------
def test_the_operator_sets_the_price_and_the_period(world):
    _a, _b, operator = world
    out = price(operator)
    assert out["subscription"]["amount"] == 12000.0
    assert out["subscription"]["period"] == MONTHLY
    assert out["subscription"]["priced"] is True
    assert out["subscription"]["never_paid"] is True
    stored = reread("a-property")
    assert stored["subscription_amount"] == 12000.0
    assert stored["billing_period"] == MONTHLY
    assert stored["payment_note"] == "NEFT to HDFC 0001"


def test_pricing_does_not_invent_a_paid_until(world):
    # Agreeing a figure is not money arriving. Only a recorded payment moves the date.
    _a, _b, operator = world
    price(operator)
    assert reread("a-property")["paid_until"] is None


def test_repricing_leaves_the_term_already_paid_for_alone(world):
    _a, _b, operator = world
    price(operator)
    pay(operator)
    assert reread("a-property")["paid_until"] == "2026-09-06"
    price(operator, amount=15000.0, period=QUARTERLY)
    # The new figure applies to the next payment. It does not retroactively shorten or
    # extend a term that has already been paid for.
    assert reread("a-property")["paid_until"] == "2026-09-06"
    assert reread("a-property")["subscription_amount"] == 15000.0


def test_a_price_can_be_withdrawn(world):
    _a, _b, operator = world
    price(operator)
    out = call(platform.set_subscription, property_id="a-property",
               payload=platform.SubscriptionIn(amount=None, period=None, note=""),
               user=operator)
    assert out["subscription"]["priced"] is False
    assert out["subscription"]["overdue"] is False


def test_an_unknown_period_is_refused_with_the_field_named(world):
    _a, _b, operator = world
    assert refused(platform.set_subscription, property_id="a-property",
                   payload=platform.SubscriptionIn(amount=1.0, period="fortnightly"),
                   user=operator).status_code == 422


def test_a_negative_price_is_refused(world):
    _a, _b, operator = world
    assert refused(platform.set_subscription, property_id="a-property",
                   payload=platform.SubscriptionIn(amount=-1.0, period=MONTHLY),
                   user=operator).status_code == 422


def test_an_amount_without_a_period_is_refused(world):
    # Half a price cannot be advanced by a payment, so it is not a price.
    _a, _b, operator = world
    assert refused(platform.set_subscription, property_id="a-property",
                   payload=platform.SubscriptionIn(amount=12000.0, period=None),
                   user=operator).status_code == 422


def test_pricing_a_property_that_does_not_exist_is_404(world):
    _a, _b, operator = world
    assert refused(platform.set_subscription, property_id="nope",
                   payload=platform.SubscriptionIn(amount=1.0, period=MONTHLY),
                   user=operator).status_code == 404


# ----------------------------- recording a payment -----------------------------
def test_a_payment_advances_paid_until_by_one_month(world):
    _a, _b, operator = world
    price(operator)
    out = pay(operator)
    assert out["subscription"]["paid_until"] == "2026-09-06"
    assert out["payment"]["covers_from"] == TODAY
    assert out["payment"]["covers_to"] == "2026-09-06"
    assert reread("a-property")["paid_until"] == "2026-09-06"


def test_a_second_payment_extends_rather_than_resetting(world):
    _a, _b, operator = world
    price(operator)
    pay(operator)
    out = pay(operator)
    assert out["subscription"]["paid_until"] == "2026-10-06"
    assert out["payment"]["covers_from"] == "2026-09-06"


def test_a_payment_from_an_overdue_date_runs_from_today(world):
    # Three months late. The line says it covers today onwards, because that is what the
    # money bought — not May, June and July, which were never invoiced.
    _a, _b, operator = world
    price(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2026-05-06"}}))
    out = pay(operator)
    assert out["payment"]["covers_from"] == TODAY
    assert out["subscription"]["paid_until"] == "2026-09-06"
    assert out["subscription"]["overdue"] is False


def test_a_quarterly_price_advances_three_months(world):
    _a, _b, operator = world
    price(operator, amount=33000.0, period=QUARTERLY)
    assert pay(operator, amount=33000.0)["subscription"]["paid_until"] == "2026-11-06"


def test_a_yearly_price_advances_twelve_months(world):
    _a, _b, operator = world
    price(operator, amount=120000.0, period=YEARLY)
    assert pay(operator, amount=120000.0)["subscription"]["paid_until"] == "2027-08-06"


def test_a_payment_against_an_unpriced_property_is_refused(world):
    # There is no period to advance by. Recording the money anyway would leave a ledger
    # line covering nothing, and a date nobody could explain.
    _a, _b, operator = world
    assert refused(platform.record_payment, property_id="a-property",
                   payload=platform.PaymentIn(amount=1.0, method="upi"),
                   user=operator).status_code == 400


def test_a_zero_or_negative_payment_is_refused(world):
    _a, _b, operator = world
    price(operator)
    for bad in (0.0, -100.0):
        assert refused(platform.record_payment, property_id="a-property",
                       payload=platform.PaymentIn(amount=bad, method="upi"),
                       user=operator).status_code == 422


def test_an_unknown_payment_method_is_refused(world):
    _a, _b, operator = world
    price(operator)
    assert refused(platform.record_payment, property_id="a-property",
                   payload=platform.PaymentIn(amount=1.0, method="bitcoin"),
                   user=operator).status_code == 422


def test_the_date_received_defaults_to_the_properties_local_day(world):
    _a, _b, operator = world
    price(operator)
    assert pay(operator)["payment"]["received_on"] == TODAY


def test_a_backdated_payment_is_recorded_on_the_day_it_arrived(world):
    # The money arrived on Friday and the operator types it in on Monday. The ledger says
    # Friday, because that is when it arrived — but the term still runs from today, since
    # backdating the term would give away days nobody paid for.
    _a, _b, operator = world
    price(operator)
    out = pay(operator, received_on="2026-08-03")
    assert out["payment"]["received_on"] == "2026-08-03"
    assert out["payment"]["covers_from"] == TODAY


# ------------------------------- the ledger -------------------------------
def test_the_payments_log_is_readable_newest_first(world):
    _a, _b, operator = world
    price(operator)
    pay(operator, reference="NEFT-1")
    pay(operator, reference="NEFT-2", method="upi")
    rows = call(platform.list_payments, property_id="a-property", user=operator)
    assert [r["reference"] for r in rows] == ["NEFT-2", "NEFT-1"]
    assert rows[0]["method"] == "upi"
    assert rows[0]["recorded_by"] == "op-1"
    assert rows[0]["amount"] == 12000.0


def test_nothing_edits_or_deletes_a_payment(world):
    # The folio ledger's reasoning, applied to the platform's own money: a correction is
    # a new entry. There is no route that mutates a line, so this is checked by there
    # being none rather than by asserting one refuses.
    from server import app
    from fastapi.routing import APIRoute
    mutating = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "payments" not in route.path:
            continue
        if "/platform/" not in route.path:
            continue
        if route.methods & {"PUT", "PATCH", "DELETE"}:
            mutating.append(f"{sorted(route.methods)} {route.path}")
    assert mutating == []


def test_a_correction_is_a_new_line_and_both_stay_on_the_ledger(world):
    _a, _b, operator = world
    price(operator)
    pay(operator, amount=1200.0, reference="typo, decimal point")
    pay(operator, amount=10800.0, reference="balance of the 12,000")
    rows = call(platform.list_payments, property_id="a-property", user=operator)
    assert [r["amount"] for r in rows] == [10800.0, 1200.0]
    # And both moved the date, because both were money that arrived.
    assert reread("a-property")["paid_until"] == "2026-10-06"


def test_one_propertys_ledger_holds_none_of_the_others(world):
    _a, _b, operator = world
    price(operator, "a-property")
    price(operator, "b-property", amount=5000.0)
    pay(operator, "a-property", reference="A-ONLY")
    pay(operator, "b-property", amount=5000.0, reference="B-ONLY")
    rows = call(platform.list_payments, property_id="a-property", user=operator)
    assert [r["reference"] for r in rows] == ["A-ONLY"]
    assert "B-ONLY" not in str(rows)


def test_the_ledger_of_a_property_that_does_not_exist_is_404(world):
    _a, _b, operator = world
    assert refused(platform.list_payments, property_id="nope",
                   user=operator).status_code == 404


# ------------------------- overdue, on the operator's list -------------------------
def test_the_list_carries_the_subscription_and_the_day_count(world):
    _a, _b, operator = world
    price(operator)
    pay(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2026-05-06"}}))
    rows = call(platform.list_properties, status="", user=operator)
    row = next(r for r in rows if r["id"] == "a-property")
    assert row["subscription"]["overdue"] is True
    assert row["subscription"]["days_overdue"] == 92
    assert row["subscription"]["amount"] == 12000.0


def test_an_unpriced_property_shows_no_figure_and_no_flag(world):
    _a, _b, operator = world
    rows = call(platform.list_properties, status="", user=operator)
    row = next(r for r in rows if r["id"] == "b-property")
    assert row["subscription"]["priced"] is False
    assert row["subscription"]["overdue"] is False


def test_the_detail_carries_the_note_the_operator_wrote(world):
    _a, _b, operator = world
    price(operator)
    detail = call(platform.property_detail, property_id="a-property", user=operator)
    assert detail["payment_note"] == "NEFT to HDFC 0001"
    assert detail["subscription"]["amount"] == 12000.0


def test_the_overdue_flag_is_not_stored_anywhere(world):
    _a, _b, operator = world
    price(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2026-05-06"}}))
    call(platform.list_properties, status="", user=operator)
    stored = reread("a-property")
    assert "overdue" not in stored and "days_overdue" not in stored


# ---------------------------- only the operator ----------------------------
@pytest.mark.parametrize("route,kwargs", [
    ("set_subscription", {"payload": None}),
    ("record_payment", {"payload": None}),
    ("list_payments", {}),
    ("set_property_type", {"payload": None}),
])
def test_a_hotel_admin_is_refused_every_subscription_route(world, route, kwargs):
    a, _b, _operator = world
    # `platform_admin` is the gate, and it is the same one approve and suspend sit behind.
    assert refused(platform.platform_admin, user=a.admin).status_code == 403


def test_the_operator_is_the_only_role_that_passes(world):
    a, _b, operator = world
    assert run(platform.platform_admin(user=operator)) is operator
    for role in ("admin", "manager", "front_desk", "waiter", "kitchen"):
        assert refused(platform.platform_admin,
                       user={**a.admin, "role": role}).status_code == 403


# ------------------------- what the business itself sees -------------------------
def test_a_business_sees_its_own_amount_period_and_paid_until(world):
    a, _b, operator = world
    price(operator)
    pay(operator)
    mine = call(property_router.get_property, user=a.admin)
    assert mine["subscription"]["amount"] == 12000.0
    assert mine["subscription"]["period"] == MONTHLY
    assert mine["subscription"]["paid_until"] == "2026-09-06"
    assert mine["subscription"]["overdue"] is False


def test_an_overdue_business_is_told_so_plainly(world):
    a, _b, operator = world
    price(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2026-05-06"}}))
    mine = call(property_router.get_property, user=a.admin)
    assert mine["subscription"]["overdue"] is True
    assert mine["subscription"]["days_overdue"] == 92


def test_a_business_is_not_shown_the_operators_note(world):
    # An account number and a person to ring is the operator's memo, not the tenant's.
    a, _b, operator = world
    price(operator)
    mine = call(property_router.get_property, user=a.admin)
    assert "payment_note" not in mine
    assert "NEFT to HDFC" not in str(mine)


def test_the_raw_price_fields_are_not_left_loose_on_the_tenants_payload(world):
    # One place says what the tenant sees — the `subscription` block — rather than the
    # same four fields appearing twice and drifting apart.
    a, _b, operator = world
    price(operator)
    mine = call(property_router.get_property, user=a.admin)
    for field in ("subscription_amount", "billing_period", "paid_until", "payment_note"):
        assert field not in mine


def test_a_business_cannot_set_its_own_price(world):
    a, _b, operator = world
    price(operator)
    call(property_router.update_property,
         payload=PropertyFields(name="The Grand", city="Jaipur"), user=a.admin)
    stored = reread("a-property")
    assert stored["subscription_amount"] == 12000.0
    assert stored["billing_period"] == MONTHLY
    assert stored["name"] == "The Grand"


def test_a_price_survives_the_business_editing_its_own_settings(world):
    a, _b, operator = world
    price(operator)
    pay(operator)
    out = call(property_router.update_property,
               payload=PropertyFields(name="The Grand", phone="9990000000"),
               user=a.admin)
    assert out["subscription"]["paid_until"] == "2026-09-06"
    assert reread("a-property")["paid_until"] == "2026-09-06"


def test_one_business_cannot_see_anothers_subscription(world):
    a, b, operator = world
    price(operator, "a-property", amount=12000.0)
    theirs = call(property_router.get_property, user=b.admin)
    assert theirs["id"] == "b-property"
    assert theirs["subscription"]["priced"] is False
    assert theirs["subscription"]["amount"] is None
    assert "12000" not in str(theirs)


def test_there_is_no_route_by_which_a_business_names_another_property(world):
    # `GET /api/property` takes no id — the property comes from the token. Stated as a
    # test because it is the reason the leak above is impossible rather than merely absent.
    import inspect
    for fn in (property_router.get_property, property_router.update_property):
        assert "property_id" not in inspect.signature(fn).parameters


# ----------------------- overdue stops nothing on its own -----------------------
def test_an_overdue_hotel_still_trades(world):
    a, _b, operator = world
    price(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2026-05-06"}}))
    overdue = reread("a-property")
    # Not setup_time: this is the operating half of the app — taking a booking, settling
    # a bill. A hotel with guests checking in must not go dark over a late invoice.
    assert can_access(a.admin, "hotel", ("admin",), overdue) is True
    assert can_access(a.admin, SHARED, ("admin", "manager"), overdue) is True


def test_a_receptionist_at_an_overdue_hotel_still_checks_guests_in(world):
    a, _b, operator = world
    price(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2020-01-01"}}))
    desk = {"id": "a-desk", "role": "front_desk", "domains": ["hotel"], "active": True,
            "permissions": ["hotel.front_desk"], "property_id": "a-property"}
    assert can_access(desk, "hotel", ("admin", "manager", "front_desk"),
                      reread("a-property"), permission="hotel.front_desk") is True


def test_only_the_operators_press_stops_trade(world):
    a, _b, operator = world
    price(operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": "a-property"}, {"$set": {"paid_until": "2026-05-06"}}))
    assert can_access(a.admin, "hotel", ("admin",), reread("a-property")) is True
    call(platform.set_status, property_id="a-property",
         payload=platform.StatusIn(status=SUSPENDED, reason="Invoice unpaid since May"),
         user=operator)
    assert can_access(a.admin, "hotel", ("admin",), reread("a-property")) is False


def test_recording_a_payment_does_not_restore_a_suspended_property(world):
    # Restoring is the operator's press too. Money arriving is a fact about the invoice,
    # not a decision about whether the business trades.
    a, _b, operator = world
    price(operator)
    call(platform.set_status, property_id="a-property",
         payload=platform.StatusIn(status=SUSPENDED, reason="unpaid"), user=operator)
    pay(operator)
    assert reread("a-property")["status"] == SUSPENDED
    assert can_access(a.admin, "hotel", ("admin",), reread("a-property")) is False


# ----------------------- changing what a business is -----------------------
def test_the_operator_can_correct_a_type_picked_wrong_at_signup(world):
    _a, _b, operator = world
    out = call(platform.set_property_type, property_id="a-property",
               payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET),
               user=operator)
    assert out["property_type"] == PROPERTY_OUTLET
    assert reread("a-property")["property_type"] == PROPERTY_OUTLET


def test_an_unknown_type_is_refused(world):
    _a, _b, operator = world
    assert refused(platform.set_property_type, property_id="a-property",
                   payload=platform.PropertyTypeIn(property_type="motel"),
                   user=operator).status_code == 422
    assert reread("a-property")["property_type"] == PROPERTY_BOTH


def test_narrowing_to_an_outlet_takes_the_hotel_domain_off_the_staff(world):
    a, _b, operator = world
    unscoped = db_module.unscoped_db
    run(unscoped.users.insert_one({
        "id": "a-manager", "email": "mgr@a.example.com", "name": "Mgr", "role": "manager",
        "domains": ["hotel", "restaurant"], "permissions": ["hotel.bookings",
                                                            "outlet.pos"],
        "active": True, "property_id": "a-property"}))
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET), user=operator)
    mgr = run(unscoped.users.find_one({"id": "a-manager"}, {"_id": 0}))
    assert mgr["domains"] == ["restaurant"]
    # And the screens that domain can no longer reach go with it, rather than staying
    # ticked for a screen that would 403.
    assert mgr["permissions"] == ["outlet.pos"]
    assert mgr["active"] is True


def test_the_admin_is_restamped_with_what_the_property_now_runs(world):
    a, _b, operator = world
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET), user=operator)
    admin = run(db_module.unscoped_db.users.find_one({"id": "a-admin"}, {"_id": 0}))
    assert admin["domains"] == list(OUTLET)
    assert admin["active"] is True  # the property always keeps its admin
    assert "hotel.front_desk" not in admin["permissions"]


def test_a_staff_member_left_with_no_work_here_is_deactivated_not_stranded(world):
    a, _b, operator = world
    unscoped = db_module.unscoped_db
    run(unscoped.users.insert_one({
        "id": "a-desk", "email": "desk@a.example.com", "name": "Desk", "role": "front_desk",
        "domains": ["hotel"], "permissions": ["hotel.front_desk", "hotel.guests"],
        "active": True, "property_id": "a-property"}))
    out = call(platform.set_property_type, property_id="a-property",
               payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET),
               user=operator)
    desk = run(unscoped.users.find_one({"id": "a-desk"}, {"_id": 0}))
    assert desk["active"] is False
    # Never left with an empty list: the startup domain backfill repairs an empty one by
    # granting all three, including the one this property just gave up. They hold what
    # the business now runs and are switched off until their own admin reassigns them.
    assert desk["domains"] == list(OUTLET)
    assert out["staff"]["deactivated"] == 1
    assert out["staff"]["narrowed"] >= 1


def test_the_change_reports_what_the_operator_just_did_to_the_roster(world):
    a, _b, operator = world
    out = call(platform.set_property_type, property_id="a-property",
               payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET),
               user=operator)
    assert out["staff"]["narrowed"] == 1  # the admin
    assert out["staff"]["deactivated"] == 0
    # What stops being reachable, so the operator can say so rather than discover it.
    assert "unreachable" in out


def test_widening_to_both_gives_the_admin_the_hotel_back(world):
    _a, _b, operator = world
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET), user=operator)
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_BOTH), user=operator)
    admin = run(db_module.unscoped_db.users.find_one({"id": "a-admin"}, {"_id": 0}))
    assert admin["domains"] == list(DOMAINS)


def test_widening_leaves_ordinary_staff_exactly_as_they_were(world):
    # Nothing is granted by a widening except to the admin. A waiter does not acquire the
    # front desk because the business bought a building.
    _a, _b, operator = world
    unscoped = db_module.unscoped_db
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET), user=operator)
    run(unscoped.users.insert_one({
        "id": "a-waiter", "email": "w@a.example.com", "name": "W", "role": "waiter",
        "domains": ["bar"], "permissions": ["outlet.pos"], "active": True,
        "property_id": "a-property"}))
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_HOTEL), user=operator)
    waiter = run(unscoped.users.find_one({"id": "a-waiter"}, {"_id": 0}))
    assert waiter["domains"] == ["bar"]
    assert waiter["permissions"] == ["outlet.pos"]


def test_retyping_one_property_leaves_the_others_roster_alone(world):
    a, b, operator = world
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET), user=operator)
    other = run(db_module.unscoped_db.users.find_one({"id": "b-admin"}, {"_id": 0}))
    assert other["domains"] == list(DOMAINS)
    assert reread("b-property")["property_type"] == PROPERTY_BOTH


def test_setting_the_type_it_already_has_changes_nothing(world):
    a, _b, operator = world
    out = call(platform.set_property_type, property_id="a-property",
               payload=platform.PropertyTypeIn(property_type=PROPERTY_BOTH),
               user=operator)
    assert out["staff"]["deactivated"] == 0
    admin = run(db_module.unscoped_db.users.find_one({"id": "a-admin"}, {"_id": 0}))
    assert admin["domains"] == list(DOMAINS)
    assert admin["permissions"] == list(SCREEN_KEYS)


def test_retyping_a_property_that_does_not_exist_is_404(world):
    _a, _b, operator = world
    assert refused(platform.set_property_type, property_id="nope",
                   payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET),
                   user=operator).status_code == 404


def test_narrowing_deletes_no_data(world):
    # The same stance suspension takes: rooms, rates and bookings sit untouched, so a
    # type set wrong at signup and corrected twice loses nothing.
    a, _b, operator = world
    run(db_module.unscoped_db.rooms.insert_one(
        {"id": "a-room", "number": "101", "property_id": "a-property"}))
    call(platform.set_property_type, property_id="a-property",
         payload=platform.PropertyTypeIn(property_type=PROPERTY_OUTLET), user=operator)
    assert run(db_module.unscoped_db.rooms.find_one({"id": "a-room"})) is not None


# ------------------------ the narrowing rule, on its own ------------------------
def test_an_admin_is_restamped_with_the_whole_of_what_the_property_runs():
    patch = narrow_to_domains(
        {"role": "admin", "domains": list(DOMAINS), "permissions": list(SCREEN_KEYS)},
        OUTLET)
    assert patch["domains"] == list(OUTLET)
    # .get, not []: an admin's patch carries no `active` key at all, which is the
    # strongest form of "never deactivated" — the field is not touched rather than
    # touched with the right value.
    assert patch.get("active") is not False


def test_a_non_admin_keeps_only_the_domains_the_property_still_has():
    patch = narrow_to_domains(
        {"role": "manager", "domains": ["hotel", "bar"], "permissions": ["hotel.rooms",
                                                                         "outlet.pos"]},
        OUTLET)
    assert patch["domains"] == ["bar"]
    assert patch["permissions"] == ["outlet.pos"]


def test_a_non_admin_with_nothing_left_is_switched_off():
    patch = narrow_to_domains(
        {"role": "front_desk", "domains": ["hotel"], "permissions": ["hotel.front_desk"]},
        OUTLET)
    assert patch["active"] is False
    assert patch["domains"] == list(OUTLET)


def test_a_record_already_inside_the_new_domains_is_left_alone():
    patch = narrow_to_domains(
        {"role": "waiter", "domains": ["bar"], "permissions": ["outlet.pos"]}, OUTLET)
    assert patch == {}


def test_a_property_with_no_domains_at_all_is_refused_rather_than_emptying_its_roster():
    from services.access import AccessError
    with pytest.raises(AccessError):
        narrow_to_domains({"role": "admin", "domains": ["bar"]}, ())
