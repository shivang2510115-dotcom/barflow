"""Tenancy at the edges of the pure predicate: the migration, the one dependency
that resolves a property, and the door.

`services/access.py` decides *whether* a caller may pass. These tests cover the two
places that feed it: the startup migration that gives every existing user a hotel, and
`require_access`, the application's only authorization dependency — the single call site
that has to look the property up. No server here; the dependency's checker is an ordinary
coroutine and is called as one.
"""
import asyncio

import pytest
from fastapi import HTTPException

import routers.auth as auth
import security
from migrations import backfill_property
from mock_db import MockDatabase
from security import NOT_PERMITTED, PENDING_APPROVAL, require_access
from services.access import LIVE, PENDING, PLATFORM_ADMIN, SHARED, SUSPENDED


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real mock database on a throwaway file, bound into every module under test.

    The three modules each hold their own `from db import db`, so the patch has to name
    all three; a missed one would quietly read the developer's own db.json.
    """
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(backfill_property, "db", handle)
    monkeypatch.setattr(security, "db", handle)
    monkeypatch.setattr(auth, "db", handle)
    return handle


def add_user(db, id="u1", role="admin", **extra):
    doc = {"id": id, "email": f"{id}@barflow.io", "name": id, "role": role,
           "domains": ["hotel", "restaurant", "bar"], "active": True}
    doc.update(extra)
    run(db.users.insert_one(doc))
    return doc


def add_property(db, status=LIVE, id="p1"):
    doc = {"id": id, "name": "The Grand", "status": status}
    run(db.properties.insert_one(doc))
    return doc


def properties(db):
    return run(db.properties.find({}, {"_id": 0}).to_list(100))


def user_row(db, id):
    return run(db.users.find_one({"id": id}, {"_id": 0}))


# --------------------------- the startup migration ---------------------------

def test_the_migration_makes_the_existing_hotel_the_first_tenant(db):
    add_user(db, "u1")
    add_user(db, "u2", role="waiter")

    created, property_id, stamped, current = run(backfill_property.backfill())

    assert created is True
    rows = properties(db)
    assert len(rows) == 1
    # Live, not pending: this hotel has been operating for months. Landing it in the
    # state that refuses bookings would take a working install off the air on deploy.
    assert rows[0]["status"] == LIVE
    assert rows[0]["id"] == property_id
    assert stamped == 2 and current == 0
    assert user_row(db, "u1")["property_id"] == property_id
    assert user_row(db, "u2")["property_id"] == property_id


def test_the_second_run_changes_nothing(db):
    add_user(db, "u1")
    _, first_id, _, _ = run(backfill_property.backfill())

    created, property_id, stamped, current = run(backfill_property.backfill())

    assert created is False
    assert property_id == first_id
    assert stamped == 0 and current == 1
    assert len(properties(db)) == 1


def test_the_migration_leaves_an_already_stamped_user_alone(db):
    add_property(db, id="p1")
    add_user(db, "u1", property_id="other")

    created, property_id, stamped, current = run(backfill_property.backfill())

    assert created is False and property_id == "p1"
    assert stamped == 0 and current == 1
    assert user_row(db, "u1")["property_id"] == "other"


def test_the_platform_operator_is_left_without_a_hotel(db):
    add_user(db, "op", role=PLATFORM_ADMIN)
    add_user(db, "u1")

    _, property_id, stamped, _ = run(backfill_property.backfill())

    assert stamped == 1
    # Stamping the operator would hand the one login that approves hotels a seat inside
    # one of them, which is exactly what "belongs to no hotel" exists to prevent.
    assert not user_row(db, "op").get("property_id")
    assert user_row(db, "u1")["property_id"] == property_id


def test_the_migration_refuses_to_guess_between_several_hotels(db):
    add_property(db, id="p1")
    add_property(db, id="p2")
    add_user(db, "u1")

    created, property_id, stamped, current = run(backfill_property.backfill())

    assert created is False and property_id is None
    # Guessing would hand one hotel's login the other hotel's data.
    assert stamped == 0
    assert not user_row(db, "u1").get("property_id")


def test_the_migration_creates_a_property_even_with_no_users(db):
    created, property_id, stamped, current = run(backfill_property.backfill())
    assert created is True and property_id
    assert stamped == 0 and current == 0


# ------------------------ require_access resolves it once ------------------------

HOTEL_MGR = ("admin", "manager")


def check(dependency, user):
    """Run the dependency's checker as the request would, returning the user or raising."""
    return run(dependency(user))


def test_a_live_property_lets_its_admin_through(db):
    add_property(db, LIVE)
    user = add_user(db, "u1", property_id="p1")
    assert check(require_access("hotel", *HOTEL_MGR), user)["id"] == "u1"


def test_a_suspended_property_refuses_its_own_admin(db):
    add_property(db, SUSPENDED)
    user = add_user(db, "u1", property_id="p1")

    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR), user)
    assert exc.value.status_code == 403
    # Not the pending wording: a suspended hotel is not waiting for anything.
    assert exc.value.detail == NOT_PERMITTED


def test_a_suspended_property_refuses_even_a_setup_time_endpoint(db):
    add_property(db, SUSPENDED)
    user = add_user(db, "u1", property_id="p1")
    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR, setup_time=True), user)
    assert exc.value.status_code == 403


def test_a_pending_property_refuses_an_operating_endpoint_and_says_why(db):
    add_property(db, PENDING)
    user = add_user(db, "u1", property_id="p1")

    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR), user)
    assert exc.value.status_code == 403
    # A bare "Forbidden" reads as a permissions bug to the hotel that is simply waiting.
    assert exc.value.detail == PENDING_APPROVAL
    assert "approval" in exc.value.detail.lower()
    assert "pending" in exc.value.detail.lower()


def test_a_pending_property_reaches_a_setup_time_endpoint(db):
    add_property(db, PENDING)
    user = add_user(db, "u1", property_id="p1")
    assert check(require_access("hotel", *HOTEL_MGR, setup_time=True), user)["id"] == "u1"


def test_a_pending_property_does_not_turn_a_wrong_role_into_a_pending_message(db):
    add_property(db, PENDING)
    user = add_user(db, "u1", role="waiter", property_id="p1")

    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR, setup_time=True), user)
    assert exc.value.detail == NOT_PERMITTED

    # And on an operating endpoint the waiter is refused for the role they lack, not
    # told that approval is what stands between them and the booking screen.
    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR), user)
    assert exc.value.detail == NOT_PERMITTED


def test_the_platform_operator_is_refused_every_hotel_endpoint(db):
    add_property(db, LIVE)
    user = add_user(db, "op", role=PLATFORM_ADMIN)

    for dependency in (require_access("hotel", "admin", "manager"),
                       require_access(SHARED, "admin"),
                       require_access("hotel", "admin", setup_time=True)):
        with pytest.raises(HTTPException) as exc:
            check(dependency, user)
        assert exc.value.status_code == 403


def test_a_user_with_no_property_is_refused(db):
    add_property(db, LIVE)
    user = add_user(db, "u1")  # never stamped by the migration
    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR), user)
    assert exc.value.status_code == 403


def test_a_property_id_naming_no_record_is_refused(db):
    user = add_user(db, "u1", property_id="ghost")
    with pytest.raises(HTTPException) as exc:
        check(require_access("hotel", *HOTEL_MGR), user)
    assert exc.value.status_code == 403


def test_the_property_is_read_once_per_request(db):
    add_property(db, LIVE)
    user = add_user(db, "u1", property_id="p1")

    reads = []
    original = db.properties.find_one

    async def counting(*args, **kwargs):
        reads.append(args)
        return await original(*args, **kwargs)

    # Patch the collection handle the dependency will fetch, not a copy of it.
    class Counting:
        def __getattr__(self, name):
            return getattr(db, name)

        @property
        def properties(self):
            class C:
                find_one = staticmethod(counting)
            return C()

    security_db = security.db
    try:
        security.db = Counting()
        check(require_access("hotel", *HOTEL_MGR), user)
    finally:
        security.db = security_db
    assert len(reads) == 1


# ----------------------------------- the door -----------------------------------

def login(email="u1@barflow.io", password="secret123"):
    return run(auth.login(auth.LoginIn(email=email, password=password)))


def add_login(db, status, id="u1", property_id="p1"):
    add_property(db, status, id=property_id)
    add_user(db, id, property_id=property_id,
             password_hash=security.hash_password("secret123"))


def test_a_live_hotels_user_logs_in(db):
    add_login(db, LIVE)
    assert login()["token"]


def test_a_pending_hotels_user_logs_in_because_setup_is_theirs_to_do(db):
    add_login(db, PENDING)
    assert login()["token"]


def test_a_suspended_hotels_user_is_refused_at_the_door(db):
    add_login(db, SUSPENDED)
    with pytest.raises(HTTPException) as exc:
        login()
    assert exc.value.status_code == 401


def test_the_suspension_refusal_is_byte_identical_to_a_wrong_password(db):
    add_login(db, SUSPENDED)
    with pytest.raises(HTTPException) as suspended:
        login()

    add_login(db, LIVE, id="u2", property_id="p2")
    with pytest.raises(HTTPException) as wrong:
        login("u2@barflow.io", "not-the-password")

    # Telling a caller the hotel exists but is switched off is telling them the hotel
    # exists. The two refusals must be indistinguishable, character for character.
    assert suspended.value.status_code == wrong.value.status_code == 401
    assert suspended.value.detail == wrong.value.detail
