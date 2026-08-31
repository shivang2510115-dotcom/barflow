"""The caller's own hotel: its details, and the admin's edits to them.

Two endpoints, both about the property the caller already belongs to — there is no id in
either path. A hotel cannot ask for another hotel's record here because it cannot name
one: the id comes from the token's user, never from the request.

Approval, suspension and the list of every hotel are not here. They belong to the
platform operator, who reaches them through `/api/platform/*` and is refused everything
in this file.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import unscoped_db
from models.property import PropertyFields
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, resolve_property
from services.access import SHARED
from services.clock import today
from services.subscription import subscription_state
from services.registration import (
    FSSAI_SHAPE, GSTIN_SHAPE, validate_fssai, validate_gstin,
)
from services.tax import TaxRateError, normalise_rate

logger = logging.getLogger(__name__)
router = APIRouter()

# Read by anyone signed in, in any part of the business: the property's name, address and
# GSTIN are the header of every printed bill, so the POS needs this as much as the front
# desk does. It carries nothing about guests, money or staff.
#
# Setup-time, like everything else here — a hotel awaiting approval is precisely the one
# filling this record in.
READ = require_access(SHARED, setup_time=True)

# Writing is the owner's. Name, GSTIN and check-out time are what a guest's bill is
# issued under; a manager who can take a booking still cannot change who the hotel legally
# is. Roles name "admin" explicitly because the role check runs before the domain bypass.
WRITE = require_access(SHARED, "admin", setup_time=True)

# The stored columns are dropped and the computed block put in their place, rather than
# both being returned: the same four values appearing twice is an invitation to read the
# stale pair. `subscription` carries what a business needs — the figure, the period, the
# date and whether it has passed — and nothing it could act on wrongly. It cannot change
# any of it: pricing is the operator's, and a business that could price itself would
# price itself at zero.
_STORED_SUBSCRIPTION = ("subscription_amount", "billing_period", "paid_until",
                        "payment_note")


def _visible(record: dict) -> dict:
    body = {k: v for k, v in record.items() if k not in _STORED_SUBSCRIPTION}
    return {**body, "subscription": subscription_state(record, today())}


async def _own_property(user: dict) -> dict:
    record = await resolve_property(user)
    if not record:
        # Unreachable through the dependencies above — both refuse a caller whose
        # property is missing — but a 404 is the honest answer if that ever changes.
        raise HTTPException(404, "Property not found")
    return record


@router.get("/property")
async def get_property(user: dict = Depends(READ)):
    """The caller's own hotel, including what it has agreed to pay us.

    The subscription is read-only here and computed rather than stored: a business can
    see its price, its billing period, when it is paid until and whether that has passed,
    which is what it needs to settle an invoice. It cannot change any of it — pricing is
    the operator's, and a business that could set its own would set it to zero — and it
    can see nothing about anybody else's.

    Being overdue is deliberately inert. It shows a banner and stops nothing: only the
    operator pressing suspend stops trade, because a hotel with guests arriving must not
    go dark over a late invoice.
    """
    return _visible(await _own_property(user))


@router.put("/property")
async def update_property(payload: PropertyFields, user: dict = Depends(WRITE)):
    """Update the caller's own property.

    The two statutory identifiers are checked here rather than by the request model, so
    that a typo comes back as a 400 naming the field and quoting the expected shape. A
    Pydantic validator on the body would produce a 422 whose message the settings form
    cannot put beside the input that is wrong.
    """
    for field, is_valid, shape in (
        ("gstin", validate_gstin, GSTIN_SHAPE),
        ("fssai_licence", validate_fssai, FSSAI_SHAPE),
    ):
        if not is_valid(getattr(payload, field)):
            raise HTTPException(400, f"{field} is not valid — expected {shape}")

    # Checked here rather than by a Pydantic validator on the body, for the reason above:
    # the settings form needs a 400 it can put beside the input that is wrong. A rate
    # outside the schedule is a typo, and a typo here is added to every bill the outlet
    # prints until somebody notices.
    try:
        normalise_rate(payload.outlet_gst_rate)
    except TaxRateError as exc:
        raise HTTPException(400, f"outlet_gst_rate is not valid — {exc}")

    record = await _own_property(user)
    # `PropertyFields` is the editable half of the record and holds none of `status`,
    # `approved_by` or the lifecycle stamps — which is why the body cannot carry them.
    # An admin who could PUT their own status would approve their own property.
    patch = payload.model_dump()
    # The one field where silence means "leave it alone" rather than "set it to the
    # default". It was added after the settings form and every integration already
    # existed, and this endpoint replaces the editable half wholesale — so a body written
    # before it existed would switch a hotel's whole pricing model off as a side effect
    # of correcting its address. See models/property.py.
    if patch.get("meal_plans_enabled") is None:
        patch.pop("meal_plans_enabled", None)
    await unscoped_db.properties.update_one({"id": record["id"]}, {"$set": patch})
    # The same shape GET answers in, so a settings form that saves and re-renders from
    # the response cannot lose the subscription block and show a business as unpriced.
    return _visible(await unscoped_db.properties.find_one({"id": record["id"]}, {"_id": 0}))


# What a trial records, and what a trial configures. The split is the whole point of the
# route below: a hotel that has finished testing wants the first gone and the second kept.
#
# `tables` is deliberately on neither list here — it is kept, and it is worth saying why:
# twenty printed QR cards encode table ids, and deleting the rows would turn every one of
# them into a dead link on a Monday morning.
TRIAL_COLLECTIONS = (
    "bookings", "guests", "folios", "folio_entries", "bills",
    "orders", "housekeeping_jobs", "housekeeping_events",
    "attendance", "salary_runs", "payslips", "advances",
    "entitlement_uses", "message_log", "message_claims",
    "reservations", "expenses",
)


class ResetIn(BaseModel):
    """`confirm` must be the literal string DELETE.

    Not a boolean. A boolean is one mistyped JSON field away from being true, and this
    route empties a hotel's transaction history. Anything else counts and deletes nothing,
    which is what the script's default mode relies on.
    """
    confirm: str = ""


@router.post("/property/reset-trial-data")
async def reset_trial_data(payload: ResetIn, user: dict = Depends(WRITE),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    """Count, or clear, everything this property recorded while it was being tested.

    Admin only, and scoped by the bound handle — there is no property id in the request,
    so this cannot reach another hotel's rows even if somebody wanted it to.

    **Counting is what happens unless `confirm` is exactly "DELETE".** A destructive route
    whose safe mode needs a flag is one keystroke from being the wrong route.

    What it keeps is as deliberate as what it removes: rooms, room types, rates, the menu,
    tables, packages, outlets, reference data and staff logins all survive. A hotel doing
    this has finished testing, not started over.
    """
    deleting = payload.confirm == "DELETE"

    counts: dict[str, int] = {}
    for name in TRIAL_COLLECTIONS:
        collection = getattr(db, name)
        rows = await collection.find({}, {"_id": 0, "id": 1}).to_list(100000)
        counts[name] = len(rows)
        if not deleting:
            continue
        # One at a time, by id, through the scoped handle. A bulk delete would be faster
        # and would also be the one call in this file able to reach past the property it
        # is bound to.
        for row in rows:
            if row.get("id"):
                await collection.delete_one({"id": row["id"]})

    logger.info("Trial data %s for %s: %s",
                "deleted" if deleting else "counted", db.property_id,
                {k: v for k, v in counts.items() if v})

    return {"deleted": deleting, "counts": counts}
