"""Room types and the physical rooms belonging to them."""
from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import OutOfOrderIn, Room, RoomIn, RoomType, RoomTypeIn
from security import require_access, require_configuration
from services.availability import count_available, ranges_overlap, room_is_available

router = APIRouter()

# Room types and rooms are configuration — what everything else is priced and booked
# against — so only the admin changes them. Marking a room out of order is not: a burst
# pipe at 2am is housekeeping, and it stays with the manager on duty (see below).
#
# Setup-time: rooms and room types are the first thing a new hotel enters, and a
# property waiting for approval that could not describe its own rooms would have nothing
# to be approved on.
CONFIG = require_configuration("hotel", setup_time=True)

# The Rooms screen reads both lists; the front desk reads /rooms too, to pick the room a
# guest is checked into, so that one endpoint names both screens.
READ_ROOM_TYPES = require_access("hotel", permission=("hotel.rooms", "hotel.rates"),
                                 setup_time=True)
READ_ROOMS = require_access("hotel", permission=("hotel.rooms", "hotel.front_desk"),
                            setup_time=True)
# Not setup-time: blocking a room for a burst pipe is running the hotel, not describing
# it, and a property that cannot take a booking has nothing to protect from the block.
OUT_OF_ORDER = require_access("hotel", "admin", "manager", permission="hotel.rooms")

# Statuses that mean a booking still matters when deleting inventory or warning
# about a maintenance block.
LIVE_STATUSES = ["tentative", "confirmed", "checked_in"]


def _validate_occupancy(base_occupancy: int, max_occupancy: int) -> None:
    if max_occupancy < base_occupancy:
        raise HTTPException(400, "max_occupancy cannot be below base_occupancy")


# --------------------------- room types ---------------------------
@router.get("/room-types")
async def list_room_types(user: dict = Depends(READ_ROOM_TYPES)):
    return await db.room_types.find({}, {"_id": 0}).to_list(200)


@router.post("/room-types")
async def create_room_type(payload: RoomTypeIn, user: dict = Depends(CONFIG)):
    _validate_occupancy(payload.base_occupancy, payload.max_occupancy)
    rt = RoomType(**payload.model_dump()).model_dump()
    await db.room_types.insert_one(rt)
    rt.pop("_id", None)
    return rt


@router.put("/room-types/{type_id}")
async def update_room_type(type_id: str, payload: RoomTypeIn, user: dict = Depends(CONFIG)):
    _validate_occupancy(payload.base_occupancy, payload.max_occupancy)
    result = await db.room_types.update_one({"id": type_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Room type not found")
    return await db.room_types.find_one({"id": type_id}, {"_id": 0})


@router.delete("/room-types/{type_id}")
async def delete_room_type(type_id: str, user: dict = Depends(CONFIG)):
    if not await db.room_types.find_one({"id": type_id}, {"_id": 0}):
        raise HTTPException(404, "Room type not found")

    rooms = await db.rooms.find({"room_type_id": type_id}, {"_id": 0}).to_list(500)
    if rooms:
        raise HTTPException(409, {
            "message": "Room type still has rooms",
            "rooms": [r["number"] for r in rooms],
        })

    live = await db.bookings.find(
        {"room_type_id": type_id, "status": {"$in": LIVE_STATUSES}}, {"_id": 0}
    ).to_list(50)
    if live:
        raise HTTPException(409, {
            "message": "Room type has live bookings",
            "bookings": [b.get("reference", b["id"]) for b in live],
        })

    await db.room_types.delete_one({"id": type_id})
    return {"ok": True}


# ------------------------------ rooms -----------------------------
@router.get("/rooms")
async def list_rooms(user: dict = Depends(READ_ROOMS)):
    return await db.rooms.find({}, {"_id": 0}).to_list(500)


@router.post("/rooms")
async def create_room(payload: RoomIn, user: dict = Depends(CONFIG)):
    if not await db.room_types.find_one({"id": payload.room_type_id}, {"_id": 0}):
        raise HTTPException(400, "Unknown room_type_id")
    if await db.rooms.find_one({"number": payload.number}, {"_id": 0}):
        raise HTTPException(409, "A room with this number already exists")

    room = Room(**payload.model_dump()).model_dump()
    await db.rooms.insert_one(room)
    room.pop("_id", None)
    return room


@router.put("/rooms/{room_id}")
async def update_room(room_id: str, payload: RoomIn, user: dict = Depends(CONFIG)):
    clash = await db.rooms.find_one(
        {"number": payload.number, "id": {"$ne": room_id}}, {"_id": 0}
    )
    if clash:
        raise HTTPException(409, "Another room already uses this number")
    if not await db.room_types.find_one({"id": payload.room_type_id}, {"_id": 0}):
        raise HTTPException(400, "Unknown room_type_id")

    result = await db.rooms.update_one({"id": room_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Room not found")
    return await db.rooms.find_one({"id": room_id}, {"_id": 0})


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: str, user: dict = Depends(CONFIG)):
    room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")

    live = await db.bookings.find(
        {"assigned_room_id": room_id, "status": {"$in": LIVE_STATUSES}}, {"_id": 0}
    ).to_list(50)
    if live:
        raise HTTPException(409, {
            "message": "Room has live bookings assigned to it",
            "bookings": [b.get("reference", b["id"]) for b in live],
        })

    await db.rooms.delete_one({"id": room_id})
    return {"ok": True}


@router.post("/rooms/{room_id}/out-of-order")
async def mark_out_of_order(room_id: str, payload: OutOfOrderIn, user: dict = Depends(OUT_OF_ORDER)):
    """Block a room for a half-open date range [from, to).

    Warns if the block drops room-type availability below what existing live
    bookings for that window need, but this endpoint never cancels or modifies a
    booking — moving/reassigning affected bookings is left to the front desk.
    """
    room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if payload.to_date <= payload.from_date:
        raise HTTPException(400, "'to' must be after 'from'")

    block = {"from": payload.from_date, "to": payload.to_date, "reason": payload.reason}
    await db.rooms.update_one({"id": room_id}, {"$push": {"out_of_order": block}})

    updated_room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    room_type_id = room["room_type_id"]

    rooms_of_type = await db.rooms.find({"room_type_id": room_type_id}, {"_id": 0}).to_list(500)
    live_bookings = await db.bookings.find(
        {"room_type_id": room_type_id, "status": {"$in": LIVE_STATUSES}}, {"_id": 0}
    ).to_list(1000)

    usable = sum(
        1 for r in rooms_of_type
        if room_is_available(r, payload.from_date, payload.to_date)
    )
    needed = sum(
        1 for b in live_bookings
        if ranges_overlap(payload.from_date, payload.to_date, b["check_in"], b["check_out"])
    )

    warning = None
    if needed > usable:
        remaining = count_available(room_type_id, payload.from_date, payload.to_date,
                                     rooms_of_type, live_bookings)
        warning = (
            f"This block leaves only {remaining} room(s) of this type available for "
            f"{payload.from_date}..{payload.to_date}, but {needed} live booking(s) need "
            "one. No booking was cancelled or modified — reassign or move them manually."
        )

    return {"ok": True, "room": updated_room, "warning": warning}
