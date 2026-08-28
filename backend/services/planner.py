"""The planning calendar: what a property puts in its own week.

A staff briefing on Tuesday, a fire drill next month, a maintenance window, a wedding in
the banquet hall, a supplier visit. None of these are bookings, housekeeping jobs or
orders — the system models all three already — and none of them are derived from
anything. They are what the people running the property write down.

Pure functions over plain dicts: no database, no request, no router. Every rule that
decides *which day something falls on* is in here, where it can be tested without a
server, for the same reason `services/access.py` holds the authorization rules and
`services/revenue.py` the money ones.

**A date here is a local calendar date and is never converted.** `models/hotel.py` says
it at the top of the file and `services/clock.py` says it about `charge_date`: a date is
already the property's own day, so running it through a timezone conversion moves it. The
one place the clock *is* consulted is at the edge — when a caller creates an event
without naming a day, "today" has to mean today at the hotel, not today in Greenwich. See
`routers/planner.py`.
"""
import uuid
from datetime import date, timedelta

# --------------------------------- recurrence ---------------------------------
# Two repeats and no more. "Staff briefing every Monday" and "stocktake on the 1st" are
# the two things a property actually asks for, and every step past them — "the second
# Tuesday", "every weekday", "every third week" — is a rule the screen then has to be
# able to say out loud and the editor has to be able to change. What is here is a repeat
# with an end date; what is deliberately absent is documented in the router.
WEEKLY = "weekly"
MONTHLY = "monthly"
REPEATS = (WEEKLY, MONTHLY)

# How far a repeat may be asked to run without an end date. A recurring event *must*
# carry `repeat_until` (the router refuses one that does not), so this is a backstop
# against a stored row that predates that rule or was hand-edited, not the rule itself:
# expansion is bounded by the window being drawn anyway, and this only stops a pathological
# row from being walked a million times to fill one month's grid.
MAX_OCCURRENCES = 1000


class PlannerError(ValueError):
    """Raised when a value that describes an event is not one this module can mean."""


def _as_date(value, what: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PlannerError(f"{what} must be a YYYY-MM-DD date") from None


def parse_date(value, what: str = "date") -> str:
    """`value` as a canonical YYYY-MM-DD string, or a PlannerError.

    Round-tripped through `date` rather than regex-checked, so "2026-02-30" is refused
    here rather than stored and then sorted into a month that has no such day.
    """
    return _as_date(value, what).isoformat()


# ------------------------------------ times ------------------------------------
# An all-day event is the common case in a hotel — "fire drill, Thursday" — so "no time"
# is a value this module has a name for rather than an empty string standing in for one.
# `None` is the whole representation: `all_day` is derived from it (see `is_all_day`)
# instead of being a second field that can disagree with it.
def clean_time(value) -> str | None:
    """A HH:MM time, or None for "no time was given".

    An empty string, whitespace, and a missing key all mean the same thing and all become
    `None`. That is the entire point: a form that posts `start_time: ""` for an untimed
    event must not create a row that is neither timed nor all-day.

    Seconds are accepted and dropped — a browser's `<input type="time">` sends `HH:MM:SS`
    on some platforms and `HH:MM` on others, and two rows for one o'clock that do not
    compare equal is a bug waiting for the day somebody sorts by this field.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise PlannerError(f"{text!r} is not a time — use HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise PlannerError(f"{text!r} is not a time — use HH:MM")
    return f"{hour:02d}:{minute:02d}"


def is_all_day(event: dict) -> bool:
    """Whether this event has no time of day. Derived, never stored.

    One source of truth for "is this all-day": the absence of a start time. A stored
    boolean beside the times is a second opinion, and it is wrong the first time somebody
    edits an event to add a start time and the flag stays true.
    """
    return not event.get("start_time")


def check_times(start_time: str | None, end_time: str | None) -> tuple[str | None, str | None]:
    """The pair, cleaned, or a PlannerError explaining which half is wrong.

    An end time with no start is refused rather than kept: "ends at 5pm" says nothing
    about when to put the event on the day, so it would render exactly as an all-day
    event while claiming to be timed. An end at or before the start is refused too — an
    event that finishes before it begins is a typo every time, and the alternative is a
    block with a negative height on the week view.

    Same-day only, deliberately: a maintenance window running past midnight is written as
    two events, because one that ends "at 02:00" on a row dated the 5th is a thing the
    month grid cannot draw honestly and the 6th does not know about.
    """
    start = clean_time(start_time)
    end = clean_time(end_time)
    if end and not start:
        raise PlannerError("An end time needs a start time — leave both empty for an "
                           "all-day event")
    if start and end and end <= start:
        raise PlannerError("The end time must be after the start time")
    return start, end


# ---------------------------------- occurrences ----------------------------------
def _add_months(day: date, months: int) -> date | None:
    """`day` moved on by whole months, or None when that month has no such date.

    The 31st of a 30-day month is **skipped**, not moved to the 30th or rolled into the
    1st. A stocktake on the 31st means the 31st; a hotel that wants the last day of every
    month has months of differing lengths to contend with either way, and silently moving
    the date is how a fire drill ends up announced for a day nobody agreed to. February
    keeps the same rule, leap year or not.
    """
    total = (day.year * 12 + day.month - 1) + months
    year, month = divmod(total, 12)
    try:
        return date(year, month + 1, day.day)
    except ValueError:
        return None


def occurrences(event: dict, start, end) -> list[str]:
    """Every date in [start, end] this event falls on, in order, as YYYY-MM-DD.

    An event that does not repeat contributes at most its own date. A repeating one
    contributes every step from its date up to and including `repeat_until`, clipped to
    the window — so a briefing that started in March and runs to December appears in
    August's grid without August having to know March existed.

    Expansion happens at read time and nothing is written per occurrence. That is the
    decision that keeps "every Monday" editable: changing the time changes one row, and
    there is no set of materialised copies to find and keep in step. What it costs is
    that a single occurrence cannot be moved or deleted on its own — see the router,
    which says so out loud rather than half-supporting it.
    """
    first = _as_date(event.get("date"), "date")
    window_start = _as_date(start, "start")
    window_end = _as_date(end, "end")
    if window_end < window_start:
        return []

    repeat = event.get("repeat")
    if repeat not in REPEATS:
        # Anything that is not a repeat this module knows is a single event on its own
        # day — including a value a later version of this file might add and this one has
        # never heard of. Guessing at it would put an event on days nobody chose.
        return [first.isoformat()] if window_start <= first <= window_end else []

    until = event.get("repeat_until")
    last = _as_date(until, "repeat_until") if until else window_end
    # Never past the window: an event repeating until 2031 is not walked to 2031 to draw
    # one month.
    last = min(last, window_end)

    out: list[str] = []
    # Each occurrence is computed from the **seed**, never accumulated from the one
    # before it. Adding a month twice to the 31st of January by stepping would land on
    # the 28th of February and stay on the 28th for the rest of the year; counted from
    # the seed, February is skipped and March is the 31st again.
    for step in range(MAX_OCCURRENCES):
        if repeat == WEEKLY:
            day: date | None = first + timedelta(days=7 * step)
        else:
            day = _add_months(first, step)
        if day is None:
            # Monthly, on a day this month does not have. The series continues past it —
            # a 31st that skips February is still a 31st in March.
            continue
        if day > last:
            break
        if day >= window_start:
            out.append(day.isoformat())
    return out


def expand(events: list[dict], start, end) -> list[dict]:
    """Every event, on every day it falls on inside the window, ready to draw.

    One row per occurrence, carrying the event it came from — so the month grid groups by
    `date` and nothing else, and a repeating event needs no special case on the screen.

    `all_day` and `occurrence_date` are added here and are not stored: the first is
    derived from the absence of a start time, the second is the whole product of this
    function. `id` stays the event's own id, because that is what an edit or a delete has
    to name; `occurrence_id` is what a list key needs and is unique per drawn cell.

    Sorted by day, then all-day first, then by start time, then by title. All-day before
    timed because "fire drill, Thursday" is a fact about the whole day and belongs at the
    top of it; the title breaks the remaining tie so that two 09:00 events come out in the
    same order on every render.
    """
    out: list[dict] = []
    for event in events:
        for day in occurrences(event, start, end):
            row = dict(event)
            row["occurrence_date"] = day
            row["occurrence_id"] = f"{event.get('id')}:{day}"
            row["all_day"] = is_all_day(event)
            row["recurring"] = event.get("repeat") in REPEATS
            out.append(row)
    out.sort(key=lambda r: (r["occurrence_date"], 0 if r["all_day"] else 1,
                            r.get("start_time") or "", (r.get("title") or "").lower()))
    return out


# ---------------------------------- categories ----------------------------------
# The five the design named, and the reason they are seeded per property rather than
# listed in a Literal: a category list hardcoded in our source is one the hotel cannot
# fix. A property that runs a spa adds "Spa"; one that never trains anybody deactivates
# "Training". Neither needs a deploy.
#
# The colours are what makes the month grid readable at a glance, so they are chosen to be
# distinguishable rather than pretty: orange is the app's accent and goes to the category
# a manager looks for first. Stored on the row, so a property can change them too.
DEFAULT_CATEGORIES = (
    ("Training", "#f97316"),
    ("Meeting", "#38bdf8"),
    ("Guest service", "#a78bfa"),
    ("Maintenance", "#facc15"),
    ("Event", "#34d399"),
)

# `#rgb` and `#rrggbb`. Checked rather than trusted because the value is written straight
# into a style attribute on the month grid, and a category whose colour is
# `red; background: url(...)` is a stored cross-site scripting hole with a colour picker
# in front of it.
_HEX = "0123456789abcdefABCDEF"


def clean_colour(value) -> str:
    """A `#rrggbb` colour, or a PlannerError."""
    text = str(value or "").strip()
    if (len(text) not in (4, 7) or not text.startswith("#")
            or any(c not in _HEX for c in text[1:])):
        raise PlannerError(f"{text!r} is not a colour — use #rrggbb, e.g. #f97316")
    if len(text) == 4:
        text = "#" + "".join(c * 2 for c in text[1:])
    return text.lower()


def clean_name(value, what: str = "name") -> str:
    text = str(value or "").strip()
    if not text:
        raise PlannerError(f"A {what} is required")
    return text


def default_categories() -> list[dict]:
    """The rows a new property starts with. New ids each call — these are stored per
    property, not shared, which is the whole point of seeding them."""
    return [{"id": str(uuid.uuid4()), "name": name, "colour": colour, "active": True}
            for name, colour in DEFAULT_CATEGORIES]


async def seed_categories(db) -> int:
    """Give one property the default categories, if it has none. Returns how many.

    `db` is a property-scoped handle, so the writes are stamped and the count is that
    property's own. Idempotent by counting first — exactly `services/reference_data.py`,
    and for the same reason: a property that has renamed "Event" to "Banquet" must not
    have "Event" put back on top of it by a restart.
    """
    if await db.calendar_categories.count_documents({}) > 0:
        return 0
    rows = default_categories()
    await db.calendar_categories.insert_many(rows)
    return len(rows)
