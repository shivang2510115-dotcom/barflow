"""Advances, and what a month came to.

Two collections and one rule that governs both: **nothing here is edited.** An advance
given by mistake is reversed by a second row; a paid salary run is corrected by reversing
it and creating another. This is money leaving the business, and the folio ledger's
reasoning applies unchanged — "what did we pay Priya in August" must have exactly one
answer, forever, and it has to survive the argument.

The arithmetic is not here. It is in `services/payroll.py`, pure and testable without a
server, and that module deliberately knows no tax law: no PF, no ESI, no professional tax,
no TDS. Deductions are named lines somebody entered. See the design document for why that
boundary exists and why it is not a gap to fill in later.
"""
import calendar
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import unscoped_db
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import SHARED
from services.clock import today as local_today
from services.payroll import payslip_figures

router = APIRouter()

# Payroll is the owner's. A manager marks attendance; only an admin decides what is paid.
PAYROLL = require_access(SHARED, "admin", permission="admin.staff")

MAX_ROWS = 20000

# Attendance statuses that credit a day, and how much of one.
_PRESENT = "present"
_HALF = "half_day"
_LEAVE = "leave"
_WEEK_OFF = "week_off"


class AdvanceIn(BaseModel):
    user_id: str
    amount: float
    reason: str = ""


class LineIn(BaseModel):
    label: str
    amount: float


class RunIn(BaseModel):
    month: str


class PayslipPatch(BaseModel):
    """What an owner adjusts on one payslip before the run is paid.

    Additions and deductions only. The attendance-derived figures are not editable —
    they come from the attendance screen, and letting a payslip disagree with it would
    make the record unanswerable.
    """
    additions: list[LineIn] = []
    deductions: list[LineIn] = []


@router.get("/advances")
async def list_advances(outstanding: bool = False, user: dict = Depends(PAYROLL),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    rows = await db.advances.find({}, {"_id": 0}).to_list(MAX_ROWS)
    if outstanding:
        rows = [a for a in rows if not a.get("recovered_in")]
    rows.sort(key=lambda a: a.get("given_at") or "", reverse=True)
    return rows


@router.post("/advances")
async def give_advance(payload: AdvanceIn, user: dict = Depends(PAYROLL),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    if payload.amount <= 0:
        raise HTTPException(400, "An advance has to be more than nothing")
    target = await unscoped_db.users.find_one(
        {"id": payload.user_id, "property_id": db.property_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "No such staff member in this property")

    row = {
        "id": str(uuid.uuid4()),
        "user_id": payload.user_id,
        "user_name": target.get("name") or "",
        "amount": round(payload.amount, 2),
        "reason": payload.reason.strip() or None,
        "given_on": local_today(),
        "given_by": user.get("name") or user.get("email") or "admin",
        "given_at": datetime.now(timezone.utc).isoformat(),
        "recovered_in": None,
    }
    await db.advances.insert_one(dict(row))
    return row


async def _figures_for(db, month: str, staff: list[dict],
                       advances: list[dict]) -> list[dict]:
    """One payslip's numbers for every active person, from that month's attendance."""
    year, mon = int(month[:4]), int(month[5:7])
    days_in_month = calendar.monthrange(year, mon)[1]

    marks = await db.attendance.find(
        {"on": {"$gte": f"{month}-01", "$lte": f"{month}-31"}}, {"_id": 0}
    ).to_list(MAX_ROWS)
    by_user: dict[str, list[dict]] = {}
    for m in marks:
        by_user.setdefault(m.get("user_id"), []).append(m)

    owed: dict[str, float] = {}
    for a in advances:
        owed[a["user_id"]] = round(owed.get(a["user_id"], 0) + a["amount"], 2)

    out = []
    for person in staff:
        rows = by_user.get(person["id"], [])
        counted = {s: sum(1 for r in rows if r.get("status") == s)
                   for s in (_PRESENT, _HALF, _LEAVE, _WEEK_OFF)}
        marked = sum(counted.values()) + sum(
            1 for r in rows if r.get("status") == "absent")

        # A day nobody marked is a day nobody marked, not an absence. Crediting the
        # unmarked remainder as present is what stops a manager who never opened the
        # screen from silently cutting everybody's pay.
        unmarked = max(0, days_in_month - marked)

        figures = payslip_figures(
            salary_monthly=person.get("salary_monthly") or 0,
            days_in_month=days_in_month,
            present=counted[_PRESENT] + unmarked,
            half_days=counted[_HALF],
            paid_leave=counted[_LEAVE],
            week_offs=counted[_WEEK_OFF],
            additions=[], deductions=[],
            advance_recovered=owed.get(person["id"], 0),
        )
        out.append({
            "user_id": person["id"],
            # Copied, not referenced. A payslip records what somebody was paid and what
            # they were called at the time; a promotion in October must not retitle their
            # August payslip.
            "name": person.get("name") or "",
            "designation": person.get("designation") or "",
            "salary_monthly": person.get("salary_monthly") or 0,
            "days_in_month": days_in_month,
            "present": counted[_PRESENT], "unmarked": unmarked,
            "half_days": counted[_HALF], "paid_leave": counted[_LEAVE],
            "week_offs": counted[_WEEK_OFF],
            "absent": sum(1 for r in rows if r.get("status") == "absent"),
            "additions": [], "deductions": [],
            **figures,
        })
    return out


@router.get("/payroll/runs")
async def list_runs(user: dict = Depends(PAYROLL),
                    db: PropertyScopedDatabase = Depends(tenant_db)):
    rows = await db.salary_runs.find({}, {"_id": 0}).to_list(MAX_ROWS)
    rows.sort(key=lambda r: r.get("month") or "", reverse=True)
    return rows


@router.post("/payroll/runs")
async def create_run(payload: RunIn, user: dict = Depends(PAYROLL),
                     db: PropertyScopedDatabase = Depends(tenant_db)):
    """Draft a month's payroll from its attendance."""
    month = payload.month.strip()
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(400, "A month looks like 2026-08")

    existing = await db.salary_runs.find_one({"month": month, "status": "draft"})
    if existing:
        raise HTTPException(409, f"{month} already has a draft run — open or delete it")

    everybody = await unscoped_db.users.find(
        {"property_id": db.property_id, "active": True}, {"_id": 0}).to_list(5000)

    # Payroll is for people with a salary recorded. Not every login is an employee — a
    # shared kitchen tablet, an account made for a QR screen, somebody whose record was
    # never finished — and generating a zero-rupee payslip for each of them buries the
    # people who are actually paid.
    #
    # The gap is reported rather than hidden: `not_on_payroll` names everyone left out,
    # so an owner sees "3 staff have no salary recorded" instead of finding three empty
    # payslips and wondering which is a mistake.
    staff = [u for u in everybody if (u.get("salary_monthly") or 0) > 0]
    skipped = [{"id": u["id"], "name": u.get("name") or ""}
               for u in everybody if (u.get("salary_monthly") or 0) <= 0]

    advances = [a for a in await db.advances.find({}, {"_id": 0}).to_list(MAX_ROWS)
                if not a.get("recovered_in")]

    run = {
        "id": str(uuid.uuid4()),
        "month": month,
        "status": "draft",
        "created_by": user.get("name") or user.get("email") or "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paid_at": None,
        "reversal_of": None,
        "reversed_by": None,
        "not_on_payroll": skipped,
    }
    await db.salary_runs.insert_one(dict(run))

    for slip in await _figures_for(db, month, staff, advances):
        await db.payslips.insert_one({
            "id": str(uuid.uuid4()), "run_id": run["id"], **slip})

    return run


@router.get("/payroll/runs/{run_id}")
async def read_run(run_id: str, user: dict = Depends(PAYROLL),
                   db: PropertyScopedDatabase = Depends(tenant_db)):
    run = await db.salary_runs.find_one({"id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, "No such payroll run")
    slips = await db.payslips.find({"run_id": run_id}, {"_id": 0}).to_list(MAX_ROWS)
    slips.sort(key=lambda s: (s.get("name") or "").lower())
    return {**run, "payslips": slips,
            "total_net": round(sum(s.get("net") or 0 for s in slips), 2)}


@router.patch("/payroll/runs/{run_id}/payslips/{payslip_id}")
async def adjust(run_id: str, payslip_id: str, payload: PayslipPatch,
                 user: dict = Depends(PAYROLL),
                 db: PropertyScopedDatabase = Depends(tenant_db)):
    """Add an overtime line, or a deduction the CA supplied. Draft runs only."""
    run = await db.salary_runs.find_one({"id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, "No such payroll run")
    if run["status"] != "draft":
        raise HTTPException(409, "A paid run cannot be changed — reverse it instead")

    slip = await db.payslips.find_one({"id": payslip_id, "run_id": run_id}, {"_id": 0})
    if not slip:
        raise HTTPException(404, "No such payslip in this run")

    additions = [l.model_dump() for l in payload.additions]
    deductions = [l.model_dump() for l in payload.deductions]
    figures = payslip_figures(
        salary_monthly=slip["salary_monthly"], days_in_month=slip["days_in_month"],
        present=slip["present"] + slip["unmarked"], half_days=slip["half_days"],
        paid_leave=slip["paid_leave"], week_offs=slip["week_offs"],
        additions=additions, deductions=deductions,
        advance_recovered=slip["advance_recovered"])

    merged = {**slip, "additions": additions, "deductions": deductions, **figures}
    await db.payslips.update_one({"id": payslip_id}, {"$set": merged})
    return merged


@router.post("/payroll/runs/{run_id}/pay")
async def mark_paid(run_id: str, user: dict = Depends(PAYROLL),
                    db: PropertyScopedDatabase = Depends(tenant_db)):
    """Freeze the run, and recover the advances it accounted for.

    After this the payslips do not move, whatever happens to the attendance behind them.
    The payslip somebody was handed and the record in the system must not be able to
    disagree — the same snapshot rule the guest bill follows.
    """
    run = await db.salary_runs.find_one({"id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, "No such payroll run")
    if run["status"] != "draft":
        raise HTTPException(409, "This run is already paid")

    slips = await db.payslips.find({"run_id": run_id}, {"_id": 0}).to_list(MAX_ROWS)
    recovered_from = {s["user_id"] for s in slips if (s.get("advance_recovered") or 0) > 0}

    # Marked against this run, so a second run in the same month cannot take the same
    # money back twice.
    for a in await db.advances.find({}, {"_id": 0}).to_list(MAX_ROWS):
        if a.get("recovered_in") or a["user_id"] not in recovered_from:
            continue
        await db.advances.update_one({"id": a["id"]}, {"$set": {"recovered_in": run_id}})

    await db.salary_runs.update_one({"id": run_id}, {"$set": {
        "status": "paid",
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }})
    return await read_run(run_id, user, db)


@router.post("/payroll/runs/{run_id}/reverse")
async def reverse(run_id: str, user: dict = Depends(PAYROLL),
                  db: PropertyScopedDatabase = Depends(tenant_db)):
    """Undo a paid run by recording that it was undone, never by deleting it.

    The reversal is its own row pointing at the original, and both survive. A run that
    could be deleted would take the answer to "what did we pay in August" with it.
    """
    run = await db.salary_runs.find_one({"id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(404, "No such payroll run")
    if run["status"] != "paid":
        raise HTTPException(409, "Only a paid run can be reversed")
    if run.get("reversed_by"):
        raise HTTPException(409, "This run has already been reversed")

    reversal = {
        "id": str(uuid.uuid4()),
        "month": run["month"],
        "status": "reversed",
        "created_by": user.get("name") or user.get("email") or "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paid_at": None,
        "reversal_of": run_id,
        "reversed_by": None,
    }
    await db.salary_runs.insert_one(dict(reversal))
    await db.salary_runs.update_one({"id": run_id},
                                    {"$set": {"reversed_by": reversal["id"]}})

    # The advances it recovered are outstanding again — the money was not, in the end,
    # taken back.
    for a in await db.advances.find({"recovered_in": run_id}, {"_id": 0}).to_list(MAX_ROWS):
        await db.advances.update_one({"id": a["id"]}, {"$set": {"recovered_in": None}})

    return reversal
