"""The caller's own hotel: its details, and the admin's edits to them.

Two endpoints, both about the property the caller already belongs to — there is no id in
either path. A hotel cannot ask for another hotel's record here because it cannot name
one: the id comes from the token's user, never from the request.

Approval, suspension and the list of every hotel are not here. They belong to the
platform operator, who reaches them through `/api/platform/*` and is refused everything
in this file.
"""
from fastapi import APIRouter, Depends, HTTPException

from db import unscoped_db
from models.property import PropertyFields
from security import require_access, resolve_property
from services.access import SHARED
from services.clock import today
from services.subscription import subscription_state
from services.registration import (
    FSSAI_SHAPE, GSTIN_SHAPE, validate_fssai, validate_gstin,
)

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

    record = await _own_property(user)
    # `PropertyFields` is the editable half of the record and holds none of `status`,
    # `approved_by` or the lifecycle stamps — which is why the body cannot carry them.
    # An admin who could PUT their own status would approve their own property.
    await unscoped_db.properties.update_one({"id": record["id"]}, {"$set": payload.model_dump()})
    # The same shape GET answers in, so a settings form that saves and re-renders from
    # the response cannot lose the subscription block and show a business as unpriced.
    return _visible(await unscoped_db.properties.find_one({"id": record["id"]}, {"_id": 0}))
