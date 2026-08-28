"""Housekeeping: the state of every room, and the append-only log of who changed it.

The rules are not here. Which statuses exist, what "ready" means and who may move a room
from one status to another all live in `services/housekeeping.py`, as pure functions with
no database under them; this router reads, writes and refuses. Anything in here that
looks like a policy decision is a bug — it belongs one file over, where it can be tested
without a server.

**The two out-of-order concepts are different things and this file touches only one of
them.** `rooms.out_of_order` is a list of date ranges that removes a room from sale and
belongs to `routers/rooms.py`; `housekeeping_status = "out_of_order"` means the room is
not usable right now and stops the desk assigning it. Nothing here reads or writes a date
range. An attendant's tap must never cost the hotel a booking.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from models.hotel import HousekeepingEvent, HousekeepingStatusIn
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.clock import today
from services.housekeeping import (
    OUT_OF_ORDER, STATUSES, can_set, is_ready, note_required, status_of)

router = APIRouter()

# Everyone whose work involves the state of a room. One declaration for the board and for
# the status change, because the finer question — *which* status this person may set on
# *this* room — needs the status the room is in now, which a dependency does not have.
# That question is `can_set`, and it is answered inside the handler with a 403.
#
# `admin` is in the tuple like every other role tuple in this application: the role check
# in `can_access` runs before the admin bypass, so an admin left out of the list is an
# admin refused.
ATTENDANT = require_access("hotel", "admin", "manager", "front_desk", "housekeeping",
                           permission="hotel.housekeeping")

# How many log lines one room's history hands back at most. The log is append-only and a
# room accumulates a few lines a day for years, so it is read newest-first and capped
# rather than returned whole.
EVENT_PAGE = 200


async def apply_status(db: PropertyScopedDatabase, room: dict, to_status: str, *,
                       note: str | None, changed_by: str | None) -> dict | None:
    """Move a room to a status and write the log line. Returns the event, or `None`.

    `None` means the room was already in that status and nothing happened — no write, no
    event. That is the design's rule about a double-tap on a phone in a corridor, and it
    lives here rather than in each caller because check-out uses this path too: a booking
    checked out of a room that somebody had already marked dirty must not add a second
    identical line to the log.

    The note is stored as the room's *current* note and therefore replaces whatever was
    there. Clearing `out_of_order` with no note clears the "burst pipe" that justified it,
    which is right — the pipe is fixed — and loses nothing, because the log line that put
    the room out of order still carries those words and is never updated.

    No permission check. This is the mechanism; `can_set` is the policy, and both callers
    apply it first. Keeping them apart is what lets check-out dirty a room without
    inventing a role for the automatic transition.
    """
    current = status_of(room)
    if current == to_status:
        return None

    now = datetime.now(timezone.utc).isoformat()
    note = (note or "").strip() or None
    await db.rooms.update_one({"id": room["id"]}, {"$set": {
        "housekeeping_status": to_status,
        "housekeeping_note": note,
        "housekeeping_updated_at": now,
        "housekeeping_updated_by": changed_by,
    }})
    event = HousekeepingEvent(
        room_id=room["id"], from_status=current, to_status=to_status, note=note,
        changed_by=changed_by, changed_at=now).model_dump()
    await db.housekeeping_events.insert_one(event)
    event.pop("_id", None)
    return event


def _room_card(room: dict, role: str, *, occupied: bool, departing: bool) -> dict:
    """One room, as the attendant's screen needs it.

    `can_set` is computed here and sent, rather than left for the client to work out from
    the status. The transition table is the product — an `out_of_order` room offers an
    attendant nothing at all, and a screen that decided that for itself would be a second
    copy of the table, disagreeing with this one the first time either changed.
    """
    status = status_of(room)
    return {
        "id": room["id"],
        "number": room.get("number"),
        "floor": room.get("floor"),
        "block": room.get("block"),
        "room_type_id": room.get("room_type_id"),
        "housekeeping_status": status,
        "housekeeping_note": room.get("housekeeping_note"),
        "housekeeping_updated_at": room.get("housekeeping_updated_at"),
        "housekeeping_updated_by": room.get("housekeeping_updated_by"),
        "ready": is_ready(status),
        "occupied": occupied,
        "departing_today": departing,
        "can_set": [s for s in STATUSES if can_set(role, status, s)],
    }


def _sort_key(card: dict):
    """Floor, then room number, and numerically where the number is a number — otherwise
    room 10 sorts between 1 and 2 on a screen somebody is holding in a corridor."""
    number = str(card.get("number") or "")
    return (str(card.get("floor") or ""), 0 if number.isdigit() else 1,
            int(number) if number.isdigit() else 0, number)


@router.get("/housekeeping")
async def housekeeping_board(user: dict = Depends(ATTENDANT),
                             db: PropertyScopedDatabase = Depends(tenant_db)):
    """Every room, its status, whether somebody is in it and whether they leave today.

    Departing-today is what tells an attendant which rooms are about to turn, which is
    the whole reason this is one call rather than the rooms list plus a guess.

    Three queries, none of them per room: the rooms, the bookings that are checked in,
    and nothing else. A property with three hundred rooms is three reads.
    """
    day = today()
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(20000)
    in_house = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(20000)

    occupied = {b.get("assigned_room_id") for b in in_house if b.get("assigned_room_id")}
    departing = {b["assigned_room_id"] for b in in_house
                 if b.get("assigned_room_id") and b.get("check_out") == day}

    role = user.get("role")
    cards = [_room_card(r, role, occupied=r["id"] in occupied,
                        departing=r["id"] in departing) for r in rooms]
    cards.sort(key=_sort_key)
    return {"date": day, "rooms": cards}


@router.put("/rooms/{room_id}/housekeeping")
async def set_housekeeping_status(room_id: str, payload: HousekeepingStatusIn,
                                  user: dict = Depends(ATTENDANT),
                                  db: PropertyScopedDatabase = Depends(tenant_db)):
    """Set a room's housekeeping status, enforcing the transition table.

    The order of the checks is the design's error table, and it is deliberate:

    * an unknown status never reaches here — the request body is typed, so it is a 422;
    * a room this property does not have is a 404;
    * **the status the room already has is a no-op**, answered before anything else is
      asked. It writes nothing and returns the room unchanged, so a double-tap on a phone
      does not fill an append-only log with noise, and does not need a note to be
      re-supplied to say what the room already says;
    * `out_of_order` with no note is a 400 — a room marked broken with no reason is one
      nobody can fix and nobody can put back;
    * anything the transition table refuses is a 403. An attendant setting `inspected`
      lands here, as does one trying to take a room back out of `out_of_order`.

    Marking an occupied room dirty is allowed and always has been: mid-stay mess is
    normal, and status never blocks anything for the guest already in the room.
    """
    room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")

    current = status_of(room)
    if payload.status == current:
        return {"room": room, "event": None, "changed": False}

    if note_required(payload.status) and not (payload.note or "").strip():
        raise HTTPException(400, "A note is required to mark a room out of order")

    if not can_set(user.get("role"), current, payload.status):
        raise HTTPException(403, _refusal(user.get("role"), current, payload.status))

    event = await apply_status(db, room, payload.status, note=payload.note,
                               changed_by=user.get("id"))
    return {"room": await db.rooms.find_one({"id": room_id}, {"_id": 0}),
            "event": event, "changed": True}


def _refusal(role: str | None, current: str, wanted: str) -> str:
    """What a refused transition says. Two of them are worth naming, because the person
    reading the message is standing in the room and needs to know what to do instead."""
    if current == OUT_OF_ORDER:
        return ("Only a manager can take a room back out of out-of-order, once the fault "
                "is confirmed fixed")
    if wanted == "inspected":
        return "Only a manager can mark a room inspected"
    return f"A {role} cannot move a room from {current} to {wanted}"


@router.get("/rooms/{room_id}/housekeeping/events")
async def housekeeping_events(room_id: str, user: dict = Depends(ATTENDANT),
                              db: PropertyScopedDatabase = Depends(tenant_db)):
    """One room's history, newest first.

    The reason the log is worth keeping and the reason attendants have individual logins:
    when a guest says the room was filthy, this answers who marked it clean and when.
    Append-only — there is no route here that edits or deletes a line, and there is not
    going to be one.
    """
    if not await db.rooms.find_one({"id": room_id}, {"_id": 0}):
        raise HTTPException(404, "Room not found")
    events = await db.housekeeping_events.find({"room_id": room_id}, {"_id": 0}).to_list(20000)
    events.sort(key=lambda e: e.get("changed_at") or "", reverse=True)
    return events[:EVENT_PAGE]
