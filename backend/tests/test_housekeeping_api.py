"""Housekeeping through the routers: status, the log, and check-out dirtying a room.

No server. The endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, so both are called as what they are — the same style as test_isolation.py and
test_tenancy.py. The authorization dependencies are called too, directly, because
`require_access(...)` returns a checker that takes the user: that is the only way to
assert from in here that a waiter cannot reach the housekeeping screen, and the
declaration on the route is the thing worth asserting about.
"""
import asyncio
from datetime import date, timedelta
from typing import get_args

import pytest
from fastapi import HTTPException

import db as db_module
import routers.frontdesk as frontdesk
import routers.housekeeping as housekeeping
import security
from models.folio import CheckOutIn
from models.hotel import HousekeepingStatusIn, HousekeepingStatus, Room
from mock_db import MockDatabase
from scoped_db import PropertyScopedDatabase, tenant_db
from services.access import DOMAINS, LIVE, SCREEN_KEYS
from services.clock import today as local_today
from services.housekeeping import STATUSES

SCREEN = "hotel.housekeeping"


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


_UNSCOPED_HOLDERS = (db_module, security)


@pytest.fixture
def hotel(tmp_path, monkeypatch):
    """One live property, one of each role, two rooms and a checked-in guest."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    run(handle.properties.insert_one({
        "id": "p1", "name": "The Grand", "status": LIVE, "property_type": "both",
        "created_at": "2026-01-01T00:00:00+00:00"}))

    people = {}
    for role, domains, permissions in (
            ("admin", DOMAINS, SCREEN_KEYS),
            ("manager", ("hotel",), (SCREEN, "hotel.front_desk", "hotel.bookings")),
            ("front_desk", ("hotel",), (SCREEN, "hotel.front_desk", "hotel.bookings")),
            ("housekeeping", ("hotel",), (SCREEN,)),
            ("waiter", ("bar",), ("outlet.pos", "outlet.kot")),
    ):
        person = {"id": f"u-{role}", "email": f"{role}@grand.example.com", "name": role,
                  "role": role, "domains": list(domains), "permissions": list(permissions),
                  "active": True, "property_id": "p1"}
        run(handle.users.insert_one(person))
        people[role] = person

    db = run(tenant_db(people["admin"]))
    run(db.room_types.insert_one({"id": "rt", "name": "Deluxe", "code": "DLX",
                                  "base_occupancy": 2, "max_occupancy": 3, "active": True}))
    for rid, number in (("r101", "101"), ("r102", "102")):
        room = Room(number=number, room_type_id="rt", floor="1").model_dump()
        room["id"] = rid
        run(db.rooms.insert_one(room))
    return people, db, handle


def room(db, room_id="r101"):
    return run(db.rooms.find_one({"id": room_id}, {"_id": 0}))


def events(db, room_id="r101"):
    return run(db.housekeeping_events.find({"room_id": room_id}, {"_id": 0}).to_list(100))


def set_status(db, room_id, status, note=None, actor=None):
    return call(housekeeping.set_housekeeping_status, room_id=room_id,
                payload=HousekeepingStatusIn(status=status, note=note), user=actor, db=db)


def refuse_status(db, room_id, status, note=None, actor=None):
    return refused(housekeeping.set_housekeeping_status, room_id=room_id,
                   payload=HousekeepingStatusIn(status=status, note=note), user=actor, db=db)


# ------------------------------ the vocabulary ------------------------------
def test_the_model_and_the_rules_hold_the_same_statuses():
    # Two lists of the same four strings, in two files, because a Literal needs its
    # members at type-check time. This is what stops them drifting.
    assert set(get_args(HousekeepingStatus)) == set(STATUSES)


def test_a_new_room_is_born_clean_and_untouched():
    fresh = Room(number="303", room_type_id="rt").model_dump()
    assert fresh["housekeeping_status"] == "clean"
    assert fresh["housekeeping_note"] is None
    assert fresh["housekeeping_updated_at"] is None
    assert fresh["housekeeping_updated_by"] is None
    # And the date ranges that control what is sold are still their own, empty field.
    assert fresh["out_of_order"] == []


def test_the_status_is_not_something_the_rooms_screen_can_overwrite():
    # RoomIn is what PUT /api/rooms/{id} sets wholesale. A housekeeping field on it would
    # be reset to its default every time somebody corrected a room's floor.
    from models.hotel import RoomIn
    assert not [f for f in RoomIn.model_fields if f.startswith("housekeeping")]


# --------------------------------- the board ---------------------------------
def test_the_board_shows_every_room_with_its_status(hotel):
    people, db, _ = hotel
    board = call(housekeeping.housekeeping_board, user=people["housekeeping"], db=db)
    assert board["date"] == local_today()
    assert [r["number"] for r in board["rooms"]] == ["101", "102"]
    assert board["rooms"][0]["housekeeping_status"] == "clean"
    assert board["rooms"][0]["ready"] is True
    assert board["rooms"][0]["occupied"] is False
    assert board["rooms"][0]["departing_today"] is False


def test_the_board_says_who_is_in_the_room_and_who_leaves_today(hotel):
    people, db, _ = hotel
    day = local_today()
    tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    run(db.bookings.insert_one({"id": "b1", "status": "checked_in", "guest_id": "g1",
                                "assigned_room_id": "r101", "check_in": day,
                                "check_out": day}))
    run(db.bookings.insert_one({"id": "b2", "status": "checked_in", "guest_id": "g2",
                                "assigned_room_id": "r102", "check_in": day,
                                "check_out": tomorrow}))
    cards = {r["number"]: r for r in
             call(housekeeping.housekeeping_board, user=people["housekeeping"],
                  db=db)["rooms"]}
    assert cards["101"]["occupied"] is True and cards["101"]["departing_today"] is True
    assert cards["102"]["occupied"] is True and cards["102"]["departing_today"] is False


def test_the_board_offers_each_role_the_statuses_it_may_actually_set(hotel):
    people, db, _ = hotel
    run(db.rooms.update_one({"id": "r101"}, {"$set": {"housekeeping_status": "dirty"}}))

    def offered(role):
        cards = call(housekeeping.housekeeping_board, user=people[role], db=db)["rooms"]
        return {c["number"]: c["can_set"] for c in cards}

    assert offered("housekeeping")["101"] == ["clean", "out_of_order"]
    assert offered("front_desk")["101"] == []          # already dirty; nothing else theirs
    assert offered("front_desk")["102"] == ["dirty"]   # a clean room they may dirty
    assert offered("manager")["101"] == ["clean", "out_of_order"]
    assert offered("admin")["102"] == ["dirty", "inspected", "out_of_order"]


def test_a_room_out_of_order_offers_an_attendant_nothing(hotel):
    people, db, _ = hotel
    set_status(db, "r101", "out_of_order", note="Burst pipe", actor=people["housekeeping"])
    cards = {r["number"]: r for r in
             call(housekeeping.housekeeping_board, user=people["housekeeping"],
                  db=db)["rooms"]}
    assert cards["101"]["can_set"] == []
    assert cards["101"]["housekeeping_note"] == "Burst pipe"
    assert cards["101"]["ready"] is False
    # ...and a manager is offered the way back.
    manager_cards = {r["number"]: r for r in
                     call(housekeeping.housekeeping_board, user=people["manager"],
                          db=db)["rooms"]}
    assert manager_cards["101"]["can_set"] == ["clean", "dirty"]


def test_a_room_the_migration_has_not_reached_reads_as_clean(hotel):
    people, db, _ = hotel
    run(db.rooms.insert_one({"id": "r-old", "number": "201", "room_type_id": "rt",
                             "active": True, "out_of_order": []}))
    cards = {r["number"]: r for r in
             call(housekeeping.housekeeping_board, user=people["manager"], db=db)["rooms"]}
    assert cards["201"]["housekeeping_status"] == "clean"
    assert cards["201"]["ready"] is True


def test_the_board_sorts_room_numbers_like_a_person_reads_them(hotel):
    people, db, _ = hotel
    for rid, number in (("r2", "2"), ("r10", "10"), ("r9", "9")):
        run(db.rooms.insert_one({"id": rid, "number": number, "room_type_id": "rt",
                                 "floor": "0", "active": True, "out_of_order": []}))
    numbers = [r["number"] for r in
               call(housekeeping.housekeeping_board, user=people["manager"],
                    db=db)["rooms"]]
    assert numbers == ["2", "9", "10", "101", "102"]


# ------------------------------- transitions -------------------------------
def test_an_attendant_cleans_a_dirty_room_and_the_log_records_who(hotel):
    people, db, _ = hotel
    run(db.rooms.update_one({"id": "r101"}, {"$set": {"housekeeping_status": "dirty"}}))
    out = set_status(db, "r101", "clean", actor=people["housekeeping"])

    assert out["changed"] is True
    assert out["room"]["housekeeping_status"] == "clean"
    assert out["room"]["housekeeping_updated_by"] == "u-housekeeping"
    assert out["room"]["housekeeping_updated_at"]

    logged = events(db)
    assert len(logged) == 1
    assert logged[0]["from_status"] == "dirty"
    assert logged[0]["to_status"] == "clean"
    assert logged[0]["changed_by"] == "u-housekeeping"
    assert logged[0]["changed_at"] == out["room"]["housekeeping_updated_at"]


def test_an_attendant_is_refused_inspected(hotel):
    people, db, _ = hotel
    refusal = refuse_status(db, "r101", "inspected", actor=people["housekeeping"])
    assert refusal.status_code == 403
    assert "manager" in refusal.detail
    assert room(db)["housekeeping_status"] == "clean"
    assert events(db) == []


def test_a_manager_inspects_a_clean_room(hotel):
    people, db, _ = hotel
    out = set_status(db, "r101", "inspected", actor=people["manager"])
    assert out["room"]["housekeeping_status"] == "inspected"
    assert out["event"]["from_status"] == "clean"


def test_out_of_order_without_a_note_is_400(hotel):
    people, db, _ = hotel
    for note in (None, "", "   "):
        refusal = refuse_status(db, "r101", "out_of_order", note=note,
                                actor=people["housekeeping"])
        assert refusal.status_code == 400
    assert room(db)["housekeeping_status"] == "clean"
    assert events(db) == []


def test_an_attendant_cannot_take_a_room_back_out_of_out_of_order(hotel):
    people, db, _ = hotel
    set_status(db, "r101", "out_of_order", note="Burst pipe", actor=people["housekeeping"])
    for status in ("clean", "dirty"):
        refusal = refuse_status(db, "r101", status, actor=people["housekeeping"])
        assert refusal.status_code == 403
    assert room(db)["housekeeping_status"] == "out_of_order"


def test_a_manager_clears_out_of_order_and_the_note_goes_with_it(hotel):
    people, db, _ = hotel
    set_status(db, "r101", "out_of_order", note="Burst pipe", actor=people["housekeeping"])
    out = set_status(db, "r101", "dirty", actor=people["manager"])
    assert out["room"]["housekeeping_status"] == "dirty"
    assert out["room"]["housekeeping_note"] is None
    # The words survive where they cannot be edited: in the line that put it out of order.
    assert [e["note"] for e in events(db) if e["to_status"] == "out_of_order"] == ["Burst pipe"]


def test_marking_a_room_out_of_order_does_not_touch_what_can_be_sold(hotel):
    people, db, _ = hotel
    # The whole reason the two concepts stay apart: an attendant's tap must not withdraw
    # a room from sale for a fortnight.
    run(db.rooms.update_one({"id": "r101"}, {"$set": {
        "out_of_order": [{"from": "2026-09-01", "to": "2026-09-05", "reason": "Painting"}]}}))
    set_status(db, "r101", "out_of_order", note="Burst pipe", actor=people["housekeeping"])
    assert room(db)["out_of_order"] == [
        {"from": "2026-09-01", "to": "2026-09-05", "reason": "Painting"}]


def test_the_front_desk_may_only_dirty(hotel):
    people, db, _ = hotel
    assert set_status(db, "r101", "dirty", actor=people["front_desk"])["changed"] is True
    assert refuse_status(db, "r101", "clean", actor=people["front_desk"]).status_code == 403


def test_a_room_with_a_guest_in_it_can_still_be_marked_dirty(hotel):
    people, db, _ = hotel
    run(db.bookings.insert_one({"id": "b1", "status": "checked_in", "guest_id": "g1",
                                "assigned_room_id": "r101", "check_in": local_today(),
                                "check_out": local_today()}))
    assert set_status(db, "r101", "dirty", actor=people["housekeeping"])["changed"] is True


def test_setting_the_status_a_room_already_has_writes_no_event(hotel):
    people, db, _ = hotel
    out = set_status(db, "r101", "clean", actor=people["housekeeping"])
    assert out["changed"] is False and out["event"] is None
    assert events(db) == []
    # Not stamped either: a double-tap must not rewrite who last touched the room.
    assert room(db)["housekeeping_updated_by"] is None


def test_a_double_tap_out_of_order_is_a_no_op_rather_than_a_400(hotel):
    people, db, _ = hotel
    set_status(db, "r101", "out_of_order", note="Burst pipe", actor=people["housekeeping"])
    again = set_status(db, "r101", "out_of_order", actor=people["housekeeping"])
    assert again["changed"] is False
    assert room(db)["housekeeping_note"] == "Burst pipe"
    assert len(events(db)) == 1


def test_a_room_this_property_does_not_have_is_404(hotel):
    people, db, _ = hotel
    assert refuse_status(db, "nobody's-room", "dirty",
                         actor=people["manager"]).status_code == 404


def test_an_unknown_status_never_reaches_the_handler(hotel):
    # A 422 from the request body, which is what the design asks for: the request is
    # malformed rather than refused.
    with pytest.raises(Exception) as exc:
        HousekeepingStatusIn(status="sparkling")
    assert "sparkling" in str(exc.value)


# --------------------------------- the log ---------------------------------
def test_the_log_reads_back_newest_first(hotel):
    people, db, _ = hotel
    set_status(db, "r101", "dirty", actor=people["front_desk"])
    set_status(db, "r101", "clean", actor=people["housekeeping"])
    set_status(db, "r101", "inspected", actor=people["manager"])
    log = call(housekeeping.housekeeping_events, room_id="r101", user=people["manager"],
               db=db)
    assert [e["to_status"] for e in log] == ["inspected", "clean", "dirty"]
    assert [e["changed_by"] for e in log] == ["u-manager", "u-housekeeping", "u-front_desk"]


def test_the_log_of_a_room_that_does_not_exist_is_404(hotel):
    people, db, _ = hotel
    assert refused(housekeeping.housekeeping_events, room_id="nope",
                   user=people["manager"], db=db).status_code == 404


# ------------------------------- check-out -------------------------------
def check_out(db, people, booking_id="b1", actor="front_desk"):
    return call(frontdesk.check_out, booking_id=booking_id, payload=CheckOutIn(),
                user=people[actor], db=db)


def stay(db, room_id="r101", balance=0.0):
    day = local_today()
    run(db.guests.insert_one({"id": "g1", "name": "Asha", "phone": "9990000001"}))
    run(db.bookings.insert_one({"id": "b1", "status": "checked_in", "guest_id": "g1",
                                "assigned_room_id": room_id, "check_in": day,
                                "check_out": day, "quote": {"nights": []}}))
    run(db.folios.insert_one({"id": "f1", "booking_id": "b1", "guest_id": "g1",
                              "status": "open", "balance": balance}))


def test_check_out_dirties_the_room_and_writes_an_event(hotel):
    people, db, _ = hotel
    stay(db)
    out = check_out(db, people)
    assert out["booking"]["status"] == "checked_out"
    assert room(db)["housekeeping_status"] == "dirty"

    logged = events(db)
    assert len(logged) == 1
    assert (logged[0]["from_status"], logged[0]["to_status"]) == ("clean", "dirty")
    # The transition is automatic; the person who pressed the button is still recorded.
    assert logged[0]["changed_by"] == "u-front_desk"
    assert out["housekeeping_event"]["id"] == logged[0]["id"]


def test_check_out_of_a_room_already_dirty_adds_no_second_line(hotel):
    people, db, _ = hotel
    stay(db)
    set_status(db, "r101", "dirty", actor=people["housekeeping"])
    out = check_out(db, people)
    assert out["housekeeping_event"] is None
    assert len(events(db)) == 1


def test_check_out_of_a_booking_holding_no_room_still_works(hotel):
    people, db, _ = hotel
    stay(db, room_id=None)
    out = check_out(db, people)
    assert out["booking"]["status"] == "checked_out"
    assert out["housekeeping_event"] is None


def test_check_out_leaves_the_date_ranges_that_control_sale_alone(hotel):
    people, db, _ = hotel
    stay(db)
    run(db.rooms.update_one({"id": "r101"}, {"$set": {
        "out_of_order": [{"from": "2026-09-01", "to": "2026-09-05"}]}}))
    check_out(db, people)
    assert room(db)["out_of_order"] == [{"from": "2026-09-01", "to": "2026-09-05"}]


def test_a_room_out_of_order_is_still_dirtied_by_a_check_out(hotel):
    people, db, _ = hotel
    stay(db)
    set_status(db, "r101", "out_of_order", note="Burst pipe", actor=people["manager"])
    check_out(db, people)
    # The automatic transition is the design's "any -> dirty" row and is not filtered by
    # the role table — a guest has left a room that has to be turned, whatever else is
    # wrong with it. The fault is not forgotten: the log still holds it.
    assert room(db)["housekeeping_status"] == "dirty"


# ---------------------------- who reaches the screen ----------------------------
def person_may(dependency, user) -> bool:
    """Call the route's own authorization dependency, as FastAPI would."""
    try:
        run(dependency(user=user))
        return True
    except HTTPException:
        return False


def test_the_screen_is_reached_by_the_four_roles_the_design_names(hotel):
    people, _db, _ = hotel
    for role in ("admin", "manager", "front_desk", "housekeeping"):
        assert person_may(housekeeping.ATTENDANT, people[role]) is True


def test_a_waiter_cannot_reach_the_housekeeping_screen(hotel):
    people, _db, handle = hotel
    assert person_may(housekeeping.ATTENDANT, people["waiter"]) is False
    # Not even with the tick: the role list refuses them first, and `bar` is not `hotel`.
    run(handle.users.update_one({"id": "u-waiter"},
                                {"$set": {"permissions": [SCREEN, "outlet.pos"]}}))
    ticked = run(handle.users.find_one({"id": "u-waiter"}, {"_id": 0}))
    assert person_may(housekeeping.ATTENDANT, ticked) is False


def test_a_hotel_user_without_the_tick_cannot_reach_it(hotel):
    people, _db, handle = hotel
    run(handle.users.update_one({"id": "u-front_desk"},
                                {"$set": {"permissions": ["hotel.front_desk"]}}))
    untick = run(handle.users.find_one({"id": "u-front_desk"}, {"_id": 0}))
    assert person_may(housekeeping.ATTENDANT, untick) is False


def test_an_attendant_reaches_the_screen_of_a_property_still_awaiting_approval(hotel):
    # It does not: housekeeping is operating, not setting up, so it locks with everything
    # else. Asserted because `setup_time` defaults to False by design and a route that
    # quietly turned it on would let an unapproved property run.
    people, _db, handle = hotel
    run(handle.properties.update_one({"id": "p1"}, {"$set": {"status": "pending"}}))
    assert person_may(housekeeping.ATTENDANT, people["housekeeping"]) is False
