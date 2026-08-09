"""Guest folio arithmetic.

Pure functions over supplied entries and bookings — no database access, so the money
rules are testable in isolation. The ledger is append-only: nothing here mutates.
"""
from datetime import date, timedelta

# Which way each kind moves the balance. A refund hands money back to the guest, so it
# increases what they owe again — it is a debit, not a credit.
ENTRY_DIRECTION = {
    "room_night": "debit",
    "outlet": "debit",
    "misc_charge": "debit",
    "payment": "credit",
    "discount": "credit",
    "refund": "debit",
}


class FolioError(Exception):
    """Raised when an operation would corrupt the ledger."""


def direction_for(kind: str) -> str:
    """Direction a new entry of this kind takes. `void` is not valid here — a void's
    direction is the opposite of the entry it reverses, via `void_direction`."""
    try:
        return ENTRY_DIRECTION[kind]
    except KeyError:
        raise FolioError(f"unknown entry kind: {kind}")


def void_direction(original_direction: str) -> str:
    """Voiding a charge credits; voiding a payment debits."""
    if original_direction == "debit":
        return "credit"
    if original_direction == "credit":
        return "debit"
    raise FolioError(f"unknown direction: {original_direction}")


def folio_balance(entries: list[dict]) -> float:
    """What the guest owes. Positive means outstanding, negative means in credit."""
    total = 0.0
    for e in entries:
        amount = float(e.get("amount") or 0)
        if e.get("direction") == "debit":
            total += amount
        elif e.get("direction") == "credit":
            total -= amount
        else:
            raise FolioError(f"entry {e.get('id')} has no valid direction")
    return round(total, 2)


def nights_due(booking: dict, as_of: str) -> list[str]:
    """Night dates that should have posted by `as_of`.

    Half-open [check_in, check_out): the departure date is never a billable night.
    Returns nothing until the booking is checked in.
    """
    if booking.get("status") not in ("checked_in", "checked_out"):
        return []
    start = date.fromisoformat(booking["check_in"])
    end = date.fromisoformat(booking["check_out"])
    cutoff = date.fromisoformat(as_of)
    last = min(cutoff, end)
    out, cur = [], start
    while cur < last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def unposted_nights(booking: dict, as_of: str, entries: list[dict]) -> list[str]:
    """Nights due that have not already been posted. This is what makes lazy posting
    idempotent in application code; the unique index enforces it at the store."""
    posted = {
        e.get("charge_date") for e in entries
        if e.get("kind") == "room_night" and e.get("charge_date")
    }
    return [d for d in nights_due(booking, as_of) if d not in posted]
