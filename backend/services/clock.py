"""The property's calendar day.

Timestamps are stored in UTC — `posted_at` on a folio entry, `settled_at` on an order —
but a hotel's books are kept in the property's own local day. Slicing `[:10]` off a UTC
timestamp answers "what day was it in Greenwich", which is not the question anyone
reporting revenue is asking.

Concretely, with the default timezone (Asia/Kolkata, UTC+5:30): a bar bill settled at
01:00 on the 6th of March is stored as `2026-03-05T19:30:00+00:00`. The naive slice
reports it on the 5th — a day early. For a bar that closes at 1–2am that is the entire
late session on the wrong day, every day; and the first 5 hours 30 minutes of trade on
the 1st of a month land in the previous month's report. Anything recorded between 00:00
and 05:29:59 local time is affected.

`charge_date` on a room night is deliberately NOT run through here. It is already a
plain local calendar date, not a timestamp — converting it again would shift it.

Kept in `services/` rather than in a router or shared into `models/` because both halves
of the revenue chart need it: `services/revenue.py` (a pure, database-free module that
may not import routers) and the routers `analytics.py`, `reports.py` and `folios.py`.
Routers already import from `services/`, and `services/` imports no router, so this is
the one place both sides can reach without creating a cycle.
"""
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# The property's timezone. Defaults to Asia/Kolkata: this property prices in ₹ and
# computes Indian GST slabs, so India is the sensible default rather than UTC. Override
# with PROPERTY_TZ for a property that sits somewhere else.
LOCAL_TZ_NAME = os.environ.get("PROPERTY_TZ", "Asia/Kolkata")
LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)

# Length of a bare YYYY-MM-DD date.
_DATE_LEN = 10


def local_date(stamp) -> str | None:
    """The local calendar day a UTC timestamp falls on, as YYYY-MM-DD.

    Returns None when there is no usable date. Legacy rows predate some of these
    fields, and losing one row from a report beats a 500 on the whole screen.

    A bare `YYYY-MM-DD` is returned untouched: a date is already a local calendar day
    and has no instant to convert. A timestamp without an offset is read as UTC,
    matching how every writer in this codebase stamps one.
    """
    if not stamp:
        return None
    text = str(stamp)
    if len(text) == _DATE_LEN:
        return text
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Unparseable. Fall back to the leading date rather than dropping the row
        # entirely — that is what the old naive slice always did.
        head = text[:_DATE_LEN]
        return head if len(head) == _DATE_LEN else None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).date().isoformat()


def today() -> str:
    """Today's date at the property, as YYYY-MM-DD.

    Before 05:30 local time the UTC date is still yesterday's, so a UTC `today` makes a
    2am report claim tonight's room night has not happened yet.
    """
    return datetime.now(LOCAL_TZ).date().isoformat()


def now_local() -> datetime:
    """The current moment in the property's timezone."""
    return datetime.now(LOCAL_TZ)
