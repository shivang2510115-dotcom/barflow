"""Which folio money is hotel revenue, and which day it belongs to.

Pure functions over supplied entries — no database — so the double-counting rule is
testable in isolation and stated in exactly one place.

The rule that matters: an `outlet` folio entry is NOT hotel revenue. A bar bill charged
to a room was already recognised as revenue at the outlet when the order settled; the
folio entry is the receivable that mirrors it. Counting both is how a combined
hotel-plus-restaurant total silently reports the same rupee twice.
"""
from datetime import date, timedelta

from models.folio import ENTRY_KINDS
from services.clock import local_date

# Signed contribution of each entry kind to hotel revenue. Anything absent contributes
# nothing: payments and refunds move cash without earning it, and `outlet` belongs to
# the outlet that sold it.
REVENUE_SIGN = {
    "room_night": 1,
    "misc_charge": 1,
    "discount": -1,
}

# A kind that exists on the ledger but is missing from REVENUE_SIGN is silently worth
# zero, which is sometimes right (a payment) and sometimes a bug. A kind here that the
# ledger cannot produce is always a bug — a typo, or a kind renamed in models/folio.py
# without anyone revisiting the money rule. Fail at import rather than at month end.
_unknown_kinds = sorted(set(REVENUE_SIGN) - set(ENTRY_KINDS))
if _unknown_kinds:
    raise ValueError(
        f"REVENUE_SIGN names entry kinds that models.folio does not define: "
        f"{', '.join(_unknown_kinds)}"
    )


def entry_revenue_date(entry: dict) -> str | None:
    """The day this entry's money belongs to, as YYYY-MM-DD, or None if unknowable.

    A room night is earned on the night it covers, not the day it was posted — three
    nights posted together at check-out must still land on three separate days.

    `charge_date` is already a local calendar date and is used as-is. `posted_at` is a
    UTC timestamp and must be converted to the property's day first, or an entry posted
    late at night is reported on the previous day. See services/clock.py.
    """
    charge_date = entry.get("charge_date")
    if charge_date:
        return str(charge_date)[:10]
    posted_at = entry.get("posted_at")
    if posted_at:
        return local_date(posted_at)
    return None


def _days(start: str, end: str) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError(f"start {start} is after end {end}")
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def revenue_days(start: str, end: str) -> list[str]:
    """Every calendar day in an inclusive [start, end] report range.

    Exported so a caller wanting only the day list — the analytics endpoint building
    its outlet buckets — does not have to run a whole empty revenue calculation to get
    at it, re-doing validation it has already done.
    """
    return _days(start, end)


def hotel_revenue(entries: list[dict], start: str, end: str) -> dict:
    """Hotel revenue over an inclusive [start, end] range of calendar days.

    Unlike a stay, which is half-open because the departure night is not slept in, a
    report range names the days the user picked — so both ends are included.
    """
    days = _days(start, end)
    in_range = set(days)

    # A void removes the entry it points at rather than adding a negative of its own.
    # Both give the same total, but this way a cancelled night disappears from the night
    # it covered instead of leaving a stray credit on the day someone noticed.
    voided = {e.get("ref_entry_id") for e in entries if e.get("kind") == "void"}
    voided.discard(None)

    by_day = {d: 0.0 for d in days}
    room_nights = 0.0
    extras = 0.0
    # How many nights were sold, counted in the same pass over the same entries that
    # sums their value. A second pass with its own filter is how the numerator and the
    # denominator of ADR end up disagreeing about which nights count — the void rule in
    # particular has to apply identically to both.
    nights_sold = 0

    for e in entries:
        sign = REVENUE_SIGN.get(e.get("kind"))
        if sign is None or e.get("id") in voided:
            continue
        day = entry_revenue_date(e)
        if day is None or day not in in_range:
            continue
        amount = float(e.get("amount") or 0) * sign
        by_day[day] += amount
        if e.get("kind") == "room_night":
            room_nights += amount
            nights_sold += 1
        else:
            extras += amount

    return {
        "total": round(room_nights + extras, 2),
        "room_nights": round(room_nights, 2),
        # The count, beside the value. `room_nights` is money; `nights_sold` is how many.
        "nights_sold": nights_sold,
        "extras": round(extras, 2),
        "by_day": [{"date": d, "revenue": round(by_day[d], 2)} for d in days],
    }
