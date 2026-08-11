"""Revenue across the whole property, filtered to the domains the caller selects."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from security import require_access
from services.access import DOMAINS, OUTLET
from services.revenue import hotel_revenue

router = APIRouter()

# Analytics is a management view in every domain, so it declares all of them and lets the
# query narrow. The role list still gates it: a waiter holds `restaurant` but has no
# business reading the property's revenue. "admin" must appear here — can_access checks
# the role before it applies the admin domain bypass, so a tuple without it locks admins
# out of their own analytics.
ANALYTICS = require_access(DOMAINS, "admin", "manager")


def _held_domains(user: dict) -> list[str]:
    """Every domain this user can report on. Admin is never domain-checked, so an admin
    holds all of them whatever their stored `domains` list happens to say."""
    if user.get("role") == "admin":
        return list(DOMAINS)
    return list(user.get("domains") or ())


def _parse_domains(raw: str | None, user: dict) -> list[str]:
    held = _held_domains(user)
    if not raw:
        return sorted(held)
    picked = [d.strip() for d in raw.split(",") if d.strip()]
    if not picked:
        return sorted(held)
    for d in picked:
        if d not in DOMAINS:
            raise HTTPException(422, f"Unknown domain: {d}")
    # Answering a narrower question than the one asked is worse than refusing it — an
    # owner reading a figure labelled "hotel" must be able to trust the label.
    missing = [d for d in picked if d not in held]
    if missing:
        raise HTTPException(403, f"You do not have access to: {', '.join(sorted(missing))}")
    return sorted(set(picked))


async def _outlet_revenue(days: list[str]) -> dict:
    """Settled outlet orders in the range, by day.

    Recognised when the order settles, whatever it was paid with. A bill charged to a
    room is revenue here and a receivable on the folio — `hotel_revenue` drops the
    matching `outlet` folio entry for exactly this reason, so the two sides add cleanly.
    Voided orders leave `settled` status behind them and so drop out on their own.
    """
    orders = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(100000)
    per_day = {d: 0.0 for d in days}
    total = 0.0
    count = 0
    for o in orders:
        day = str(o.get("settled_at") or "")[:10]
        if not day or day not in per_day:
            continue
        amount = float(o.get("total") or 0)
        per_day[day] += amount
        total += amount
        count += 1
    return {"total": round(total, 2), "orders": count, "by_day": per_day}


@router.get("/analytics/revenue")
async def revenue(
    start: str = Query(...),
    end: str = Query(...),
    domains: str | None = Query(None),
    user: dict = Depends(ANALYTICS),
):
    # Who may ask is settled before what they asked is answered.
    picked = _parse_domains(domains, user)

    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD dates")
    if a > b:
        raise HTTPException(400, "start must not be after end")

    # Borrowed from hotel_revenue so both sides of the chart use one definition of the
    # range, and every day in it appears even when nothing happened on it.
    days = [d["date"] for d in hotel_revenue([], start, end)["by_day"]]

    hotel = None
    if "hotel" in picked:
        entries = await db.folio_entries.find({}, {"_id": 0}).to_list(100000)
        hotel = hotel_revenue(entries, start, end)

    # This property's bar and restaurant share one POS and one set of orders. Selecting
    # either shows outlet revenue; selecting both must not show it twice.
    outlets = None
    if any(d in picked for d in OUTLET):
        outlets = await _outlet_revenue(days)

    by_day = []
    for i, d in enumerate(days):
        h = hotel["by_day"][i]["revenue"] if hotel else 0.0
        o = outlets["by_day"][d] if outlets else 0.0
        by_day.append({"date": d, "hotel": round(h, 2), "outlets": round(o, 2),
                       "total": round(h + o, 2)})

    if outlets:
        outlets.pop("by_day")

    return {
        "start": start, "end": end, "domains": picked,
        # Stated rather than implied, so the screen can label it instead of leaving the
        # user to wonder whether ticking both outlets doubled the figure.
        "outlets_combined": sum(1 for d in OUTLET if d in picked) > 1,
        "total": round((hotel["total"] if hotel else 0.0)
                       + (outlets["total"] if outlets else 0.0), 2),
        "hotel": {k: hotel[k] for k in ("total", "room_nights", "extras")} if hotel else None,
        "outlets": outlets,
        "by_day": by_day,
    }
