"""The places a property serves guests: its restaurants, bars, salon, gym, laundry.

This collection is what replaced `OUTLET = ("restaurant", "bar")`. A hotel adds its own
rather than waiting for us, which is the same reason signup is self-serve: a hotel
waiting on the platform operator to add a salon is a support ticket that scales with the
customer count.

**Writes are admin-only, reads are not.** Deciding that the property has a salon is a
decision about how the business is arranged; knowing that it does is something every
staff member needs in order to be shown the right sidebar. So the write routes carry the
`admin.outlets` key and the read route carries none — the same split routers/planner.py
uses, and for the same reason. Gating the read on a screen key would hide the navigation
from everyone who does not administer the place, and would reach no existing staff
anyway: `backfill_permissions` fills in a *missing* permissions field and never touches
one that is present, so a key invented today reaches nobody hired yesterday.

**Nothing here is deleted.** An outlet is deactivated, because its past orders name it
and a row that vanishes takes the label off every one of them.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from models.outlet import OutletIn, OutletPatch
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import DOMAINS, SHARED
from services.outlets import KIND_DOMAIN, outlet_problem

router = APIRouter()

# Reading the list. No screen key: a waiter needs to know the property has a salon in
# order for the sidebar to be right. Every role that works in a property is named,
# `admin` among them — the role check runs before the admin domain-bypass, so a tuple
# omitting it would lock admins out of their own outlets.
READ = require_access(DOMAINS, "admin", "manager", "front_desk", "waiter", "housekeeping")

# Writing. Admin only, behind the screen key.
WRITE = require_access(SHARED, "admin", permission="admin.outlets")


def _public(row: dict) -> dict:
    """One outlet, as the client sees it."""
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "kind": row.get("kind"),
        "domain": row.get("domain"),
        "charges_to_folio": bool(row.get("charges_to_folio")),
        "takes_direct_payment": bool(row.get("takes_direct_payment")),
        "active": bool(row.get("active", True)),
        "created_at": row.get("created_at"),
    }


@router.get("/outlets")
async def list_outlets(user: dict = Depends(READ),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    rows = await db.outlets.find({}, {"_id": 0}).to_list(200)
    # Active first, then by name. A switched-off outlet is still shown — it names past
    # orders — but it belongs at the bottom rather than interleaved with the live ones.
    rows.sort(key=lambda r: (not r.get("active", True), (r.get("name") or "").lower()))
    return [_public(r) for r in rows]


@router.post("/outlets")
async def create_outlet(payload: OutletIn, user: dict = Depends(WRITE),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    name = payload.name.strip()
    problem = outlet_problem(name, payload.kind,
                             payload.charges_to_folio, payload.takes_direct_payment)
    if problem:
        raise HTTPException(400, problem)

    # No database enforces uniqueness here — `create_index` is a no-op in both the mock
    # and Firestore — so this pre-check is the only guard. Two salons with different
    # names is a hotel's own business; two identical rows from a double-tapped Save is
    # not, and this is the cheapest place to notice.
    if await db.outlets.find_one({"name": name, "kind": payload.kind}):
        raise HTTPException(409, f"This property already has a {payload.kind} called {name}")

    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "kind": payload.kind,
        # Derived, never accepted from the client.
        "domain": KIND_DOMAIN[payload.kind],
        "charges_to_folio": payload.charges_to_folio,
        "takes_direct_payment": payload.takes_direct_payment,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.outlets.insert_one(row)
    return _public(row)


@router.patch("/outlets/{outlet_id}")
async def update_outlet(outlet_id: str, payload: OutletPatch,
                        user: dict = Depends(WRITE),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    row = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    if not row:
        # 404 rather than 403 for an outlet in another property: the scoped handle
        # filtered it out, so from here it does not exist. A 403 would confirm that some
        # other hotel has an outlet with this id.
        raise HTTPException(404, "No such outlet")

    changes = payload.model_dump(exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()

    # Validate the outlet as it will be, not as it was: switching off the last way it
    # takes money has to be refused even when the request only mentions one of the two.
    merged = {**row, **changes}
    problem = outlet_problem(merged.get("name") or "", merged.get("kind") or "",
                             bool(merged.get("charges_to_folio")),
                             bool(merged.get("takes_direct_payment")))
    if problem:
        raise HTTPException(400, problem)

    if changes:
        await db.outlets.update_one({"id": outlet_id}, {"$set": changes})
    return _public(merged)
