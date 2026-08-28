"""Hiring somebody with an email, a phone number, or both — and never with neither.

Direct coroutine calls against a real (file-backed) mock database, the same style as
test_isolation.py: the endpoint is an ordinary coroutine and the admin is an ordinary
dict, so what is exercised is the real router with only the transport absent.

The rule this file exists for is the either/or. Neither field is individually mandatory,
and an account holding neither can never sign in — so it is refused with a 400 that says
so, rather than stored as a row nobody will ever be able to use and nobody will think to
look at until the waiter is standing at the till.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import db as db_module
import security
from mock_db import MockDatabase
from routers import staff
from services.identity import PHONE_SHAPE

PHONE_PLAIN = "9876543210"
PHONE_TRUNK = "09876543210"
PHONE_PRETTY = "+91 98765 43210"
PHONE_STORED = "+919876543210"

PASSWORD = "a decent shift password"


def run(coro):
    return asyncio.run(coro)


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        run(fn(**kwargs))
    return exc.value


@pytest.fixture
def world(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in (db_module, security, staff):
        monkeypatch.setattr(module, "unscoped_db", handle)

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one({
        "id": "p1", "name": "The Grand", "status": "live", "property_type": "both",
        "created_at": now}))
    admin = {"id": "u-admin", "email": "owner@grand.example.com", "phone": None,
             "name": "Alex Mercer", "role": "admin",
             "domains": ["hotel", "restaurant", "bar"],
             "permissions": [], "active": True, "property_id": "p1",
             "password_hash": "x", "created_at": now}
    run(handle.users.insert_one(admin))
    return handle, admin


def hire(world, **overrides):
    _, admin = world
    body = {"name": "Riley Cole", "password": PASSWORD, "role": "waiter",
            "domains": ["bar"]}
    body.update(overrides)
    return run(staff.create_staff(staff.StaffIn(**body), user=admin))


def hire_refused(world, **overrides) -> HTTPException:
    _, admin = world
    body = {"name": "Riley Cole", "password": PASSWORD, "role": "waiter",
            "domains": ["bar"]}
    body.update(overrides)
    return refused(staff.create_staff, payload=staff.StaffIn(**body), user=admin)


# --------------------------- neither is the one refusal ---------------------------
def test_an_account_with_neither_identifier_is_refused(world):
    """The point of the whole change. Not a 422 about a missing field — nothing is
    missing, the request is well formed; what is wrong is that the resulting account
    could never be signed into."""
    problem = hire_refused(world)
    assert problem.status_code == 400
    assert "email" in problem.detail.lower() and "phone" in problem.detail.lower()


def test_neither_identifier_stores_nothing_at_all(world):
    handle, _ = world
    before = run(handle.users.count_documents({}))
    hire_refused(world)
    assert run(handle.users.count_documents({})) == before


def test_blank_strings_count_as_neither(world):
    """`""` is not an identifier. An owner who tabs through both fields must get the
    same refusal as one who omits them, or the record is stored holding two empties that
    then collide with the next person who does the same."""
    assert hire_refused(world, phone="   ").status_code == 400


# ------------------------------ either one is enough ------------------------------
def test_a_waiter_is_hired_with_a_phone_and_no_email(world):
    created = hire(world, phone=PHONE_PLAIN)
    assert created["phone"] == PHONE_STORED
    assert created["email"] is None


def test_a_waiter_is_still_hired_with_an_email_and_no_phone(world):
    created = hire(world, email="riley@grand.example.com")
    assert created["email"] == "riley@grand.example.com"
    assert created["phone"] is None


def test_both_together_are_accepted(world):
    created = hire(world, email="riley@grand.example.com", phone=PHONE_TRUNK)
    assert created["email"] == "riley@grand.example.com"
    assert created["phone"] == PHONE_STORED


@pytest.mark.parametrize("typed", [PHONE_PLAIN, PHONE_TRUNK, PHONE_PRETTY])
def test_however_the_number_is_typed_one_form_is_stored(world, typed):
    """Normalised on write as well as on lookup. Storing what was typed would mean the
    sign-in box could only ever find the account if the same spelling was used twice."""
    assert hire(world, phone=typed)["phone"] == PHONE_STORED


def test_an_email_is_stored_lowercased_and_trimmed_as_before(world):
    assert hire(world, email="  Riley@Grand.Example  ")["email"] == "riley@grand.example"


# -------------------------------- a number that is not one --------------------------------
@pytest.mark.parametrize("bad", ["12345", "1234567890", "not a phone", "+14155550123"])
def test_a_number_that_is_not_an_indian_mobile_is_refused_by_shape(world, bad):
    """Refused rather than stored as typed. `1234567890` is what somebody enters to get
    past a field they do not want to fill in — the fake-email problem in its new
    spelling — and an account behind it is one nobody can ever sign into."""
    problem = hire_refused(world, phone=bad)
    assert problem.status_code == 400
    assert PHONE_SHAPE in problem.detail


def test_a_bad_number_is_refused_even_when_an_email_was_given(world):
    """Silently dropping it would tell the owner they had recorded a number when they
    had not, and there is no other screen that shows them otherwise."""
    problem = hire_refused(world, email="riley@grand.example.com", phone="12345")
    assert problem.status_code == 400


# ------------------------------- uniqueness, both ways -------------------------------
def test_two_accounts_cannot_share_a_phone_number(world):
    hire(world, phone=PHONE_PLAIN)
    clash = hire_refused(world, name="Sam Ash", phone=PHONE_PLAIN)
    assert clash.status_code == 409
    assert "phone" in clash.detail.lower()


def test_the_duplicate_is_caught_however_the_second_person_typed_it(world):
    """The comparison is on the canonical form, so a clash cannot be walked past by
    typing the same number a different way."""
    hire(world, phone=PHONE_PLAIN)
    assert hire_refused(world, name="Sam Ash", phone=PHONE_PRETTY).status_code == 409
    assert hire_refused(world, name="Sam Ash", phone=PHONE_TRUNK).status_code == 409


def test_two_accounts_still_cannot_share_an_email(world):
    hire(world, email="riley@grand.example.com")
    clash = hire_refused(world, name="Sam Ash", email="Riley@Grand.Example.com")
    assert clash.status_code == 409


def test_a_second_account_with_no_phone_is_not_a_duplicate_of_the_first(world):
    """The trap in making a field optional: two accounts both holding nothing must not
    read as two accounts holding the same thing."""
    hire(world, email="riley@grand.example.com")
    second = hire(world, name="Sam Ash", email="sam@grand.example.com")
    assert second["phone"] is None


def test_a_second_account_with_no_email_is_not_a_duplicate_either(world):
    hire(world, phone=PHONE_PLAIN)
    second = hire(world, name="Sam Ash", phone="9876543211")
    assert second["email"] is None


def test_a_phone_cannot_be_taken_by_an_account_that_holds_it_as_an_email(world):
    """Uniqueness is across both fields, not within each. Nothing can produce this today
    — a canonical number starts `+91` and an address needs an `@` — but the check costs
    one query and the day either format loosens is not the day to discover it."""
    handle, _ = world
    run(handle.users.update_one({"id": "u-admin"}, {"$set": {"email": PHONE_STORED}}))
    assert hire_refused(world, phone=PHONE_PLAIN).status_code == 409


# ---------------------------------- the roster ----------------------------------
def test_the_roster_shows_the_phone_beside_the_email(world):
    _, admin = world
    hire(world, phone=PHONE_PLAIN)
    rows = run(staff.list_staff(user=admin))
    riley = next(r for r in rows if r["name"] == "Riley Cole")
    assert riley["phone"] == PHONE_STORED
    assert riley["email"] is None


def test_the_roster_never_leaks_a_password_hash(world):
    _, admin = world
    hire(world, phone=PHONE_PLAIN)
    assert all("password_hash" not in row for row in run(staff.list_staff(user=admin)))


def test_an_existing_record_with_no_phone_key_reads_as_having_none(world):
    """Every account that predates this change is stored without the field. It has to
    read as `None` rather than raise, which is what makes a document migration
    unnecessary — see the report."""
    handle, admin = world
    run(handle.users.insert_one({
        "id": "u-old", "email": "old@grand.example.com", "name": "Old Timer",
        "role": "waiter", "domains": ["bar"], "permissions": ["outlet.pos"],
        "active": True, "property_id": "p1", "password_hash": "x"}))
    old = next(r for r in run(staff.list_staff(user=admin)) if r["id"] == "u-old")
    assert old["phone"] is None
