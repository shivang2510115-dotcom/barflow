"""Who worked which day.

The screen a manager opens daily, and the thing that makes payroll real rather than a
spreadsheet. One row per person per day, and nothing more — this is not a roster, not a
shift plan and not a clock-in system. It records what happened, which is all the salary
run needs.

**Marking is idempotent.** The row's id is derived from the person and the day, so
marking somebody twice corrects the row rather than creating a second one. Room nights
and entitlement uses use the same trick for the same reason: a double-tapped Save must
not produce two answers to "was Priya in on Tuesday".

**A day with no row is not an absence.** It is a day nobody marked, and
services/payroll.py treats it as present. Deducting for unmarked days would mean a
manager who forgets to open this screen for a week silently cuts everybody's pay, and
that is discovered on payday.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import unscoped_db
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import SHARED
from services.clock import today as local_today

router = APIRouter()

# Marking attendance is a manager's daily job, not only an owner's. "admin" is named
# because the role check runs before the admin domain-bypass.
MARK = require_access(SHARED, "admin", "manager", permission="admin.staff")

STATUSES = ("present", "absent", "leave", "week_off", "half_day")

# Fixed forever. Regenerating it would orphan every row already written and let the same
# day be marked twice.
_NAMESPACE = uuid.UUID("2f8b6c14-9d3a-5e77-b021-4c6a8e15d9f3")

MAX_ROWS = 20000


class MarkIn(BaseModel):
    user_id: str
    on: str
    status: str
    note: str = ""


def _row_id(user_id: str, on: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{user_id}|{on}"))


@router.get("/attendance")
async def month(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                user: dict = Depends(MARK),
                db: PropertyScopedDatabase = Depends(tenant_db)):
    """One month's marks, and who they are for.

    The roster comes back with them so the screen can draw a row per person without a
    second request — including people nobody has marked yet, who are the whole point of
    opening it.
    """
    rows = await db.attendance.find(
        {"on": {"$gte": f"{month}-01", "$lte": f"{month}-31"}}, {"_id": 0}
    ).to_list(MAX_ROWS)

    # `users` stands outside tenancy, so this says the property out loud — the same hand
    # -paid explicitness routers/staff.py uses for the same reason.
    staff = await unscoped_db.users.find(
        {"property_id": db.property_id, "active": True}, {"_id": 0}).to_list(5000)

    return {
        "month": month,
        "today": local_today(),
        "staff": sorted(
            [{"id": u["id"], "name": u.get("name") or "",
              "designation": u.get("designation") or "",
              "role": u.get("role")} for u in staff],
            key=lambda x: x["name"].lower()),
        "marks": rows,
    }


@router.put("/attendance")
async def mark(payload: MarkIn, user: dict = Depends(MARK),
               db: PropertyScopedDatabase = Depends(tenant_db)):
    """Mark one person for one day. Marking again corrects it."""
    if payload.status not in STATUSES:
        raise HTTPException(
            400, f"{payload.status} is not an attendance status — expected one of: "
                 f"{', '.join(STATUSES)}")

    target = await unscoped_db.users.find_one(
        {"id": payload.user_id, "property_id": db.property_id}, {"_id": 0})
    if not target:
        # 404 rather than 403 for somebody else's staff: the property filter above
        # already means they do not exist from here.
        raise HTTPException(404, "No such staff member in this property")

    row = {
        "id": _row_id(payload.user_id, payload.on),
        "user_id": payload.user_id,
        "on": payload.on,
        "status": payload.status,
        "note": payload.note.strip() or None,
        "marked_by": user.get("name") or user.get("email") or "staff",
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.attendance.update_one({"id": row["id"]}, {"$set": row}, upsert=True)
    return row
