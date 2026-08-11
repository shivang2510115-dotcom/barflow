"""Pydantic models for the guest folio.

The ledger is append-only: there is deliberately no model for updating an entry.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field

FolioStatus = Literal["open", "settled", "closed_unpaid"]
EntryKind = Literal[
    "room_night", "outlet", "misc_charge", "payment", "refund", "discount", "void"
]

# The same set as a plain tuple, so code that has to reason about every kind — notably
# services.revenue, which decides what each one is worth — can check itself against the
# one definition instead of keeping a second copy that drifts.
ENTRY_KINDS: tuple[str, ...] = get_args(EntryKind)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Folio(BaseModel):
    id: str = Field(default_factory=_uuid)
    booking_id: str
    guest_id: str
    status: FolioStatus = "open"
    # Cached for list views only. Every decision recomputes from entries.
    balance: float = 0.0
    opened_at: str = Field(default_factory=_now)
    closed_at: Optional[str] = None
    closed_reason: Optional[str] = None


class FolioEntry(BaseModel):
    id: str = Field(default_factory=_uuid)
    folio_id: str
    kind: EntryKind
    direction: Literal["debit", "credit"]
    amount: float
    description: str
    posted_at: str = Field(default_factory=_now)
    posted_by: Optional[str] = None
    ref_order_id: Optional[str] = None
    ref_entry_id: Optional[str] = None
    charge_date: Optional[str] = None


class CheckInIn(BaseModel):
    room_id: str
    id_proof_type: str
    id_proof_number: str


class CheckOutIn(BaseModel):
    force: bool = False
    reason: Optional[str] = None


class ChargeIn(BaseModel):
    amount: float
    description: str


class PaymentIn(BaseModel):
    amount: float
    method: Literal["cash", "card", "online"] = "cash"
    kind: Literal["payment", "refund", "discount"] = "payment"
    description: Optional[str] = None


class VoidIn(BaseModel):
    reason: str
