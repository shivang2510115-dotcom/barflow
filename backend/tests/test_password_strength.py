"""What this application will let a password be, and where that is enforced.

The rule was eight characters. Against the previous code, POST /api/staff accepted
`password`, `12345678`, `qwerty12` and `iloveyou` — all four with HTTP 200 — which means
every hotel on the platform could have an account whose password is the first guess in
any dictionary.

Two halves here: the rule itself, and the three doors that have to apply it. The second
half is the one that rots — a rule with three call sites grows a fourth that forgets it —
so each door is exercised rather than assumed.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import db as db_module
import security
from mock_db import MockDatabase
from routers import signup, staff
from services.password import (
    COMMON_PASSWORDS, MIN_PASSWORD_LENGTH, password_problem)

GOOD = "harbour-lamp-4127"


def run(coro):
    return asyncio.run(coro)


# ------------------------------- the rule -------------------------------
def test_a_reasonable_password_is_accepted():
    assert password_problem(GOOD) is None


@pytest.mark.parametrize("weak", ["password", "12345678", "qwerty12", "iloveyou"])
def test_the_four_that_used_to_be_accepted(weak):
    """The exploit, as a rule rather than as a request."""
    assert password_problem(weak) is not None


def test_short_is_still_short():
    assert password_problem("a" * (MIN_PASSWORD_LENGTH - 1)) is not None
    assert password_problem("") is not None


def test_case_and_padding_do_not_smuggle_a_common_password_through():
    assert password_problem("PASSWORD") is not None
    assert password_problem("Password") is not None
    assert password_problem("  password  ") is not None


def test_the_repos_own_published_logins_are_refused():
    """A password printed in a public git history is not a password."""
    for published in ("admin123", "manager123", "waiter123", "kitchen123", "barflow123"):
        assert password_problem(published) is not None


def test_one_character_repeated_is_not_a_password():
    assert password_problem("aaaaaaaaaa") is not None
    assert password_problem("1111111111") is not None


def test_your_own_email_is_not_a_password():
    assert password_problem("nina.patel", "nina.patel@grand.example.com") is not None
    assert password_problem("nina.patel@grand.example.com",
                            "nina.patel@grand.example.com") is not None
    # And the check is only ever made when an email is given.
    assert password_problem("nina.patel") is None


def test_a_long_ordinary_passphrase_is_not_refused_for_lacking_symbols():
    """No composition rule, on purpose: they produce `Password1!` and a note on the
    monitor, not entropy."""
    for fine in ("correct horse battery", "the blue kettle in room 12",
                 "manchester tuesday rain"):
        assert password_problem(fine) is None


def test_the_list_is_lowercase_so_the_comparison_can_be():
    assert all(entry == entry.lower() for entry in COMMON_PASSWORDS)


# ------------------------------- the three doors -------------------------------
@pytest.fixture
def world(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.setattr(security, "unscoped_db", handle)
    monkeypatch.setattr(staff, "unscoped_db", handle)
    monkeypatch.setattr(signup, "unscoped_db", handle)
    signup.SIGNUPS_PER_ADDRESS.reset()

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))
    admin = {"id": "a1", "email": "owner@grand.example.com", "name": "Owner",
             "role": "admin", "domains": ["hotel", "restaurant", "bar"],
             "permissions": ["admin.staff"], "active": True, "property_id": "p1",
             "password_hash": "x", "created_at": now}
    run(handle.users.insert_one(dict(admin)))
    return handle, admin


class FakeRequest:
    def __init__(self, ip="203.0.113.7"):
        self.client = type("C", (), {"host": ip})()
        self.cookies = {}
        self.headers = Headers({})


def refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


def new_staff(world, password, email="new@grand.example.com"):
    _handle, admin = world
    return run(staff.create_staff(staff.StaffIn(
        name="New Person", email=email, password=password, role="waiter",
        domains=["bar"]), admin))


def test_hiring_somebody_applies_the_rule(world):
    assert refused(lambda: new_staff(world, "password")).status_code == 400
    assert new_staff(world, GOOD)["email"] == "new@grand.example.com"


def test_resetting_a_password_applies_the_rule(world):
    handle, admin = world
    made = new_staff(world, GOOD)
    assert refused(lambda: run(staff.reset_password(
        made["id"], staff.PasswordIn(password="letmein123"), admin))).status_code == 400
    assert run(staff.reset_password(
        made["id"], staff.PasswordIn(password="another-good-one-88"), admin)) == {"ok": True}
    # And the weak one really was not stored.
    row = run(handle.users.find_one({"id": made["id"]}, {"_id": 0}))
    assert security.verify_password("another-good-one-88", row["password_hash"])


def test_a_reset_is_checked_against_the_account_it_belongs_to(world):
    """Not against the admin doing the resetting — it is the target who will type it."""
    _handle, admin = world
    made = new_staff(world, GOOD, email="riley@grand.example.com")
    assert refused(lambda: run(staff.reset_password(
        made["id"], staff.PasswordIn(password="riley"), admin))).status_code == 400


def test_signing_a_hotel_up_applies_the_rule(world):
    def sign_up(password, email="new-hotel@example.com"):
        return run(signup.signup(signup.SignupIn(
            hotel_name="The Second Grand", city="Jaipur", gstin="",
            admin_name="Owner", admin_email=email, admin_password=password),
            FakeRequest()))

    assert refused(lambda: sign_up("welcome1")).status_code == 400
    assert sign_up(GOOD)["status"] == "pending"


def test_an_existing_weak_password_still_signs_in(world, monkeypatch):
    """The rule runs where a password is *set* and nowhere else. Tightening it must not
    lock a hotel out of its own system mid-service, so the owner whose password is
    literally `password` keeps working until they change it."""
    from routers import auth
    handle, _admin = world
    monkeypatch.setattr(auth, "unscoped_db", handle)
    auth.LOGIN_FAILURES_PER_ADDRESS.reset()
    auth.LOGIN_FAILURES_PER_IDENTIFIER.reset()
    run(handle.users.update_one({"id": "a1"}, {"$set": {
        "password_hash": security.hash_password("password")}}))

    signed_in = run(auth.login(
        auth.LoginIn(email="owner@grand.example.com", password="password"),
        FakeRequest()))
    assert signed_in["token"]
