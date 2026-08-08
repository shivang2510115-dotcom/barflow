"""Availability search and room bookings.

Availability is an indexed overlap query, not a maintained ledger — see the spec for why,
and for the documented double-booking window this design accepts.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import Booking, BookingIn, BookingUpdateIn, CancelIn
from security import require_roles
from services.availability import CONSUMING_STATUSES, count_available
from services.pricing import MissingRateError, daterange, quote_stay

router = APIRouter()

BOOK = require_roles("admin", "manager", "front_desk")
LIVE = list(CONSUMING_STATUSES)


async def _load_pricing_context() -> tuple[list, list, list]:
    """Rates, periods and tax slabs — everything quote_stay needs."""
    rates = await db.rates.find({}, {"_id": 0}).to_list(500)
    periods = await db.rate_periods.find({"active": True}, {"_id": 0}).to_list(200)
    slabs = await db.tax_slabs.find({"active": True}, {"_id": 0}).to_list(20)
    return rates, periods, slabs


async def _reference() -> str:
    """Human-quotable code, e.g. BF-2608-0042."""
    stamp = datetime.now(timezone.utc).strftime("%y%m")
    count = await db.bookings.count_documents({})
    return f"BF-{stamp}-{count + 1:04d}"


def _validate_window(check_in: str, check_out: str) -> None:
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")


def _validate_occupancy(room_type: dict, adults: int, children: int, extra_beds: int) -> None:
    ceiling = room_type.get("max_occupancy", 2) + room_type.get("max_extra_beds", 0)
    if adults + children > ceiling:
        raise HTTPException(
            400,
            f"{room_type['name']} sleeps at most {ceiling} "
            f"({room_type.get('max_occupancy')} plus {room_type.get('max_extra_beds')} extra beds)",
        )
    if adults < 1:
        raise HTTPException(400, "At least one adult is required")


async def _quote_or_422(room_type: dict, check_in: str, check_out: str,
                        adults: int, children: int, meal_plan: dict) -> dict:
    rates, periods, slabs = await _load_pricing_context()
    try:
        return quote_stay(
            check_in, check_out, room_type["id"], adults, children,
            room_type.get("base_occupancy", 2), meal_plan, rates, periods, slabs,
        )
    except MissingRateError as e:
        raise HTTPException(422, {
            "message": f"No rate is defined for {room_type['name']} on these dates",
            "dates": e.dates,
        })


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


@router.get("/availability")
async def availability(check_in: str, check_out: str, adults: int = 2, children: int = 0,
                       user: dict = Depends(BOOK)):
    """Free rooms and a priced quote per room type per meal plan."""
    _validate_window(check_in, check_out)

    room_types = await db.room_types.find({"active": True}, {"_id": 0}).to_list(200)
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(500)
    bookings = await db.bookings.find({"status": {"$in": LIVE}}, {"_id": 0}).to_list(5000)
    meal_plans = await db.meal_plans.find({"active": True}, {"_id": 0}).to_list(50)
    rates, periods, slabs = await _load_pricing_context()

    results = []
    for rt in room_types:
        free = count_available(rt["id"], check_in, check_out, rooms, bookings)

        quotes, unpriced = [], None
        for plan in meal_plans:
            try:
                q = quote_stay(check_in, check_out, rt["id"], adults, children,
                               rt.get("base_occupancy", 2), plan, rates, periods, slabs)
                quotes.append({**q, "meal_plan": plan})
            except MissingRateError as e:
                unpriced = e.dates
                break

        ceiling = rt.get("max_occupancy", 2) + rt.get("max_extra_beds", 0)
        results.append({
            "room_type": rt,
            "available": free,
            "quotes": quotes,
            "unpriced_dates": unpriced,
            "fits_party": adults + children <= ceiling,
        })
    return results


@router.get("/bookings/calendar")
async def calendar(start: str, end: str, user: dict = Depends(BOOK)):
    """Per-room-type occupancy for each night in the window."""
    _validate_window(start, end)

    room_types = await db.room_types.find({"active": True}, {"_id": 0}).to_list(200)
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(500)
    bookings = await db.bookings.find({"status": {"$in": LIVE}}, {"_id": 0}).to_list(5000)

    grid = []
    for rt in room_types:
        nights = []
        for day in daterange(start, end):
            free = count_available(rt["id"], day, _next_day(day), rooms, bookings)
            total = sum(1 for r in rooms if r["room_type_id"] == rt["id"] and r.get("active", True))
            nights.append({"date": day, "available": free, "total": total,
                           "occupied": max(0, total - free)})
        grid.append({"room_type": rt, "nights": nights})
    return grid


@router.get("/bookings")
async def list_bookings(start: str = "", end: str = "", status: str = "", q: str = "",
                        user: dict = Depends(BOOK)):
    query: dict = {}
    if status:
        query["status"] = status
    if start:
        query["check_out"] = {"$gt": start}
    if end:
        query["check_in"] = {"$lt": end}

    rows = await db.bookings.find(query, {"_id": 0}).to_list(2000)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    for b in rows:
        b["guest"] = guests.get(b["guest_id"])

    if q:
        needle = q.lower()
        rows = [
            b for b in rows
            if needle in (b.get("reference") or "").lower()
            or needle in ((b.get("guest") or {}).get("name") or "").lower()
            or needle in ((b.get("guest") or {}).get("phone") or "")
        ]
    return sorted(rows, key=lambda b: b["check_in"])


@router.post("/bookings")
async def create_booking(payload: BookingIn, user: dict = Depends(BOOK)):
    _validate_window(payload.check_in, payload.check_out)

    room_type = await db.room_types.find_one({"id": payload.room_type_id}, {"_id": 0})
    if not room_type:
        raise HTTPException(400, "Unknown room_type_id")
    if not await db.guests.find_one({"id": payload.guest_id}):
        raise HTTPException(400, "Unknown guest_id")
    meal_plan = await db.meal_plans.find_one({"id": payload.meal_plan_id}, {"_id": 0})
    if not meal_plan:
        raise HTTPException(400, "Unknown meal_plan_id")

    _validate_occupancy(room_type, payload.adults, payload.children, payload.extra_beds)
    quote = await _quote_or_422(room_type, payload.check_in, payload.check_out,
                                payload.adults, payload.children, meal_plan)

    # Re-checked here, immediately before the write. The spec documents the residual
    # race: without transactions this narrows the window but does not close it.
    rooms = await db.rooms.find({"room_type_id": room_type["id"]}, {"_id": 0}).to_list(500)
    live = await db.bookings.find(
        {"room_type_id": room_type["id"], "status": {"$in": LIVE}}, {"_id": 0}
    ).to_list(5000)
    if count_available(room_type["id"], payload.check_in, payload.check_out, rooms, live) < 1:
        raise HTTPException(409, {
            "message": f"No {room_type['name']} free for these dates",
            "check_in": payload.check_in, "check_out": payload.check_out,
        })

    booking = Booking(
        **payload.model_dump(),
        reference=await _reference(),
        quote=quote,
        created_by=user.get("id"),
    ).model_dump()
    await db.bookings.insert_one(booking)
    booking.pop("_id", None)
    return booking


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: str, user: dict = Depends(BOOK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    booking["guest"] = await db.guests.find_one({"id": booking["guest_id"]}, {"_id": 0})
    booking["room_type"] = await db.room_types.find_one(
        {"id": booking["room_type_id"]}, {"_id": 0})
    booking["meal_plan"] = await db.meal_plans.find_one(
        {"id": booking["meal_plan_id"]}, {"_id": 0})
    return booking


@router.put("/bookings/{booking_id}")
async def update_booking(booking_id: str, payload: BookingUpdateIn, user: dict = Depends(BOOK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] in ("cancelled", "checked_out", "no_show"):
        raise HTTPException(409, f"A {booking['status']} booking cannot be edited")

    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not changes:
        return booking

    merged = {**booking, **changes}
    _validate_window(merged["check_in"], merged["check_out"])

    room_type = await db.room_types.find_one({"id": merged["room_type_id"]}, {"_id": 0})
    meal_plan = await db.meal_plans.find_one({"id": merged["meal_plan_id"]}, {"_id": 0})
    _validate_occupancy(room_type, merged["adults"], merged["children"], merged["extra_beds"])

    repricing = any(k in changes for k in
                    ("check_in", "check_out", "adults", "children", "meal_plan_id"))
    if repricing:
        rooms = await db.rooms.find({"room_type_id": room_type["id"]}, {"_id": 0}).to_list(500)
        live = await db.bookings.find({
            "room_type_id": room_type["id"], "status": {"$in": LIVE},
            "id": {"$ne": booking_id},
        }, {"_id": 0}).to_list(5000)

        if count_available(room_type["id"], merged["check_in"],
                           merged["check_out"], rooms, live) < 1:
            raise HTTPException(409, {
                "message": "Those dates are full — the booking was not changed",
                "check_in": merged["check_in"], "check_out": merged["check_out"],
            })

        changes["quote"] = await _quote_or_422(
            room_type, merged["check_in"], merged["check_out"],
            merged["adults"], merged["children"], meal_plan)

    await db.bookings.update_one({"id": booking_id}, {"$set": changes})
    return await db.bookings.find_one({"id": booking_id}, {"_id": 0})


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, payload: CancelIn, user: dict = Depends(BOOK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] == "checked_in":
        raise HTTPException(409, "Check the guest out instead of cancelling")
    if booking["status"] == "cancelled":
        return booking

    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "cancellation_reason": payload.reason,
    }})
    return await db.bookings.find_one({"id": booking_id}, {"_id": 0})
