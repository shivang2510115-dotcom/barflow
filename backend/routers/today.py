"""The morning screen: what is happening in this property right now.

One request, because this is the screen somebody opens with a guest already at the desk
and it must not arrive in four instalments. It answers the questions a receptionist and
a manager actually ask first — who is arriving, who is leaving, who is still here, which
rooms are not ready, and how the rooms side is performing.

**Nothing here is new data.** Arrivals and departures come from `bookings`, the ready
count from each room's `housekeeping_status`, and the three metrics from the same folio
entries `/analytics` reads, through the same void-corrected aggregator. This router
computes nothing that another screen would compute differently — where it needed a
figure, it imported the function that already produces it rather than writing a second
one that could drift.

**Why it is not behind a screen key.** The board shows a person their own day, and
`ROLE_SCREENS` is frozen by design — a key added today reaches nobody hired yesterday,
so gating this would hide the landing screen from every existing member of staff
permanently. `routers/planner.py::READ` and `routers/outlets.py::READ` set the same
precedent. What each role *sees* is narrowed below instead: the money is for the people
who are allowed money elsewhere.
"""
from fastapi import APIRouter, Depends

from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import DOMAINS, can_access
from services.clock import today as local_today
from services.housekeeping import READY_STATUSES, status_of
from services.metrics import occupancy_metrics
from services.revenue import hotel_revenue

router = APIRouter()

# Everyone who works here. No screen key, for the reason in the module docstring.
READ = require_access(DOMAINS, "admin", "manager", "front_desk", "waiter",
                      "housekeeping", "kitchen")

MAX_ROWS = 20000


def _guest_line(booking: dict, guests: dict, rooms: dict) -> dict:
    """One row of the arrivals or departures list.

    Deliberately thin. This is a board read at a glance, and it is reachable by a waiter
    and a housekeeper — neither of whom has any other route to a guest record — so it
    carries a name, a room and a time and nothing that would be a leak: no phone, no
    address, no identity document, no folio balance, no booking notes.
    """
    guest = guests.get(booking.get("guest_id")) or {}
    room = rooms.get(booking.get("assigned_room_id")) or {}
    return {
        "booking_id": booking.get("id"),
        "guest_name": guest.get("name") or "Guest",
        "room_number": room.get("number"),
        "check_in": booking.get("check_in"),
        "check_out": booking.get("check_out"),
        "status": booking.get("status"),
    }


@router.get("/today")
async def today_board(user: dict = Depends(READ),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    day = local_today()

    bookings = await db.bookings.find({"$or": [
        {"check_in": day},
        {"check_out": day},
        {"status": "checked_in"},
    ]}, {"_id": 0}).to_list(MAX_ROWS)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(MAX_ROWS)
    rooms_by_id = {r["id"]: r for r in rooms}

    arrivals = [b for b in bookings
                if b.get("check_in") == day and b.get("status") in ("tentative", "confirmed")]
    departures = [b for b in bookings
                  if b.get("check_out") == day and b.get("status") == "checked_in"]
    in_house = [b for b in bookings if b.get("status") == "checked_in"]

    # Only rooms that are part of the sellable inventory. An inactive room is not being
    # withheld from sale, it is not in the building's count at all, and including it
    # would understate occupancy against a denominator the hotel does not recognise.
    live_rooms = [r for r in rooms if r.get("active", True)]
    not_ready = [r for r in live_rooms if status_of(r) not in READY_STATUSES]

    board = {
        "date": day,
        "arrivals": [_guest_line(b, guests, rooms_by_id) for b in arrivals],
        "departures": [_guest_line(b, guests, rooms_by_id) for b in departures],
        "in_house_count": len(in_house),
        "rooms": {
            "total": len(live_rooms),
            "not_ready": len(not_ready),
            "not_ready_numbers": sorted(
                (r.get("number") or "") for r in not_ready)[:12],
        },
    }

    # The money is narrowed to the people who are allowed money elsewhere. A waiter's
    # board is the same board without a revenue line — `can_access` decides that rather
    # than a role list written here, so this cannot disagree with /analytics about who
    # may see a figure.
    may_see_money = can_access(user, DOMAINS, ("admin", "manager"),
                               permission="admin.analytics")
    if may_see_money:
        entries = await db.folio_entries.find({"$or": [
            {"charge_date": day},
            {"posted_at": {"$gte": f"{day}T00:00:00", "$lt": f"{day}T23:59:59.999999"}},
        ]}, {"_id": 0}).to_list(MAX_ROWS)
        ids = [e["id"] for e in entries if e.get("id")]
        voids = await db.folio_entries.find(
            {"kind": "void", "ref_entry_id": {"$in": ids}}, {"_id": 0}).to_list(MAX_ROWS)
        seen = {e.get("id") for e in entries}
        entries += [v for v in voids if v.get("id") not in seen]

        rev = hotel_revenue(entries, day, day)
        board["metrics"] = {
            **occupancy_metrics(rev["room_nights"], rev["nights_sold"],
                                len(live_rooms), 1),
            "room_revenue": rev["room_nights"],
        }

    return board
