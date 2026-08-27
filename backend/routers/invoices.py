"""The operator's GST invoices: who the platform is, and the document each payment gets.

A file of its own rather than another two hundred lines in routers/platform.py, which
already carries approval, suspension, pricing and the payments ledger. Every route here
is `platform_admin` only — the same gate, imported rather than re-declared, because two
copies of an authorization rule is one copy too many.

`platform_settings` and `platform_invoices` both stand outside tenancy, named in
scoped_db.py beside `properties` and `subscription_payments` and for the same reason: the
platform's own registration and the tax documents it issues are not any hotel's data, and
the operator belongs to no property to be scoped to. Every read here is filtered in the
open.

**What is deliberately absent.** There is no PUT, PATCH or DELETE on an invoice. Not
disabled, not permission-gated — absent, so there is no code path that could be reached
by a route somebody adds later without reading this. An issued invoice is a tax document;
a correction is a credit note that references it, and both documents stay. That is the
append-only rule services/folio.py already follows, and this is the stricter case of it.

**Numbering under concurrency.** Two invoices issued in the same instant must not take
the same number, and neither may leave a gap. Three things arrange that, in the order
they are reached:

1. *the payment already has one* — issuing is idempotent per payment, so the ordinary
   double-click never allocates at all and comes back with the document that exists;
2. *an in-process lock* serialises allocate-and-insert. This deployment is a single
   uvicorn process (a container) or a single Cloud Function instance, so within it the
   read of the highest number and the insert that follows it cannot interleave;
3. *a unique index on `number`*, created in seed_data. Against real MongoDB this is what
   holds when there is more than one process; the insert fails and the handler tries
   again with a freshly read number. The JSON mock and Firestore both no-op
   `create_index`, so on those the lock is the whole guarantee — which is stated here
   rather than assumed, and is true of every other unique index in this application.

A failed insert consumes nothing, because the number is derived from what is *stored* at
the moment of allocation rather than from a counter that has already been incremented.
That is what keeps the series gapless.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import unscoped_db
from models.invoice import (
    PlatformInvoice, PlatformSettings, PlatformSettingsFields, Party)
from routers.platform import platform_admin
from services.clock import today
from services.invoicing import (
    CREDIT_NOTE_PREFIX, INVOICE_PREFIX, InvoiceError, SUPPLY_LABELS, amount_in_words,
    financial_year, invoice_amounts, invoice_number, next_sequence, place_of_supply,
)
from services.registration import GSTIN_SHAPE, validate_gstin

router = APIRouter()

SETTINGS_ID = "platform"

# How many times to re-read and re-number when a concurrent writer took the number first.
# Small on purpose: past a couple of collisions the honest answer is that something else
# is wrong, and a handler that retried forever would hold the request open while it did.
_NUMBER_ATTEMPTS = 5

# Serialises allocate-and-insert within this process. See the module docstring for what
# it does and does not cover.
_numbering = asyncio.Lock()


# --------------------------------- the settings ---------------------------------
async def _settings() -> dict:
    """The platform's own record, or an empty one.

    Absent is a normal state — a deployment that has not been set up yet — and it reads
    as blank fields on the console rather than a 404 the operator cannot act on. What it
    does *not* do is let an invoice be issued: `place_of_supply` refuses without a state,
    and that refusal names the field.
    """
    record = await unscoped_db.platform_settings.find_one({"id": SETTINGS_ID}, {"_id": 0})
    return record or PlatformSettings().model_dump()


@router.get("/platform/settings")
async def get_settings(user: dict = Depends(platform_admin)):
    return await _settings()


@router.put("/platform/settings")
async def update_settings(payload: PlatformSettingsFields,
                          user: dict = Depends(platform_admin)):
    """Who the platform is on the invoices it issues.

    The GSTIN is format-checked here and answered as a 400 naming the field, the same way
    a hotel's own is in routers/property.py — a settings form needs a message it can put
    beside the input that is wrong, which a 422 from the body model cannot be.

    Upserted rather than created-then-updated: there is one platform, and a first save
    and a hundredth are the same operation.
    """
    if not validate_gstin(payload.gstin or None):
        raise HTTPException(400, f"gstin is not valid — expected {GSTIN_SHAPE}")

    body = payload.model_dump()
    body.update(id=SETTINGS_ID, updated_by=user["id"],
                updated_at=datetime.now(timezone.utc).isoformat())
    await unscoped_db.platform_settings.update_one(
        {"id": SETTINGS_ID}, {"$set": body}, upsert=True)
    return await _settings()


# ---------------------------------- the invoice ----------------------------------
class IssueIn(BaseModel):
    """What the operator may say when issuing.

    `place_of_supply` is the override the design asks for — the rule reads the hotel's
    state against the platform's, and there are cases it does not cover. Left unset, the
    rule decides; set, it wins, and it is recorded on the document either way.
    """
    place_of_supply: Optional[str] = None


class CreditNoteIn(BaseModel):
    reason: str = ""


def _party_from_settings(settings: dict) -> Party:
    return Party(
        name=settings.get("legal_name") or "",
        legal_name=settings.get("legal_name") or "",
        gstin=settings.get("gstin") or "",
        address=_address(settings),
        state=settings.get("state") or "",
    )


def _party_from_property(record: dict) -> Party:
    return Party(
        name=record.get("name") or "",
        legal_name=record.get("legal_name") or record.get("name") or "",
        gstin=record.get("gstin") or "",
        address=_address(record),
        state=record.get("state") or "",
    )


def _address(record: dict) -> str:
    """One printable address from the parts a record keeps it in.

    Blank parts are dropped rather than left as empty lines, so a hotel that filled in
    three of five fields does not get an invoice with gaps down the middle of it.
    """
    parts = [record.get(k) or "" for k in
             ("address_line1", "address_line2", "city", "state", "pincode")]
    return ", ".join(p.strip() for p in parts if p and p.strip())


async def _issued_numbers() -> list[str]:
    rows = await unscoped_db.platform_invoices.find({}, {"_id": 0}).to_list(100000)
    return [r.get("number") or "" for r in rows]


async def _insert_numbered(document: dict, prefix: str, fy: str) -> dict:
    """Allocate the next number in a series and store the document under it.

    Held under `_numbering` so the read of the highest issued number and the insert that
    follows cannot interleave within this process. The retry above it is for the case
    that lock cannot see — a second process — where the unique index on `number` refuses
    the insert and the honest response is to read again rather than to force it.
    """
    async with _numbering:
        for _attempt in range(_NUMBER_ATTEMPTS):
            number = invoice_number(prefix, fy, next_sequence(
                await _issued_numbers(), prefix, fy))
            # Re-checked inside the lock against the store rather than trusted from the
            # list above, because this is the assertion that matters and it costs one
            # read: no document may ever be written under a number that exists.
            if await unscoped_db.platform_invoices.find_one({"number": number}, {"_id": 0}):
                continue
            document["number"] = number
            try:
                await unscoped_db.platform_invoices.insert_one(dict(document))
            except Exception as exc:  # a unique-index refusal from another process
                if "duplicate" not in str(exc).lower():
                    raise
                continue
            return document
    raise HTTPException(
        503, "Could not allocate an invoice number — another invoice is being issued. "
             "Try again.")


async def _property_or_404(property_id: str) -> dict:
    record = await unscoped_db.properties.find_one({"id": property_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "No such property")
    return record


@router.post("/platform/properties/{property_id}/payments/{payment_id}/invoice")
async def issue_invoice(property_id: str, payment_id: str, payload: IssueIn,
                        user: dict = Depends(platform_admin)):
    """Issue the tax invoice for one recorded payment.

    **Idempotent per payment.** A payment that already has an invoice comes back with
    that invoice and nothing is allocated. This is not politeness about double-clicks: a
    second document for one payment is a second tax invoice for money that arrived once,
    and it cannot be deleted afterwards.

    Both parties and the period are copied onto the document as they stand today. An
    invoice that re-rendered itself from the property record would silently restate last
    year's document against this year's address.
    """
    record = await _property_or_404(property_id)
    payment = await unscoped_db.subscription_payments.find_one(
        {"id": payment_id, "property_id": property_id}, {"_id": 0})
    if not payment:
        raise HTTPException(404, "No such payment on this property")

    existing = await unscoped_db.platform_invoices.find_one(
        {"payment_id": payment_id, "kind": "invoice"}, {"_id": 0})
    if existing:
        return existing

    settings = await _settings()
    try:
        supply = place_of_supply(settings.get("state"), record.get("state"),
                                 override=payload.place_of_supply)
    except InvoiceError as exc:
        # 400 rather than 422: this is a missing *record*, not a malformed request, and
        # the message names which one so the operator knows where to go.
        raise HTTPException(400, str(exc))

    amounts = invoice_amounts(payment["amount"], supply,
                              inclusive=bool(settings.get("prices_include_gst", True)))
    day = today()
    document = PlatformInvoice(
        number="",  # allocated under the lock, below
        financial_year=financial_year(day),
        kind="invoice",
        property_id=property_id,
        payment_id=payment_id,
        supplier=_party_from_settings(settings),
        customer=_party_from_property(record),
        period_from=payment.get("covers_from") or "",
        period_to=payment.get("covers_to") or "",
        total_in_words=amount_in_words(amounts["total"]),
        issued_on=day,
        issued_by=user["id"],
        **{k: amounts[k] for k in ("place_of_supply", "gst_rate", "taxable_value",
                                   "cgst", "sgst", "igst", "tax_total", "total")},
    ).model_dump()
    return await _insert_numbered(document, INVOICE_PREFIX, document["financial_year"])


@router.post("/platform/invoices/{invoice_id}/credit-note")
async def issue_credit_note(invoice_id: str, payload: CreditNoteIn,
                            user: dict = Depends(platform_admin)):
    """Correct an issued invoice the only way a tax document can be corrected.

    A new document, in its own series, naming the one it reverses and carrying the same
    figures with the sign turned round. The original is untouched and stays readable —
    that is the point of it, not a limitation of it.

    An invoice may be credited once, and a second attempt is refused by name. Two
    reversals of one invoice is a balance somebody has to net off by hand against a
    document neither of them can edit, and the operator asking for it has almost always
    pressed the button twice rather than meant it.
    """
    original = await unscoped_db.platform_invoices.find_one(
        {"id": invoice_id}, {"_id": 0})
    if not original or original.get("kind") != "invoice":
        raise HTTPException(404, "No such invoice")

    already = await unscoped_db.platform_invoices.find_one(
        {"corrects": original["number"]}, {"_id": 0})
    if already:
        raise HTTPException(
            409, f"{original['number']} has already been credited by "
                 f"{already['number']}")

    day = today()
    fy = financial_year(day)
    document = PlatformInvoice(
        number="",
        financial_year=fy,
        kind="credit_note",
        property_id=original["property_id"],
        # Deliberately not the payment's: the payment has an invoice, and a credit note
        # that claimed the same payment id would make the idempotency check above find a
        # reversal where it looks for a document.
        payment_id=None,
        supplier=Party(**original["supplier"]),
        customer=Party(**original["customer"]),
        period_from=original.get("period_from") or "",
        period_to=original.get("period_to") or "",
        place_of_supply=original["place_of_supply"],
        gst_rate=original["gst_rate"],
        taxable_value=-original["taxable_value"],
        cgst=-original["cgst"], sgst=-original["sgst"], igst=-original["igst"],
        tax_total=-original["tax_total"], total=-original["total"],
        total_in_words=amount_in_words(-original["total"]),
        corrects=original["number"],
        reason=payload.reason.strip(),
        issued_on=day,
        issued_by=user["id"],
    ).model_dump()
    return await _insert_numbered(document, CREDIT_NOTE_PREFIX, fy)


@router.get("/platform/properties/{property_id}/invoices")
async def list_invoices(property_id: str, user: dict = Depends(platform_admin)):
    """Every document issued to one business, newest first.

    Nothing here edits or deletes a line, exactly as the payments ledger beside it does
    not. `place_of_supply_label` comes back humanised so the console prints what the
    server said rather than looking the word up a second time and disagreeing with it.
    """
    await _property_or_404(property_id)
    rows = await unscoped_db.platform_invoices.find(
        {"property_id": property_id}, {"_id": 0}).to_list(10000)
    rows.sort(key=lambda r: (r.get("issued_on") or "", r.get("number") or ""),
              reverse=True)
    return [{**r, "place_of_supply_label": SUPPLY_LABELS.get(r.get("place_of_supply"), "")}
            for r in rows]


@router.get("/platform/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, user: dict = Depends(platform_admin)):
    """One document, for printing. Read-only, like everything else about it."""
    row = await unscoped_db.platform_invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "No such invoice")
    return {**row,
            "place_of_supply_label": SUPPLY_LABELS.get(row.get("place_of_supply"), "")}
