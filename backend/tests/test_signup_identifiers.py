"""Registering a property: the founding admin is identified the same way everyone else is.

The account POST /api/signup creates is a staff account like any other, so the either/or
rule and the canonical phone form are the same ones POST /api/staff applies — they come
from the same function, deliberately, so the two routes cannot drift into storing one
number two ways.

The half that must not move is email. Every existing caller of this endpoint sends
`admin_email` and nothing else, and each of them has to behave exactly as it did.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import db as db_module
import security
from mock_db import MockDatabase
from routers import auth, signup, staff
from services.access import PENDING

PHONE_PLAIN = "9876543210"
PHONE_PRETTY = "+91 98765 43210"
PHONE_STORED = "+919876543210"
PASSWORD = "the blue kettle in room 12"


def run(coro):
    return asyncio.run(coro)


class FakeRequest:
    def __init__(self, ip="203.0.113.7"):
        self.client = type("C", (), {"host": ip})()
        self.cookies = {}
        self.headers = Headers({})


@pytest.fixture
def world(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in (db_module, security, staff, signup, auth):
        monkeypatch.setattr(module, "unscoped_db", handle)
    run(signup.SIGNUPS_PER_ADDRESS.reset())
    return handle


def register(**overrides):
    body = {"hotel_name": "The Grand", "city": "Jaipur", "admin_name": "Alex Mercer",
            "admin_password": PASSWORD}
    body.update(overrides)
    return run(signup.signup(signup.SignupIn(**body), FakeRequest()))


def register_refused(**overrides) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        register(**overrides)
    return exc.value


def founder(handle, property_id) -> dict:
    rows = run(handle.users.find({}, {"_id": 0}).to_list(100))
    return next(u for u in rows if u.get("property_id") == property_id)


# --------------------------- email works exactly as before ---------------------------
def test_registering_with_an_email_is_unchanged(world):
    result = register(admin_email="owner@grand.example.com")
    assert result["status"] == PENDING
    assert result["property_id"]

    admin = founder(world, result["property_id"])
    assert admin["email"] == "owner@grand.example.com"
    assert admin["role"] == "admin"
    assert admin["active"] is True


def test_the_property_record_still_carries_the_owners_email(world):
    result = register(admin_email="Owner@Grand.Example.com")
    record = run(world.properties.find_one({"id": result["property_id"]}))
    assert record["email"] == "owner@grand.example.com"


def test_an_email_already_in_use_is_still_refused(world):
    register(admin_email="owner@grand.example.com")
    clash = register_refused(admin_email="owner@grand.example.com")
    assert clash.status_code == 409


# ------------------------------ and a phone now works too ------------------------------
def test_a_property_can_be_registered_with_a_phone_and_no_email(world):
    """An owner setting up from a phone at the end of service has the same problem their
    waiters do. Refusing them an account for want of an address they do not use would
    make signup the one door still demanding the field this change exists to stop
    demanding."""
    result = register(admin_phone=PHONE_PRETTY)
    admin = founder(world, result["property_id"])
    assert admin["phone"] == PHONE_STORED
    assert admin["email"] is None


def test_the_founding_admin_can_then_sign_in_by_phone(world):
    """End to end through the two routes that matter: the account signup created is one
    the login door can find, by the number as typed in any of its spellings."""
    register(admin_phone=PHONE_PLAIN)
    result = run(auth.login(
        auth.LoginIn(identifier=PHONE_PRETTY, password=PASSWORD), FakeRequest()))
    assert result["user"]["role"] == "admin"
    assert result["user"]["phone"] == PHONE_STORED


def test_a_phone_only_property_is_left_without_a_contact_email(world):
    """Stated rather than papered over. `properties.email` is the address the platform
    would write to, and a property registered by phone has none — so the field is blank
    and the property screen asks for it again, exactly as it does for a GSTIN that was
    in a drawer at signup time."""
    result = register(admin_phone=PHONE_PLAIN)
    record = run(world.properties.find_one({"id": result["property_id"]}))
    assert record["email"] == ""


def test_both_identifiers_together_are_accepted(world):
    result = register(admin_email="owner@grand.example.com", admin_phone=PHONE_PLAIN)
    admin = founder(world, result["property_id"])
    assert admin["email"] == "owner@grand.example.com"
    assert admin["phone"] == PHONE_STORED


# --------------------------------- neither, and clashes ---------------------------------
def test_registering_with_neither_identifier_is_refused(world):
    problem = register_refused()
    assert problem.status_code == 400
    assert "email" in problem.detail.lower() and "phone" in problem.detail.lower()


def test_a_refused_registration_leaves_no_property_behind(world):
    """Two writes and no transaction. A property with no admin can never be logged into
    and never deleted through the app — so the identifier rule is applied before either
    write, not between them."""
    register_refused()
    assert run(world.properties.count_documents({})) == 0
    assert run(world.users.count_documents({})) == 0


def test_a_phone_already_in_use_is_refused(world):
    register(admin_phone=PHONE_PLAIN)
    clash = register_refused(admin_phone=PHONE_PRETTY, hotel_name="The Other Grand")
    assert clash.status_code == 409
    assert "phone" in clash.detail.lower()


def test_a_number_that_is_not_a_mobile_is_refused_by_shape(world):
    assert register_refused(admin_phone="1234567890").status_code == 400


def test_an_owner_cannot_take_a_number_a_waiter_already_has(world):
    """Uniqueness is global and crosses the two routes: signup and the staff screen read
    the same collection, so a number hired into one property is not free in another."""
    first = register(admin_email="owner@grand.example.com")
    admin = founder(world, first["property_id"])
    run(staff.create_staff(staff.StaffIn(
        name="Riley Cole", phone=PHONE_PLAIN, password=PASSWORD, role="waiter",
        domains=["bar"]), user=admin))
    assert register_refused(admin_phone=PHONE_PRETTY,
                            hotel_name="The Other Grand").status_code == 409


def test_the_password_rule_still_reads_the_email_when_there_is_one(world):
    assert register_refused(admin_email="thegrand@grand.example.com",
                            admin_password="thegrand").status_code == 400


def test_the_password_rule_survives_having_no_email_to_check_against(world):
    """`password_problem(pw, None)` is the supported call — the strength rule still
    applies, only the "not your own address" clause has nothing to compare to."""
    assert register_refused(admin_phone=PHONE_PLAIN,
                            admin_password="admin123").status_code == 400
    assert register(admin_phone=PHONE_PLAIN)["status"] == PENDING
