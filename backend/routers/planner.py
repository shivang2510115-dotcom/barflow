"""The planning calendar: what the people running the property have decided to do.

A staff briefing on Tuesday, a fire drill next month, a maintenance window, a wedding in
the banquet hall, a supplier visit. Not bookings, not housekeeping jobs, not orders — all
three exist already and none of them is this. Nothing here is derived from anything; it is
written down by a person and read by everybody.

**The rules are not in this file.** Which day a repeat lands on, what "no time" means, and
whether a colour is a colour all live in `services/planner.py`, as pure functions with no
database under them. This router reads, writes and refuses. Anything in here that looks
like a decision about dates is a bug — it belongs one file over, where it can be tested
without a server.

**Recurrence: what is supported and what is not.** A weekly or a monthly repeat, with an
end date, expanded at read time from the single stored row. That is the whole of it.
There are no per-occurrence exceptions: you cannot move next Monday's briefing to Tuesday
and leave the rest of the series alone, and you cannot delete one occurrence — editing or
deleting a recurring event edits or deletes the series. Exceptions are where recurrence
becomes expensive (a second collection of overrides, a merge on every read, and a repeat
whose end date now has to be reconciled against them), and the honest version of "we did
not build that" is a screen that never offers it. The screen says "every week" beside a
recurring event and its editor changes the series; it does not offer an option that
silently does something else.

**Dates.** An event's `date` is a local calendar date and is stored exactly as written —
`services/clock.py` says the same about `charge_date`, and for the same reason: it is
already the property's own day, so converting it would move it. The clock is consulted at
exactly two edges, both of them where a *day* has to be named and nobody named it:
creating an event without a date, and telling the screen which day to draw as today. Both
go through `services.clock.today()`, which is the property's local day. A UTC `today` is
yesterday's date for the first five and a half hours of every Indian morning, so a
manager writing "fire drill" into the planner at 00:30 would find it on the 5th when
everyone in the building would call it the 6th.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from models.planner import (
    CalendarCategory, CalendarCategoryIn, CalendarEvent, CalendarEventIn)
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, resolve_property
from services.access import DOMAINS, can_access
from services.clock import today
from services.planner import (
    PlannerError, REPEATS, check_times, clean_colour, clean_name, expand, parse_date)

router = APIRouter()

SCREEN = "property.planner"

# Who may plan. "admin" is in the tuple like every other role tuple in this application:
# the role check in `can_access` runs before the admin bypass, so an admin left out of the
# list is an admin refused. `DOMAINS` and not a single domain — a fire drill is the
# property's, not the hotel's — which is `admin.analytics`' declaration exactly, and it
# means an outlet property with no rooms still has a planner.
EDIT_ROLES = ("admin", "manager")
PLAN = require_access(DOMAINS, *EDIT_ROLES, permission=SCREEN)

# Who may read it: anybody signed in who works anywhere in this property.
#
# **No role list and no screen key, and both omissions are the design.** A waiter has to
# be able to see that Tuesday's briefing is at 4pm, and a kitchen hand that the fire drill
# is on Thursday. A role list would leave somebody out by construction, and a screen key
# would be worse — `ROLE_SCREENS` is frozen, so a key added today reaches no existing
# waiter and no future one either, and the calendar would be invisible to exactly the
# people it is posted for. The precedent is `housekeeping.py::ALERT`, which declares the
# domain alone for the same reason.
#
# An empty role list means no role check at all: `can_access` skips it when `roles` is
# falsy, so the admin bypass is not needed here and the domain is doing the work. Nothing
# is disclosed by it that is not already pinned to the staff-room wall.
READ = require_access(DOMAINS)

# The longest window the calendar will draw at once. Month, week and day are all well
# inside it; the number exists so that a hand-typed range cannot ask a weekly repeat to be
# expanded across a decade to answer one request.
MAX_WINDOW_DAYS = 400

# A backstop, not the filter. Every query below is bounded by property and mostly by date;
# this only stops a pathological property from paging its whole history into memory.
MAX_ROWS = 20_000


# ------------------------------- shared plumbing -------------------------------
def _window(start: str | None, end: str | None) -> tuple[str, str]:
    """The range being drawn, validated. Defaults to the property's current month.

    Defaulted rather than required so that `GET /api/planner/events` with no arguments
    answers the useful question — month view is the default on the screen — and the
    default is computed from the property's own today, not the server's.
    """
    if not start and not end:
        this_day = date.fromisoformat(today())
        first = this_day.replace(day=1)
        # The last day of this month, reached by stepping into the next one and back.
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first.isoformat(), (nxt - timedelta(days=1)).isoformat()
    try:
        lo = parse_date(start, "start")
        hi = parse_date(end, "end")
    except PlannerError as exc:
        raise HTTPException(400, str(exc)) from None
    if hi < lo:
        raise HTTPException(400, "start must not be after end")
    if (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1 > MAX_WINDOW_DAYS:
        raise HTTPException(400, f"A window may cover at most {MAX_WINDOW_DAYS} days")
    return lo, hi


async def _may_plan(user: dict) -> bool:
    """Whether this caller may write to the calendar.

    Sent to the screen so it can hide a "New event" button the API would refuse. Asked of
    `can_access` with the same arguments `PLAN` declares, rather than re-derived from the
    role and the permissions list here — a second copy of the rule is how a button appears
    for somebody who then gets a 403 when they press it.
    """
    return can_access(user, DOMAINS, EDIT_ROLES, await resolve_property(user),
                      permission=SCREEN)


async def _categories(db: PropertyScopedDatabase) -> list[dict]:
    rows = await db.calendar_categories.find({}, {"_id": 0}).to_list(MAX_ROWS)
    rows.sort(key=lambda c: (c.get("name") or "").lower())
    return rows


async def _category_or_400(db: PropertyScopedDatabase, category_id: str) -> dict:
    """The category this event is filed under, or a refusal naming why.

    A category from another property is simply not found by the scoped handle, so it lands
    here as "no such category" rather than as a leak.
    """
    row = await db.calendar_categories.find_one({"id": category_id}, {"_id": 0})
    if not row:
        raise HTTPException(400, "That category does not exist")
    if not row.get("active", True):
        # Retired categories keep colouring the events that already carry them, but
        # nothing new is filed under one — otherwise switching a category off would mean
        # nothing at all.
        raise HTTPException(400, f"{row['name']} has been retired — pick another category")
    return row


def _event_fields(payload: CalendarEventIn) -> dict:
    """Everything the body decides, validated together. Raises HTTPException(400).

    One function for create and update, so an event cannot be edited into a shape it could
    not have been created in — which is the usual way a validated field stops being
    validated.
    """
    try:
        title = clean_name(payload.title, "title")
        # The one place the clock is consulted on a write: a body with no date means
        # "today", and today is the property's day. See the module docstring.
        day = parse_date(payload.date) if payload.date else today()
        start_time, end_time = check_times(payload.start_time, payload.end_time)
    except PlannerError as exc:
        raise HTTPException(400, str(exc)) from None

    repeat = payload.repeat
    until = payload.repeat_until
    if repeat and repeat not in REPEATS:
        # Unreachable through the typed body, which is a 422 — kept because
        # `services.planner` treats an unknown repeat as a single event, and a row that
        # silently stopped repeating is worse than a refusal.
        raise HTTPException(400, f"{repeat} is not a repeat this calendar supports")
    if until and not repeat:
        raise HTTPException(400, "An end date needs a repeat — remove one or set the other")
    if repeat:
        if not until:
            raise HTTPException(400, "A repeating event needs a date to repeat until")
        try:
            until = parse_date(until, "repeat_until")
        except PlannerError as exc:
            raise HTTPException(400, str(exc)) from None
        if until < day:
            raise HTTPException(400, "The repeat must end on or after the event's own date")

    return {
        "title": title,
        "description": (payload.description or "").strip() or None,
        "date": day,
        "start_time": start_time,
        "end_time": end_time,
        "category_id": payload.category_id,
        "repeat": repeat,
        "repeat_until": until if repeat else None,
    }


async def _event_or_404(db: PropertyScopedDatabase, event_id: str) -> dict:
    event = await db.calendar_events.find_one({"id": event_id}, {"_id": 0})
    if not event:
        # 404 and not 403 for another property's event: the scoped handle simply does not
        # find it, and a 403 would confirm that it exists.
        raise HTTPException(404, "Event not found")
    return event


# ---------------------------------- reading ----------------------------------
@router.get("/planner/events")
async def list_events(start: str | None = Query(None), end: str | None = Query(None),
                      user: dict = Depends(READ),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    """Everything planned in [start, end], one row per day an event falls on.

    Two queries, and the second one is why. A non-repeating event is found by its own date
    and the range filter does all the work. A repeating one is *not*: "every Monday since
    March" has a stored date in March and has to appear in August, so no filter on `date`
    could find it. Repeating rows are read whole — there are a handful of them per property
    — and expanded in Python by `services.planner.expand`.

    The categories ride along rather than being fetched separately. The grid colours every
    cell from them, and two calls to draw one month is two chances for them to disagree
    about a category that was renamed between them.
    """
    lo, hi = _window(start, end)

    dated = await db.calendar_events.find(
        {"date": {"$gte": lo, "$lte": hi}}, {"_id": 0}).to_list(MAX_ROWS)
    repeating = await db.calendar_events.find(
        {"repeat": {"$in": list(REPEATS)}}, {"_id": 0}).to_list(MAX_ROWS)

    # A repeating event whose own date falls inside the window is found by both queries.
    # De-duplicated on the id before expansion, or it would be drawn twice on its first day.
    seen = {e["id"] for e in repeating}
    events = repeating + [e for e in dated if e["id"] not in seen]

    return {
        "start": lo, "end": hi,
        # The property's own today, so the grid highlights the day the people in the
        # building would call today rather than the day it is in Greenwich.
        "today": today(),
        "can_edit": await _may_plan(user),
        "categories": await _categories(db),
        "events": expand(events, lo, hi),
    }


@router.get("/planner/categories")
async def list_categories(user: dict = Depends(READ),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """The property's own vocabulary, active and retired alike.

    Retired ones are sent too: an event filed under a category that was switched off last
    month still has to be drawn in its colour, and a list that omitted it would leave the
    screen colouring old events grey.
    """
    return {"categories": await _categories(db), "can_edit": await _may_plan(user)}


# ---------------------------------- writing ----------------------------------
@router.post("/planner/events")
async def create_event(payload: CalendarEventIn, user: dict = Depends(PLAN),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    fields = _event_fields(payload)
    await _category_or_400(db, fields["category_id"])
    event = CalendarEvent(
        **fields, created_by=user.get("id"), created_by_name=user.get("name")).model_dump()
    await db.calendar_events.insert_one(event)
    event.pop("_id", None)
    return event


@router.put("/planner/events/{event_id}")
async def update_event(event_id: str, payload: CalendarEventIn,
                       user: dict = Depends(PLAN),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    """Change an event. For a recurring one this changes the whole series — see the
    module docstring on what recurrence deliberately does not support.

    `created_by` is never rewritten: who wrote a thing down is a fact about the past, and
    an edit by somebody else does not change it. `updated_at` is what records that an edit
    happened at all.
    """
    await _event_or_404(db, event_id)
    fields = _event_fields(payload)
    await _category_or_400(db, fields["category_id"])
    await db.calendar_events.update_one(
        {"id": event_id},
        {"$set": {**fields, "updated_at": datetime.now(timezone.utc).isoformat()}})
    return await db.calendar_events.find_one({"id": event_id}, {"_id": 0})


@router.delete("/planner/events/{event_id}")
async def delete_event(event_id: str, user: dict = Depends(PLAN),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    """Remove an event, and for a recurring one the whole series.

    Deleted rather than archived, unlike a category: an event carries no history that
    anything else points at, and a planner full of cancelled fire drills is a planner
    nobody reads.
    """
    await _event_or_404(db, event_id)
    await db.calendar_events.delete_one({"id": event_id})
    return {"deleted": event_id}


@router.post("/planner/categories")
async def create_category(payload: CalendarCategoryIn, user: dict = Depends(PLAN),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """A category this property names itself — the point of the whole collection."""
    try:
        name = clean_name(payload.name)
        colour = clean_colour(payload.colour)
    except PlannerError as exc:
        raise HTTPException(400, str(exc)) from None

    existing = await _categories(db)
    if any((c.get("name") or "").lower() == name.lower() for c in existing):
        # Two categories called "Meeting" is two colours for one idea and a picker nobody
        # can use. 409 rather than silently reusing the first: the caller asked for
        # something that cannot be granted, and which of the two they meant is theirs to say.
        raise HTTPException(409, f"There is already a category called {name}")

    row = CalendarCategory(name=name, colour=colour, active=payload.active).model_dump()
    await db.calendar_categories.insert_one(row)
    row.pop("_id", None)
    return row


@router.put("/planner/categories/{category_id}")
async def update_category(category_id: str, payload: CalendarCategoryIn,
                          user: dict = Depends(PLAN),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    row = await db.calendar_categories.find_one({"id": category_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Category not found")
    try:
        name = clean_name(payload.name)
        colour = clean_colour(payload.colour)
    except PlannerError as exc:
        raise HTTPException(400, str(exc)) from None

    clash = [c for c in await _categories(db)
             if c["id"] != category_id and (c.get("name") or "").lower() == name.lower()]
    if clash:
        raise HTTPException(409, f"There is already a category called {name}")

    await db.calendar_categories.update_one(
        {"id": category_id},
        {"$set": {"name": name, "colour": colour, "active": payload.active}})
    return await db.calendar_categories.find_one({"id": category_id}, {"_id": 0})


@router.delete("/planner/categories/{category_id}")
async def delete_category(category_id: str, user: dict = Depends(PLAN),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """Remove a category nothing is filed under.

    One that events already carry is refused with a 409 that says how many, rather than
    deleted — which would leave those events pointing at nothing and drawn in no colour —
    and rather than cascading, which would delete a year of the property's planning to
    tidy up a word. The alternative is on the same screen: switch it off, and it leaves
    the picker while the events that carry it keep their colour.
    """
    row = await db.calendar_categories.find_one({"id": category_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Category not found")
    in_use = await db.calendar_events.count_documents({"category_id": category_id})
    if in_use:
        raise HTTPException(409, {
            "message": f"{row['name']} is used by {in_use} "
                       f"{'event' if in_use == 1 else 'events'}. Switch it off instead to "
                       f"take it out of the picker and leave them their colour.",
            "events": in_use,
        })
    await db.calendar_categories.delete_one({"id": category_id})
    return {"deleted": category_id}
