"""The `housekeeping` role: one screen, and the migration that hands it out.

An attendant reaches the corridor they work in and nothing else. That is one sentence and
four places have to agree with it — the role list, the screen catalogue, the defaults a
new account is created with, and the migration that reaches the accounts that already
exist — so it is asserted here rather than assumed.
"""
import asyncio
from typing import get_args

import pytest

import migrations.backfill_housekeeping as migration
import security
from migrations.backfill_permissions import backfill as backfill_permissions
from mock_db import MockDatabase
from services.access import (
    DOMAINS, LIVE, ROLE_SCREENS, SCREENS, can_access, default_permissions,
    permission_in_domains)

SCREEN = "hotel.housekeeping"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(migration, "unscoped_db", handle)
    monkeypatch.setattr(security, "unscoped_db", handle)
    import migrations.backfill_permissions as permissions_migration
    monkeypatch.setattr(permissions_migration, "unscoped_db", handle)
    return handle


def add_user(db, id, role, permissions=None, domains=("hotel",), **extra):
    doc = {"id": id, "email": f"{id}@barflow.io", "name": id, "role": role,
           "domains": list(domains), "active": True, "property_id": "p1"}
    if permissions is not None:
        doc["permissions"] = list(permissions)
    doc.update(extra)
    run(db.users.insert_one(doc))
    return doc


def user_row(db, id):
    return run(db.users.find_one({"id": id}, {"_id": 0}))


def hk(permissions=(SCREEN,), domains=("hotel",), active=True):
    return {"id": "u-hk", "role": "housekeeping", "domains": list(domains),
            "permissions": list(permissions), "active": active, "property_id": "p1"}


PROPERTY = {"id": "p1", "name": "The Grand", "status": LIVE, "property_type": "both"}


# ------------------------------ the role exists ------------------------------
def test_housekeeping_is_a_role_the_staff_screen_can_assign():
    # routers/staff.py types its bodies on this Literal, so a role missing here cannot be
    # created at all — the account exists in the design and nowhere else.
    assert "housekeeping" in get_args(security.Role)


def test_the_housekeeping_screen_is_in_the_catalogue_and_belongs_to_the_hotel():
    assert SCREENS[SCREEN]["domains"] == ("hotel",)
    assert SCREENS[SCREEN]["section"] == "Hotel"
    # An outlet-only waiter could never hold it, which is what stops it being ticked.
    assert permission_in_domains(SCREEN, ["bar", "restaurant"]) is False
    assert permission_in_domains(SCREEN, ["hotel"]) is True


def test_a_new_attendant_starts_with_that_screen_and_only_that_screen():
    assert ROLE_SCREENS["housekeeping"] == (SCREEN,)
    assert default_permissions("housekeeping", ["hotel"]) == [SCREEN]


def test_the_manager_and_the_admin_reach_it_without_being_granted_anything_new():
    # Both entries are computed from the whole catalogue, so a new screen joins them by
    # construction. Only the accounts already stored need the migration below.
    assert SCREEN in ROLE_SCREENS["admin"]
    assert SCREEN in ROLE_SCREENS["manager"]


def test_the_front_desks_default_screens_are_unchanged():
    # Deliberate: their list is hand-written rather than computed, and the desk's real
    # need — a status label on the check-in room picker — comes from GET /api/rooms.
    assert SCREEN not in ROLE_SCREENS["front_desk"]
    assert SCREEN not in ROLE_SCREENS["waiter"]
    assert SCREEN not in ROLE_SCREENS["kitchen"]


# --------------------------- what an attendant reaches ---------------------------
def test_an_attendant_reaches_the_housekeeping_screen():
    assert can_access(hk(), "hotel", ("admin", "manager", "front_desk", "housekeeping"),
                      PROPERTY, permission=SCREEN) is True


def test_an_attendant_reaches_nothing_else():
    # The four declarations from the routers, spelled out rather than imported, so that a
    # role quietly added to one of those lists fails here.
    for domains, roles, permission in (
            ("hotel", ("admin", "manager", "front_desk"), "hotel.bookings"),
            ("hotel", ("admin", "manager", "front_desk"), "hotel.front_desk"),
            ("hotel", ("admin", "manager"), "hotel.rates"),
            (("restaurant", "bar"), (), "outlet.pos"),
            (("restaurant", "bar"), (), "outlet.kot"),
    ):
        assert can_access(hk(), domains, roles, PROPERTY, permission=permission) is False


def test_an_attendant_who_only_works_the_outlets_reaches_nothing():
    # Their tick is outside their domains, so it cannot take effect — the domain check
    # runs before the screen check, which is why the tick would have been refused on save.
    assert can_access(hk(domains=("bar",)), "hotel",
                      ("admin", "manager", "housekeeping"), PROPERTY,
                      permission=SCREEN) is False


def test_a_deactivated_attendant_reaches_nothing():
    assert can_access(hk(active=False), "hotel", ("admin", "manager", "housekeeping"),
                      PROPERTY, permission=SCREEN) is False


def test_a_waiter_does_not_reach_the_housekeeping_screen():
    waiter = {"id": "u-w", "role": "waiter", "domains": ["bar"], "permissions": [SCREEN],
              "active": True, "property_id": "p1"}
    # Refused twice over: not in the role list, and `hotel` is not a domain they hold.
    # The ticked screen is there to prove the tick alone grants nothing.
    assert can_access(waiter, "hotel", ("admin", "manager", "front_desk", "housekeeping"),
                      PROPERTY, permission=SCREEN) is False


# ------------------------------- the migration -------------------------------
def test_the_migration_grants_the_screen_to_managers_and_admins(db):
    add_user(db, "u-mgr", "manager", permissions=["hotel.front_desk"])
    add_user(db, "u-adm", "admin", permissions=["admin.staff"])
    granted, current = run(migration.grant_screen())
    assert (granted, current) == (2, 0)
    assert SCREEN in user_row(db, "u-mgr")["permissions"]
    assert SCREEN in user_row(db, "u-adm")["permissions"]
    # Added, never replaced: whatever the owner had already ticked survives.
    assert "hotel.front_desk" in user_row(db, "u-mgr")["permissions"]


def test_the_migration_grants_the_screen_to_an_existing_attendant(db):
    add_user(db, "u-hk", "housekeeping", permissions=[])
    assert run(migration.grant_screen()) == (1, 0)
    assert user_row(db, "u-hk")["permissions"] == [SCREEN]


def test_the_migration_is_idempotent(db):
    add_user(db, "u-mgr", "manager", permissions=["hotel.front_desk"])
    run(migration.grant_screen())
    before = user_row(db, "u-mgr")["permissions"]
    assert run(migration.grant_screen()) == (0, 1)
    assert user_row(db, "u-mgr")["permissions"] == before


def test_the_migration_leaves_the_front_desk_the_waiter_and_the_kitchen_alone(db):
    add_user(db, "u-desk", "front_desk", permissions=["hotel.front_desk"])
    add_user(db, "u-wait", "waiter", permissions=["outlet.pos"], domains=("bar",))
    add_user(db, "u-cook", "kitchen", permissions=["outlet.kot"], domains=("bar",))
    assert run(migration.grant_screen()) == (0, 0)
    for uid in ("u-desk", "u-wait", "u-cook"):
        assert SCREEN not in user_row(db, uid)["permissions"]


def test_a_manager_outside_the_hotel_domain_is_not_given_a_tick_that_does_nothing(db):
    add_user(db, "u-mgr", "manager", permissions=["outlet.pos"], domains=("bar",))
    assert run(migration.grant_screen()) == (0, 0)
    assert user_row(db, "u-mgr")["permissions"] == ["outlet.pos"]


def test_an_account_with_no_permissions_field_is_left_to_the_screen_backfill(db):
    # Touching it here would grant one screen and hide the account from the migration
    # whose job is to derive the whole set from the role.
    add_user(db, "u-mgr", "manager", permissions=None, domains=DOMAINS)
    assert run(migration.grant_screen()) == (0, 0)
    assert "permissions" not in user_row(db, "u-mgr")

    run(backfill_permissions())
    assert SCREEN in user_row(db, "u-mgr")["permissions"]


def test_the_two_migrations_together_leave_every_hotel_role_where_it_should_be(db):
    # The startup order: screens first, then this one. A fresh account and an old one
    # must end up in the same place.
    add_user(db, "u-new", "housekeeping", permissions=None)
    add_user(db, "u-old", "manager", permissions=["hotel.front_desk"])
    run(backfill_permissions())
    run(migration.grant_screen())
    assert user_row(db, "u-new")["permissions"] == [SCREEN]
    assert SCREEN in user_row(db, "u-old")["permissions"]


# ------------------------------ the room stamp ------------------------------
def test_every_existing_room_is_stamped_clean(db):
    run(db.rooms.insert_one({"id": "r1", "number": "101", "property_id": "p1"}))
    run(db.rooms.insert_one({"id": "r2", "number": "102", "property_id": "p2"}))
    assert run(migration.seed_room_status()) == (2, 0)
    for rid in ("r1", "r2"):
        room = run(db.rooms.find_one({"id": rid}, {"_id": 0}))
        assert room["housekeeping_status"] == "clean"
        assert room["housekeeping_note"] is None
        assert room["housekeeping_updated_at"] is None
        assert room["housekeeping_updated_by"] is None
        # Every tenant's rooms, each keeping the property it already had.
        assert room["property_id"] == ("p1" if rid == "r1" else "p2")


def test_the_room_stamp_is_idempotent_and_never_re_cleans_a_broken_room(db):
    run(db.rooms.insert_one({"id": "r1", "number": "101", "property_id": "p1"}))
    run(migration.seed_room_status())
    run(db.rooms.update_one({"id": "r1"}, {"$set": {
        "housekeeping_status": "out_of_order", "housekeeping_note": "Burst pipe"}}))
    assert run(migration.seed_room_status()) == (0, 1)
    room = run(db.rooms.find_one({"id": "r1"}, {"_id": 0}))
    assert room["housekeeping_status"] == "out_of_order"
    assert room["housekeeping_note"] == "Burst pipe"


def test_the_room_stamp_does_not_touch_the_date_ranges_that_control_what_is_sold(db):
    # The two out-of-order concepts are separate fields with separate owners. A migration
    # that touched the ranges would take rooms off sale on the morning it deployed.
    run(db.rooms.insert_one({"id": "r1", "number": "101", "property_id": "p1",
                             "out_of_order": [{"from": "2026-09-01", "to": "2026-09-05"}]}))
    run(migration.seed_room_status())
    assert run(db.rooms.find_one({"id": "r1"}, {"_id": 0}))["out_of_order"] == [
        {"from": "2026-09-01", "to": "2026-09-05"}]


def test_both_halves_run_together(db):
    add_user(db, "u-mgr", "manager", permissions=["hotel.front_desk"])
    run(db.rooms.insert_one({"id": "r1", "number": "101", "property_id": "p1"}))
    assert run(migration.backfill()) == (1, 0, 1, 0)
    assert run(migration.backfill()) == (0, 1, 0, 1)
