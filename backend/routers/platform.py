"""The platform operator's console: approve a hotel, suspend it, restore it.

Every route here is `platform_admin` only, and a `platform_admin` belongs to no hotel —
`can_access` refuses them every hotel endpoint on the role check. That is the whole
design: the operator's login is not a master key into customer data. They can see that a
hotel exists, how big it is, and how far through setup it got. They cannot see a guest, a
folio, a booking or an identity document, and there is no route here that would let them.

`unscoped_db` is used because the operator works across tenants by definition — the list
of hotels is not something any one hotel's scoped handle could return.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import unscoped_db
from scoped_db import PropertyScopedDatabase
from security import get_current_user
from services.access import LIVE, PENDING, PLATFORM_ADMIN, SUSPENDED

router = APIRouter()

STATUSES = (PENDING, LIVE, SUSPENDED)


async def platform_admin(user: dict = Depends(get_current_user)) -> dict:
    """Refuse anyone who is not the operator.

    Deliberately not `require_access`: that dependency resolves the caller's property and
    refuses a caller who has none, which is exactly what a platform admin is. The two
    gates are separate because the things they guard are separate — hotel data, and the
    list of hotels.
    """
    if user.get("role") != PLATFORM_ADMIN:
        raise HTTPException(403, "Not permitted")
    return user


class StatusIn(BaseModel):
    status: str
    reason: str = ""


def _summary(record: dict) -> dict:
    return {
        "id": record["id"],
        "name": record.get("name") or "",
        "city": record.get("city") or "",
        "gstin": record.get("gstin") or "",
        "status": record.get("status"),
        "created_at": record.get("created_at"),
        "approved_at": record.get("approved_at"),
        "suspended_at": record.get("suspended_at"),
        "suspension_reason": record.get("suspension_reason"),
    }


@router.get("/platform/properties")
async def list_properties(status: str = "", user: dict = Depends(platform_admin)):
    query = {}
    if status:
        if status not in STATUSES:
            raise HTTPException(422, f"Unknown status: {status}")
        query["status"] = status
    rows = await unscoped_db.properties.find(query, {"_id": 0}).to_list(5000)
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return [_summary(r) for r in rows]


@router.get("/platform/properties/{property_id}")
async def property_detail(property_id: str, user: dict = Depends(platform_admin)):
    """How big the hotel is and how far through setup it got — never who stayed there.

    Counts only. No guest, booking, folio or identity document is reachable from this
    file, because deciding whether to approve a business does not require reading its
    customers' records.
    """
    record = await unscoped_db.properties.find_one({"id": property_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "No such property")

    scoped = PropertyScopedDatabase(property_id)
    rooms = await scoped.rooms.count_documents({})
    room_types = await scoped.room_types.count_documents({})
    rates = await scoped.rates.count_documents({})
    menu = await scoped.menu.count_documents({})
    tables = await scoped.tables.count_documents({})
    staff = await unscoped_db.users.count_documents({"property_id": property_id})

    return {
        **_summary(record),
        "counts": {"rooms": rooms, "room_types": room_types, "rates": rates,
                   "menu_items": menu, "tables": tables, "staff": staff},
        # What the operator actually wants to know before approving: has this hotel done
        # enough setup to be a real business, or did somebody fill in a form and leave.
        "setup": {
            "has_rooms": rooms > 0,
            "has_rates": rates > 0,
            "has_gstin": bool(record.get("gstin")),
            "ready_to_trade": rooms > 0 and rates > 0,
        },
    }


@router.post("/platform/properties/{property_id}/status")
async def set_status(property_id: str, payload: StatusIn,
                     user: dict = Depends(platform_admin)):
    if payload.status not in STATUSES:
        raise HTTPException(422, f"Unknown status: {payload.status}")
    record = await unscoped_db.properties.find_one({"id": property_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "No such property")

    now = datetime.now(timezone.utc).isoformat()
    patch: dict = {"status": payload.status}
    if payload.status == LIVE:
        # Recorded on every approval, including a restore from suspension: the useful
        # question later is "when did this hotel last become able to trade", not "when
        # was it first approved".
        patch["approved_at"] = now
        patch["approved_by"] = user["id"]
        patch["suspended_at"] = None
        patch["suspension_reason"] = None
    elif payload.status == SUSPENDED:
        patch["suspended_at"] = now
        patch["suspension_reason"] = payload.reason.strip()

    # Idempotent by construction: setting the status a property already holds rewrites the
    # same value and returns 200. An operator double-clicking Approve must not get an error.
    await unscoped_db.properties.update_one({"id": property_id}, {"$set": patch})
    updated = await unscoped_db.properties.find_one({"id": property_id}, {"_id": 0})
    return _summary(updated)
