"""Availability search and room bookings.

Availability is an indexed overlap query, not a maintained ledger — see the spec for why,
and for the documented double-booking window this design accepts.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

# The property record is unscoped by definition — it is the thing everything else is
# scoped *to*. Imported as a module and read through the attribute, never bound at
# import, so a test that swaps the handle swaps this too. Same arrangement as
# routers/orders.py, which reads the outlet's GST rate off the same record.
import db as _db_module
from models.hotel import (
    Booking, BookingIn, BookingUpdateIn, CancelIn, ExtendStayIn, RoomAssignmentIn)
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.availability import (
    CONSUMING_STATUSES, blocking_out_of_order, booking_holding_room, count_available)
from services.pricing import (
    MissingRateError, daterange, meal_plans_enabled, quote_stay)

router = APIRouter()

# Rooms are the hotel's business: a restaurant-only manager holds the right role but not
# the right domain, and is refused. The rest of the call sites move to require_access in
# the migration task; this one moves early because the staff API is what finally makes a
# domain-scoped user creatable, and therefore makes the refusal testable.
# Taking, amending and cancelling a booking is operational, not configuration: a
# receptionist who cannot take a booking is not a receptionist. Availability is the
# search behind the new-booking screen, so it carries the same key.
BOOK = require_access("hotel", "admin", "manager", "front_desk", permission="hotel.bookings")
# The occupancy chart is its own screen and its own tick.
CALENDAR = require_access("hotel", "admin", "manager", "front_desk", permission="hotel.calendar")
# Giving a booking a room is operational, not configuration: a returning guest asking
# for the same room, a family wanting adjacent doors, a ground-floor room held for
# someone who cannot manage stairs — that is the receptionist's job, and require_configuration
# would mean the desk writes it on paper instead. It is reached from the booking screen
# and from the desk while preparing tomorrow's arrivals, so it names both screen keys
# the way check-out does.
ASSIGN = require_access("hotel", "admin", "manager", "front_desk",
                        permission=("hotel.bookings", "hotel.front_desk"))
# Extending a stay is the same shape of act as assigning a room, and takes the same key:
# the request arrives at the desk, from a guest standing in front of it or on the phone,
# and a receptionist who has to fetch the owner to add two nights is a receptionist the
# hotel works around. It is reached from the booking screen and from the front-desk
# board, so it names both screen keys.
EXTEND = require_access("hotel", "admin", "manager", "front_desk",
                        permission=("hotel.bookings", "hotel.front_desk"))
LIVE = list(CONSUMING_STATUSES)

# The three an amendment is refused for, and the one list both `update_booking` and
# `extend_stay` ask. A cancelled or no-show booking holds nothing to extend and a
# departed one is history — adding a night to it would put a charge on a settled folio.
CLOSED_TO_AMENDMENT = ("cancelled", "checked_out", "no_show")


async def _property_record(user: dict) -> dict | None:
    """The caller's own hotel, for the settings that change how a booking is priced.

    Read through the unscoped handle because that is where properties live; the caller
    can only ever name their own, because the id comes from their token and never from
    the request.
    """
    property_id = user.get("property_id")
    if not property_id:
        return None
    return await _db_module.unscoped_db.properties.find_one(
        {"id": property_id}, {"_id": 0})


async def _plans_on(user: dict) -> bool:
    return meal_plans_enabled(await _property_record(user))


async def _plan_for_booking(db, booking: dict) -> dict | None:
    """The meal plan that prices this booking, now and for the rest of its life.

    **The plan on the booking wins, not the setting on the property.** A booking taken on
    half board keeps being priced on half board even after the hotel switches meal plans
    off, because the guest was quoted a number that included it; and a booking taken at
    the all-inclusive rate stays plan-less even after the hotel switches them on. The
    property setting decides what a *new* booking may be taken on and what the screens
    ask for — it is not a retrospective repricing of everything already sold.
    """
    plan_id = booking.get("meal_plan_id")
    if not plan_id:
        return None
    return await db.meal_plans.find_one({"id": plan_id}, {"_id": 0})


async def _load_pricing_context(db) -> tuple[list, list, list]:
    """Rates, periods and tax slabs — everything quote_stay needs."""
    rates = await db.rates.find({}, {"_id": 0}).to_list(20000)
    periods = await db.rate_periods.find({"active": True}, {"_id": 0}).to_list(5000)
    slabs = await db.tax_slabs.find({"active": True}, {"_id": 0}).to_list(20)
    return rates, periods, slabs


async def _reference(db) -> str:
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


async def _quote_or_422(db, room_type: dict, check_in: str, check_out: str,
                        adults: int, children: int, meal_plan: dict | None) -> dict:
    rates, periods, slabs = await _load_pricing_context(db)
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
                       user: dict = Depends(BOOK),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    """Free rooms and a priced quote per room type.

    One quote per meal plan when the property sells them, and a single all-inclusive
    quote — `meal_plan: None` — when it does not. The shape of the response is the same
    either way, a list of quotes, so the new-booking screen renders one price per room
    type without a second code path.
    """
    _validate_window(check_in, check_out)

    room_types = await db.room_types.find({"active": True}, {"_id": 0}).to_list(5000)
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(20000)
    bookings = await db.bookings.find({"status": {"$in": LIVE}}, {"_id": 0}).to_list(5000)
    # `[None]` rather than an empty list: a property with plans off still gets exactly one
    # quote per room type. An empty list here would produce a room type with no price at
    # all, which the screen cannot tell apart from a room type nobody has set a rate for.
    plans = (await db.meal_plans.find({"active": True}, {"_id": 0}).to_list(50)
             if await _plans_on(user) else [None])
    rates, periods, slabs = await _load_pricing_context(db)

    results = []
    for rt in room_types:
        free = count_available(rt["id"], check_in, check_out, rooms, bookings)

        quotes, unpriced = [], None
        for plan in plans:
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
async def calendar(start: str, end: str, user: dict = Depends(CALENDAR),
                   db: PropertyScopedDatabase = Depends(tenant_db)):
    """Per-room-type occupancy for each night in the window."""
    _validate_window(start, end)

    room_types = await db.room_types.find({"active": True}, {"_id": 0}).to_list(5000)
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(20000)
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
                        user: dict = Depends(BOOK),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    query: dict = {}
    if status:
        query["status"] = status
    if start:
        query["check_out"] = {"$gt": start}
    if end:
        query["check_in"] = {"$lt": end}

    rows = await db.bookings.find(query, {"_id": 0}).to_list(2000)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    # The room comes back on the list, not just on the booking, because "who still needs
    # a room for tomorrow" is a question about the whole list and is asked every morning.
    # Read as one lookup and joined here rather than per row, the way the front-desk
    # board does it.
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(20000)}
    for b in rows:
        b["guest"] = guests.get(b["guest_id"])
        b["room"] = rooms.get(b.get("assigned_room_id"))

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
async def create_booking(payload: BookingIn, user: dict = Depends(BOOK),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    _validate_window(payload.check_in, payload.check_out)

    room_type = await db.room_types.find_one({"id": payload.room_type_id}, {"_id": 0})
    if not room_type:
        raise HTTPException(400, "Unknown room_type_id")
    if not await db.guests.find_one({"id": payload.guest_id}):
        raise HTTPException(400, "Unknown guest_id")

    # Which pricing model this hotel sells on. With plans on, nothing below has changed:
    # a plan is required and must exist, and a booking without one is still refused — as
    # a 400 naming the field now that the model no longer refuses it as a 422, which is a
    # message the booking screen can put beside the input.
    #
    # With plans off the room rate is all-inclusive, so there is no plan to name. A plan
    # id sent anyway is dropped rather than honoured: this property does not sell one,
    # and charging a guest a breakfast supplement it does not price separately because a
    # stale client sent an id is worse than ignoring the id.
    if await _plans_on(user):
        if not payload.meal_plan_id:
            raise HTTPException(400, "meal_plan_id is required — this property quotes "
                                     "per meal plan")
        meal_plan = await db.meal_plans.find_one({"id": payload.meal_plan_id}, {"_id": 0})
        if not meal_plan:
            raise HTTPException(400, "Unknown meal_plan_id")
    else:
        meal_plan = None

    _validate_occupancy(room_type, payload.adults, payload.children, payload.extra_beds)
    quote = await _quote_or_422(db, room_type, payload.check_in, payload.check_out,
                                payload.adults, payload.children, meal_plan)

    # Re-checked here, immediately before the write. The spec documents the residual
    # race: without transactions this narrows the window but does not close it.
    rooms = await db.rooms.find({"room_type_id": room_type["id"]}, {"_id": 0}).to_list(20000)
    live = await db.bookings.find(
        {"room_type_id": room_type["id"], "status": {"$in": LIVE}}, {"_id": 0}
    ).to_list(5000)
    if count_available(room_type["id"], payload.check_in, payload.check_out, rooms, live) < 1:
        raise HTTPException(409, {
            "message": f"No {room_type['name']} free for these dates",
            "check_in": payload.check_in, "check_out": payload.check_out,
        })

    # The package this stay was sold with, taken from the rate that priced it and
    # copied onto the booking. A booking stores no rate_id on purpose: a rate is
    # editable, and a price change next month must not retroactively change what a
    # guest already staying was entitled to.
    priced_on = await db.rates.find_one(
        {"room_type_id": room_type["id"], "package_id": {"$ne": None}}, {"_id": 0})
    package_id = (priced_on or {}).get("package_id")

    booking = Booking(
        **{**payload.model_dump(),
           "meal_plan_id": meal_plan["id"] if meal_plan else None},
        package_id=package_id,
        reference=await _reference(db),
        quote=quote,
        created_by=user.get("id"),
    ).model_dump()
    await db.bookings.insert_one(booking)
    booking.pop("_id", None)
    return booking


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: str, user: dict = Depends(BOOK),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    booking["guest"] = await db.guests.find_one({"id": booking["guest_id"]}, {"_id": 0})
    booking["room_type"] = await db.room_types.find_one(
        {"id": booking["room_type_id"]}, {"_id": 0})
    # None for a booking taken at an all-inclusive rate. The screen shows no plan row
    # rather than an empty one — see BookingDetail.jsx, which already skips a null fact.
    booking["meal_plan"] = await _plan_for_booking(db, booking)
    booking["room"] = await db.rooms.find_one(
        {"id": booking.get("assigned_room_id")}, {"_id": 0}
    ) if booking.get("assigned_room_id") else None
    return booking


@router.put("/bookings/{booking_id}")
async def update_booking(booking_id: str, payload: BookingUpdateIn,
                         user: dict = Depends(BOOK),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] in CLOSED_TO_AMENDMENT:
        raise HTTPException(409, f"A {booking['status']} booking cannot be edited")

    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not changes:
        return booking

    merged = {**booking, **changes}
    _validate_window(merged["check_in"], merged["check_out"])

    room_type = await db.room_types.find_one({"id": merged["room_type_id"]}, {"_id": 0})
    # The booking's own plan, which may be None on a property that sells one rate. Never
    # the property setting: an edit to a plan-carrying booking reprices it on its plan
    # even if meal plans have since been switched off.
    meal_plan = await _plan_for_booking(db, merged)
    _validate_occupancy(room_type, merged["adults"], merged["children"], merged["extra_beds"])

    repricing = any(k in changes for k in
                    ("check_in", "check_out", "adults", "children", "meal_plan_id"))
    if repricing:
        rooms = await db.rooms.find({"room_type_id": room_type["id"]}, {"_id": 0}).to_list(20000)
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
            db, room_type, merged["check_in"], merged["check_out"],
            merged["adults"], merged["children"], meal_plan)

        # The other door into the double-booking this feature exists to prevent. The
        # clash check guards the assignment, but the stay window is half of what it
        # compares — so a booking already holding room 204 could be stretched across
        # the nights somebody else holds 204 for, and arrive at two guests behind one
        # door without an assignment ever being made. Re-asked here, against the new
        # window, and the whole edit is refused rather than the room being silently
        # dropped: losing the room a guest was promised is not a quieter failure than
        # refusing the date change, it is the same failure discovered later.
        if merged.get("assigned_room_id"):
            await room_for_booking_or_409(db, merged, merged["assigned_room_id"])

    await db.bookings.update_one({"id": booking_id}, {"$set": changes})
    return await db.bookings.find_one({"id": booking_id}, {"_id": 0})


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, payload: CancelIn,
                         user: dict = Depends(BOOK),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
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


# ------------------------- which room a booking holds -------------------------
async def room_for_booking_or_409(db, booking: dict, room_id: str) -> dict:
    """The room this booking may hold, or the refusal that says why it may not.

    The one place the rule lives. `POST /bookings/{id}/check-in` imports it too, and it
    has to: before pre-assignment only a checked-in booking could hold a room, so
    check-in could clash-check against `status: "checked_in"` alone. Now a confirmed
    booking holds one as well, and a check-in still asking the old question would hand
    tomorrow's held room to today's walk-in — the same two-guests-one-door bug arrived
    at from the other end. A second copy of this check is a second thing to forget.

    Four ways it refuses, each with a 404 or a 409 the desk can act on:

    * the room does not exist — 404, and property-scoped, so another hotel's room is
      not found rather than found and refused;
    * it is not of the booked type — a suite handed to a standard booking silently
      changes what the guest pays for;
    * it is inactive;
    * something already has it for part of this stay — an out-of-order block, or
      another live booking, which the 409 names so the receptionist can go and move it
      rather than only being told no.

    The database query narrows to rooms and statuses that could possibly clash; the
    pure predicate in services/availability.py decides. The residual race is the one
    `POST /bookings` documents — without transactions, two assignments issued in the
    same instant can both pass — and it is narrowed here the same way, by checking
    immediately before the write.
    """
    room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if room["room_type_id"] != booking["room_type_id"]:
        raise HTTPException(409, "That room is not of the booked room type")
    if not room.get("active", True):
        raise HTTPException(409, "That room is inactive")

    block = blocking_out_of_order(room, booking["check_in"], booking["check_out"])
    if block:
        reason = f" — {block['reason']}" if block.get("reason") else ""
        raise HTTPException(409, {
            "message": (f"Room {room['number']} is out of order "
                        f"{block['from']} → {block['to']}{reason}"),
            "from": block["from"], "to": block["to"],
        })

    held = await db.bookings.find(
        {"assigned_room_id": room_id, "status": {"$in": LIVE}}, {"_id": 0}
    ).to_list(5000)
    clash = booking_holding_room(room_id, booking["check_in"], booking["check_out"],
                                 held, exclude_booking_id=booking["id"])
    if clash:
        raise HTTPException(409, {
            "message": (f"Room {room['number']} is already held by {clash['reference']} "
                        f"({clash['check_in']} → {clash['check_out']})"),
            "booking_id": clash["id"],
            "reference": clash["reference"],
            "check_in": clash["check_in"],
            "check_out": clash["check_out"],
        })
    return room


@router.put("/bookings/{booking_id}/room")
async def set_booking_room(booking_id: str, payload: RoomAssignmentIn,
                           user: dict = Depends(ASSIGN),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    """Assign, reassign or clear the physical room a booking holds.

    Before check-in as well as after: hotels pre-assign routinely — a returning guest
    who asks for the same room, a family who need adjacent doors, a group blocked
    together — and being unable to record that until the guest is at the desk means it
    is written on paper instead.

    PUT rather than POST, and one endpoint rather than three, because this sets a single
    field to a single value: sending the same room twice is the same booking in the same
    room, and `room_id: null` clears it.
    """
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] not in CONSUMING_STATUSES:
        raise HTTPException(409, f"A {booking['status']} booking cannot hold a room")

    if payload.room_id is None:
        # An in-house guest is physically in a room, the folio is open against it and
        # the POS finds them by its number. Clearing that is not a room move, it is a
        # lost guest — move them to another room, or check them out.
        if booking["status"] == "checked_in":
            raise HTTPException(
                409, "Move an in-house guest to another room, or check them out")
        await db.bookings.update_one({"id": booking_id},
                                     {"$set": {"assigned_room_id": None}})
        return {**await db.bookings.find_one({"id": booking_id}, {"_id": 0}),
                "room": None}

    room = await room_for_booking_or_409(db, booking, payload.room_id)
    await db.bookings.update_one({"id": booking_id},
                                 {"$set": {"assigned_room_id": room["id"]}})
    return {**await db.bookings.find_one({"id": booking_id}, {"_id": 0}), "room": room}


# ---------------------------- extending a stay ----------------------------
def _extended_quote(existing: dict, added: dict) -> dict:
    """The stay's quote with the added nights appended, and nothing else recomputed.

    Every night already in `existing` keeps the tariff and the GST it was quoted at. The
    guest was told a number for those nights; a rate rise since — or a seasonal period
    somebody added over the dates — must not reach backwards and change it. Only the
    totals move, and they move by exactly the sum of the new lines.

    Written as a merge rather than a re-quote of the whole window on purpose: re-quoting
    is the obvious implementation, it produces the right answer on the day the rate has
    not changed, and it silently reprices the whole stay on the day it has.
    """
    nights = list(existing.get("nights") or []) + list(added["nights"])
    room_subtotal = round(sum(float(n["tariff"]) for n in nights), 2)
    tax_total = round(sum(float(n["gst_amount"]) for n in nights), 2)
    return {**existing, "nights": nights, "room_subtotal": room_subtotal,
            "tax_total": tax_total, "total": round(room_subtotal + tax_total, 2)}


async def _added_nights_are_free_or_409(db, booking: dict, room_type: dict,
                                        added_from: str, added_to: str) -> None:
    """Is there a room for the extra nights? Refuse naming what is in the way if not.

    Two questions, and which one is asked depends on whether this booking already holds a
    physical room:

    * **it does** — then it is *that* room that has to be free, and the question is the
      per-room one `PUT /bookings/{id}/room` asks. Reused rather than re-implemented:
      `room_for_booking_or_409` is where the rule lives, it already refuses an
      out-of-order block and a room another live booking holds, and it already names the
      blocker so the desk can go and move it. A second copy here would be a second place
      for the two-guests-one-door bug to come back.
    * **it does not** — then any room of the type will do at check-in, so the question is
      the type's inventory, exactly as `POST /bookings` asks it.

    Only the *added* nights are examined, never the whole stay: this booking occupies the
    nights it already has, and asking about them would find the booking blocking its own
    extension.
    """
    if booking.get("assigned_room_id"):
        # A view of this booking over the added nights alone. `room_for_booking_or_409`
        # compares a room against a window, and the window an extension is about is the
        # new tail — not the stay so far, which this booking is legitimately holding.
        await room_for_booking_or_409(
            db, {**booking, "check_in": added_from, "check_out": added_to},
            booking["assigned_room_id"])
        return

    rooms = await db.rooms.find(
        {"room_type_id": booking["room_type_id"]}, {"_id": 0}).to_list(20000)
    live = await db.bookings.find({
        "room_type_id": booking["room_type_id"], "status": {"$in": LIVE},
        "id": {"$ne": booking["id"]},
    }, {"_id": 0}).to_list(5000)
    if count_available(booking["room_type_id"], added_from, added_to, rooms, live) < 1:
        raise HTTPException(409, {
            "message": (f"No {room_type['name']} is free for "
                        f"{added_from} → {added_to} — the stay was not extended"),
            "check_in": added_from,
            "check_out": added_to,
        })


@router.post("/bookings/{booking_id}/extend")
async def extend_stay(booking_id: str, payload: ExtendStayIn,
                      user: dict = Depends(EXTEND),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    """"Can I stay two more nights?" — the operation, not a date edit.

    An explicit endpoint rather than `PUT /bookings/{id}` with a new `check_out`, because
    for a guest already in the room this has to do more than move a date, and each of the
    extra things is one somebody would otherwise have to remember:

    * **check-out only.** `ExtendStayIn` has no `check_in`, so an extension cannot move a
      guest's arrival. Moving a *future* booking's arrival is an ordinary edit and
      `update_booking` still does it.
    * **the extra nights have to be free** — the pre-assigned room, or the type's
      inventory — and a clash comes back as a 409 that names the booking, or the
      out-of-order block, standing in the way.
    * **only the added nights are priced.** `update_booking` reprices the whole stay,
      which is right for an amendment the guest is being re-quoted for and wrong here:
      the first three nights were already sold at a number.
    * **an in-house guest's new nights reach the folio by themselves.** Nothing here
      posts anything. `routers/folios.py::post_due_nights` derives what is owed from the
      booking's `check_out` and its stored quote, both of which this endpoint has just
      moved, so the next folio read picks the new nights up — at the price quoted here,
      not at today's rate. That is deliberately the *only* mechanism: a second one could
      disagree with it, and two mechanisms disagreeing about a room night is a guest
      charged twice.

    Refused for the same three statuses `update_booking` refuses. A `tentative` or
    `confirmed` booking extends before arrival; a `checked_in` one extends at the desk,
    which is where this is usually asked.
    """
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] in CLOSED_TO_AMENDMENT:
        raise HTTPException(409, f"A {booking['status']} booking cannot be extended")

    added_from = booking["check_out"]
    if payload.check_out <= added_from:
        raise HTTPException(
            400, f"An extension must move check-out later than {added_from}")

    room_type = await db.room_types.find_one({"id": booking["room_type_id"]}, {"_id": 0})
    if not room_type:
        raise HTTPException(400, "This booking's room type no longer exists")

    await _added_nights_are_free_or_409(db, booking, room_type,
                                        added_from, payload.check_out)

    # Priced before anything is written, so an unpriceable night refuses the whole
    # extension rather than leaving a stay that has grown and a quote that has not.
    added = await _quote_or_422(db, room_type, added_from, payload.check_out,
                                booking["adults"], booking["children"],
                                await _plan_for_booking(db, booking))

    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "check_out": payload.check_out,
        "quote": _extended_quote(booking.get("quote") or {}, added),
    }})
    # `added` alongside the booking so the desk can quote the extension itself — "two
    # more nights, ₹13,440" — without subtracting one total from another on screen.
    return {**await db.bookings.find_one({"id": booking_id}, {"_id": 0}), "added": added}
