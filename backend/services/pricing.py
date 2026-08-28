"""Room pricing: rate resolution, meal plans, and per-night GST.

Pure functions — every input is passed in, nothing is read from a database. All dates are
YYYY-MM-DD strings and every range is half-open [from, to).
"""
from datetime import date, timedelta
from typing import Optional

# Whether this property splits its price into meal plans (EP / CP / MAP) or sells one
# all-inclusive room rate and bills anything extra to the folio as it is consumed.
#
# False is the default because it is the simpler model and what most small Indian hotels
# actually run: one price for the room, a misc charge for the breakfast somebody took.
# A resort selling a breakfast-inclusive package switches it on and gets exactly the
# behaviour this app has always had. Neither is a global rule — several hotels share one
# deployment, so this is a per-property setting rather than a decision made here.
#
# The startup migration deliberately does NOT stamp this default onto records that
# predate the field; see migrations/backfill_meal_plans.py for why.
DEFAULT_MEAL_PLANS_ENABLED = False


class MissingRateError(Exception):
    """No rate covers one or more nights. Never price these as zero."""

    def __init__(self, dates: list[str]):
        self.dates = dates
        super().__init__(f"No rate defined for: {', '.join(dates)}")


def meal_plans_enabled(property_record: Optional[dict]) -> bool:
    """Does this property quote per meal plan?

    An absent key reads as the default rather than raising: a record the migration has
    not reached yet, or a caller with no property at all, must still get an answer, and
    the answer that changes nothing about the money is the room-only one.
    """
    if not property_record:
        return DEFAULT_MEAL_PLANS_ENABLED
    value = property_record.get("meal_plans_enabled")
    return DEFAULT_MEAL_PLANS_ENABLED if value is None else bool(value)


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def daterange(check_in: str, check_out: str) -> list[str]:
    """Night dates billed for a stay. Half-open: check_out is not billed."""
    start, end = _parse(check_in), _parse(check_out)
    if end <= start:
        raise ValueError("check_out must be after check_in")
    out, cur = [], start
    while cur < end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _period_for(day: str, periods: list[dict]) -> Optional[dict]:
    """Highest-priority active period covering `day`. Ties break on most recently created
    (missing `created_at` sorts as if it were the empty string, i.e. oldest); any remaining
    tie breaks on `id` (ascending). The `id` tie-break is total, so the result never depends
    on the order periods were passed in."""
    matches = [
        p for p in periods
        if p.get("active", True) and p["start_date"] <= day < p["end_date"]
    ]
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda p: (p.get("priority", 0), p.get("created_at", ""), p["id"]),
    )[-1]


def resolve_rate(day: str, room_type_id: str, rates: list[dict], periods: list[dict]) -> dict:
    """Rate row for one night. Falls back to the default row (period_id None)."""
    period = _period_for(day, periods)
    if period:
        for r in rates:
            if r["room_type_id"] == room_type_id and r.get("period_id") == period["id"]:
                return r
    for r in rates:
        if r["room_type_id"] == room_type_id and r.get("period_id") is None:
            return r
    raise MissingRateError([day])


def gst_for(tariff: float, slabs: list[dict]) -> float:
    """GST percentage for a nightly tariff. Slab upper bounds are inclusive: a ₹7,500
    tariff sits in the 12% band, ₹7,500.01 in the 18% band."""
    for s in sorted(
        (s for s in slabs if s.get("active", True)),
        key=lambda s: s["min_tariff"],
    ):
        upper = s.get("max_tariff")
        if tariff >= s["min_tariff"] and (upper is None or tariff <= upper):
            return float(s["rate_percent"])
    raise ValueError(f"No GST slab covers tariff {tariff}")


def quote_stay(
    check_in: str,
    check_out: str,
    room_type_id: str,
    adults: int,
    children: int,
    base_occupancy: int,
    meal_plan: Optional[dict],
    rates: list[dict],
    periods: list[dict],
    slabs: list[dict],
) -> dict:
    """Priced breakdown for a stay. Raises MissingRateError listing every uncovered night.

    `meal_plan` is `None` for a property selling one all-inclusive rate. That adds
    nothing per head — the room rate already contains it, and anything extra is billed to
    the folio as it is consumed — but it does **not** make the stay free: the room rate
    still has to exist, and a night no rate covers raises `MissingRateError` here exactly
    as it does on the plan path. Plan-less and unpriceable are different answers and only
    one of them is a booking.
    """
    plan = meal_plan or {}
    nights = daterange(check_in, check_out)

    uncovered = []
    for day in nights:
        try:
            resolve_rate(day, room_type_id, rates, periods)
        except MissingRateError:
            uncovered.append(day)
    if uncovered:
        raise MissingRateError(uncovered)

    extra_adults = max(0, adults - base_occupancy)
    lines, room_subtotal, tax_total = [], 0.0, 0.0

    for day in nights:
        rate = resolve_rate(day, room_type_id, rates, periods)
        tariff = (
            float(rate["base_rate"])
            + float(rate.get("extra_adult_rate", 0)) * extra_adults
            + float(rate.get("extra_child_rate", 0)) * children
            + float(plan.get("price_per_adult_per_night", 0)) * adults
            + float(plan.get("price_per_child_per_night", 0)) * children
        )
        tariff = round(tariff, 2)
        percent = gst_for(tariff, slabs)
        tax = round(tariff * percent / 100, 2)

        lines.append({
            "date": day,
            "tariff": tariff,
            "gst_percent": percent,
            "gst_amount": tax,
        })
        room_subtotal += tariff
        tax_total += tax

    room_subtotal = round(room_subtotal, 2)
    tax_total = round(tax_total, 2)
    return {
        "nights": lines,
        "room_subtotal": room_subtotal,
        "tax_total": tax_total,
        "total": round(room_subtotal + tax_total, 2),
    }
