"""Front desk: arrivals, departures, in-house and check-in."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.folio import CheckInIn, CheckOutIn, Folio
from security import require_access
from services.access import SHARED

router = APIRouter()

DESK = require_access("hotel", "admin", "manager", "front_desk")
# A waiter needs to find the in-house guest to charge at the POS, and nothing more:
# not the front-desk board, not check-in, not check-out.
#
# Declared shared, not hotel: an outlet waiter's domains are ["bar"] or ["restaurant"],
# never "hotel", so a hotel-only declaration 403s the very caller the waiter role was
# put on this list for — the POS asks for /in-house the moment payment method "room" is
# chosen, finds no folio, and a bar bill can no longer be charged to a room. Widening
# the domain leaks nothing, because every guest here goes through _pos_guest, which
# projects down to id, name and phone for all callers alike: no id_proof_number, no
# address, email or notes reach any caller of this route. Same reasoning that puts
# guest records on shared — a bar regular and a hotel guest are the same person.
IN_HOUSE_LOOKUP = require_access(SHARED, "admin", "manager", "front_desk", "waiter")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@router.get("/front-desk")
async def front_desk(user: dict = Depends(DESK)):
    today = _today()
    arrivals = await db.bookings.find(
        {"check_in": today, "status": {"$in": ["tentative", "confirmed"]}}, {"_id": 0}
    ).to_list(500)
    departures = await db.bookings.find(
        {"check_out": today, "status": "checked_in"}, {"_id": 0}).to_list(500)
    in_house_rows = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(500)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(500)}

    def decorate(rows: list[dict]) -> list[dict]:
        out = [{**b, "guest": guests.get(b["guest_id"]),
                "room": rooms.get(b.get("assigned_room_id"))} for b in rows]
        return sorted(out, key=lambda b: b["check_in"])

    return {"date": today,
            "arrivals": decorate(arrivals),
            "departures": decorate(departures),
            "in_house": decorate(in_house_rows)}


def _pos_guest(guest: dict | None) -> dict | None:
    """Project a guest down to what the POS needs to charge a room: enough to look
    the guest up and label the button. Waiters can reach this endpoint but have no
    other access to guest records, so id_proof_number, id_proof_type, address,
    email and notes must never leave the server here."""
    if not guest:
        return None
    return {"id": guest.get("id"), "name": guest.get("name"), "phone": guest.get("phone")}


@router.get("/in-house")
async def in_house(q: str = "", user: dict = Depends(IN_HOUSE_LOOKUP)):
    """Checked-in guests, for the POS room search. Only in-house bookings are
    chargeable — a departed folio must never be reachable from the POS."""
    bookings = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(500)
    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(500)}
    folios = {f["booking_id"]: f for f in await db.folios.find(
        {"status": "open"}, {"_id": 0}).to_list(500)}

    rows = []
    for b in bookings:
        folio = folios.get(b["id"])
        if not folio:
            continue
        rows.append({"booking": b, "guest": guests.get(b["guest_id"]),
                     "room": rooms.get(b.get("assigned_room_id")), "folio": folio})

    if q:
        needle = q.lower()
        rows = [
            r for r in rows
            if needle in ((r["room"] or {}).get("number") or "").lower()
            or needle in ((r["guest"] or {}).get("name") or "").lower()
            or needle in ((r["guest"] or {}).get("phone") or "")
        ]

    for r in rows:
        r["guest"] = _pos_guest(r["guest"])
    return rows


@router.post("/bookings/{booking_id}/check-in")
async def check_in(booking_id: str, payload: CheckInIn, user: dict = Depends(DESK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] == "checked_in":
        raise HTTPException(409, "This booking is already checked in")
    if booking["status"] not in ("tentative", "confirmed"):
        raise HTTPException(409, f"A {booking['status']} booking cannot be checked in")

    # Legally required in India, and the guest is physically present — capture it here
    # rather than at booking, where often only a phone number is known.
    if not payload.id_proof_type.strip() or not payload.id_proof_number.strip():
        raise HTTPException(400, "ID proof type and number are required at check-in")

    room = await db.rooms.find_one({"id": payload.room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if room["room_type_id"] != booking["room_type_id"]:
        raise HTTPException(409, "That room is not of the booked room type")
    if not room.get("active", True):
        raise HTTPException(409, "That room is inactive")

    clash = await db.bookings.find_one({
        "assigned_room_id": payload.room_id, "status": "checked_in",
        "id": {"$ne": booking_id}})
    if clash:
        raise HTTPException(409, f"Room {room['number']} is occupied by {clash['reference']}")

    now = datetime.now(timezone.utc).isoformat()
    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "status": "checked_in", "assigned_room_id": payload.room_id, "checked_in_at": now}})
    await db.guests.update_one({"id": booking["guest_id"]}, {"$set": {
        "id_proof_type": payload.id_proof_type.strip(),
        "id_proof_number": payload.id_proof_number.strip()}})

    folio = await db.folios.find_one({"booking_id": booking_id}, {"_id": 0})
    if not folio:
        folio = Folio(booking_id=booking_id, guest_id=booking["guest_id"]).model_dump()
        await db.folios.insert_one(folio)
        folio.pop("_id", None)

    return {"booking": await db.bookings.find_one({"id": booking_id}, {"_id": 0}),
            "folio": folio,
            "room": room}


@router.post("/bookings/{booking_id}/check-out")
async def check_out(booking_id: str, payload: CheckOutIn, user: dict = Depends(DESK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] != "checked_in":
        raise HTTPException(409, f"A {booking['status']} booking cannot be checked out")

    folio = await db.folios.find_one({"booking_id": booking_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "No folio for this booking")

    # Post any nights still due before deciding, or a departing guest is undercharged.
    # Imported here rather than at module scope to keep the two routers independent.
    from routers.folios import post_due_nights, _sync_balance
    await post_due_nights(folio["id"])
    balance = await _sync_balance(folio["id"])

    if abs(balance) > 0.005:
        if not payload.force:
            raise HTTPException(409, {
                "message": "Folio has an outstanding balance",
                "balance": balance})
        if user.get("role") not in ("admin", "manager"):
            raise HTTPException(403, "Only a manager can check out an unsettled folio")
        if not (payload.reason or "").strip():
            raise HTTPException(400, "A reason is required to check out with a balance")

    now = datetime.now(timezone.utc).isoformat()
    settled = abs(balance) <= 0.005
    await db.folios.update_one({"id": folio["id"]}, {"$set": {
        "status": "settled" if settled else "closed_unpaid",
        "closed_at": now,
        "closed_reason": None if settled else payload.reason.strip()}})
    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "status": "checked_out", "checked_out_at": now}})

    return {"booking": await db.bookings.find_one({"id": booking_id}, {"_id": 0}),
            "folio": await db.folios.find_one({"id": folio["id"]}, {"_id": 0}),
            "balance": balance}
