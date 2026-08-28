"""Guest records. Phone is the identity key across bar, restaurant and rooms."""
from fastapi import APIRouter, Depends, HTTPException

from models.hotel import Guest, GuestIn, Occasion, OccasionIn
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import SHARED
from services.identity import normalise_phone
from services.messaging import month_day, normalise_label

router = APIRouter()

# Guests are shared: a bar regular and a hotel guest are the same person. The
# new-booking screen creates one mid-booking, so it reaches these too — hence both keys.
MANAGE = require_access(SHARED, "admin", "manager", "front_desk",
                        permission=("hotel.guests", "hotel.bookings"))


async def find_by_phone(db: PropertyScopedDatabase, phone: str):
    """The guest this number belongs to, however either of them is spelled.

    Phone is the identity key, and it has never been stored in one canonical shape: the
    Guests screen stores what the receptionist typed, a bill carries what the waiter
    typed at the till, and `09876500001`, `+91 98765 00001` and `9876500001` are all one
    person. Matching on the literal string makes them three, which for messaging means
    three birthday greetings and a follow-up to somebody who came in last week.

    Three lookups, cheapest first, and the last one is what actually does the work:
    the E.164 form (what anything written since services/identity.py stores), the raw
    string (what was typed), then the trailing ten digits, which is the one comparison
    every spelling agrees on.

    Deliberately *not* a migration that rewrites every stored number. That would be the
    tidier database and it would also silently edit the contact details on records this
    feature has no business touching; the read-side match costs one indexed query on the
    common path and is reversible.
    """
    raw = (phone or "").strip()
    if not raw:
        return None
    canonical = normalise_phone(raw)
    for candidate in (canonical, raw):
        if not candidate:
            continue
        found = await db.guests.find_one({"phone": candidate}, {"_id": 0})
        if found:
            return found
    if not canonical:
        return None
    # `$` anchors it, so 9876500001 does not match 89876500001 — a different number that
    # happens to end the same way.
    return await db.guests.find_one(
        {"phone": {"$regex": f"{canonical[-10:]}$"}}, {"_id": 0})


def _stored_patch(payload: GuestIn) -> dict:
    """The body as it should be written, with "not mentioned" left alone.

    `no_messages` is the one field where silence means "leave whatever is stored" rather
    than "set it to the default" — see models/hotel.py. This endpoint replaces the
    editable half of a guest wholesale, which is fine for every field that has always
    been sent and quietly serious for a consent flag added afterwards: a form written
    before it existed omits the key, Pydantic fills in `False`, and somebody who asked not
    to be messaged is re-consented as a side effect of a spelling correction.
    """
    patch = payload.model_dump()
    if patch.get("no_messages") is None:
        patch.pop("no_messages", None)
    return patch


@router.get("/guests")
async def list_guests(q: str = "", limit: int = 50, user: dict = Depends(MANAGE),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    query = {}
    if q:
        query = {"$or": [
            {"phone": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]}
    return await db.guests.find(query, {"_id": 0}).to_list(limit)


@router.post("/guests")
async def create_guest(payload: GuestIn, user: dict = Depends(MANAGE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(400, "Phone is required")

    existing = await db.guests.find_one({"phone": phone}, {"_id": 0})
    if existing:
        # Return the existing record so the desk can open it instead of retyping.
        raise HTTPException(409, {"message": "A guest with this phone already exists",
                                  "guest": existing})

    guest = Guest(**{**_stored_patch(payload), "phone": phone}).model_dump()
    await db.guests.insert_one(guest)
    guest.pop("_id", None)
    return guest


@router.get("/guests/{guest_id}")
async def get_guest(guest_id: str, user: dict = Depends(MANAGE),
                    db: PropertyScopedDatabase = Depends(tenant_db)):
    guest = await db.guests.find_one({"id": guest_id}, {"_id": 0})
    if not guest:
        raise HTTPException(404, "Guest not found")

    stays = await db.bookings.find({"guest_id": guest_id}, {"_id": 0}).to_list(5000)
    orders = await db.orders.find(
        {"customer_phone": guest["phone"], "status": "settled"}, {"_id": 0}
    ).to_list(5000)

    return {
        **guest,
        "stays": stays,
        "outlet_orders": len(orders),
        "outlet_spend": round(sum(o.get("total", 0) for o in orders), 2),
        "occasions": await db.occasions.find(
            {"guest_id": guest_id}, {"_id": 0}).to_list(100),
    }


@router.put("/guests/{guest_id}")
async def update_guest(guest_id: str, payload: GuestIn, user: dict = Depends(MANAGE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    clash = await db.guests.find_one({"phone": payload.phone.strip(), "id": {"$ne": guest_id}})
    if clash:
        raise HTTPException(409, "Another guest already uses this phone")

    result = await db.guests.update_one({"id": guest_id}, {"$set": _stored_patch(payload)})
    if result.matched_count == 0:
        raise HTTPException(404, "Guest not found")
    return await db.guests.find_one({"id": guest_id}, {"_id": 0})


# ------------------------------- occasions --------------------------------
# Here rather than in routers/messaging.py because an occasion is a fact about a person,
# not about a message: it is worth recording whether or not the property ever obtains a
# WhatsApp template, and a hotel that knows a regular's wedding anniversary can put a
# card on the table instead. Messaging reads these; it does not own them.
#
# Behind the same MANAGE dependency as the rest of the guest record, so the desk edits
# somebody's occasions exactly where it edits their address. The waiter's path is
# deliberately *not* this one — see routers/messaging.py::capture_occasion, which is
# reachable at the till and creates the guest as a side effect of the bill.

@router.get("/guests/{guest_id}/occasions")
async def list_occasions(guest_id: str, user: dict = Depends(MANAGE),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    if not await db.guests.find_one({"id": guest_id}, {"_id": 0}):
        raise HTTPException(404, "Guest not found")
    return await db.occasions.find({"guest_id": guest_id}, {"_id": 0}).to_list(100)


@router.post("/guests/{guest_id}/occasions")
async def add_occasion(guest_id: str, payload: OccasionIn, user: dict = Depends(MANAGE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    if not await db.guests.find_one({"id": guest_id}, {"_id": 0}):
        raise HTTPException(404, "Guest not found")
    return await record_occasion(db, guest_id, payload.label, payload.date,
                                 user.get("id"))


async def record_occasion(db: PropertyScopedDatabase, guest_id: str, label: str,
                          day: str, by: str | None) -> dict:
    """Write one occasion, or hand back the one that is already there.

    Shared with the till (routers/messaging.py) so that the two ways of recording an
    occasion cannot disagree about what one is. Idempotent on (guest, label, month-day):
    a waiter who taps Birthday twice on the settle screen, or a desk that records what a
    waiter already did, must not put the same guest on today's list twice — which would
    be two greetings, since each row claims separately.
    """
    clean = normalise_label(label)
    if not clean:
        raise HTTPException(400, "An occasion needs a label — what is being marked")
    recurring = month_day(day)
    if not recurring:
        raise HTTPException(400, "An occasion needs a date, as YYYY-MM-DD")

    existing = await db.occasions.find_one(
        {"guest_id": guest_id, "label": clean, "month_day": recurring}, {"_id": 0})
    if existing:
        return existing

    occasion = Occasion(guest_id=guest_id, label=clean, date=day.strip(),
                        month_day=recurring, created_by=by).model_dump()
    await db.occasions.insert_one(occasion)
    occasion.pop("_id", None)
    return occasion


@router.delete("/guests/{guest_id}/occasions/{occasion_id}")
async def remove_occasion(guest_id: str, occasion_id: str, user: dict = Depends(MANAGE),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """Forget an occasion. Deleted rather than flagged, unlike the message log beside it:
    this is the guest's own information, recorded on their say-so, and "we stopped
    sending it but kept the date" is not what somebody asking for it to be removed
    means."""
    result = await db.occasions.delete_one({"id": occasion_id, "guest_id": guest_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Occasion not found")
    return {"deleted": occasion_id}
