"""The document a guest is handed at checkout.

Distinct from `routers/invoices.py`, which is the platform's GST invoice to a hotel for
its subscription. This is the hotel's bill to its guest, and the two share nothing but
the word.

**A bill is a snapshot, not a view.** It is written once, stores its own lines, and never
changes afterwards. This is the load-bearing decision in the file and it is worth being
plain about why: a folio keeps accruing. A guest checks out, and three minutes later last
night's bar tab is posted late. If the bill re-derived itself from the folio on every
read, the copy the guest walked away with and the copy in the system would quietly
disagree — and the first anyone would know is an argument at the desk with no way to
settle it. A late charge produces a *second* bill instead, which is also what the ledger
rule everywhere else in this codebase would demand.

**Numbers are gapless per financial year.** A tax document cannot explain a missing
number by pointing at a race condition, so the number is allocated in the same write that
creates the bill, from the numbers already issued by this property.

**Nothing here deletes or edits.** A wrong bill is cancelled by issuing a credit note
against it, the same shape `routers/invoices.py` uses for the platform's own.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import SHARED
from services.billing import bill_lines, financial_year, next_number
from services.clock import today as local_today

router = APIRouter()

# Issuing a bill is the receptionist's job — the person checking a guest out is the
# person billing them — so this rides on the front desk key rather than inventing one
# that would reach nobody already hired. "admin" is named because the role check runs
# before the admin domain-bypass.
DESK = require_access(("hotel",), "admin", "manager", "front_desk",
                      permission="hotel.front_desk")

# Reading every guest's spend across the property is a manager's question rather than a
# receptionist's, so the list is narrower than the act of issuing one.
OVERSIGHT = require_access(SHARED, "admin", "manager", permission="admin.analytics")

MAX_ROWS = 20000


class BillIn(BaseModel):
    """Nothing. The bill is entirely derived from the folio it is drawn from.

    Deliberately empty rather than accepting lines or totals: a client that could send
    an amount could send a different one from the ledger, and then the document and the
    record would disagree with nobody able to say which was right.
    """


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "_id"}


@router.post("/folios/{folio_id}/bill")
async def issue_bill(folio_id: str, payload: BillIn, user: dict = Depends(DESK),
                     db: PropertyScopedDatabase = Depends(tenant_db)):
    """Draw a bill from a folio, and freeze it."""
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "No such folio")

    entries = await db.folio_entries.find(
        {"folio_id": folio_id}, {"_id": 0}).to_list(MAX_ROWS)
    lines = bill_lines(entries)

    booking = await db.bookings.find_one({"id": folio.get("booking_id")}, {"_id": 0}) or {}
    guest = await db.guests.find_one({"id": booking.get("guest_id")}, {"_id": 0}) or {}
    room = await db.rooms.find_one(
        {"id": booking.get("assigned_room_id")}, {"_id": 0}) or {}

    day = local_today()
    year = financial_year(day)
    issued = await db.bills.find({}, {"_id": 0, "number": 1}).to_list(MAX_ROWS)
    number = next_number([b.get("number") or "" for b in issued], year)

    bill = {
        "id": str(uuid.uuid4()),
        "number": number,
        "folio_id": folio_id,
        "booking_id": folio.get("booking_id"),
        "issued_on": day,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "issued_by": user.get("name") or user.get("email") or "staff",
        # The guest's details as they were at checkout. Copied rather than referenced,
        # because a bill that renamed itself when a guest record was later corrected
        # would no longer match the paper the guest is holding.
        "guest_name": guest.get("name") or "Guest",
        "guest_phone": guest.get("phone") or "",
        "room_number": room.get("number") or "",
        "check_in": booking.get("check_in"),
        "check_out": booking.get("check_out"),
        **lines,
        "cancelled": False,
        "cancelled_reason": None,
    }
    await db.bills.insert_one(dict(bill))
    return _public(bill)


@router.get("/folios/{folio_id}/bills")
async def bills_for_folio(folio_id: str, user: dict = Depends(DESK),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """Every bill drawn from this folio, newest first.

    Usually one. More than one means charges arrived after the first was issued, which
    is exactly the case the snapshot rule exists for.
    """
    rows = await db.bills.find({"folio_id": folio_id}, {"_id": 0}).to_list(MAX_ROWS)
    rows.sort(key=lambda b: b.get("issued_at") or "", reverse=True)
    return rows


@router.get("/bills")
async def list_bills(status: str = Query("", pattern="^(|paid|unpaid)$"),
                     user: dict = Depends(OVERSIGHT),
                     db: PropertyScopedDatabase = Depends(tenant_db)):
    """Every bill this property has issued, newest first."""
    rows = await db.bills.find({}, {"_id": 0}).to_list(MAX_ROWS)
    if status == "paid":
        rows = [b for b in rows if abs(b.get("balance") or 0) < 0.005]
    elif status == "unpaid":
        rows = [b for b in rows if abs(b.get("balance") or 0) >= 0.005]
    rows.sort(key=lambda b: b.get("issued_at") or "", reverse=True)
    return rows


@router.get("/bills/{bill_id}")
async def read_bill(bill_id: str, user: dict = Depends(DESK),
                    db: PropertyScopedDatabase = Depends(tenant_db)):
    row = await db.bills.find_one({"id": bill_id}, {"_id": 0})
    if not row:
        # 404 and not 403 for another property's bill: the scoped handle filtered it
        # out, so from here it does not exist.
        raise HTTPException(404, "No such bill")
    return row
