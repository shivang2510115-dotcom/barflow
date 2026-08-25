"""What a business agreed to pay, what a payment buys, and when an invoice is past due.

Pricing here is **manual**. There is no gateway, no card and no self-serve checkout: the
operator agrees a figure offline, records it, and the money arrives by bank transfer or
UPI. The software's whole job is to remember what was agreed and make overdue visible.

Pure functions over plain dicts — no database, no framework — for the same reason
`pricing.py` and `revenue.py` beside it are: this is money, and money rules have to be
readable in one place and testable without a server. Every date is a plain `YYYY-MM-DD`
local calendar day. The caller supplies today; it is never read from the clock here, so
the arithmetic is the same on a test machine at 2am as it is in Bengaluru at noon. The
day itself comes from `services/clock.py::today()` at the call site, which is the
property's local day rather than the server's UTC one.

Two rules carry the design, and both exist to stop the same failure — money changing
hands for a term the business did not get:

* paying while still in credit **extends** the term. A hotel that pays a month early has
  bought thirteen months, not twelve and a day.
* paying while overdue runs from **today**, never from the stale `paid_until`. A hotel
  three months late buys the month ahead; it is not billed for May, June and July, which
  it was never invoiced for and never had.

And one that is about the shape of the record rather than the sum: **overdue is derived,
never stored.** A flag on the property goes stale the instant nobody recomputes it, and
the failure is silent in both directions — a business chased for an invoice it settled,
or one trading free because a nightly job stopped running. `is_overdue` takes the day as
an argument for exactly that reason: the same record answers differently tomorrow with
nothing rewritten in between.

Nothing here suspends anybody. An overdue business keeps trading until the operator
presses suspend, deliberately, on the platform console — a hotel with guests checking in
must not go dark because an invoice was four days late. That is a business decision, and
this module only makes it visible.
"""
import calendar
from datetime import date

# The billing periods a price can be agreed on. Whole months, all three, so the
# arithmetic below is one rule rather than three — see PERIOD_MONTHS.
MONTHLY = "monthly"
QUARTERLY = "quarterly"
YEARLY = "yearly"

BILLING_PERIODS = (MONTHLY, QUARTERLY, YEARLY)

# How far each period moves `paid_until`. Months rather than days on purpose: a business
# billed on the 6th expects the next invoice on the 6th, and 30-day arithmetic walks that
# date backwards through the year until a monthly subscription bills thirteen times.
PERIOD_MONTHS = {MONTHLY: 1, QUARTERLY: 3, YEARLY: 12}

# How the money actually arrives. There is no card here by design, so this is the list a
# bank statement can be reconciled against — a method outside it is a typo, and a typo in
# how ₹12,000 arrived is the line nobody can match six months later.
PAYMENT_METHODS = ("bank_transfer", "upi", "cash", "cheque")

# How each is written when it is shown to a person.
METHOD_LABELS = {
    "bank_transfer": "Bank transfer",
    "upi": "UPI",
    "cash": "Cash",
    "cheque": "Cheque",
}

# Guards the labels against a method added above and forgotten here — the console would
# otherwise render a raw key beside a payment.
if set(METHOD_LABELS) != set(PAYMENT_METHODS):
    raise RuntimeError(
        f"METHOD_LABELS {sorted(METHOD_LABELS)} has drifted from PAYMENT_METHODS "
        f"{sorted(PAYMENT_METHODS)}")


class SubscriptionError(Exception):
    """Raised when an input cannot mean anything — an unknown period, a method that is
    not one of the four, a date that is not a date.

    A raise rather than a default. Every silent fallback available here is wrong in a way
    that costs somebody money: guessing `monthly` bills a yearly customer twelve times,
    and reading an unparseable date as "today" moves a term the payer did not buy.
    """


def normalise_period(period) -> str:
    """One of the three billing periods, or a refusal naming what was given."""
    key = str(period or "").strip().lower()
    if key not in PERIOD_MONTHS:
        raise SubscriptionError(
            f"unknown billing period: {period!r} — expected one of "
            f"{', '.join(BILLING_PERIODS)}")
    return key


def normalise_method(method) -> str:
    """One of the four payment methods, or a refusal naming what was given."""
    key = str(method or "").strip().lower()
    if key not in PAYMENT_METHODS:
        raise SubscriptionError(
            f"unknown payment method: {method!r} — expected one of "
            f"{', '.join(PAYMENT_METHODS)}")
    return key


def _parse(day) -> date:
    """A plain YYYY-MM-DD, as a date. Raises rather than guessing — see SubscriptionError.

    Only ever called where a bad date must stop the operation. The read paths below use
    `_parse_or_none` instead, because a hand-edited record should not 500 the operator's
    list of every business.
    """
    try:
        return date.fromisoformat(str(day))
    except (TypeError, ValueError):
        raise SubscriptionError(f"not a date: {day!r} — expected YYYY-MM-DD") from None


def _parse_or_none(day) -> date | None:
    """The same, for a stored value that may be absent or malformed.

    Absent is the normal state — a property nobody has priced yet, or one priced and not
    yet paid. Malformed is a bug in a record somebody edited by hand, and the honest
    answer on a *read* is "there is no date here", not a red flag beside a business's
    name that nobody can explain.
    """
    if not day:
        return None
    try:
        return date.fromisoformat(str(day))
    except (TypeError, ValueError):
        return None


def add_months(day: str, months: int) -> str:
    """`day` moved forward by whole calendar months, clamped to the month's last day.

    The 31st of January plus one month is the 28th of February (the 29th in a leap year).
    Clamping is the only answer that neither skips a month nor rolls into the next one:
    rolling would hand every business billed on a 31st three free days each February, and
    then bill them on the 3rd forever after, because the date would have moved.
    """
    start = _parse(day)
    total = start.month - 1 + int(months)
    year = start.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()


def _term_start(paid_until, today: str) -> date:
    """The day the term being bought actually begins — the rule, in one place.

    Later of "what is already owned" and "now". Both halves matter and both are about not
    charging for time the business does not get:

    * in credit → the existing `paid_until`, so an early payment extends rather than
      resetting, and the unused part of the term is not quietly confiscated;
    * overdue, or never paid → today, so a business three months late buys the month
      ahead rather than the three it was never invoiced for.

    Equal is the safe boundary: paid *through* today is paid, and both branches give the
    same answer, so nothing changes as the day rolls over.
    """
    now = _parse(today)
    current = _parse_or_none(paid_until)
    if current is None or current < now:
        return now
    return current


def period_covered(paid_until, period: str, today: str) -> tuple[str, str]:
    """The term a payment made today buys: (from, to), as plain dates.

    This is what goes on the ledger line, and it is the honest record of *what was
    bought* rather than of what was owed — which is why an overdue payer's line starts
    today and says so, instead of quietly claiming to cover months that were never
    invoiced.
    """
    start = _term_start(paid_until, today)
    return start.isoformat(), add_months(start.isoformat(), PERIOD_MONTHS[
        normalise_period(period)])


def advance_paid_until(paid_until, period: str, today: str) -> str:
    """Where `paid_until` stands after a payment recorded today.

    The far end of `period_covered`, named separately because that is the field the
    property record actually keeps and the two must never be worked out differently.
    """
    return period_covered(paid_until, period, today)[1]


def is_overdue(paid_until, today: str) -> bool:
    """Whether an invoice has gone past due, as at `today`.

    Derived, every time, from the two dates — there is no stored flag to go stale, and no
    job whose failure would silently stop this being true.

    Three things are deliberately *not* overdue: no date at all (nothing has come due), an
    unreadable date (a bug to fix, not a debt to chase), and a date equal to today (paid
    through today is paid).
    """
    due = _parse_or_none(paid_until)
    if due is None:
        return False
    return due < _parse(today)


def days_overdue(paid_until, today: str) -> int:
    """How many days past due, or 0 when it is not. Never negative: a business in credit
    is not "minus twelve days overdue", it is not overdue, and a console that had to know
    which sign meant which would get it wrong."""
    due = _parse_or_none(paid_until)
    if due is None:
        return 0
    return max(0, (_parse(today) - due).days)


def subscription_state(property_record: dict, today: str) -> dict:
    """The whole subscription picture for one property, as at `today`.

    A plain dict in — the stored property record — and a plain dict out, so the platform
    list, the platform detail and the business's own `GET /api/property` all answer from
    this one function rather than three readings of the same four fields.

    `priced` is false for a property nobody has agreed a figure with yet, and that is a
    normal state rather than an error: businesses are approved before they are priced,
    and a console that treated the gap as a fault would show a fault for every new
    signup. An unpriced property is never overdue whatever date is left on the record —
    chasing an invoice nobody agreed is worse than missing one.

    `never_paid` separates "no invoice has come due" from "one came due and was settled",
    which the operator cannot otherwise tell apart from a null `paid_until`.

    `payment_note` is absent on purpose. It is the operator's memo about how the money
    arrives — an account number, a person to ring — and this dict is also what the
    business itself is shown.
    """
    record = property_record or {}
    amount = record.get("subscription_amount")
    period = record.get("billing_period") or None
    paid_until = record.get("paid_until") or None
    # A price is both halves. An amount with no period cannot be advanced by a payment,
    # and a period with no amount is not a price anyone agreed.
    priced = amount is not None and period in PERIOD_MONTHS
    overdue = priced and is_overdue(paid_until, today)
    return {
        "amount": float(amount) if amount is not None else None,
        "period": period,
        "paid_until": paid_until,
        "priced": priced,
        "never_paid": priced and not paid_until,
        "overdue": overdue,
        "days_overdue": days_overdue(paid_until, today) if overdue else 0,
    }
