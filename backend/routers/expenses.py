"""What the property spends, and the profit that falls out of it.

Three things live here: the categories a property names for itself, the expenses it
records against them, and the combined report that sets those against what it earned.

**Revenue is not recomputed.** The combined report calls `routers/analytics.py::revenue`
— the endpoint function, directly, with the caller's own user and scoped handle — so
there is exactly one answer in this codebase to "what did we earn", and it is the one
that already knows to drop an `outlet` folio entry so a bar bill charged to a room is not
counted twice. A second implementation here would be a second answer, and two answers to
that question is worse than none.

**Expenses are append-only.** Nothing here edits or deletes one. See `void_expense`.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import routers.analytics as analytics
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, require_configuration
from services.access import DOMAINS
from services.clock import today as local_today
from services.expenses import (
    CATEGORY_NAME_MAX, PAYMENT_METHODS, ExpenseError, combine, expense_day, in_range,
    normalise_category_name, same_category_name, summarise)

router = APIRouter()

# A backstop, not the filter — the same one `routers/analytics.py` keeps and for the same
# reason. Every query below that can be bounded by date is; this only stops a
# pathological range paging a decade of spending into memory.
MAX_ROWS = 200_000

# The screen this whole module sits behind.
#
# `DOMAINS` because expenditure spans the property: salaries are not the restaurant's or
# the hotel's, they are the business's, and a report that could only cover half of what a
# place spends is not a report about whether it made money. Declared exactly as
# `admin.analytics` is, and for the same reason — an outlet property holds `restaurant`,
# so the property check ahead of the admin bypass still passes for it.
SCREEN = "admin.expenses"

# Reading is whoever holds the key: no role tuple at all, so the tick on the staff screen
# is the whole decision. That is what the owner asked for — "everyone who has access" —
# and it is the mechanism the product already has for saying so.
READ = require_access(DOMAINS, permission=SCREEN)

# Recording is narrower. "admin" must appear here: `can_access` checks the role before it
# applies the admin domain bypass, so a tuple without it locks admins out of their own
# books.
RECORD = require_access(DOMAINS, "admin", "manager", permission=SCREEN)

# Naming the categories is configuration — the taxonomy every figure on the report is
# grouped by, the same shape of thing as a tax slab or a menu item, and admin-only for
# the same reason. A manager records against the list; the owner decides what the list is.
CONFIG = require_configuration(DOMAINS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_range(start: str, end: str) -> None:
    """Both ends parse, and they are the right way round. Identical to the analytics
    endpoint's check, deliberately: two report screens that disagreed about what a valid
    range is would refuse different halves of the same question."""
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD dates")
    if a > b:
        raise HTTPException(400, "start must not be after end")


async def _category_names(db) -> dict[str, str]:
    """Every category id this property has, against the name it goes by *now*.

    Read fresh on each report rather than stamped onto the expense at the time it was
    recorded, so renaming a category renames it on last month's chart too. The alternative
    — a snapshot on the row — makes a rename produce two slices for one thing, which is
    the failure the rename was trying to fix.
    """
    rows = await db.expense_categories.find({}, {"_id": 0}).to_list(MAX_ROWS)
    return {r["id"]: r["name"] for r in rows}


# --------------------------------- the categories ---------------------------------
class CategoryIn(BaseModel):
    name: str = Field(max_length=CATEGORY_NAME_MAX * 4)
    active: bool = True


@router.get("/expense-categories")
async def list_expense_categories(
    include_inactive: bool = Query(False),
    user: dict = Depends(READ),
    db: PropertyScopedDatabase = Depends(tenant_db),
):
    """This property's categories, in name order.

    Retired ones are excluded by default so the recording form does not offer them, and
    included on request so the screen that manages the list can show what it retired.
    """
    rows = await db.expense_categories.find({}, {"_id": 0}).sort("name", 1).to_list(MAX_ROWS)
    if include_inactive:
        return rows
    return [r for r in rows if r.get("active", True)]


@router.post("/expense-categories")
async def create_expense_category(payload: CategoryIn, user: dict = Depends(CONFIG),
                                  db: PropertyScopedDatabase = Depends(tenant_db)):
    try:
        name = normalise_category_name(payload.name)
    except ExpenseError as exc:
        raise HTTPException(400, str(exc))

    existing = await db.expense_categories.find({}, {"_id": 0}).to_list(MAX_ROWS)
    for row in existing:
        if same_category_name(row["name"], name):
            # 409 naming the row, rather than a second category with the same name: an
            # owner who typed "utilities" under an existing "Utilities" meant that one,
            # and two slices for one thing is what the breakdown exists to avoid.
            raise HTTPException(409, f"There is already a category called {row['name']}")

    record = {"id": str(uuid.uuid4()), "name": name, "active": payload.active,
              "created_at": _now()}
    await db.expense_categories.insert_one(record)
    record.pop("_id", None)
    return record


@router.put("/expense-categories/{category_id}")
async def update_expense_category(category_id: str, payload: CategoryIn,
                                  user: dict = Depends(CONFIG),
                                  db: PropertyScopedDatabase = Depends(tenant_db)):
    """Rename a category, or retire and restore one.

    Renaming reaches every past report, because the reports resolve the name at read time
    — see `_category_names`. Retiring takes it out of the recording form and leaves it
    naming the money already spent under it, which is why there is a flag rather than
    only a delete.
    """
    current = await db.expense_categories.find_one({"id": category_id}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Category not found")
    try:
        name = normalise_category_name(payload.name)
    except ExpenseError as exc:
        raise HTTPException(400, str(exc))

    others = await db.expense_categories.find({}, {"_id": 0}).to_list(MAX_ROWS)
    for row in others:
        if row["id"] != category_id and same_category_name(row["name"], name):
            raise HTTPException(409, f"There is already a category called {row['name']}")

    await db.expense_categories.update_one(
        {"id": category_id}, {"$set": {"name": name, "active": payload.active}})
    return await db.expense_categories.find_one({"id": category_id}, {"_id": 0})


@router.delete("/expense-categories/{category_id}")
async def delete_expense_category(category_id: str, user: dict = Depends(CONFIG),
                                  db: PropertyScopedDatabase = Depends(tenant_db)):
    """Delete a category nothing has been recorded against.

    One that has been used is refused, with the count, and the refusal says to retire it
    instead. Deleting it would leave real money labelled "Uncategorised" on every report
    that has already been read — the figures would still add up, and the answer to "where
    did it go" would have quietly got worse.
    """
    current = await db.expense_categories.find_one({"id": category_id}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Category not found")

    used = await db.expenses.count_documents({"category_id": category_id})
    if used:
        raise HTTPException(409, {
            "message": f"{current['name']} has {used} expense{'s' if used != 1 else ''} "
                       f"recorded against it, so it cannot be deleted. Retire it instead "
                       f"and it will stop being offered without changing past reports.",
            "expenses": used})

    await db.expense_categories.delete_one({"id": category_id})
    return {"deleted": category_id}


# ---------------------------------- the expenses ----------------------------------
class ExpenseIn(BaseModel):
    amount: float
    category_id: str
    # Omitted means today at the property. Never derived from a timestamp on the server
    # or in the browser — see `services/clock.py` and the note in `record_expense`.
    spent_on: Optional[str] = None
    description: str = ""
    payment_method: Literal[PAYMENT_METHODS] = "cash"  # type: ignore[valid-type]
    payee: str = ""
    # A bill or invoice number. Optional because a chai run has no bill, and free text
    # because every supplier numbers their own way.
    reference: str = ""


class VoidIn(BaseModel):
    reason: str = ""


# The Literal above is built from the tuple in services/expenses.py, so the two cannot
# drift; this asserts the shape it was built from at import rather than trusting it.
assert PAYMENT_METHODS and all(isinstance(m, str) for m in PAYMENT_METHODS)


@router.get("/expenses")
async def list_expenses(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    include_voided: bool = Query(True),
    sort: str = Query("date"),
    direction: int = Query(-1),
    user: dict = Depends(READ),
    db: PropertyScopedDatabase = Depends(tenant_db),
):
    """The transactions themselves, filtered and sorted.

    The date range is pushed to the database (one equality on the property and one range
    on `spent_on` — the composite index for it is declared in `firestore.indexes.json`).
    Everything else is filtered in this process, the way `routers/reports.py` does: they
    are low-cardinality or free text over a set already bounded by date, and each one
    pushed down would be another composite index for no gain.

    Voided rows are included by default and carry their `voided_at`. They are not
    money the property spent — nothing here or in `summarise` counts them — but hiding
    them would make an append-only ledger look like an editable one, and a correction
    nobody can see is not a correction.
    """
    if start and end:
        _check_range(start, end)

    query: dict = {}
    if start:
        query["spent_on"] = {"$gte": start}
    if end:
        query.setdefault("spent_on", {})["$lte"] = end
    rows = await db.expenses.find(query, {"_id": 0}).to_list(MAX_ROWS)

    if category_id:
        rows = [r for r in rows if r.get("category_id") == category_id]
    if payment_method:
        rows = [r for r in rows if r.get("payment_method") == payment_method]
    if not include_voided:
        rows = [r for r in rows if not r.get("voided_at")]
    if q:
        needle = q.strip().casefold()
        if needle:
            rows = [r for r in rows
                    if needle in " ".join(str(r.get(f) or "") for f in
                                          ("description", "payee", "reference")).casefold()]

    names = await _category_names(db)
    for row in rows:
        # Resolved on the way out rather than stored, so a rename reaches this list too.
        row["category_name"] = names.get(row.get("category_id")) or "Uncategorised"

    keys = {"date": lambda r: (r.get("spent_on") or "", r.get("recorded_at") or ""),
            "amount": lambda r: float(r.get("amount") or 0),
            "category": lambda r: r.get("category_name", "").casefold()}
    rows.sort(key=keys.get(sort, keys["date"]), reverse=direction < 0)
    return rows


@router.post("/expenses")
async def record_expense(payload: ExpenseIn, user: dict = Depends(RECORD),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    """Record one expense against a category of this property's.

    **The date.** `spent_on` is a plain local calendar date and is stored as one. When the
    client does not send it, it comes from `services.clock.today()` — the property's own
    day, not the server's UTC one. That is the whole of the 1am problem: for the five and
    a half hours after midnight in Kolkata the UTC date is still yesterday's, so a
    `datetime.utcnow().date()` default would file a bill paid on the 6th under the 5th,
    every night, and push the first hours of the 1st into last month's figures.
    `recorded_at` is a UTC timestamp and is kept separately — it says when somebody typed
    this in, which is a different fact and never the one a report is grouped by.
    """
    if payload.amount is None or payload.amount <= 0:
        # Not merely "not negative". A zero-rupee expense is a row with no money in it,
        # and the only way to record a credit from a supplier honestly is as a category
        # of its own or a reversal, not as a negative expense hiding inside a total.
        raise HTTPException(400, "An expense needs an amount greater than zero")

    category = await db.expense_categories.find_one(
        {"id": payload.category_id}, {"_id": 0})
    if not category:
        # 404 rather than 400: the id arrives from a browser, and a category belonging to
        # another property does not exist as far as this one is concerned. The scoped
        # handle is what makes that true rather than the message.
        raise HTTPException(404, "Category not found")
    if not category.get("active", True):
        raise HTTPException(400, f"{category['name']} has been retired, so nothing new "
                                 f"can be recorded against it")

    spent_on = payload.spent_on or local_today()
    try:
        date.fromisoformat(spent_on)
    except (ValueError, TypeError):
        raise HTTPException(400, "spent_on must be a YYYY-MM-DD date")

    record = {
        "id": str(uuid.uuid4()),
        "amount": round(float(payload.amount), 2),
        "category_id": payload.category_id,
        "spent_on": spent_on,
        "description": payload.description.strip(),
        "payment_method": payload.payment_method,
        "payee": payload.payee.strip(),
        "reference": payload.reference.strip(),
        # Who recorded it. The name is stored beside the id on purpose: a staff member
        # who leaves is deactivated rather than deleted, but the book still has to read
        # without a join against a roster that may have been narrowed since.
        "recorded_by": user.get("id"),
        "recorded_by_name": user.get("name") or "",
        "recorded_at": _now(),
        # Written null rather than omitted, so "not voided" is one shape and not two.
        "voided_at": None, "voided_by": None, "voided_by_name": None, "void_reason": None,
    }
    await db.expenses.insert_one(record)
    record.pop("_id", None)
    return {**record, "category_name": category["name"]}


@router.post("/expenses/{expense_id}/void")
async def void_expense(expense_id: str, payload: VoidIn, user: dict = Depends(RECORD),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    """Reverse an expense that should not have been recorded.

    **There is no edit and no delete, deliberately.** This codebase keeps money
    append-only everywhere else it keeps money: a folio correction is a reversing entry
    and a platform invoice is immutable. An expense is the same kind of record. It is
    what a profit figure the owner has already read was computed from, and in India it is
    also what backs an input-credit claim and a deduction — a row that can be changed
    afterwards is a row that cannot answer "what did the report say on the 3rd".

    So a mistake is corrected the way a folio's is: the wrong entry is reversed, with a
    reason and a name against it, and the right one is recorded beside it. Both stay
    visible. What was spent, on what, on which day, by whom is never rewritten; the only
    thing this endpoint adds is the fact that it was reversed, which is new information
    rather than a change to old information.

    Voiding twice is refused rather than treated as idempotent — a second reversal means
    whoever pressed it is looking at a stale screen, and telling them so is more useful
    than a silent 200.
    """
    expense = await db.expenses.find_one({"id": expense_id}, {"_id": 0})
    if not expense:
        raise HTTPException(404, "Expense not found")
    if expense.get("voided_at"):
        raise HTTPException(409, "This expense has already been reversed")

    patch = {"voided_at": _now(), "voided_by": user.get("id"),
             "voided_by_name": user.get("name") or "",
             "void_reason": payload.reason.strip()}
    await db.expenses.update_one({"id": expense_id}, {"$set": patch})
    return {**expense, **patch}


# ----------------------------------- the report -----------------------------------
@router.get("/expenses/report")
async def expenses_report(
    start: str = Query(...),
    end: str = Query(...),
    user: dict = Depends(READ),
    db: PropertyScopedDatabase = Depends(tenant_db),
):
    """Income against expenditure over a range: the shape by day, the split by category,
    and what is left.

    Revenue comes from `routers/analytics.py::revenue`, called as the coroutine it is
    with this caller's own user and scoped handle. Nothing is recomputed and nothing is
    re-authorised behind the caller's back — they have already passed `READ` above, and
    the analytics function's own `Depends` are not involved when it is called directly,
    which is exactly how `test_isolation.py` and `test_tenancy.py` exercise every
    endpoint in this application.

    `domains=None` means "every domain this caller holds", which for the profit figure is
    the right question: an owner asking what is left over is asking about the whole
    business. A manager who works in one half sees the income of that half against the
    property's spending, and the response says which domains it covered so the screen can
    label that honestly rather than implying a whole-property profit.

    **Note on who sees this.** Holding `admin.expenses` reveals revenue as well as
    expenditure, because profit is the point of the screen and there is no honest way to
    show what is left without showing what came in. That is why the migration grants this
    key to the same audience `admin.analytics` already has.
    """
    _check_range(start, end)

    revenue = await analytics.revenue(start=start, end=end, domains=None, user=user, db=db)

    rows = await db.expenses.find(
        {"spent_on": {"$gte": start, "$lte": end}}, {"_id": 0}).to_list(MAX_ROWS)
    # The database filter is on `spent_on`, which every expense written by this router
    # carries. `in_range` re-applies the rule through `expense_day`, which also covers a
    # row that somehow has none and has to fall back to its UTC `recorded_at` — the day
    # rule lives in one function and this is it.
    counted = in_range(rows, start, end)
    expenses = summarise(counted, start, end, await _category_names(db))
    combined = combine(revenue, expenses)

    return {
        "start": start, "end": end,
        "domains": revenue["domains"],
        "revenue": revenue,
        "expenses": expenses,
        # The three figures the owner came for. `net` is what is left: negative when the
        # property spent more than it earned, which is a real answer and not an error.
        "totals": {"earned": revenue["total"], "spent": expenses["total"],
                   "net": combined["net"]},
        "by_day": combined["by_day"],
    }


# `services/expenses.py::expense_day` is the only place that decides which day money
# lands on, and it is re-exported here so a reader of this router can find it without
# guessing. Nothing in this file slices a timestamp for a date.
__all__ = ["router", "expense_day"]
