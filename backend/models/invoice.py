"""The platform's own settings, and the tax invoices it issues against them.

Two records that are not a hotel's. `properties` and `subscription_payments` already
stand outside tenancy for the reason given in scoped_db.py — what a business pays the
platform is not the business's own data — and these two stand there beside them.

**An invoice is immutable once issued.** There is no edit route and no delete route, and
there is nothing here that could be used to write one: a correction is a *credit note*
referencing the original, and both documents stay. That is the same append-only rule
`services/folio.py` follows, for a stronger version of the same reason — a folio line
that can be rewritten cannot settle an argument, and a tax document that can be rewritten
is not a tax document at all.

**Both parties are snapshotted onto the invoice**, rather than rendered from the property
and settings records when it is read. A hotel that moves office, or a platform that
changes its registered address, must not silently restate an invoice that was issued and
filed a year ago against the address it was actually issued from.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from services.invoicing import GST_RATE, INTER_STATE, INTRA_STATE

# What kind of document this is. A credit note is not an invoice with a minus sign in
# front of it — it runs in its own number series and names the invoice it corrects — so
# the two are told apart on the record rather than inferred from the sign of the total.
DocumentKind = Literal["invoice", "credit_note"]
PlaceOfSupply = Literal["intra", "inter"]

# Guards the Literal above against the vocabulary moving in services/invoicing.py, the
# same arrangement models/property.py uses for its three.
if set(PlaceOfSupply.__args__) != {INTRA_STATE, INTER_STATE}:  # pragma: no cover
    raise RuntimeError(
        "models.invoice.PlaceOfSupply has drifted from services.invoicing — "
        "update the Literal above")


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlatformSettingsFields(BaseModel):
    """Who the platform is, on the invoices it issues.

    Not per-hotel and not a deployment variable: the legal name and GSTIN on a tax
    document are the operator's own registration, they change when the company's
    registration changes, and nobody is redeploying a container to correct an address.

    `state` is the load-bearing one. The place-of-supply rule reads it against the
    hotel's, and without it no invoice can be issued at all — which is the right
    failure, because the alternative is an invoice with the tax under a head the hotel
    cannot claim against.
    """
    legal_name: str = ""
    gstin: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    email: str = ""
    phone: str = ""
    # Whether an agreed subscription figure is what the hotel transfers, or what it
    # transfers before tax.
    #
    # True — the default — means a recorded ₹12,000 payment produces a ₹12,000 invoice
    # with the tax inside it, so the document reconciles line for line against the bank
    # statement it was matched from. False means ₹12,000 was the taxable value and the
    # invoice totals ₹14,160, which is a real arrangement and a real reconciliation
    # problem: it is offered as a choice rather than assumed either way.
    prices_include_gst: bool = True


class PlatformSettings(PlatformSettingsFields):
    """The stored singleton. One row, one id, because there is one platform.

    A fixed id rather than a uuid: this is the record every invoice is issued against,
    and "the settings" has to be findable without a list to pick from. It is the one
    place in this codebase where a singleton constant is correct — the reason
    `models/property.py` refuses one is that a second *hotel* must not collide with the
    first, and there is no second platform.
    """
    id: str = "platform"
    updated_at: str = Field(default_factory=_now)
    updated_by: Optional[str] = None


class Party(BaseModel):
    """One side of the invoice, as it stood on the day it was issued."""
    name: str = ""
    legal_name: str = ""
    gstin: str = ""
    address: str = ""
    state: str = ""


class PlatformInvoice(BaseModel):
    """One issued document: an invoice, or the credit note that corrects one.

    Nothing mutates this. There is no route that edits a field on it and no route that
    deletes it — see routers/invoices.py, where that is stated as an absence and as a
    comment, because the first thing anybody tries on a mistyped invoice is to fix it in
    place and finding no button is only an answer if you know it was deliberate.
    """
    id: str = Field(default_factory=_uuid)
    # The series number: BF/2026-27/0001. Unique across every document, per series and
    # per financial year, and never reused.
    number: str
    financial_year: str
    kind: DocumentKind = "invoice"

    # Which business, and which recorded payment this documents. `payment_id` is what
    # makes issuing idempotent: one payment has one invoice, so a double-click on the
    # operator's console cannot spend a second number.
    property_id: str
    payment_id: Optional[str] = None

    supplier: Party
    customer: Party

    # The term the payment bought, copied from the ledger line rather than recomputed:
    # the rule that produced it read the property's state at the moment of payment, and
    # that state has since moved on.
    period_from: str = ""
    period_to: str = ""

    place_of_supply: PlaceOfSupply
    gst_rate: float = GST_RATE
    taxable_value: float
    cgst: float = 0.0
    sgst: float = 0.0
    igst: float = 0.0
    tax_total: float
    total: float
    # Indian invoices conventionally carry it, and it is a check on the figure beside it:
    # a total altered by one digit is caught by the words that did not change with it.
    total_in_words: str

    # A credit note names the invoice it corrects, and why. An invoice has neither.
    corrects: Optional[str] = None
    reason: str = ""

    issued_on: str
    issued_at: str = Field(default_factory=_now)
    issued_by: Optional[str] = None
