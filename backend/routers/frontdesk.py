"""Front desk: arrivals, departures, in-house and check-in."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from models.folio import CheckInIn, CheckOutIn, Folio
from routers.bookings import room_for_booking_or_409
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import SHARED
from services.clock import today

router = APIRouter()

# Arrivals, departures and the in-house board, plus checking a guest in and out. All
# operational: the desk runs the day, the admin sets the rates.
DESK = require_access("hotel", "admin", "manager", "front_desk", permission="hotel.front_desk")
# Checking out is reached from the booking as well as from the board, so it takes either.
CHECK_OUT = require_access("hotel", "admin", "manager", "front_desk",
                           permission=("hotel.front_desk", "hotel.bookings"))
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
# The booking and the folio go through _pos_booking and _pos_folio for the same reason:
# widening the domain without projecting them would hand every outlet waiter the
# booking notes and the folio balance of every in-house guest.
IN_HOUSE_LOOKUP = require_access(SHARED, "admin", "manager", "front_desk", "waiter",
                                 permission=("outlet.pos", "hotel.front_desk"))


def _today() -> str:
    """The property's today, not the server's.

    Booking dates are plain local calendar dates, so comparing them against a UTC
    date shows the wrong board between midnight and 05:30 IST: the desk would see
    yesterday's arrivals during the night shift, which is exactly when someone is
    checking in a late flight.
    """
    return today()


@router.get("/front-desk")
async def front_desk(user: dict = Depends(DESK),
                     db: PropertyScopedDatabase = Depends(tenant_db)):
    day = _today()
    arrivals = await db.bookings.find(
        {"check_in": day, "status": {"$in": ["tentative", "confirmed"]}}, {"_id": 0}
    ).to_list(20000)
    departures = await db.bookings.find(
        {"check_out": day, "status": "checked_in"}, {"_id": 0}).to_list(20000)
    in_house_rows = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(20000)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(20000)}

    def decorate(rows: list[dict]) -> list[dict]:
        out = [{**b, "guest": guests.get(b["guest_id"]),
                "room": rooms.get(b.get("assigned_room_id"))} for b in rows]
        return sorted(out, key=lambda b: b["check_in"])

    return {"date": day,
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


def _pos_booking(booking: dict | None) -> dict | None:
    """Project a booking down to what the POS needs: the row's identity, and nothing
    else. This route is declared shared, so any user holding any domain reaches it and
    every field left in the response is a field an outlet waiter can read. The POS
    reads no booking field at all — it labels the row from the room and the guest and
    settles against the folio id — so the free-text notes, the priced quote, the rate
    and occupancy, the source and who created the booking all stay on the server."""
    if not booking:
        return None
    return {"id": booking.get("id")}


def _pos_folio(folio: dict | None) -> dict | None:
    """Project a folio down to what the POS needs: the id to settle a room bill
    against. Same reasoning as _pos_booking — this route is shared, so anything kept
    here is visible to every outlet waiter and outlet manager. The balance is a hotel
    figure the POS never displays, and the endpoint already filters to open folios, so
    neither balance nor status is worth handing to an outlet."""
    if not folio:
        return None
    return {"id": folio.get("id")}


@router.get("/in-house")
async def in_house(q: str = "", user: dict = Depends(IN_HOUSE_LOOKUP),
                   db: PropertyScopedDatabase = Depends(tenant_db)):
    """Checked-in guests, for the POS room search. Only in-house bookings are
    chargeable — a departed folio must never be reachable from the POS."""
    bookings = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(20000)
    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(20000)}
    folios = {f["booking_id"]: f for f in await db.folios.find(
        {"status": "open"}, {"_id": 0}).to_list(20000)}

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
        r["booking"] = _pos_booking(r["booking"])
        r["folio"] = _pos_folio(r["folio"])
    return rows


@router.post("/bookings/{booking_id}/check-in")
async def check_in(booking_id: str, payload: CheckInIn, user: dict = Depends(DESK),
                   db: PropertyScopedDatabase = Depends(tenant_db)):
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

    # The same rule the pre-assignment endpoint applies, and deliberately the same code.
    # This used to ask only whether another *checked-in* booking held the room, which
    # was true enough when check-in was the only thing that could assign one. Now a
    # confirmed booking holds a room too, and the old question would give away a room
    # already held for tomorrow's arrival. It also now refuses a room that is out of
    # order for part of the stay, which the old check never looked at.
    #
    # The booking is free to arrive with a different room than it was pre-assigned: the
    # guest is at the desk and may need another one, and the desk decides.
    room = await room_for_booking_or_409(db, booking, payload.room_id)

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
async def check_out(booking_id: str, payload: CheckOutIn, user: dict = Depends(CHECK_OUT),
                    db: PropertyScopedDatabase = Depends(tenant_db)):
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
    await post_due_nights(db, folio["id"])
    balance = await _sync_balance(db, folio["id"])

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
