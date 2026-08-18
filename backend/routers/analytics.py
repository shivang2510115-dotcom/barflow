"""Revenue across the whole property, filtered to the domains the caller selects."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from routers.folios import post_due_nights
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import DOMAINS, OUTLET
from services.clock import local_date
from services.revenue import hotel_revenue, revenue_days

router = APIRouter()

# A backstop, not the filter. Every query below is bounded by date; this only stops a
# pathological range from paging the whole collection into memory.
MAX_ROWS = 200_000

# A UTC timestamp can be up to a day away from the local calendar date it belongs to, in
# either direction, so a query bounded by local dates has to reach one day past each end
# and let the per-row conversion decide. One day covers every real timezone (UTC-12 to
# UTC+14) with room to spare.
_TZ_SLACK = timedelta(days=1)

# Analytics is a management view in every domain, so it declares all of them and lets the
# query narrow. The role list still gates it: a waiter holds `restaurant` but has no
# business reading the property's revenue. "admin" must appear here — can_access checks
# the role before it applies the admin domain bypass, so a tuple without it locks admins
# out of their own analytics.
ANALYTICS = require_access(DOMAINS, "admin", "manager", permission="admin.analytics")


def _held_domains(user: dict) -> list[str]:
    """Every domain this user can report on. Admin is never domain-checked, so an admin
    holds all of them whatever their stored `domains` list happens to say.

    Unknown values are dropped rather than echoed. A domain this endpoint refuses on the
    query string with a 422 must not come back out of it in the response as though it
    were a real one the report covers.
    """
    if user.get("role") == "admin":
        return list(DOMAINS)
    return [d for d in (user.get("domains") or ()) if d in DOMAINS]


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


def _timestamp_bounds(start: str, end: str) -> tuple[str, str]:
    """A UTC-timestamp range that is guaranteed to contain every row whose *local* day
    falls in [start, end]. Deliberately wider than the answer — the exact filtering is
    done per row by `local_date`, which knows the property's timezone."""
    lo = (date.fromisoformat(start) - _TZ_SLACK).isoformat()
    # Exclusive upper bound: any timestamp on this date sorts after every timestamp on
    # the day before it, and ISO-8601 strings compare in chronological order.
    hi = (date.fromisoformat(end) + _TZ_SLACK + timedelta(days=1)).isoformat()
    return lo, hi


async def _post_due_nights_for_open_folios(db) -> int:
    """Bring every open folio's room nights up to date, and report how many were posted.

    Room nights are posted lazily: `post_due_nights` writes them, and nothing schedules
    it — a guest who checked in on the 14th for three nights contributes nothing to the
    ledger until somebody opens their folio. Reading `db.folio_entries` without this
    first means hotel revenue omits every stay currently in the building, and the nights
    appear *retroactively* on their own dates whenever the folio is next read, so the
    same range queried twice a day apart gives two different answers.

    Idempotent, because `post_due_nights` filters the due nights through
    `services/folio.py::unposted_nights` against what is already on the folio — a second
    call in the same second posts nothing. Only open folios are considered; a closed one
    already had its nights posted at check-out and `post_due_nights` refuses it anyway.
    """
    folios = await db.folios.find({"status": "open"}, {"_id": 0}).to_list(MAX_ROWS)
    posted = 0
    for folio in folios:
        posted += await post_due_nights(db, folio["id"])
    return posted


async def _hotel_entries(db, start: str, end: str) -> list[dict]:
    """Folio entries that could contribute to hotel revenue in [start, end].

    Bounded by date rather than read whole. An unfiltered read with a `to_list` cap
    truncates silently once the property has enough history: the report stays internally
    consistent while quietly dropping rows, and because truncation is order-dependent it
    can drop a `void` while keeping the entry it cancels, letting a cancelled room night
    back into revenue.

    Two dates matter, so the filter is an `$or`: a room night is dated by `charge_date`
    (the night it covers), everything else by `posted_at`.
    """
    lo, hi = _timestamp_bounds(start, end)
    entries = await db.folio_entries.find({"$or": [
        {"charge_date": {"$gte": start, "$lte": end}},
        {"posted_at": {"$gte": lo, "$lt": hi}},
    ]}, {"_id": 0}).to_list(MAX_ROWS)

    # A void removes the entry it points at, and it can be written long after the night
    # it cancels — a night voided at check-out, or a bill disputed a week later. Dating
    # the void out of the range would leave the cancelled entry counted, so fetch the
    # voids by what they reference instead of by when they were written.
    ids = [e["id"] for e in entries if e.get("id")]
    voids = await db.folio_entries.find(
        {"kind": "void", "ref_entry_id": {"$in": ids}}, {"_id": 0}).to_list(MAX_ROWS)

    seen = {e.get("id") for e in entries}
    return entries + [v for v in voids if v.get("id") not in seen]


async def _outlet_revenue(db, start: str, end: str, days: list[str]) -> dict:
    """Settled outlet orders in the range, by day.

    Recognised when the order settles, whatever it was paid with. A bill charged to a
    room is revenue here and a receivable on the folio — `hotel_revenue` drops the
    matching `outlet` folio entry for exactly this reason, so the two sides add cleanly.
    Voided orders leave `settled` status behind them and so drop out on their own.

    `settled_at` is a UTC timestamp, so the day it belongs to is the property's local
    day, not the one in the string. See services/clock.py.
    """
    lo, hi = _timestamp_bounds(start, end)
    orders = await db.orders.find(
        {"status": "settled", "settled_at": {"$gte": lo, "$lt": hi}},
        {"_id": 0}).to_list(MAX_ROWS)
    per_day = {d: 0.0 for d in days}
    total = 0.0
    count = 0
    for o in orders:
        day = local_date(o.get("settled_at"))
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
    db: PropertyScopedDatabase = Depends(tenant_db),
):
    # Who may ask is settled before what they asked is answered.
    picked = _parse_domains(domains, user)

    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD dates")
    if a > b:
        raise HTTPException(400, "start must not be after end")

    # Shared with hotel_revenue so both sides of the chart use one definition of the
    # range, and every day in it appears even when nothing happened on it.
    days = revenue_days(start, end)

    hotel = None
    if "hotel" in picked:
        # DELIBERATE: this read endpoint writes. Room nights only exist on the ledger
        # once `post_due_nights` has posted them, and nothing schedules that — so a
        # report that just read `folio_entries` would show ₹0 for every guest currently
        # in the building, then show their nights retroactively on those same dates the
        # moment somebody happened to open the folio. GET /folios/{id} already posts on
        # read for exactly this reason; this is the same act, for the same reason, on
        # behalf of a caller who is not going to open every folio first.
        #
        # Only when the hotel domain is actually selected: an outlets-only report has no
        # business touching the guest ledger. Safe to repeat — see the helper's docstring.
        await _post_due_nights_for_open_folios(db)
        hotel = hotel_revenue(await _hotel_entries(db, start, end), start, end)

    # This property's bar and restaurant share one POS and one set of orders. Selecting
    # either shows outlet revenue; selecting both must not show it twice.
    outlets = None
    if any(d in picked for d in OUTLET):
        outlets = await _outlet_revenue(db, start, end, days)

    # Joined on the date, not on position. Both sides come from the same `days` list
    # today, so an index would work — but if they ever stopped agreeing, the failure
    # would be money silently drawn on the wrong dates rather than an error.
    hotel_by_day = {d["date"]: d["revenue"] for d in hotel["by_day"]} if hotel else {}
    by_day = []
    for d in days:
        h = hotel_by_day.get(d, 0.0)
        o = outlets["by_day"].get(d, 0.0) if outlets else 0.0
        by_day.append({"date": d, "hotel": round(h, 2), "outlets": round(o, 2),
                       "total": round(h + o, 2)})

    return {
        "start": start, "end": end, "domains": picked,
        "total": round((hotel["total"] if hotel else 0.0)
                       + (outlets["total"] if outlets else 0.0), 2),
        "hotel": {k: hotel[k] for k in ("total", "room_nights", "extras")} if hotel else None,
        # `covers` says what the figure contains, not what the user ticked. This
        # property's restaurant and bar share one till and one set of orders, and an
        # order carries no domain, so a manager holding only `bar` still receives every
        # restaurant rupee — the screen needs to be able to label that honestly. The day
        # the tills are split this becomes ["bar"] and a label rendered from the data
        # follows on its own.
        "outlets": {"total": outlets["total"], "orders": outlets["orders"],
                    "covers": list(OUTLET)} if outlets else None,
        "by_day": by_day,
    }
