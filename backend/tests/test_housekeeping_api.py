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
from models.hotel import (
    GuestRequestIn, HousekeepingCancelIn, HousekeepingJobIn, HousekeepingPriority,
    HousekeepingStatus, HousekeepingStatusIn, Room)
from mock_db import MockDatabase
from scoped_db import PropertyScopedDatabase, tenant_db
from services.access import DOMAINS, LIVE, SCREEN_KEYS
from services.clock import today as local_today
from services.housekeeping import PRIORITIES, STATUSES

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


# --------------------------------- requests ---------------------------------
def raise_job(db, actor, room_id="r101", priority="normal", reason=""):
    return call(housekeeping.raise_job,
                payload=HousekeepingJobIn(room_id=room_id, priority=priority,
                                          reason=reason),
                user=actor, db=db)


def jobs(db, actor, status=""):
    return call(housekeeping.list_jobs, status=status, user=actor, db=db)


def test_the_model_and_the_rules_hold_the_same_priorities():
    assert set(get_args(HousekeepingPriority)) == set(PRIORITIES)


def test_a_receptionist_raises_a_request_and_it_names_the_room(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Spill on the carpet",
                    priority="high")
    assert job["status"] == "open"
    assert job["room_id"] == "r101"
    assert job["room_number"] == "101"
    assert job["priority"] == "high"
    assert job["reason"] == "Spill on the carpet"
    assert job["raised_by"] == "u-front_desk"
    assert job["source"] == "staff"
    assert job["acknowledged_at"] is None and job["completed_at"] is None
    assert [h["action"] for h in job["history"]] == ["raised"]


def test_a_request_for_a_room_that_does_not_exist_is_404(hotel):
    people, db, _ = hotel
    assert refused(housekeeping.raise_job,
                   payload=HousekeepingJobIn(room_id="nope"),
                   user=people["manager"], db=db).status_code == 404


def test_an_unknown_priority_never_reaches_the_handler(hotel):
    with pytest.raises(Exception) as exc:
        HousekeepingJobIn(room_id="r101", priority="urgent")
    assert "urgent" in str(exc.value)


def test_an_empty_reason_is_allowed(hotel):
    people, db, _ = hotel
    assert raise_job(db, people["housekeeping"])["reason"] == ""


def test_two_staff_requests_for_one_room_are_two_requests(hotel):
    people, db, _ = hotel
    # Deliberately not merged. A receptionist raising "fix the AC" on a room that already
    # has "spill on the carpet" outstanding is describing a second problem, and they are
    # looking at the list that shows them the first.
    raise_job(db, people["front_desk"], reason="Spill on the carpet")
    raise_job(db, people["front_desk"], reason="Fix the AC")
    assert len(jobs(db, people["housekeeping"], status="live")) == 2


def test_a_request_is_acknowledged_then_completed(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    picked = call(housekeeping.acknowledge_job, job_id=job["id"],
                  user=people["housekeeping"], db=db)
    assert picked["status"] == "in_progress"
    assert picked["acknowledged_by"] == "u-housekeeping" and picked["acknowledged_at"]

    done = call(housekeeping.complete_job, job_id=job["id"],
                user=people["housekeeping"], db=db)
    assert done["status"] == "done"
    assert done["completed_by"] == "u-housekeeping" and done["completed_at"]
    assert [h["action"] for h in done["history"]] == ["raised", "acknowledged", "completed"]


def test_a_request_can_be_completed_without_being_acknowledged(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["housekeeping"], reason="Towels")
    done = call(housekeeping.complete_job, job_id=job["id"],
                user=people["housekeeping"], db=db)
    assert done["status"] == "done" and done["acknowledged_at"] is None


def test_two_people_acknowledging_at_once_is_an_error_for_neither(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    first = call(housekeeping.acknowledge_job, job_id=job["id"],
                 user=people["housekeeping"], db=db)
    second = call(housekeeping.acknowledge_job, job_id=job["id"],
                  user=people["manager"], db=db)
    assert first["status"] == second["status"] == "in_progress"
    # Last write wins on the field, and the second acknowledgement writes nothing at all
    # — the job is already there, so there is nothing to record and nothing to undo.
    stored = run(db.housekeeping_jobs.find_one({"id": job["id"]}, {"_id": 0}))
    assert stored["acknowledged_by"] == "u-housekeeping"
    assert [h["action"] for h in stored["history"]] == ["raised", "acknowledged"]


def test_a_done_request_cannot_be_reopened(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    call(housekeeping.complete_job, job_id=job["id"], user=people["housekeeping"], db=db)

    clash = refused(housekeeping.acknowledge_job, job_id=job["id"],
                    user=people["housekeeping"], db=db)
    assert clash.status_code == 409 and "done" in clash.detail
    assert refused(housekeeping.cancel_job, job_id=job["id"],
                   payload=HousekeepingCancelIn(reason="changed my mind"),
                   user=people["manager"], db=db).status_code == 409
    assert run(db.housekeeping_jobs.find_one({"id": job["id"]}))["status"] == "done"


def test_completing_a_done_request_twice_is_not_an_error(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    call(housekeeping.complete_job, job_id=job["id"], user=people["housekeeping"], db=db)
    again = call(housekeeping.complete_job, job_id=job["id"],
                 user=people["housekeeping"], db=db)
    assert again["status"] == "done"
    assert [h["action"] for h in again["history"]] == ["raised", "completed"]


def test_a_cancelled_request_keeps_who_asked_and_who_called_it_off(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    off = call(housekeeping.cancel_job, job_id=job["id"],
               payload=HousekeepingCancelIn(reason="Guest sorted it themselves"),
               user=people["manager"], db=db)
    assert off["status"] == "cancelled"
    assert off["raised_by"] == "u-front_desk"
    assert off["cancelled_by"] == "u-manager"
    assert off["cancel_reason"] == "Guest sorted it themselves"
    # Never confused with completion: the job was not done.
    assert off["completed_at"] is None and off["completed_by"] is None


def test_a_cancelled_request_cannot_be_reopened(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    call(housekeeping.cancel_job, job_id=job["id"], payload=HousekeepingCancelIn(),
         user=people["manager"], db=db)
    assert refused(housekeeping.acknowledge_job, job_id=job["id"],
                   user=people["housekeeping"], db=db).status_code == 409
    assert refused(housekeeping.complete_job, job_id=job["id"],
                   user=people["housekeeping"], db=db).status_code == 409


def test_a_request_this_property_does_not_have_is_404(hotel):
    people, db, _ = hotel
    assert refused(housekeeping.acknowledge_job, job_id="not-ours",
                   user=people["manager"], db=db).status_code == 404


def test_the_list_filters_by_status_and_by_live(hotel):
    people, db, _ = hotel
    open_job = raise_job(db, people["front_desk"], reason="Towels")
    picked = raise_job(db, people["front_desk"], reason="Spill", room_id="r102")
    finished = raise_job(db, people["front_desk"], reason="Bulb")
    call(housekeeping.acknowledge_job, job_id=picked["id"], user=people["housekeeping"],
         db=db)
    call(housekeeping.complete_job, job_id=finished["id"], user=people["housekeeping"],
         db=db)

    assert {j["id"] for j in jobs(db, people["housekeeping"], "open")} == {open_job["id"]}
    assert {j["id"] for j in jobs(db, people["housekeeping"], "in_progress")} == {picked["id"]}
    assert {j["id"] for j in jobs(db, people["housekeeping"], "done")} == {finished["id"]}
    assert {j["id"] for j in jobs(db, people["housekeeping"], "live")} == {
        open_job["id"], picked["id"]}
    assert len(jobs(db, people["housekeeping"])) == 3
    # Newest first, so the screen opens on what has just come in.
    assert [j["id"] for j in jobs(db, people["housekeeping"])][0] == finished["id"]


def test_the_board_carries_the_requests_outstanding_on_each_room(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Spill on the carpet",
                    priority="high")
    finished = raise_job(db, people["front_desk"], reason="Bulb")
    call(housekeeping.complete_job, job_id=finished["id"], user=people["housekeeping"],
         db=db)

    cards = {r["number"]: r for r in
             call(housekeeping.housekeeping_board, user=people["housekeeping"],
                  db=db)["rooms"]}
    assert [j["id"] for j in cards["101"]["jobs"]] == [job["id"]]
    assert cards["101"]["jobs"][0]["priority"] == "high"
    assert cards["102"]["jobs"] == []


def test_completing_a_request_does_not_by_itself_clean_the_room(hotel):
    people, db, _ = hotel
    # Two axes, kept apart: the request is what somebody asked for, the status is what the
    # room is. An attendant who brings towels has not cleaned the room.
    run(db.rooms.update_one({"id": "r101"}, {"$set": {"housekeeping_status": "dirty"}}))
    job = raise_job(db, people["front_desk"], reason="Towels")
    call(housekeeping.complete_job, job_id=job["id"], user=people["housekeeping"], db=db)
    assert room(db)["housekeeping_status"] == "dirty"


# --------------------------------- the alert ---------------------------------
def alerts(db, actor):
    return call(housekeeping.housekeeping_alerts, user=actor, db=db)


def test_an_open_request_appears_in_the_alert(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Spill on the carpet",
                    priority="high")
    out = alerts(db, people["manager"])
    assert out["count"] == 1
    assert out["jobs"][0] == {
        "id": job["id"], "room_id": "r101", "room_number": "101", "priority": "high",
        "reason": "Spill on the carpet", "source": "staff",
        "created_at": job["created_at"]}


def test_the_alert_names_the_room_without_reading_the_rooms_collection(hotel):
    people, db, _ = hotel
    raise_job(db, people["front_desk"], reason="Towels")
    # The number is stored on the job precisely so the poll is one query. Deleting the
    # room does not make the alert stop naming it — which is also the honest record of
    # what somebody asked for.
    run(db.rooms.delete_one({"id": "r101"}))
    assert alerts(db, people["manager"])["jobs"][0]["room_number"] == "101"


def test_a_request_raised_by_a_guest_reaches_the_alert(hotel):
    people, db, _ = hotel
    guest_asks(reason="The kettle is broken")
    out = alerts(db, people["front_desk"])
    assert out["count"] == 1
    assert out["jobs"][0]["source"] == "guest"
    assert out["jobs"][0]["reason"] == "The kettle is broken"


def test_acknowledging_removes_it_from_the_alert_but_leaves_it_in_the_log(hotel):
    people, db, _ = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    call(housekeeping.acknowledge_job, job_id=job["id"], user=people["housekeeping"],
         db=db)
    assert alerts(db, people["manager"])["count"] == 0
    # Still there, still whole: acknowledging is not a delete.
    stored = run(db.housekeeping_jobs.find_one({"id": job["id"]}, {"_id": 0}))
    assert stored["status"] == "in_progress"
    assert stored["reason"] == "Towels"
    assert [h["action"] for h in stored["history"]] == ["raised", "acknowledged"]
    assert {j["id"] for j in jobs(db, people["housekeeping"], "live")} == {job["id"]}


def test_a_done_or_cancelled_request_is_not_in_the_alert(hotel):
    people, db, _ = hotel
    done = raise_job(db, people["front_desk"], reason="Towels")
    off = raise_job(db, people["front_desk"], reason="Bulb")
    call(housekeeping.complete_job, job_id=done["id"], user=people["housekeeping"], db=db)
    call(housekeeping.cancel_job, job_id=off["id"], payload=HousekeepingCancelIn(),
         user=people["manager"], db=db)
    assert alerts(db, people["manager"])["count"] == 0


def test_the_alert_discloses_nothing_about_who_is_dealing_with_what(hotel):
    people, db, _ = hotel
    raise_job(db, people["front_desk"], reason="Towels")
    blob = str(alerts(db, people["manager"]))
    # No staff ids, no history, no acknowledgement fields: this route declares no screen,
    # so every field left in it is one that any hotel-domain user may read.
    for field in ("raised_by", "u-front_desk", "history", "acknowledged", "completed"):
        assert field not in blob


def test_the_alert_is_oldest_first(hotel):
    people, db, _ = hotel
    first = raise_job(db, people["front_desk"], reason="One")
    second = raise_job(db, people["front_desk"], reason="Two", room_id="r102")
    assert [j["id"] for j in alerts(db, people["manager"])["jobs"]] == [
        first["id"], second["id"]]


def test_the_alert_costs_one_query(hotel):
    people, db, _ = hotel
    raise_job(db, people["front_desk"], reason="Towels")

    reads = []
    original = db.__class__._collection

    def counting(self, name):
        reads.append(name)
        return original(self, name)

    db.__class__._collection = counting
    try:
        alerts(db, people["manager"])
    finally:
        db.__class__._collection = original
    # One collection touched, once. No join against rooms, no second call for a count —
    # this is fetched four times a minute by every signed-in hotel user.
    assert reads == ["housekeeping_jobs"]


def test_every_hotel_user_gets_the_alert_and_no_outlet_user_does(hotel):
    people, _db, handle = hotel
    for role in ("admin", "manager", "front_desk", "housekeeping"):
        assert person_may(housekeeping.ALERT, people[role]) is True
    # An outlet-only waiter holds `bar`, so the domain refuses them before anything else.
    assert person_may(housekeeping.ALERT, people["waiter"]) is False


def test_the_alert_reaches_a_hotel_user_who_holds_no_screens_at_all(hotel):
    people, _db, handle = hotel
    # Deliberate: the alert appears on every screen of every signed-in hotel user, so a
    # screen key on this route would silence it for people it is meant to reach.
    run(handle.users.update_one({"id": "u-front_desk"}, {"$set": {"permissions": []}}))
    stripped = run(handle.users.find_one({"id": "u-front_desk"}, {"_id": 0}))
    assert person_may(housekeeping.ALERT, stripped) is True
    assert person_may(housekeeping.ATTENDANT, stripped) is False


def test_acknowledging_from_the_alert_needs_no_screen(hotel):
    people, db, handle = hotel
    job = raise_job(db, people["front_desk"], reason="Towels")
    run(handle.users.update_one({"id": "u-front_desk"}, {"$set": {"permissions": []}}))
    stripped = run(handle.users.find_one({"id": "u-front_desk"}, {"_id": 0}))
    assert person_may(housekeeping.ACKNOWLEDGE, stripped) is True
    # A button on a page that 403s the person who presses it is worse than no button.
    assert call(housekeeping.acknowledge_job, job_id=job["id"], user=stripped,
                db=db)["status"] == "in_progress"


# ------------------------------ the card in the room ------------------------------
# The limiter's counters live in the database rather than in this process — see
# services/ratelimit.py — so the `hotel` fixture's throwaway file gives each test its own
# allowance without anything here having to reset them. The two flood tests below reset
# anyway, because they are the ones that would be wrong in silence.
def guest_asks(room_id="r101", reason="", request=None):
    return call(housekeeping.guest_request, room_id=room_id,
                payload=GuestRequestIn(reason=reason), request=request)


def live_jobs(db):
    return run(db.housekeeping_jobs.find(
        {"status": {"$in": ["open", "in_progress"]}}, {"_id": 0}).to_list(100))


def test_a_guest_raises_a_request_from_the_card_in_their_room(hotel):
    _people, db, _ = hotel
    answer = guest_asks(reason="Spill on the carpet")
    assert answer == {"ok": True, "received": True, "room_number": "101"}

    jobs_raised = live_jobs(db)
    assert len(jobs_raised) == 1
    job = jobs_raised[0]
    assert job["room_id"] == "r101" and job["room_number"] == "101"
    assert job["reason"] == "Spill on the carpet"
    assert job["status"] == "open"
    assert job["priority"] == "normal"
    # A guest has no account. `source` is what says so; the absence alone would read as a
    # record somebody failed to write.
    assert job["raised_by"] is None and job["source"] == "guest"


def test_the_card_shows_the_hotels_name_and_the_room_number_and_nothing_else(hotel):
    _people, db, _ = hotel
    run(db.rooms.update_one({"id": "r101"}, {"$set": {
        "housekeeping_status": "out_of_order", "housekeeping_note": "Burst pipe",
        "out_of_order": [{"from": "2026-09-01", "to": "2026-09-05"}]}}))
    card = call(housekeeping.guest_room_card, room_id="r101")
    assert card == {"property_name": "The Grand", "room_number": "101"}


def test_a_guest_cannot_read_anything_else_about_the_room(hotel):
    _people, db, _ = hotel
    guest_asks(reason="Towels")
    answer = guest_asks(reason="Towels")
    card = call(housekeeping.guest_room_card, room_id="r101")
    # Nothing in either response mentions the room's state, the request that exists, the
    # guest in the room, or anything with an id somebody could use for something else.
    for response in (answer, card):
        blob = str(response)
        for secret in ("dirty", "clean", "out_of_order", "housekeeping_status", "r101",
                       "open", "in_progress", "job", "reason", "u-"):
            assert secret not in blob


def test_a_guest_pressing_twice_merges_rather_than_duplicating(hotel):
    _people, db, _ = hotel
    guest_asks(reason="Spill on the carpet")
    guest_asks(reason="Also need fresh towels")

    jobs_raised = live_jobs(db)
    assert len(jobs_raised) == 1
    assert jobs_raised[0]["reason"] == "Spill on the carpet\nAlso need fresh towels"
    # Both presses are on the record, so "why was this room visited twice" is answerable.
    assert [h["action"] for h in jobs_raised[0]["history"]] == ["raised", "guest_request"]
    assert jobs_raised[0]["history"][1]["by"] is None


def test_pressing_twice_with_the_same_words_adds_nothing_to_the_reason(hotel):
    _people, db, _ = hotel
    guest_asks(reason="Spill on the carpet")
    guest_asks(reason="  spill on the CARPET  ")
    jobs_raised = live_jobs(db)
    assert len(jobs_raised) == 1
    assert jobs_raised[0]["reason"] == "Spill on the carpet"
    assert len(jobs_raised[0]["history"]) == 2


def test_the_answer_is_the_same_whether_it_merged_or_not(hotel):
    _people, _db, _ = hotel
    # A disclosure decision, not laziness: "we already have one of these" would tell
    # whoever scanned the card that a request is outstanding on that room.
    first = guest_asks(reason="Towels")
    assert guest_asks(reason="Towels again") == first


def test_a_guest_request_merges_into_one_an_attendant_has_already_picked_up(hotel):
    people, db, _ = hotel
    guest_asks(reason="Spill on the carpet")
    job = live_jobs(db)[0]
    call(housekeeping.acknowledge_job, job_id=job["id"], user=people["housekeeping"],
         db=db)
    guest_asks(reason="And the kettle is broken")

    outstanding = live_jobs(db)
    assert len(outstanding) == 1
    assert outstanding[0]["status"] == "in_progress"
    assert outstanding[0]["reason"] == "Spill on the carpet\nAnd the kettle is broken"


def test_a_guest_request_after_the_last_one_was_finished_is_a_new_request(hotel):
    people, db, _ = hotel
    guest_asks(reason="Towels")
    call(housekeeping.complete_job, job_id=live_jobs(db)[0]["id"],
         user=people["housekeeping"], db=db)
    guest_asks(reason="Towels")
    # A finished job is not reopened — that is what makes "who asked and when" survive.
    all_jobs = run(db.housekeeping_jobs.find({}, {"_id": 0}).to_list(100))
    assert len(all_jobs) == 2
    assert {j["status"] for j in all_jobs} == {"done", "open"}


def test_a_guest_request_merges_into_one_a_receptionist_raised(hotel):
    people, db, _ = hotel
    raise_job(db, people["front_desk"], reason="Guest phoned about the AC",
              priority="high")
    guest_asks(reason="The AC is still not working")
    outstanding = live_jobs(db)
    assert len(outstanding) == 1
    # The priority the desk set survives; a guest does not triage their own request.
    assert outstanding[0]["priority"] == "high"
    assert outstanding[0]["reason"] == (
        "Guest phoned about the AC\nThe AC is still not working")


def test_an_empty_reason_from_a_guest_is_allowed(hotel):
    _people, db, _ = hotel
    # "Something is wrong in 204" is still worth knowing.
    assert guest_asks(reason="")["received"] is True
    assert live_jobs(db)[0]["reason"] == ""


def test_an_empty_second_press_does_not_blank_the_reason(hotel):
    _people, db, _ = hotel
    guest_asks(reason="Spill on the carpet")
    guest_asks(reason="")
    assert live_jobs(db)[0]["reason"] == "Spill on the carpet"


def test_a_request_for_a_room_that_does_not_exist_is_404_to_a_guest_too(hotel):
    _people, _db, _ = hotel
    assert refused(housekeeping.guest_request, room_id="nope",
                   payload=GuestRequestIn(reason="x")).status_code == 404
    assert refused(housekeeping.guest_room_card, room_id="nope").status_code == 404


def test_a_flood_from_one_room_is_refused(hotel):
    _people, db, _ = hotel
    limit = housekeeping.GUEST_REQUESTS_PER_ROOM.limit
    run(housekeeping.GUEST_REQUESTS_PER_ROOM.reset())
    run(housekeeping.GUEST_REQUESTS_PER_ADDRESS.reset())
    for _ in range(limit):
        assert guest_asks(reason="Towels")["received"] is True

    refusal = refused(housekeeping.guest_request, room_id="r101",
                      payload=GuestRequestIn(reason="Towels"))
    assert refusal.status_code == 429
    assert "front desk" in refusal.detail
    # And it says nothing about which limit, or how many are left.
    assert str(limit) not in refusal.detail
    # The flood cost a counter, not a hundred cards on the attendant's screen.
    assert len(live_jobs(db)) == 1


def test_one_room_being_flooded_does_not_silence_the_room_next_door(hotel):
    _people, db, _ = hotel
    run(housekeeping.GUEST_REQUESTS_PER_ROOM.reset())
    run(housekeeping.GUEST_REQUESTS_PER_ADDRESS.reset())
    for _ in range(housekeeping.GUEST_REQUESTS_PER_ROOM.limit):
        guest_asks(reason="Towels")
    assert refused(housekeeping.guest_request, room_id="r101",
                   payload=GuestRequestIn()).status_code == 429
    # 102's own budget is untouched: the key is the room, not the hotel's one address.
    assert guest_asks(room_id="r102", reason="Towels")["received"] is True


def test_the_limiters_use_the_shared_implementation(hotel):
    # Not a second one. Two rate limiters is how two of them end up with different
    # behaviour and only one of them gets fixed.
    from services.ratelimit import RateLimiter
    assert isinstance(housekeeping.GUEST_REQUESTS_PER_ROOM, RateLimiter)
    assert isinstance(housekeeping.GUEST_REQUESTS_PER_ADDRESS, RateLimiter)
    assert housekeeping.GUEST_REQUESTS_PER_ROOM.name != housekeeping.GUEST_REQUESTS_PER_ADDRESS.name


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
