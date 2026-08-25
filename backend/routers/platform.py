"""The platform operator's console: approve a hotel, suspend it, restore it.

Every route here is `platform_admin` only, and a `platform_admin` belongs to no hotel —
`can_access` refuses them every hotel endpoint on the role check. That is the whole
design: the operator's login is not a master key into customer data. They can see that a
hotel exists, how big it is, and how far through setup it got. They cannot see a guest, a
folio, a booking or an identity document, and there is no route here that would let them.

`unscoped_db` is used because the operator works across tenants by definition — the list
of hotels is not something any one hotel's scoped handle could return.

The subscription routes below are the operator's other job. Pricing is **manual**: there
is no gateway, no card and no self-serve checkout. A figure is agreed offline, recorded
here, and the money arrives by bank transfer or UPI. Two things follow, and both are
deliberate:

* an overdue business **keeps trading**. Nothing in this file switches a property off on
  a date. `POST .../status` is the only thing that stops trade and it is a person
  pressing a button, because a hotel with guests checking in must not go dark over a
  four-day-late invoice;
* the payments log is **append-only**. There is no route here that edits or deletes a
  line — a correction is a new entry — following the folio ledger's reasoning in
  services/folio.py. This is money changing hands outside any gateway, reconciled by
  hand against a bank statement, and a record that can be rewritten cannot settle an
  argument about whether ₹12,000 was ever received.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import unscoped_db
from models.property import SubscriptionPayment
from scoped_db import PropertyScopedDatabase
from security import get_current_user
from services.access import (
    LIVE, PENDING, PLATFORM_ADMIN, PROPERTY_TYPES, SUSPENDED,
    domains_for_property_type, narrow_to_domains)
from services.clock import today
from services.subscription import (
    METHOD_LABELS, SubscriptionError, advance_paid_until, normalise_method,
    normalise_period, period_covered, subscription_state)

router = APIRouter()

STATUSES = (PENDING, LIVE, SUSPENDED)

# What stops being reachable when a property gives up its hotel side. Reported back on a
# retype so the operator can tell the owner, rather than the owner discovering it.
HOTEL_COLLECTIONS = ("rooms", "room_types", "rates", "bookings", "folios")


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


class SubscriptionIn(BaseModel):
    """The figure agreed offline. Both halves or neither.

    Plain `Optional`s rather than Literals, so an unknown period arrives at the handler
    and comes back as a 422 that names it and lists the three — pydantic's enum error
    would recite the vocabulary without saying which field it belonged to, and this route
    is typed into a console by the one person who cannot ask anybody else.

    Sending both as None withdraws the price, which is a real thing to want: a business
    moved to a different arrangement should stop showing a figure rather than keep an
    old one nobody honours.
    """
    amount: Optional[float] = None
    period: Optional[str] = None
    note: str = ""


class PaymentIn(BaseModel):
    """Money that arrived. `received_on` defaults to the property's local day."""
    amount: float
    method: str
    received_on: Optional[str] = None
    reference: str = ""


class PropertyTypeIn(BaseModel):
    property_type: str


def _summary(record: dict, day: str) -> dict:
    """One row of the operator's list.

    `subscription` is computed here, from the record, on every read — see
    services/subscription.py for why there is no stored flag to read instead. It costs
    nothing (two date comparisons) and it cannot be stale, which a nightly recompute
    could be for a whole day without anybody noticing.

    `payment_note` is not here. It is the operator's memo and it belongs on the detail
    panel of the one property they opened, not beside every name in a list.
    """
    return {
        "id": record["id"],
        "name": record.get("name") or "",
        "city": record.get("city") or "",
        "gstin": record.get("gstin") or "",
        "status": record.get("status"),
        "property_type": record.get("property_type"),
        "created_at": record.get("created_at"),
        "approved_at": record.get("approved_at"),
        "suspended_at": record.get("suspended_at"),
        "suspension_reason": record.get("suspension_reason"),
        "subscription": subscription_state(record, day),
    }


async def _record_or_404(property_id: str) -> dict:
    record = await unscoped_db.properties.find_one({"id": property_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "No such property")
    return record


@router.get("/platform/properties")
async def list_properties(status: str = "", user: dict = Depends(platform_admin)):
    query = {}
    if status:
        if status not in STATUSES:
            raise HTTPException(422, f"Unknown status: {status}")
        query["status"] = status
    rows = await unscoped_db.properties.find(query, {"_id": 0}).to_list(5000)
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    # One day for the whole list, so two properties cannot be reported against different
    # days because the clock ticked over midway through the loop.
    day = today()
    return [_summary(r, day) for r in rows]


@router.get("/platform/properties/{property_id}")
async def property_detail(property_id: str, user: dict = Depends(platform_admin)):
    """How big the hotel is and how far through setup it got — never who stayed there.

    Counts only. No guest, booking, folio or identity document is reachable from this
    file, because deciding whether to approve a business does not require reading its
    customers' records.
    """
    record = await _record_or_404(property_id)

    scoped = PropertyScopedDatabase(property_id)
    rooms = await scoped.rooms.count_documents({})
    room_types = await scoped.room_types.count_documents({})
    rates = await scoped.rates.count_documents({})
    menu = await scoped.menu.count_documents({})
    tables = await scoped.tables.count_documents({})
    staff = await unscoped_db.users.count_documents({"property_id": property_id})

    return {
        **_summary(record, today()),
        # The operator's own memo about how this business pays. Deliberately not in
        # _summary — it belongs on the one property they opened, not beside every name
        # in the list.
        "payment_note": record.get("payment_note") or "",
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
    return _summary(updated, today())


class SubscriptionIn(BaseModel):
    """What was agreed. Both halves or neither.

    Nullable because a price can be withdrawn — a business moved to a different
    arrangement, or priced by mistake — and the way to say "there is no agreement here"
    has to be the same shape as the way to state one.
    """
    amount: Optional[float] = None
    period: Optional[str] = None
    note: str = ""


class PaymentIn(BaseModel):
    amount: float
    method: str
    # None means "today": the transfer is usually reconciled the day it lands, and the
    # operator should not have to retype the date to say so.
    received_on: Optional[str] = None
    reference: str = ""


@router.put("/platform/properties/{property_id}/subscription")
async def set_subscription(property_id: str, payload: SubscriptionIn,
                           user: dict = Depends(platform_admin)):
    """What this business has agreed to pay, and how often.

    The operator's alone. A business that could price itself would set its own figure to
    zero, which is why this is here and not on /api/property.

    Setting a price does not touch `paid_until`: agreeing a number is not the same as
    receiving money, and inventing a paid-up term from a price would show a business as
    paid for a month nobody had paid for. Only recording a payment moves that date.
    """
    await _record_or_404(property_id)
    amount, raw_period = payload.amount, payload.period
    # Half a price cannot be advanced by a payment, so half a price is not a price.
    if (amount is None) != (raw_period is None):
        raise HTTPException(
            422, "An agreed price needs both an amount and a billing period")
    patch = {"payment_note": payload.note.strip()}
    if amount is None:
        patch["subscription_amount"] = None
        patch["billing_period"] = None
    else:
        if amount < 0:
            raise HTTPException(422, "An agreed price cannot be negative")
        try:
            patch["billing_period"] = normalise_period(raw_period)
        except SubscriptionError as exc:
            raise HTTPException(422, str(exc))
        patch["subscription_amount"] = round(amount, 2)
    await unscoped_db.properties.update_one({"id": property_id}, {"$set": patch})
    return _summary(await _record_or_404(property_id), today())


@router.post("/platform/properties/{property_id}/payments")
async def record_payment(property_id: str, payload: PaymentIn,
                         user: dict = Depends(platform_admin)):
    """Money arrived. Write the line, then move the paid-until date.

    In that order on purpose: the ledger line is the fact, and `paid_until` is a
    convenience derived from it. If the second write failed the money would still be
    recorded, which is the right way round to be wrong.
    """
    record = await _record_or_404(property_id)
    if payload.amount <= 0:
        raise HTTPException(422, "A payment has to be for something")
    period = record.get("billing_period")
    if not period:
        # Advancing by a period nobody agreed would invent a term. Price it first.
        raise HTTPException(
            400, "Agree a price and a billing period before recording a payment")
    try:
        period = normalise_period(period)
        method = normalise_method(payload.method)
    except SubscriptionError as exc:
        raise HTTPException(422, str(exc))

    day = today()
    received = (payload.received_on or "").strip() or day
    covers_from, covers_to = period_covered(record.get("paid_until"), period, day)
    line = SubscriptionPayment(
        property_id=property_id, amount=round(payload.amount, 2),
        received_on=received, covers_from=covers_from, covers_to=covers_to,
        method=method, reference=payload.reference.strip(),
        recorded_by=user["id"],
    ).model_dump()
    await unscoped_db.subscription_payments.insert_one(line)
    await unscoped_db.properties.update_one({"id": property_id}, {"$set": {
        "paid_until": advance_paid_until(record.get("paid_until"), period, day),
    }})
    # The line and the state it produced, together: the operator has just typed a figure
    # and the next thing they want to know is what it bought and until when.
    return {**_summary(await _record_or_404(property_id), day), "payment": line}


@router.get("/platform/properties/{property_id}/payments")
async def list_payments(property_id: str, user: dict = Depends(platform_admin)):
    """The ledger, newest first. Nothing here edits or deletes a line."""
    await _record_or_404(property_id)
    rows = await unscoped_db.subscription_payments.find(
        {"property_id": property_id}, {"_id": 0}).to_list(10000)
    rows.sort(key=lambda r: (r.get("received_on") or "", r.get("recorded_at") or ""),
              reverse=True)
    return [{**r, "method_label": METHOD_LABELS.get(r.get("method"), r.get("method"))}
            for r in rows]


@router.post("/platform/properties/{property_id}/type")
async def set_property_type(property_id: str, payload: PropertyTypeIn,
                            user: dict = Depends(platform_admin)):
    """Correct what a business is when the wrong thing was picked at signup.

    Narrowing takes domains away — `both` to `outlet` removes the hotel — so every staff
    record has to come with it. Left alone they would hold a domain the property no
    longer has and screens that 403 on click, which is the same lie the staff screen
    refuses to store when the ticking is done by hand.
    """
    if payload.property_type not in PROPERTY_TYPES:
        raise HTTPException(422, f"Unknown property type: {payload.property_type}")
    record = await _record_or_404(property_id)
    allowed = domains_for_property_type(payload.property_type)

    narrowed = deactivated = 0
    staff = await unscoped_db.users.find(
        {"property_id": property_id}, {"_id": 0, "password_hash": 0}).to_list(10000)
    for member in staff:
        patch = narrow_to_domains(member, allowed)
        if not patch:
            continue
        await unscoped_db.users.update_one({"id": member["id"]}, {"$set": patch})
        narrowed += 1
        if patch.get("active") is False:
            deactivated += 1

    await unscoped_db.properties.update_one(
        {"id": property_id}, {"$set": {"property_type": payload.property_type}})
    # Reported, not silent: someone was switched off by this, and the operator who did it
    # is the only person in a position to tell that business why.
    # Which domains this business just gave up, so the operator can tell them what stops
    # working rather than leaving them to find out when a receptionist cannot sign in.
    lost = [d for d in domains_for_property_type(record.get("property_type") or "both")
            if d not in allowed]
    return {**_summary(await _record_or_404(property_id), today()),
            "staff": {"narrowed": narrowed, "deactivated": deactivated},
            "unreachable": lost}
