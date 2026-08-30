"""The folio ledger, turned into the document a guest is handed at checkout.

Pure functions over plain dicts, so the arithmetic that decides what a guest owes can
be exercised without a server. The ledger stays the record; this is the *statement* of
it, and the two are deliberately different things:

* the ledger keeps a voided charge and the void that cancelled it, because an
  append-only record has to explain itself later;
* the bill shows neither, because a guest reading their bill wants to know what they
  owe, not the hotel's correction history.

Nothing here writes. `routers/bills.py` composes these into a stored document, and the
document is a snapshot — see its module docstring for why it is not a live view.
"""
import re

# Which side of the bill each kind of entry belongs on. `direction` on the entry says
# how it moves the balance; this says where it is *printed*, and the two differ for
# exactly one kind: a discount is a credit, and a guest reads it beside their payments
# rather than as a negative charge.
_CHARGES = ("room_night", "outlet", "misc_charge")
_CREDITS = ("payment", "discount")


def _amount(entry: dict) -> float:
    return round(float(entry.get("amount") or 0), 2)


def bill_lines(entries: list[dict]) -> dict:
    """Charges, credits and totals for one folio.

    A void removes the entry it points at rather than adding a negative of its own —
    the same rule `services/revenue.py` applies to reporting, for the same reason: a
    cancelled night should disappear from the bill, not appear twice with a correction.
    """
    voided = {e.get("ref_entry_id") for e in entries if e.get("kind") == "void"}
    voided.discard(None)

    charges, payments = [], []
    room_total = extras_total = paid_total = 0.0
    nights = 0

    for e in sorted(entries, key=lambda x: x.get("posted_at") or ""):
        kind = e.get("kind")
        if kind == "void" or e.get("id") in voided:
            continue
        amount = _amount(e)
        line = {
            "description": e.get("description") or "",
            "amount": amount,
            "kind": kind,
            "date": e.get("charge_date") or (e.get("posted_at") or "")[:10] or None,
        }
        if kind in _CHARGES:
            charges.append(line)
            if kind == "room_night":
                room_total += amount
                nights += 1
            else:
                extras_total += amount
        elif kind in _CREDITS:
            payments.append(line)
            paid_total += amount
        elif kind == "refund":
            # Money handed back. It undoes a payment rather than a charge, so it reduces
            # what has been paid rather than increasing what is owed on the charges side.
            payments.append(line)
            paid_total -= amount

    charges_total = round(room_total + extras_total, 2)
    paid_total = round(paid_total, 2)
    return {
        "charges": charges,
        "payments": payments,
        "room_total": round(room_total, 2),
        "extras_total": round(extras_total, 2),
        "charges_total": charges_total,
        "paid_total": paid_total,
        "balance": round(charges_total - paid_total, 2),
        "nights": nights,
    }


_NUMBER = re.compile(r"^(\d{4}-\d{2})/(\d{4,})$")


def next_number(existing: list[str], year: str) -> str:
    """The next bill number for a property, in `2026-27/0001` form.

    Gapless within a financial year, because that is what a tax document requires and a
    hotel cannot explain a missing number to an auditor by pointing at a race condition.
    The sequence restarts each year and the year is part of the string, so two years can
    never collide.

    A value this function did not issue — hand-edited, imported, blank — is ignored
    rather than allowed to derail the count.
    """
    highest = 0
    for value in existing:
        m = _NUMBER.match(value or "")
        if m and m.group(1) == year:
            highest = max(highest, int(m.group(2)))
    return f"{year}/{highest + 1:04d}"


def financial_year(day: str) -> str:
    """The Indian financial year a date falls in: April to March.

    `2026-08-30` is in `2026-27`; `2026-02-14` is in `2025-26`. Taken from the
    property's local day, never from a UTC timestamp — see services/clock.py.
    """
    y, m = int(day[:4]), int(day[5:7])
    start = y if m >= 4 else y - 1
    return f"{start}-{str(start + 1)[2:]}"
