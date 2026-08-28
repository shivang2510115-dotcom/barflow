"""What a property spends: which day it lands on, and how it adds up.

Pure functions over supplied rows — no database — so the arithmetic is testable in
isolation and stated in exactly one place. The deliberate twin of `services/revenue.py`,
which does the same for the other side of the ledger; between them the two modules are
the whole definition of "did this property make money".

Two rules carry the weight here.

**The day.** An expense carries `spent_on`, a plain local calendar date, for the same
reason a room night carries `charge_date`: the day money belongs to is the day the
property calls it, not the day Greenwich was having when somebody typed it in. A bill
paid at 1am on the 6th is the 6th's, and `services/clock.py` is what knows that. Nothing
here ever slices a timestamp for its date — see `expense_day`.

**The paise.** Sums are accumulated as integer paise and converted back once, at the end.
Rounding each category to two decimals and rounding the total separately gives a
breakdown that misses its own total by a paise often enough to be noticed, and a report
whose parts do not add up is a report nobody believes. Integers make "the breakdown sums
to the total" true by construction rather than by luck.
"""
import uuid

from services.clock import local_date
from services.revenue import revenue_days

# What an Indian hotel or restaurant actually spends on, as the starting point every new
# property is given. Seeded rather than hardcoded — the property renames, adds to and
# retires these from its own screen, because a list the hotel cannot fix is a list that
# is wrong for every hotel but the one it was written for.
#
# Order is display order: the two that dominate a hospitality P&L first, the catch-all
# last.
DEFAULT_CATEGORIES = (
    "Salaries and Wages",
    "Utilities",
    "Maintenance and Repairs",
    "Supplies",
    "Marketing",
    "Rent",
    "Licences and Taxes",
    "Miscellaneous",
)

# How the money left. A fixed vocabulary rather than free text, because this is a filter
# on the transactions screen and free text turns "UPI", "upi" and "Upi" into three
# payment methods. "other" is the escape hatch, so the list never has to be complete.
PAYMENT_METHODS = ("cash", "upi", "bank_transfer", "card", "cheque", "other")

# Long enough for "Maintenance and Repairs" and a property's own longer wording, short
# enough that a paste accident does not become a chart axis label.
CATEGORY_NAME_MAX = 60


class ExpenseError(ValueError):
    """A rule about expenses that the caller's input breaks. Routers turn this into a
    refusal; keeping it out of `fastapi` is what lets these functions be tested without
    a request."""


def normalise_category_name(name) -> str:
    """One spelling of a category name: trimmed, with runs of whitespace collapsed.

    "  Salaries   and Wages " and "Salaries and Wages" are the same category, and a
    property that ends up holding both has a pie chart with two slices for one thing.
    """
    text = " ".join(str(name or "").split())
    if not text:
        raise ExpenseError("A category needs a name")
    if len(text) > CATEGORY_NAME_MAX:
        raise ExpenseError(f"A category name may be at most {CATEGORY_NAME_MAX} characters")
    return text


def same_category_name(a, b) -> bool:
    """Whether two names are the same category. Case-insensitive: an owner who types
    "utilities" under an existing "Utilities" meant the one they already have."""
    return normalise_category_name(a).casefold() == normalise_category_name(b).casefold()


def default_categories() -> list[dict]:
    """The seed set, as records. Fresh ids each call — these are one property's own
    categories, not a shared table, so two properties renaming "Supplies" differently is
    the normal case rather than a conflict."""
    return [{"id": str(uuid.uuid4()), "name": name, "active": True}
            for name in DEFAULT_CATEGORIES]


async def seed_expense_categories(db) -> int:
    """Give one property the default categories, if it has none. Returns how many were
    written.

    `db` is a property-scoped handle, so the writes are stamped and the count is that
    property's own. Idempotent by counting first, exactly like
    `services/reference_data.py::seed_reference_data`: a property that has renamed or
    retired its categories must never have the defaults put back on top of them.
    """
    if await db.expense_categories.count_documents({}) > 0:
        return 0
    rows = default_categories()
    await db.expense_categories.insert_many(rows)
    return len(rows)


# ------------------------------- the day and the money -------------------------------
def expense_day(expense: dict) -> str | None:
    """The property's calendar day this expense belongs to, as YYYY-MM-DD.

    `spent_on` is already a local calendar date and is used as-is — running it through
    `local_date` again would shift it, which is the mistake `charge_date` is protected
    from in `services/revenue.py::entry_revenue_date` for the same reason.

    `recorded_at` is the fallback and it is a UTC timestamp, so it is converted. That is
    the 1am case: an expense entered at 01:00 on the 6th of March in Kolkata is stamped
    `2026-03-05T19:30:00+00:00`, and slicing that string reports the money on the 5th.

    None when there is no usable date at all. Losing one row from a report beats a 500 on
    the whole screen — the same stance the revenue side takes towards a legacy row.
    """
    spent_on = expense.get("spent_on")
    if spent_on:
        return str(spent_on)[:10]
    return local_date(expense.get("recorded_at"))


def is_counted(expense: dict) -> bool:
    """Whether this expense is money the property actually spent.

    A voided expense is kept and shown — the ledger is append-only, so a mistake is
    corrected by reversing it and recording the right one, never by editing the wrong one
    away — but it is not spent money and must not reach a total.
    """
    return not expense.get("voided_at")


def _paise(amount) -> int:
    """Rupees as whole paise. Money arrives as a float from JSON; every sum below is done
    on these integers so that the parts and the whole cannot drift apart."""
    return int(round(float(amount or 0) * 100))


def _rupees(paise: int) -> float:
    return round(paise / 100.0, 2)


def in_range(expenses: list[dict], start: str, end: str) -> list[dict]:
    """The counted expenses whose local day falls inside an inclusive [start, end].

    Inclusive at both ends, matching `hotel_revenue`: a report range names the days the
    user picked, unlike a stay, whose departure night is not slept in.
    """
    days = set(revenue_days(start, end))
    return [e for e in expenses if is_counted(e) and expense_day(e) in days]


def summarise(expenses: list[dict], start: str, end: str,
              category_names: dict[str, str] | None = None) -> dict:
    """Expenditure over an inclusive [start, end] range: the total, the shape by day, and
    the share each category took.

    `category_names` maps category id to the name it is called *now*, so renaming
    "Utilities" to "Power and water" renames it on every past report too. A row whose
    category has been deleted outright falls back to a label rather than disappearing —
    money that was spent is money that was spent.

    Every day in the range appears, including the empty ones, so a chart can take
    `by_day` as its data with no gap-filling — the same contract `hotel_revenue` offers.

    `by_category` is ordered by amount, largest first: the question the breakdown answers
    is "where does it go", and the answer is read top-down.
    """
    names = category_names or {}
    days = revenue_days(start, end)
    in_days = set(days)

    by_day: dict[str, int] = {d: 0 for d in days}
    by_category: dict[str, int] = {}
    total = 0
    count = 0

    for e in expenses:
        if not is_counted(e):
            continue
        day = expense_day(e)
        if day is None or day not in in_days:
            continue
        paise = _paise(e.get("amount"))
        by_day[day] += paise
        key = e.get("category_id") or ""
        by_category[key] = by_category.get(key, 0) + paise
        total += paise
        count += 1

    rows = [{"category_id": cid or None,
             "name": names.get(cid) or "Uncategorised",
             "amount": _rupees(paise),
             # A percentage, to one decimal. Shares are presentation and may not add to
             # exactly 100 once rounded; the amounts are the figures that must add up,
             # and they do, because they came out of the same integers as the total.
             "share": round(paise * 100.0 / total, 1) if total else 0.0}
            for cid, paise in by_category.items()]
    rows.sort(key=lambda r: (-r["amount"], r["name"]))

    return {
        "total": _rupees(total),
        "count": count,
        "by_day": [{"date": d, "amount": _rupees(by_day[d])} for d in days],
        "by_category": rows,
    }


def combine(revenue: dict, expenses: dict) -> dict:
    """Income against expenditure, day by day, and what is left.

    Joined on the date rather than on position. Both sides come from `revenue_days`
    today, so an index would work — but if they ever stopped agreeing, the failure would
    be money silently drawn against the wrong day rather than an error. The same
    reasoning, and the same join, as `routers/analytics.py` uses for its two revenue
    blocks.

    Subtraction happens in paise for the reason everything else here does: `12345.67 -
    12345.60` is not `0.07` in binary floating point, and a profit figure that reads
    ₹0.07 when the property broke even is exactly the kind of number an owner rings up
    about.
    """
    spent = {d["date"]: _paise(d["amount"]) for d in expenses["by_day"]}
    by_day = []
    for row in revenue["by_day"]:
        earned = _paise(row["total"])
        out = spent.get(row["date"], 0)
        by_day.append({"date": row["date"], "income": _rupees(earned),
                       "expenditure": _rupees(out), "net": _rupees(earned - out)})
    return {"net": _rupees(_paise(revenue["total"]) - _paise(expenses["total"])),
            "by_day": by_day}
