"""Pydantic models for the planning calendar.

`date` and `repeat_until` are YYYY-MM-DD strings and never datetimes, for the reason
models/hotel.py gives at the top of itself: a planned day is a calendar date, and storing
it as an instant reintroduces the timezone drift that put money on the wrong day once
already. `created_at` is the opposite kind of thing — an instant, in UTC, like every
other stamp in this application.

The times are `Optional[str]` and **`None` is a value, not a gap**. An all-day event is
the common case in a hotel — "fire drill, Thursday" — so "no time" has to be a state the
model can hold rather than an empty string standing in for one. The router runs both
through `services.planner.check_times`, which turns `""` into `None` at the edge, so a
form that posts an empty string for an untimed event cannot create a row that is neither
timed nor all-day.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Kept as a Literal rather than built from `services.planner.REPEATS`, exactly as
# `HousekeepingStatus` is kept apart from `services.housekeeping.STATUSES`: a Literal
# needs its members at type-check time. tests/test_planner_api.py asserts the two agree,
# so they cannot drift in silence.
Repeat = Literal["weekly", "monthly"]


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CalendarCategoryIn(BaseModel):
    """A category the property names itself.

    There is no Literal here and there is not going to be one. The five the design
    mentioned — Training, Meeting, Guest Service, Maintenance, Event — are seeded per
    property (see `services.planner.seed_categories`) precisely so that a hotel running a
    spa can add "Spa" without a deploy: a category list hardcoded in our source is one
    the hotel cannot fix.
    """
    name: str
    colour: str
    # Retired rather than deleted, when events already point at it. A category with
    # history behind it cannot be removed without either orphaning those events or
    # rewriting them, so it is switched off: it disappears from the picker and the events
    # that carry it keep their colour.
    active: bool = True


class CalendarCategory(CalendarCategoryIn):
    id: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now)


class CalendarEventIn(BaseModel):
    """What the form sends.

    `date` is optional and **means "today at the property"** when it is absent, which is
    the one place in this feature the clock is consulted at all — see
    `routers/planner.py`. Everything else about a date in this feature is a string that
    is stored and compared as written.
    """
    title: str
    description: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    category_id: str
    repeat: Optional[Repeat] = None
    # Required when `repeat` is set, and the router is what enforces that rather than the
    # type: a repeat with no end runs forever, and "forever" is not something a month grid
    # or a manager can reason about.
    repeat_until: Optional[str] = None


class CalendarEvent(BaseModel):
    id: str = Field(default_factory=_uuid)
    title: str
    description: Optional[str] = None
    date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    category_id: str
    repeat: Optional[Repeat] = None
    repeat_until: Optional[str] = None

    # Who wrote it down. The id is what identifies them; the name is stamped beside it
    # because `users` stands outside tenancy — resolving a name at read time would mean an
    # unscoped read per event on every month load, through a handle a router is not
    # supposed to be holding. A staff member who later changes their name does not rewrite
    # what the calendar already says, which is what anybody reading "who put this here"
    # actually wants.
    created_by: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    updated_at: Optional[str] = None
