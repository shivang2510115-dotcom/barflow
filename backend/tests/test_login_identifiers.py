"""Signing in with either identifier: an email address, a phone number, or both.

Direct coroutine calls, the same style as test_login_rate_limit.py — the endpoint is an
ordinary coroutine and the database is a real (file-backed) mock, so what is exercised is
the real router with only the transport absent. Hashes are bcrypt at cost 4 so that a
test which deliberately fails a dozen times finishes in under a second.

The line this file guards hardest is the one that is easy to break and invisible when it
is broken: a deactivated phone-only account and a wrong password must be **byte-identical
401s**. The moment they differ, a former employee can tell that their guess at the number
was right.
"""
import asyncio
from datetime import datetime, timezone

import bcrypt
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import db as db_module
import security
from mock_db import MockDatabase
from routers import auth

PASSWORD = "correct horse battery"

# One person, three spellings. The waiter recites the first, the owner's contact card
# holds the third, and the trunk-prefixed second is how it is written on a rota.
PHONE_PLAIN = "9876543210"
PHONE_TRUNK = "09876543210"
PHONE_PRETTY = "+91 98765 43210"
PHONE_STORED = "+919876543210"

EMAIL = "owner@grand.example.com"


def run(coro):
    return asyncio.run(coro)


class Client:
    def __init__(self, host):
        self.host = host


class FakeRequest:
    def __init__(self, ip: str = "203.0.113.7"):
        self.client = Client(ip)
        self.cookies = {}
        self.headers = Headers({})


def cheap_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def world(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.setattr(security, "unscoped_db", handle)
    monkeypatch.setattr(auth, "unscoped_db", handle)
    run(auth.LOGIN_FAILURES_PER_ADDRESS.reset())
    run(auth.LOGIN_FAILURES_PER_IDENTIFIER.reset())

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))

    def person(uid, name, email, phone):
        run(handle.users.insert_one({
            "id": uid, "email": email, "phone": phone, "name": name, "role": "waiter",
            "domains": ["bar"], "permissions": ["outlet.pos"], "active": True,
            "property_id": "p1", "password_hash": cheap_hash(PASSWORD),
            "created_at": now}))

    # The waiter this whole change exists for: a phone and no email at all.
    person("u-phone", "Riley Cole", None, PHONE_STORED)
    # And the owner, who has always had an email and must keep signing in unchanged.
    person("u-email", "Alex Mercer", EMAIL, None)
    return handle


def attempt(identifier, password=PASSWORD, ip="203.0.113.7", field="identifier"):
    """The result of one sign-in: the response dict, or the HTTPException raised."""
    try:
        return run(auth.login(auth.LoginIn(**{field: identifier, "password": password}),
                              FakeRequest(ip)))
    except HTTPException as exc:
        return exc


def status(*args, **kwargs):
    result = attempt(*args, **kwargs)
    return result.status_code if isinstance(result, HTTPException) else 200


# ------------------------- either identifier gets you in -------------------------
def test_a_phone_only_account_can_sign_in(world):
    """The whole point. Before this, this account could not exist."""
    result = attempt(PHONE_PLAIN)
    assert not isinstance(result, HTTPException), getattr(result, "detail", result)
    assert result["user"]["id"] == "u-phone"
    assert result["user"]["phone"] == PHONE_STORED
    assert result["user"]["email"] is None
    assert result["token"]


def test_an_email_only_account_still_signs_in_exactly_as_before(world):
    result = attempt(EMAIL)
    assert not isinstance(result, HTTPException), getattr(result, "detail", result)
    assert result["user"]["id"] == "u-email"


def test_the_same_number_in_three_formats_reaches_one_account(world):
    """The failure this prevents is the quiet one: the owner types the number with a
    `+91` at the sign-in box after having typed it plain on the staff screen, and is
    refused by their own record."""
    reached = set()
    for typed in (PHONE_PLAIN, PHONE_TRUNK, PHONE_PRETTY, " 98765 43210 "):
        result = attempt(typed)
        assert not isinstance(result, HTTPException), f"{typed}: {result.detail}"
        reached.add(result["user"]["id"])
    assert reached == {"u-phone"}


def test_an_email_is_still_matched_case_insensitively(world):
    assert status(EMAIL.upper()) == 200


# --------------------- the field name, and what still sends it ---------------------
def test_the_old_email_field_name_is_still_accepted(world):
    """`installDemo.js`, every integration test and any deployed client posts
    `{"email": ...}`. The field is named `identifier` now because it is one of two
    things, and `email` is kept as an alias so that not one of those callers breaks."""
    assert status(EMAIL, field="email") == 200
    # And the alias carries a phone number perfectly well, which is ugly to read and is
    # exactly why the canonical name is the honest one.
    assert status(PHONE_PLAIN, field="email") == 200


def test_the_new_field_name_takes_both_kinds(world):
    assert status(EMAIL, field="identifier") == 200
    assert status(PHONE_PLAIN, field="identifier") == 200


# ----------------------------- every refusal is one refusal -----------------------------
def test_a_wrong_password_and_an_unknown_number_are_indistinguishable(world):
    wrong = attempt(PHONE_PLAIN, password="not-the-password")
    unknown = attempt("9000000001")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.detail == unknown.detail


def test_a_deactivated_phone_only_user_gets_the_same_401_as_a_wrong_password(world):
    """Named in the brief and worth the emphasis: a distinct 'account disabled' would
    confirm to a former employee that their guess at the number was right."""
    run(world.users.update_one({"id": "u-phone"}, {"$set": {"active": False}}))
    disabled = attempt(PHONE_PLAIN)
    wrong = attempt(PHONE_PLAIN, password="not-the-password")
    assert disabled.status_code == wrong.status_code == 401
    assert disabled.detail == wrong.detail


def test_a_suspended_property_refuses_a_phone_login_the_same_way(world):
    run(world.properties.update_one({"id": "p1"}, {"$set": {"status": "suspended"}}))
    suspended = attempt(PHONE_PLAIN)
    wrong = attempt("9000000001", password="whatever")
    assert suspended.status_code == wrong.status_code == 401
    assert suspended.detail == wrong.detail


def test_a_deactivated_email_user_is_still_refused_identically(world):
    run(world.users.update_one({"id": "u-email"}, {"$set": {"active": False}}))
    disabled = attempt(EMAIL)
    wrong = attempt(EMAIL, password="not-the-password")
    assert disabled.detail == wrong.detail == attempt(PHONE_PLAIN, password="x").detail


def test_the_refusal_does_not_name_only_email(world):
    """The message a phone-only waiter reads has to mention the thing they typed."""
    assert "phone" in attempt("9000000001").detail.lower()


def test_an_unreadable_identifier_is_refused_like_any_other_wrong_guess(world):
    """Not a 422. A malformed identifier answered differently from a well-formed unknown
    one would tell a guesser which of their attempts were even worth making."""
    for nonsense in ("", "   ", "not a phone", "12345", "@@@"):
        assert status(nonsense, password="whatever") == 401


def test_a_blank_identifier_never_matches_an_account_without_one(world):
    """An account stored with `email: None` must not be reachable by sending nothing —
    the filter has to miss, not match a null against a blank."""
    result = attempt("", password=PASSWORD)
    assert isinstance(result, HTTPException) and result.status_code == 401


# ------------------------------- the rate-limit bucket -------------------------------
def test_three_spellings_of_one_number_share_one_allowance(world):
    """The bug this is here for: a per-identifier bucket keyed on the raw text hands a
    guesser ten fresh tries for the dash and ten more for the `+91`."""
    limit = auth.LOGIN_FAILURES_PER_IDENTIFIER.limit
    spellings = [PHONE_PLAIN, PHONE_TRUNK, PHONE_PRETTY]
    for n in range(limit):
        assert status(spellings[n % 3], password=f"guess-{n}") == 401
    # The allowance is spent whichever spelling asks next.
    for typed in spellings:
        assert status(typed, password=PASSWORD) == 429


def test_a_different_number_keeps_its_own_allowance(world):
    limit = auth.LOGIN_FAILURES_PER_IDENTIFIER.limit
    for n in range(limit):
        assert status(PHONE_PLAIN, password=f"guess-{n}") == 401
    assert status(EMAIL) == 200


def test_signing_in_by_phone_clears_that_number_and_not_the_address(world):
    limit = auth.LOGIN_FAILURES_PER_IDENTIFIER.limit
    for n in range(limit - 1):
        assert status(PHONE_TRUNK, password=f"guess-{n}") == 401
    # The correct password, typed in a third spelling, clears the same bucket.
    assert status(PHONE_PRETTY) == 200
    assert status(PHONE_PLAIN, password="wrong") == 401
