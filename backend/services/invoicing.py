"""The platform's own GST invoice: numbering, place of supply, and the amount in words.

Pure functions over plain values — no database, no framework — for the same reason
`pricing.py`, `subscription.py` and `tax.py` beside it are. This one is a *tax document*,
which raises the stakes on every default: an invoice with a guessed number, a guessed
split or a guessed year is not a document a hotel's accountant can use, and it is not one
the operator can withdraw either, because an issued invoice is immutable.

Three rules carry it, and all three are statutory rather than preferences.

**Place of supply decides the split.** A hotel in the same state as the platform is
charged CGST 9% + SGST 9%; one in a different state is charged IGST 18%. The same 18%
either way — what differs is which head it sits under, and an accountant cannot claim
against a head that was never charged. It follows from the hotel's `state` against the
platform's own, with an override for the cases the rule does not cover.

**Numbering is sequential per financial year**, and the Indian financial year begins on
1 April. `BF/2026-27/0001`. Never reused, never reordered: a gap in an invoice series is
a question from an auditor, so the next number is derived from the highest one *issued*
rather than from a count — a count hands back a number already on a document the moment
one insert has ever failed between two successes. Credit notes run in their own series
(`BF/CN/…`) so that a correction does not punch a hole in the invoice run.

**The total is written in words**, in the Indian system — lakhs and crores, not millions
— because Indian invoices conventionally carry it and a figure that has been altered by
one digit is caught by the words beside it.

The tax arithmetic itself is `services/tax.py::split_tax`, not a second copy here: an
outlet bill at 5% and a subscription invoice at 18% are the same sum on different
numbers, and two implementations of it eventually disagree about a paisa in front of
somebody who is counting.
"""
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from services.tax import split_tax

# The rate on a software subscription, and the two heads it splits into within one state.
GST_RATE = 18.0
CGST_RATE = 9.0
SGST_RATE = 9.0
IGST_RATE = 18.0

# The two answers place of supply can have. Strings rather than a bool because
# `is_interstate=False` reads as "not interstate" at the call site and the reader has to
# remember which way round that leaves the split.
INTRA_STATE = "intra"
INTER_STATE = "inter"
SUPPLY_KINDS = (INTRA_STATE, INTER_STATE)

# What each is called on the invoice and in the console.
SUPPLY_LABELS = {
    INTRA_STATE: "Within state — CGST + SGST",
    INTER_STATE: "Inter-state — IGST",
}

# The series. The invoice run and the credit-note run are numbered separately so that a
# correction never leaves a hole in the invoice sequence.
INVOICE_PREFIX = "BF"
CREDIT_NOTE_PREFIX = "BF/CN"

# The Indian financial year begins on 1 April.
FY_START_MONTH = 4

_PAISE = Decimal("0.01")

_NUMBER_SHAPE = re.compile(r"^(?P<prefix>.+)/(?P<fy>\d{4}-\d{2})/(?P<seq>\d+)$")

# States that were renamed. Two spellings of one state must not read as two states, or an
# invoice for a hotel in Orissa carries IGST because the platform's record says Odisha.
_STATE_ALIASES = {
    "orissa": "odisha",
    "pondicherry": "puducherry",
    "uttaranchal": "uttarakhand",
    "newdelhi": "delhi",
    "nctofdelhi": "delhi",
    "delhinct": "delhi",
}


class InvoiceError(Exception):
    """Raised when an invoice cannot be produced from what was given.

    A raise, never a default. Every fallback available in this module writes something
    wrong onto a document that cannot afterwards be edited: a guessed year numbers the
    invoice into the wrong series, and a guessed place of supply puts the tax under a
    head the hotel cannot claim against.
    """


def _money(value) -> Decimal:
    return Decimal(str(value))


def _paise(value: Decimal) -> float:
    return float(value.quantize(_PAISE, rounding=ROUND_HALF_UP))


# --------------------------------- the year ----------------------------------
def financial_year(day) -> str:
    """The Indian financial year a date falls in, as `2026-27`.

    1 April to 31 March. A March invoice belongs to the year that began the previous
    April, which is the whole reason this is a function rather than `day[:4]`.
    """
    try:
        d = date.fromisoformat(str(day))
    except (TypeError, ValueError):
        raise InvoiceError(
            f"not a date: {day!r} — expected YYYY-MM-DD, and an invoice cannot be "
            f"numbered into a year nobody can name") from None
    start = d.year if d.month >= FY_START_MONTH else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


# -------------------------------- the number ---------------------------------
def invoice_number(prefix: str, fy: str, sequence: int) -> str:
    """One number in a series: `BF/2026-27/0001`.

    Padded to four digits for presentation and *not* truncated to them — a platform that
    issues its ten-thousandth invoice gets `10000`, not a second `0000`.
    """
    return f"{prefix}/{fy}/{int(sequence):04d}"


def parse_invoice_number(number) -> tuple[str, str, int] | None:
    """`(prefix, financial year, sequence)`, or None for anything that is not one of ours.

    Read from the right, so a prefix containing a slash — the credit-note series does —
    comes back whole.
    """
    match = _NUMBER_SHAPE.match(str(number or ""))
    if not match:
        return None
    return match["prefix"], match["fy"], int(match["seq"])


def next_sequence(issued, prefix: str, fy: str) -> int:
    """The next number in one series, from every number already issued.

    The **highest issued plus one**, never the count. They differ the first time an
    insert fails between two that succeed, and then the count hands back a number that is
    already on a document — which is the one mistake in a tax series that cannot be
    undone, because neither document may be edited or deleted afterwards.

    Numbers from another prefix or another year are not this series and are ignored, so
    a credit note never consumes an invoice number and April never continues March.
    """
    highest = 0
    for number in issued or ():
        parsed = parse_invoice_number(
            number if isinstance(number, str) else (number or {}).get("number"))
        if not parsed:
            continue
        got_prefix, got_fy, seq = parsed
        if got_prefix == prefix and got_fy == fy:
            highest = max(highest, seq)
    return highest + 1


# ----------------------------- place of supply -------------------------------
def normalise_state(name) -> str:
    """A state name reduced to something two spellings of it agree on.

    Case, spacing and punctuation are dropped — a hotel typing its own address types it
    however it likes, and "tamil nadu" must not read as a different state from "Tamil
    Nadu" and put IGST on an invoice that should have carried CGST and SGST. Renamed
    states are mapped to one name for the same reason.

    Returns "" for anything blank, which the caller treats as "not known" rather than as
    a state that happens to match nothing.
    """
    key = re.sub(r"[^a-z0-9]", "", str(name or "").casefold())
    return _STATE_ALIASES.get(key, key)


def place_of_supply(supplier_state, customer_state, override=None) -> str:
    """`INTRA_STATE` or `INTER_STATE` — which decides the whole split.

    An explicit override wins outright, including when a state is missing: that is what
    it is for. A hotel whose address nobody has finished filling in can still be
    invoiced, deliberately, by somebody saying which it is.

    Otherwise both states have to be known. Neither default is available: guessing
    intra-state puts CGST and SGST on an invoice for another state, which the hotel
    cannot claim, and guessing inter-state does the reverse. Refusing costs the operator
    one field on a form and is the only answer that cannot be wrong quietly.
    """
    if override is not None:
        if override not in SUPPLY_KINDS:
            raise InvoiceError(
                f"unknown place of supply: {override!r} — expected one of "
                f"{', '.join(SUPPLY_KINDS)}")
        return override

    supplier, customer = normalise_state(supplier_state), normalise_state(customer_state)
    if not supplier:
        raise InvoiceError(
            "the platform's own state is not set, so the place of supply cannot be "
            "worked out — set it in the platform settings")
    if not customer:
        raise InvoiceError(
            "this hotel's state is not recorded, so the place of supply cannot be "
            "worked out — record it, or say which it is on the invoice")
    return INTRA_STATE if supplier == customer else INTER_STATE


# --------------------------------- the split ---------------------------------
def tax_split(taxable_value: float, supply: str) -> dict:
    """The 18% on a taxable value, under the heads the place of supply puts it.

    CGST and SGST are **halves of the one tax figure**, not two independent 9%
    calculations. 9% of ₹10,169.49 is ₹915.2541, which rounds to ₹915.25 twice and comes
    to ₹1,830.50 — while 18% of the same value is ₹1,830.51. A paisa, on a document that
    is supposed to reconcile.
    """
    if supply not in SUPPLY_KINDS:
        raise InvoiceError(
            f"unknown place of supply: {supply!r} — expected one of "
            f"{', '.join(SUPPLY_KINDS)}")
    total = _paise(_money(taxable_value) * _money(GST_RATE) / 100)
    if supply == INTER_STATE:
        return {"cgst": 0.0, "sgst": 0.0, "igst": total, "tax_total": total}
    cgst = _paise(_money(total) / 2)
    return {
        "cgst": cgst,
        # The remainder, so the two lines add up to the tax they are halves of whichever
        # way the halving rounded.
        "sgst": _paise(_money(total) - _money(cgst)),
        "igst": 0.0,
        "tax_total": total,
    }


def invoice_amounts(amount: float, supply: str, inclusive: bool = True) -> dict:
    """Every figure on the face of the invoice, from the money that changed hands.

    `inclusive` is the operator's own setting, and it decides what the recorded payment
    *was*. Inclusive — the default — means the hotel transferred ₹12,000 and the invoice
    totals ₹12,000 with the tax inside it, which is what reconciles against a bank
    statement. Exclusive means the agreed figure was before tax and the invoice totals
    more than the transfer, which is a real arrangement and a real reconciliation
    problem, so it is a choice rather than an assumption.
    """
    base = split_tax(amount, GST_RATE, inclusive=inclusive)
    split = tax_split(base["taxable_value"], supply)
    return {
        "taxable_value": base["taxable_value"],
        "cgst": split["cgst"],
        "sgst": split["sgst"],
        "igst": split["igst"],
        "tax_total": split["tax_total"],
        "total": _paise(_money(base["taxable_value"]) + _money(split["tax_total"])),
        "place_of_supply": supply,
        "gst_rate": GST_RATE,
    }


# ------------------------------ amount in words -------------------------------
_ONES = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty",
         "Ninety")


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, rest = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[rest]}" if rest else "")


def _under_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if not hundreds:
        return _under_hundred(rest)
    words = f"{_ONES[hundreds]} Hundred"
    return f"{words} and {_under_hundred(rest)}" if rest else words


def _indian_words(n: int) -> str:
    """A whole number in the Indian system: crore, lakh, thousand, hundred.

    Not the international one. ₹1,23,45,678 is "one crore twenty three lakh…", and a
    document that said "twelve million" would be read by nobody it is written for.
    """
    if n == 0:
        return _ONES[0]
    parts = []
    for divisor, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        count, n = divmod(n, divisor)
        if count:
            parts.append(f"{_indian_words(count) if divisor == 10_000_000 else _under_hundred(count)} {label}")
    if n:
        # "and" before a tail under a hundred, when something came before it: one
        # thousand and five, rather than one thousand five.
        joiner = " and " if parts and n < 100 else " "
        return (joiner.join([" ".join(parts), _under_thousand(n)]) if parts
                else _under_thousand(n))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    """The amount as Indian invoices conventionally write it.

    Rounded to the paise first, so the words and the figure on the same document can
    never disagree — which is the entire reason the words are there.

    A negative amount says "Minus". A credit note is a negative document and dropping the
    sign would let it read as a second invoice for the same money.
    """
    value = _money(amount or 0).quantize(_PAISE, rounding=ROUND_HALF_UP)
    sign = "Minus " if value < 0 else ""
    value = abs(value)
    rupees = int(value)
    paise = int((value - rupees) * 100)

    words = f"Rupees {sign}{_indian_words(rupees)}"
    if paise:
        words += f" and {_indian_words(paise)} Paise"
    return f"{words} Only"
