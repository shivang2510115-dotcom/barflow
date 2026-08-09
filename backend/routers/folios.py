"""Guest folio: an append-only ledger of charges and payments.

Nothing in this module updates or deletes an entry. Corrections are new reversing
entries, so a folio can always be reconstructed and a disputed bill has an audit trail.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.folio import ChargeIn, FolioEntry, PaymentIn, VoidIn
from security import require_roles
from services.folio import direction_for, folio_balance, unposted_nights, void_direction

router = APIRouter()

DESK = require_roles("admin", "manager", "front_desk")
MANAGER = require_roles("admin", "manager")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _entries(folio_id: str) -> list[dict]:
    rows = await db.folio_entries.find({"folio_id": folio_id}, {"_id": 0}).to_list(5000)
    return sorted(rows, key=lambda e: e.get("posted_at") or "")


async def _sync_balance(folio_id: str) -> float:
    balance = folio_balance(await _entries(folio_id))
    await db.folios.update_one({"id": folio_id}, {"$set": {"balance": balance}})
    return balance


async def _require_open(folio_id: str) -> dict:
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "Folio not found")
    if folio["status"] != "open":
        raise HTTPException(409, f"This folio is {folio['status']} and cannot be changed")
    return folio


async def post_due_nights(folio_id: str) -> int:
    """Post every room night due but not yet posted. Called on every folio read.

    Lazy rather than scheduled: a server that slept cannot silently skip a night, and
    under real MongoDB the unique index on (folio_id, kind, charge_date) also guards
    this, but mock_db's create_index is a no-op, so unposted_nights is the real protection.
    Amounts come from the booking's quote snapshot so the folio agrees with the price
    the guest was actually quoted, even if rates have changed since.
    """
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio or folio["status"] != "open":
        return 0
    booking = await db.bookings.find_one({"id": folio["booking_id"]}, {"_id": 0})
    if not booking:
        return 0

    existing = await _entries(folio_id)
    due = unposted_nights(booking, _today(), existing)
    if not due:
        return 0

    by_date = {n["date"]: n for n in (booking.get("quote") or {}).get("nights", [])}
    posted = 0
    for night in due:
        priced = by_date.get(night)
        if not priced:
            continue
        amount = round(float(priced["tariff"]) + float(priced["gst_amount"]), 2)
        entry = FolioEntry(
            folio_id=folio_id, kind="room_night", direction="debit", amount=amount,
            description=f"Room night {night}", charge_date=night,
            posted_by="system").model_dump()
        await db.folio_entries.insert_one(entry)
        posted += 1

    if posted:
        await _sync_balance(folio_id)
    return posted


@router.get("/folios")
async def list_folios(status: str = "", user: dict = Depends(DESK)):
    query = {"status": status} if status else {}
    folios = await db.folios.find(query, {"_id": 0}).to_list(1000)
    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    bookings = {b["id"]: b for b in await db.bookings.find({}, {"_id": 0}).to_list(5000)}
    for f in folios:
        f["guest"] = guests.get(f["guest_id"])
        f["booking"] = bookings.get(f["booking_id"])
    return folios


@router.get("/folios/{folio_id}")
async def get_folio(folio_id: str, user: dict = Depends(DESK)):
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "Folio not found")

    await post_due_nights(folio_id)
    entries = await _entries(folio_id)
    balance = folio_balance(entries)
    await db.folios.update_one({"id": folio_id}, {"$set": {"balance": balance}})

    folio["balance"] = balance
    folio["entries"] = entries
    folio["guest"] = await db.guests.find_one({"id": folio["guest_id"]}, {"_id": 0})
    folio["booking"] = await db.bookings.find_one({"id": folio["booking_id"]}, {"_id": 0})
    return folio


@router.post("/folios/{folio_id}/charges")
async def add_charge(folio_id: str, payload: ChargeIn, user: dict = Depends(DESK)):
    await _require_open(folio_id)
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    if not payload.description.strip():
        raise HTTPException(400, "A description is required")

    entry = FolioEntry(
        folio_id=folio_id, kind="misc_charge", direction=direction_for("misc_charge"),
        amount=round(payload.amount, 2), description=payload.description.strip(),
        posted_by=user.get("id")).model_dump()
    await db.folio_entries.insert_one(entry)
    entry.pop("_id", None)
    return {"entry": entry, "balance": await _sync_balance(folio_id)}


@router.post("/folios/{folio_id}/payments")
async def add_payment(folio_id: str, payload: PaymentIn, user: dict = Depends(DESK)):
    await _require_open(folio_id)
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be greater than zero")

    # A refund moves money back to the guest. Managers only.
    if payload.kind == "refund" and user.get("role") not in ("admin", "manager"):
        raise HTTPException(403, "Only a manager can issue a refund")

    default_text = {"payment": f"Payment ({payload.method})",
                    "refund": f"Refund ({payload.method})",
                    "discount": "Discount"}[payload.kind]
    entry = FolioEntry(
        folio_id=folio_id, kind=payload.kind, direction=direction_for(payload.kind),
        amount=round(payload.amount, 2),
        description=(payload.description or default_text).strip(),
        posted_by=user.get("id")).model_dump()
    await db.folio_entries.insert_one(entry)
    entry.pop("_id", None)
    return {"entry": entry, "balance": await _sync_balance(folio_id)}


@router.post("/folios/{folio_id}/entries/{entry_id}/void")
async def void_entry(folio_id: str, entry_id: str, payload: VoidIn,
                     user: dict = Depends(MANAGER)):
    """Reverse an entry by writing a compensating one. Nothing is ever deleted, so a
    disputed bill keeps its audit trail.

    An outlet entry also voids the underlying order: outlet revenue was recognised when
    the bill was served, so leaving the order settled would permanently overstate it.
    """
    await _require_open(folio_id)

    original = await db.folio_entries.find_one(
        {"id": entry_id, "folio_id": folio_id}, {"_id": 0})
    if not original:
        raise HTTPException(404, "Entry not found on this folio")
    if original["kind"] == "void":
        raise HTTPException(409, "A void cannot itself be voided")
    if not payload.reason.strip():
        raise HTTPException(400, "A reason is required to void an entry")

    already = await db.folio_entries.find_one({"kind": "void", "ref_entry_id": entry_id})
    if already:
        raise HTTPException(409, "That entry has already been voided")

    entry = FolioEntry(
        folio_id=folio_id, kind="void",
        direction=void_direction(original["direction"]),
        amount=original["amount"],
        description=f"Void: {original['description']} — {payload.reason.strip()}",
        ref_entry_id=entry_id, posted_by=user.get("id")).model_dump()
    await db.folio_entries.insert_one(entry)
    entry.pop("_id", None)

    voided_order = None
    if original["kind"] == "outlet" and original.get("ref_order_id"):
        await db.orders.update_one({"id": original["ref_order_id"]}, {"$set": {
            "status": "voided",
            "voided_at": datetime.now(timezone.utc).isoformat(),
            "void_reason": payload.reason.strip()}})
        voided_order = original["ref_order_id"]

    return {"entry": entry,
            "balance": await _sync_balance(folio_id),
            "voided_order_id": voided_order}
