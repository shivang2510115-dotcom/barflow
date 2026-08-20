"""How many passwords may be tried against this platform, and by whom.

`POST /auth/login` had no limit of any kind. Against the previous code, 500 wrong
passwords in a row against one account from one address returned five hundred 401s and
nothing else — the whole platform's accounts, every hotel's, guessable at whatever rate
the network allowed.

Direct coroutine calls, the same style as test_tenancy.py. The stored hashes are bcrypt
at cost 4 rather than the default so that a test which deliberately fails a hundred
times finishes in under a second; nothing here depends on the cost.
"""
import asyncio
import time
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
OWNER = "owner@grand.example.com"


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
    auth.LOGIN_FAILURES_PER_ADDRESS.reset()
    auth.LOGIN_FAILURES_PER_EMAIL.reset()

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))
    for n, email in enumerate([OWNER, "desk@grand.example.com", "chef@grand.example.com"]):
        run(handle.users.insert_one(
            {"id": f"u{n}", "email": email, "name": "Somebody", "role": "admin",
             "domains": ["bar"], "permissions": [], "active": True, "property_id": "p1",
             "password_hash": cheap_hash(PASSWORD), "created_at": now}))
    return handle


def attempt(email=OWNER, password="wrong", ip="203.0.113.7"):
    """The status code this attempt gets: 200, 401 or 429."""
    try:
        run(auth.login(auth.LoginIn(email=email, password=password), FakeRequest(ip)))
        return 200
    except HTTPException as exc:
        return exc.status_code


def guess(times, email=OWNER, ip="203.0.113.7"):
    return [attempt(email, f"guess-{n}", ip) for n in range(times)]


# ------------------------------- the hole itself -------------------------------
def test_guessing_at_one_account_stops(world):
    limit = auth.LOGIN_FAILURES_PER_EMAIL.limit
    assert set(guess(limit)) == {401}
    assert attempt() == 429


def test_the_right_password_is_refused_while_the_account_is_throttled(world):
    """The uncomfortable half, asserted rather than left implicit: this is a throttle on
    the *door*, so it holds the real owner out too for the length of the window. A
    throttle that let the correct password through would not be one — the guesser would
    simply keep guessing and be told, by the one answer that was not 429, when they had
    found it."""
    guess(auth.LOGIN_FAILURES_PER_EMAIL.limit)
    assert attempt(password=PASSWORD) == 429


def test_the_window_lifts_by_itself(world):
    """Nothing is disabled and nobody has to unlock anything — which is the difference
    between a throttle and the account lockouts that generate support tickets."""
    guess(auth.LOGIN_FAILURES_PER_EMAIL.limit)
    assert attempt(password=PASSWORD) == 429

    # The same failures, aged past the window rather than waiting fifteen minutes.
    expired = time.time() - auth.LOGIN_FAILURES_PER_EMAIL.window_seconds - 1
    auth.LOGIN_FAILURES_PER_EMAIL.reset()
    for _ in range(auth.LOGIN_FAILURES_PER_EMAIL.limit * 2):
        auth.LOGIN_FAILURES_PER_EMAIL.record(OWNER, expired)
    assert attempt(password=PASSWORD) == 200


# ------------------------------- per address -------------------------------
def test_one_address_cannot_spray_one_password_across_every_account(world):
    """The attack the per-account limit cannot see: each account is tried once, so no
    account's own counter ever rises."""
    for _ in range(auth.LOGIN_FAILURES_PER_ADDRESS.limit):
        auth.LOGIN_FAILURES_PER_ADDRESS.record("198.51.100.9")
    assert attempt(email="desk@grand.example.com", ip="198.51.100.9") == 429


def test_another_address_is_unaffected(world):
    for n in range(auth.LOGIN_FAILURES_PER_ADDRESS.limit):
        auth.LOGIN_FAILURES_PER_ADDRESS.record("198.51.100.9")
    assert attempt(password=PASSWORD, ip="192.0.2.55") == 200


# ------------------------------- counting rules -------------------------------
def test_a_successful_login_does_not_count(world):
    """A hotel's staff all sign in within a minute of each other at the start of service.
    Counting successes would throttle the thing the door is for."""
    for _ in range(auth.LOGIN_FAILURES_PER_EMAIL.limit * 3):
        assert attempt(password=PASSWORD) == 200


def test_signing_in_clears_that_account_and_not_the_address(world):
    """The typo-then-correct case, without handing an attacker a reset button: clearing
    the email needs the password to that email, which is what they are looking for."""
    guess(auth.LOGIN_FAILURES_PER_EMAIL.limit - 1)
    assert attempt(password=PASSWORD) == 200
    assert guess(auth.LOGIN_FAILURES_PER_EMAIL.limit - 1) == [401] * (
        auth.LOGIN_FAILURES_PER_EMAIL.limit - 1)
    # The address, meanwhile, has been counting all along.
    assert auth.LOGIN_FAILURES_PER_ADDRESS.blocked("203.0.113.7") is False
    for _ in range(auth.LOGIN_FAILURES_PER_ADDRESS.limit):
        auth.LOGIN_FAILURES_PER_ADDRESS.record("203.0.113.7")
    assert attempt(password=PASSWORD, email="chef@grand.example.com") == 429


def test_a_deactivated_account_is_throttled_like_a_wrong_password(world):
    """It gets the same 401 so that the two cannot be told apart. An unthrottled path
    among them would tell them apart anyway, by which one starts answering 429."""
    run(world.users.update_one({"email": OWNER}, {"$set": {"active": False}}))
    limit = auth.LOGIN_FAILURES_PER_EMAIL.limit
    assert set(attempt(password=PASSWORD) for _ in range(limit)) == {401}
    assert attempt(password=PASSWORD) == 429


def test_a_suspended_hotel_is_throttled_like_a_wrong_password(world):
    run(world.properties.update_one({"id": "p1"}, {"$set": {"status": "suspended"}}))
    limit = auth.LOGIN_FAILURES_PER_EMAIL.limit
    assert set(attempt(password=PASSWORD) for _ in range(limit)) == {401}
    assert attempt(password=PASSWORD) == 429


def test_an_unknown_email_is_counted_too(world):
    """Otherwise enumeration is free: guess an address, and whether it counts against you
    tells you whether it exists."""
    limit = auth.LOGIN_FAILURES_PER_EMAIL.limit
    assert set(guess(limit, email="nobody@example.com")) == {401}
    assert attempt(email="nobody@example.com") == 429


def test_the_refusal_does_not_say_which_limit_stopped_you(world):
    guess(auth.LOGIN_FAILURES_PER_EMAIL.limit)
    with pytest.raises(HTTPException) as exc:
        run(auth.login(auth.LoginIn(email=OWNER, password=PASSWORD), FakeRequest()))
    assert exc.value.detail == auth.TOO_MANY_ATTEMPTS
    assert "email" not in exc.value.detail.lower()
