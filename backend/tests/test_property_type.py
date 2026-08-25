"""What kind of business a tenant is, and what follows from it.

A property is a hotel, an outlet (a restaurant or a bar, with no rooms), or both. The
type is not decoration: it decides which work domains the property has, and therefore
which domains its staff may hold and which endpoints anybody there can reach.

The tests are grouped by where the rule has to hold, because the design's claim is that
it holds in every one of them: the pure mapping, the stored record, the startup
migration, the request-time predicate, signup, and both staff-writing routes. A rule
that is only enforced on the route somebody remembered is not enforced.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import Headers

import db as db_module
import security
from migrations import backfill_property_type
from mock_db import MockDatabase
from models.property import Property, PropertyFields, PropertyType
from routers import signup, staff
from services.access import (
    DOMAINS, LIVE, OUTLET, PROPERTY_BOTH, PROPERTY_HOTEL, PROPERTY_OUTLET,
    PROPERTY_TYPES, SHARED, AccessError, can_access, domains_for_property_type,
    property_domains,
)


def run(coro):
    return asyncio.run(coro)


def refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


# ----------------------------- the rule, on its own -----------------------------
# A pure function beside DOMAINS, because it is a rule about the business rather than a
# router's concern — and because the API and the staff screen both have to ask it.

def test_the_three_types_are_the_ones_the_record_stores():
    from typing import get_args
    assert set(get_args(PropertyType)) == set(PROPERTY_TYPES)
    assert PROPERTY_TYPES == (PROPERTY_HOTEL, PROPERTY_OUTLET, PROPERTY_BOTH)


def test_a_hotel_has_every_domain():
    # Rooms *and* outlets: a hotel with a restaurant in it is the ordinary case.
    assert domains_for_property_type(PROPERTY_HOTEL) == DOMAINS


def test_both_is_the_same_shape_as_a_hotel():
    # Kept distinct from `hotel` only because the signup wording differs and the operator
    # wants to see which is which — the domains it grants are identical.
    assert domains_for_property_type(PROPERTY_BOTH) == DOMAINS


def test_an_outlet_has_no_hotel_domain():
    assert domains_for_property_type(PROPERTY_OUTLET) == OUTLET
    assert "hotel" not in domains_for_property_type(PROPERTY_OUTLET)


def test_an_unknown_type_is_refused_loudly():
    # Same stance as an unknown domain: a typo must fail where it is written, not
    # silently hand somebody the wrong half of the business.
    with pytest.raises(AccessError):
        domains_for_property_type("guesthouse")


def test_a_property_record_with_no_type_reads_as_both():
    # What every property has been operating as. The migration stamps them, but the rule
    # must give the same answer whether or not it has run yet.
    assert property_domains({"id": "p1", "status": LIVE}) == DOMAINS


def test_a_property_record_with_a_type_reads_as_that_type():
    assert property_domains(
        {"id": "p1", "status": LIVE, "property_type": PROPERTY_OUTLET}) == OUTLET


def test_a_property_record_with_a_nonsense_type_grants_nothing():
    # A hand-edited record is a bug, not a licence: guessing "all of it" is the
    # permissive guess and it is the wrong one to make.
    assert property_domains({"id": "p1", "property_type": "guesthouse"}) == ()


# ------------------------------- the stored record -------------------------------

def test_a_new_property_record_defaults_to_both():
    assert Property(name="The Grand").property_type == PROPERTY_BOTH


def test_a_property_can_be_created_as_an_outlet():
    assert Property(name="Tinto", property_type=PROPERTY_OUTLET).property_type == "outlet"


def test_an_unknown_property_type_is_refused_by_the_record():
    with pytest.raises(ValidationError):
        Property(name="The Grand", property_type="guesthouse")


def test_the_type_is_not_something_the_hotel_may_edit_about_itself():
    """`PUT /api/property` takes PropertyFields. An admin who could set their own type
    would grant their restaurant a hotel, and strand every staff member whose domains
    the change took away."""
    assert "property_type" not in PropertyFields.model_fields


# -------------------------------- the migration --------------------------------

@pytest.fixture
def handle(tmp_path, monkeypatch):
    db = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(backfill_property_type, "unscoped_db", db)
    return db


def properties(db):
    return run(db.properties.find({}, {"_id": 0}).to_list(100))


def test_the_migration_says_existing_properties_are_both(handle):
    run(handle.properties.insert_one({"id": "p1", "name": "The Grand", "status": LIVE}))
    updated, current = run(backfill_property_type.backfill())
    assert (updated, current) == (1, 0)
    assert properties(handle)[0]["property_type"] == PROPERTY_BOTH


def test_the_migration_is_idempotent(handle):
    run(handle.properties.insert_one({"id": "p1", "name": "The Grand", "status": LIVE}))
    run(backfill_property_type.backfill())
    updated, current = run(backfill_property_type.backfill())
    assert (updated, current) == (0, 1)


def test_the_migration_leaves_a_property_that_already_said_what_it_is(handle):
    run(handle.properties.insert_one(
        {"id": "p1", "name": "Tinto", "status": LIVE, "property_type": PROPERTY_OUTLET}))
    updated, current = run(backfill_property_type.backfill())
    assert (updated, current) == (0, 1)
    assert properties(handle)[0]["property_type"] == PROPERTY_OUTLET


# --------------------------- the request-time predicate ---------------------------
# The backstop. Domains are bounded when they are written, but the founding admin of an
# outlet property is still an admin, and an admin is never domain-checked — so without a
# check on the property itself the hotel API would answer them.

def outlet_property():
    return {"id": "p1", "status": LIVE, "property_type": PROPERTY_OUTLET}


def hotel_property():
    return {"id": "p1", "status": LIVE, "property_type": PROPERTY_HOTEL}


def person(role="admin", domains=("restaurant", "bar")):
    return {"id": "u1", "role": role, "domains": list(domains), "active": True,
            "property_id": "p1", "permissions": []}


def test_an_outlet_property_refuses_the_hotel_endpoints_to_its_own_admin():
    assert can_access(person(), "hotel", ("admin",), outlet_property()) is False


def test_an_outlet_property_still_answers_its_own_endpoints():
    assert can_access(person(), OUTLET, ("admin",), outlet_property()) is True


def test_an_outlet_property_still_answers_the_shared_endpoints():
    # Guests, inventory and the property record itself are not hotel screens: a bar
    # regular is a guest, and one store room supplies the kitchen and the bar.
    assert can_access(person(), SHARED, ("admin",), outlet_property()) is True


def test_a_hotel_property_reaches_everything_it_did_before():
    for required in ("hotel", OUTLET, SHARED):
        assert can_access(person(domains=DOMAINS), required, ("admin",),
                          hotel_property()) is True


def test_a_property_with_no_type_reaches_everything_it_did_before():
    for required in ("hotel", OUTLET, SHARED):
        assert can_access(person(domains=DOMAINS), required, ("admin",),
                          {"id": "p1", "status": LIVE}) is True


def test_the_property_check_runs_ahead_of_the_admin_bypass():
    """A non-admin holding `hotel` by some route this test cannot reach is refused too —
    the gate is on the property, so no user record can talk past it."""
    stowaway = person(role="manager", domains=("hotel", "restaurant"))
    assert can_access(stowaway, "hotel", ("admin", "manager"), outlet_property()) is False


# ------------------------------------ signup ------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    """One live hotel and its admin, with signup and staff bound to the same database."""
    db = MockDatabase(str(tmp_path / "db.json"))
    for module in (db_module, security, staff, signup):
        monkeypatch.setattr(module, "unscoped_db", db)
    signup.SIGNUPS_PER_ADDRESS.reset()
    return db


class FakeRequest:
    def __init__(self, ip="203.0.113.7"):
        self.client = type("C", (), {"host": ip})()
        self.cookies = {}
        self.headers = Headers({})


def sign_up(kind, email="owner@tinto.example.com", name="Tinto"):
    return run(signup.signup(signup.SignupIn(
        hotel_name=name, city="Goa", gstin="", admin_name="Owner",
        admin_email=email, admin_password="a-good-long-password",
        property_type=kind), FakeRequest()))


def founder(db, email):
    return run(db.users.find_one({"email": email}, {"_id": 0}))


def stored_property(db, property_id):
    return run(db.properties.find_one({"id": property_id}, {"_id": 0}))


def test_signing_up_an_outlet_stores_the_type(world):
    made = sign_up(PROPERTY_OUTLET)
    assert stored_property(world, made["property_id"])["property_type"] == PROPERTY_OUTLET


def test_the_founding_admin_of_an_outlet_gets_no_hotel_domain(world):
    sign_up(PROPERTY_OUTLET)
    assert founder(world, "owner@tinto.example.com")["domains"] == list(OUTLET)


def test_the_founding_admin_of_an_outlet_gets_no_hotel_screens(world):
    sign_up(PROPERTY_OUTLET)
    granted = founder(world, "owner@tinto.example.com")["permissions"]
    assert not [k for k in granted if k.startswith("hotel.") and k != "hotel.guests"]
    # And still gets the outlet's own screens, rather than being handed nothing.
    assert "outlet.pos" in granted and "admin.staff" in granted


def test_the_founding_admin_of_a_hotel_still_gets_all_three(world):
    sign_up(PROPERTY_HOTEL, email="owner@grand.example.com", name="The Grand")
    admin = founder(world, "owner@grand.example.com")
    assert admin["domains"] == list(DOMAINS)
    assert "hotel.front_desk" in admin["permissions"]


def test_signing_up_without_saying_leaves_the_property_as_it_always_was(world):
    made = run(signup.signup(signup.SignupIn(
        hotel_name="The Grand", city="Jaipur", gstin="", admin_name="Owner",
        admin_email="owner@grand.example.com", admin_password="a-good-long-password"),
        FakeRequest()))
    assert stored_property(world, made["property_id"])["property_type"] == PROPERTY_BOTH


def test_an_unknown_type_never_reaches_the_handler():
    """Pydantic refuses it on the body, which FastAPI answers 422 — the shape the signup
    form already handles for a malformed email."""
    with pytest.raises(ValidationError):
        signup.SignupIn(
            hotel_name="Tinto", city="Goa", gstin="", admin_name="Owner",
            admin_email="owner@tinto.example.com", admin_password="a-good-long-password",
            property_type="guesthouse")


# ------------------------- the staff routes, both of them -------------------------

def outlet_world(world):
    """A signed-up outlet property, and the admin who runs it."""
    made = sign_up(PROPERTY_OUTLET)
    return made["property_id"], founder(world, "owner@tinto.example.com")


def test_hiring_a_hotel_person_into_an_outlet_is_refused(world):
    _pid, admin = outlet_world(world)
    refusal = refused(lambda: run(staff.create_staff(staff.StaffIn(
        name="Desk", email="desk@tinto.example.com", password="a-good-long-password",
        role="front_desk", domains=["hotel"]), admin)))
    assert refusal.status_code == 400
    assert "hotel" in refusal.detail.lower()


def test_hiring_somebody_partly_into_the_hotel_is_refused_too(world):
    """The whole list is checked, not just its first entry — a domain the property does
    not have is refused however it is smuggled in."""
    _pid, admin = outlet_world(world)
    refusal = refused(lambda: run(staff.create_staff(staff.StaffIn(
        name="Both", email="both@tinto.example.com", password="a-good-long-password",
        role="manager", domains=["restaurant", "hotel"]), admin)))
    assert refusal.status_code == 400


def test_hiring_into_the_outlet_still_works(world):
    _pid, admin = outlet_world(world)
    made = run(staff.create_staff(staff.StaffIn(
        name="Waiter", email="waiter@tinto.example.com", password="a-good-long-password",
        role="waiter", domains=["restaurant", "bar"]), admin))
    assert made["domains"] == ["restaurant", "bar"]


def test_editing_somebody_into_the_hotel_is_refused(world):
    _pid, admin = outlet_world(world)
    made = run(staff.create_staff(staff.StaffIn(
        name="Waiter", email="waiter@tinto.example.com", password="a-good-long-password",
        role="waiter", domains=["restaurant"]), admin))
    refusal = refused(lambda: run(staff.update_staff(made["id"], staff.StaffUpdateIn(
        name="Waiter", role="waiter", domains=["hotel"]), admin)))
    assert refusal.status_code == 400
    # And the stored row is untouched: a refused edit must not half-apply.
    row = run(world.users.find_one({"id": made["id"]}, {"_id": 0}))
    assert row["domains"] == ["restaurant"]


def test_a_second_admin_of_an_outlet_gets_the_outlets_domains_not_all_three(world):
    """An admin created with no domains is stored holding every domain, so that a later
    demotion does not leave an account reaching nothing. In an outlet that must be the
    outlet's two, not the hotel's three."""
    _pid, admin = outlet_world(world)
    made = run(staff.create_staff(staff.StaffIn(
        name="Second", email="second@tinto.example.com", password="a-good-long-password",
        role="admin", domains=[]), admin))
    assert made["domains"] == list(OUTLET)


def test_a_hotel_property_can_still_hire_into_the_hotel(world):
    sign_up(PROPERTY_HOTEL, email="owner@grand.example.com", name="The Grand")
    admin = founder(world, "owner@grand.example.com")
    made = run(staff.create_staff(staff.StaffIn(
        name="Desk", email="desk@grand.example.com", password="a-good-long-password",
        role="front_desk", domains=["hotel"]), admin))
    assert made["domains"] == ["hotel"]


def test_a_hotel_screen_cannot_be_ticked_in_an_outlet_by_any_route(world):
    """The screens follow the domains, so there is no second door: a tick for a hotel
    screen is refused because the domain it needs is one nobody in this property holds."""
    _pid, admin = outlet_world(world)
    refusal = refused(lambda: run(staff.create_staff(staff.StaffIn(
        name="Sneak", email="sneak@tinto.example.com", password="a-good-long-password",
        role="manager", domains=["restaurant"],
        permissions=["outlet.pos", "hotel.bookings"]), admin)))
    assert refusal.status_code == 400


def test_nobody_in_an_outlet_property_ends_up_holding_the_hotel_domain(world):
    """The claim, stated once as a claim: after every write route has been tried, the
    property's roster contains no hotel-domain user."""
    _pid, admin = outlet_world(world)
    for attempt in (
        lambda: run(staff.create_staff(staff.StaffIn(
            name="A", email="a@tinto.example.com", password="a-good-long-password",
            role="manager", domains=["hotel"]), admin)),
        lambda: run(staff.create_staff(staff.StaffIn(
            name="B", email="b@tinto.example.com", password="a-good-long-password",
            role="admin", domains=["hotel"]), admin)),
    ):
        assert refused(attempt).status_code == 400

    rows = run(world.users.find({}, {"_id": 0}).to_list(100))
    assert rows, "the founding admin at least"
    assert not [r for r in rows if "hotel" in (r.get("domains") or [])]


def test_the_refusal_says_why_rather_than_just_no(world):
    _pid, admin = outlet_world(world)
    refusal = refused(lambda: run(staff.create_staff(staff.StaffIn(
        name="Desk", email="desk@tinto.example.com", password="a-good-long-password",
        role="front_desk", domains=["hotel"]), admin)))
    detail = refusal.detail.lower()
    # Names the domain that was refused and what this property actually runs, so the
    # owner can act on it rather than filing a ticket.
    assert "hotel" in detail
    assert "restaurant" in detail or "bar" in detail


def test_a_property_whose_record_has_vanished_cannot_be_hired_into(world):
    """`property_domains(None)` is the empty tuple, so a broken tenant grants nothing
    rather than everything — the same stance `_property_usable` takes."""
    _pid, admin = outlet_world(world)
    run(world.properties.delete_one({"id": admin["property_id"]}))
    refusal = refused(lambda: run(staff.create_staff(staff.StaffIn(
        name="Ghost", email="ghost@tinto.example.com", password="a-good-long-password",
        role="waiter", domains=["restaurant"]), admin)))
    assert refusal.status_code == 400
