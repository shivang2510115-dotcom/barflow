"""Packages: what a rate includes, and what a guest has left of it.

A rate points at a package; a package holds inclusions. **That is the whole of how an
elite room differs from a normal one** — the elite rate points at a package with more in
it. Nothing in this codebase branches on room class, which is what stops "elite"
becoming a special case that leaks into a dozen files.

The rules are not here. How much an inclusion grants, whether it has run out, and what
it applies to all live in `services/packages.py` as pure functions with no database
under them. This router reads, writes and refuses.

**Consumption is append-only and idempotent.** A use is written with a deterministic id,
so a retried request or a double-tapped Save consumes one allowance rather than two.
`routers/folios.py` carries the identical trick for room nights, because a duplicate
posting there once double-charged a guest; burning both of a guest's two free massages
on one tap is the same failure with a worse conversation at checkout.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from models.package import InclusionIn, PackageIn
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import SHARED
from services.clock import today as local_today
from services.packages import (
    PERIODS, SCOPES, covers, package_for_stay, remaining)

router = APIRouter()

# Deciding what a rate includes is configuring the business, so it sits with rates.
# "admin" is named because the role check runs before the admin domain-bypass.
CONFIGURE = require_access(("hotel",), "admin", "manager", permission="hotel.rates")

# Reading what a guest is entitled to is a serving question, so anyone who can sell to
# them can ask it — a waiter needs to know breakfast is included before they charge for
# it, and a salon attendant needs the same.
SERVE = require_access(SHARED, "admin", "manager", "front_desk", "waiter")

MAX_ROWS = 5000

# Namespace for deterministic use ids. Fixed forever: regenerating it would make every
# past use unmatched and let each one be consumed a second time.
_USE_NAMESPACE = uuid.UUID("9c4a1e77-6b2f-5d38-a91c-7e5b0d2f4a86")


@router.get("/packages")
async def list_packages(user: dict = Depends(CONFIGURE),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    rows = await db.packages.find({}, {"_id": 0}).to_list(MAX_ROWS)
    incs = await db.inclusions.find({}, {"_id": 0}).to_list(MAX_ROWS)
    by_package: dict[str, list] = {}
    for i in incs:
        by_package.setdefault(i.get("package_id"), []).append(i)
    rows.sort(key=lambda p: (p.get("name") or "").lower())
    return [{**p, "inclusions": by_package.get(p["id"], [])} for p in rows]


@router.post("/packages")
async def create_package(payload: PackageIn, user: dict = Depends(CONFIGURE),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "The package needs a name")
    if await db.packages.find_one({"name": name}):
        raise HTTPException(409, f"This property already has a package called {name}")
    row = {"id": str(uuid.uuid4()), "name": name, "active": True,
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.packages.insert_one(dict(row))
    return {**row, "inclusions": []}


@router.post("/packages/{package_id}/inclusions")
async def add_inclusion(package_id: str, payload: InclusionIn,
                        user: dict = Depends(CONFIGURE),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    if not await db.packages.find_one({"id": package_id}):
        raise HTTPException(404, "No such package")
    if payload.scope not in SCOPES:
        raise HTTPException(400, f"{payload.scope} is not a scope — expected one of: {', '.join(SCOPES)}")
    if payload.period not in PERIODS:
        raise HTTPException(400, f"{payload.period} is not a period — expected one of: {', '.join(PERIODS)}")
    if payload.quantity < 1:
        raise HTTPException(400, "An inclusion has to include at least one of something")

    outlet = await db.outlets.find_one({"id": payload.outlet_id}, {"_id": 0})
    if not outlet:
        raise HTTPException(400, "No such outlet in this property")
    # An entitlement is spent by posting a zero-value line to a folio. An outlet that
    # cannot reach a folio has nowhere to post it, so the inclusion could never be
    # honoured — better refused here than discovered at the counter.
    if not outlet.get("charges_to_folio"):
        raise HTTPException(
            400, f"{outlet.get('name')} does not charge to a room folio, "
                 "so nothing there can be included in a package")

    row = {"id": str(uuid.uuid4()), "package_id": package_id,
           "outlet_id": payload.outlet_id, "scope": payload.scope,
           "ref_id": payload.ref_id, "quantity": payload.quantity,
           "period": payload.period,
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.inclusions.insert_one(dict(row))
    return row


@router.get("/bookings/{booking_id}/entitlements")
async def entitlements(booking_id: str, outlet_id: str = "",
                       user: dict = Depends(SERVE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    """What this guest still has, so the POS can say Included before anyone taps.

    Answers for a booking with no package at all — an empty list, not a 404. Most stays
    have no package, and a screen that had to distinguish "no package" from "booking not
    found" would treat the ordinary case as an error.
    """
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "No such booking")

    # The booking's own snapshot first: what it was sold with is fixed at the moment of
    # sale, and a room type re-pointed next month must not change what a guest already
    # staying is entitled to. The room type is only consulted for a booking taken before
    # packages existed.
    room_type = await db.room_types.find_one(
        {"id": booking.get("room_type_id")}, {"_id": 0}) or {}
    package_id = (booking.get("package_id") or "").strip() or package_for_stay(None, room_type)
    if not package_id:
        return {"package": None, "inclusions": []}

    package = await db.packages.find_one({"id": package_id}, {"_id": 0})
    if not package:
        return {"package": None, "inclusions": []}

    query = {"package_id": package_id}
    if outlet_id:
        query["outlet_id"] = outlet_id
    incs = await db.inclusions.find(query, {"_id": 0}).to_list(MAX_ROWS)
    uses = await db.entitlement_uses.find(
        {"booking_id": booking_id}, {"_id": 0}).to_list(MAX_ROWS)

    nights = _nights(booking)
    day = local_today()
    return {
        "package": {"id": package["id"], "name": package.get("name")},
        "inclusions": [
            {**i, "remaining": remaining(i, uses, nights, day)} for i in incs
        ],
    }


def _nights(booking: dict) -> int:
    """Nights in a stay: check-out minus check-in, never counting the departure night."""
    ci, co = booking.get("check_in"), booking.get("check_out")
    if not ci or not co:
        return 0
    from datetime import date
    try:
        return max(0, (date.fromisoformat(co) - date.fromisoformat(ci)).days)
    except ValueError:
        return 0


@router.post("/bookings/{booking_id}/entitlements/{inclusion_id}/use")
async def consume(booking_id: str, inclusion_id: str, folio_entry_id: str = "",
                  user: dict = Depends(SERVE),
                  db: PropertyScopedDatabase = Depends(tenant_db)):
    """Spend one of a guest's allowances.

    The id is derived from the three things that identify this consumption, so the same
    request twice writes the same row twice and consumes one allowance. Without it, a
    double-tapped Save burns both of a guest's two free massages.
    """
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "No such booking")
    inclusion = await db.inclusions.find_one({"id": inclusion_id}, {"_id": 0})
    if not inclusion:
        raise HTTPException(404, "No such inclusion")

    uses = await db.entitlement_uses.find(
        {"booking_id": booking_id}, {"_id": 0}).to_list(MAX_ROWS)
    day = local_today()
    left = remaining(inclusion, uses, _nights(booking), day)
    if left < 1:
        # 409 rather than a silent charge: the caller decides what to do next, and the
        # POS turns this into "beyond package" at full price with the reason shown.
        raise HTTPException(409, "That allowance is used up")

    key = f"{booking_id}|{inclusion_id}|{folio_entry_id or day}"
    row = {
        "id": str(uuid.uuid5(_USE_NAMESPACE, key)),
        "booking_id": booking_id,
        "inclusion_id": inclusion_id,
        "folio_entry_id": folio_entry_id or None,
        "used_on": day,
        "used_at": datetime.now(timezone.utc).isoformat(),
        "used_by": user.get("name") or user.get("email") or "staff",
    }
    await db.entitlement_uses.update_one({"id": row["id"]}, {"$set": row}, upsert=True)
    return {**row, "remaining": remaining(inclusion, uses + [row],
                                          _nights(booking), day)}
