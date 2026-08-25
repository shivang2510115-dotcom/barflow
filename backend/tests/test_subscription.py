"""Subscription arithmetic: what a payment buys, and when an invoice went past due.

No server, no database, no framework — `services/subscription.py` is pure, like
`pricing.py` and `revenue.py` beside it, and every case below is a plain dict in and a
plain answer out. That matters more here than anywhere else in the codebase: a wrong
answer is not a wrong screen, it is a business billed for months it never had, or one
trading for free because a date quietly walked forwards.

Two cases carry the whole design and both are stated twice, once in each direction:

* paying while still in credit **extends** the term rather than resetting it — a hotel
  that pays a month early has bought thirteen months, not twelve and a day;
* paying while overdue runs from **today**, not from the stale `paid_until` — a hotel
  three months late pays for the month ahead, not for the three it was never invoiced
  for and never had.
"""
import pytest

from services.subscription import (
    BILLING_PERIODS, MONTHLY, PAYMENT_METHODS, PERIOD_MONTHS, QUARTERLY, YEARLY,
    SubscriptionError, add_months, advance_paid_until, days_overdue, is_overdue,
    normalise_method, normalise_period, period_covered, subscription_state,
)

TODAY = "2026-08-06"


# ------------------------------ the vocabulary ------------------------------
def test_the_three_billing_periods_are_the_ones_the_arithmetic_knows():
    assert set(BILLING_PERIODS) == set(PERIOD_MONTHS)
    assert BILLING_PERIODS == (MONTHLY, QUARTERLY, YEARLY)


def test_a_period_is_a_whole_number_of_months():
    assert PERIOD_MONTHS == {MONTHLY: 1, QUARTERLY: 3, YEARLY: 12}


def test_an_unknown_period_is_refused_rather_than_guessed():
    with pytest.raises(SubscriptionError):
        normalise_period("fortnightly")
    with pytest.raises(SubscriptionError):
        normalise_period(None)


def test_the_payment_methods_are_the_four_money_actually_arrives_by():
    # No card, no gateway: the operator agrees a price offline and the money arrives by
    # transfer. A method outside this list is a typo, and a typo in how ₹12,000 arrived
    # is the thing that cannot be reconciled against a bank statement later.
    assert PAYMENT_METHODS == ("bank_transfer", "upi", "cash", "cheque")


def test_an_unknown_payment_method_is_refused():
    with pytest.raises(SubscriptionError):
        normalise_method("bitcoin")


def test_a_method_is_taken_case_and_space_insensitively():
    assert normalise_method(" UPI ") == "upi"
    assert normalise_period(" Monthly ") == MONTHLY


# ------------------------------ month arithmetic ------------------------------
def test_a_plain_month_is_the_same_day_next_month():
    assert add_months("2026-08-06", 1) == "2026-09-06"


def test_the_end_of_a_long_month_lands_on_the_end_of_a_short_one():
    # There is no 31st of February. Clamping to the last day of the target month is the
    # only answer that does not either skip a month or roll into the next one — the
    # latter would silently hand a January payer three free days every year.
    assert add_months("2026-01-31", 1) == "2026-02-28"
    assert add_months("2026-03-31", 1) == "2026-04-30"


def test_february_is_the_29th_in_a_leap_year():
    assert add_months("2028-01-31", 1) == "2028-02-29"


def test_a_quarter_and_a_year_are_the_same_rule():
    assert add_months("2026-01-31", 3) == "2026-04-30"
    assert add_months("2026-08-06", 12) == "2027-08-06"
    assert add_months("2028-02-29", 12) == "2029-02-28"


def test_a_year_crosses_december_correctly():
    assert add_months("2026-11-30", 3) == "2027-02-28"
    assert add_months("2026-12-31", 1) == "2027-01-31"


def test_a_malformed_date_is_refused_rather_than_defaulted():
    with pytest.raises(SubscriptionError):
        add_months("06-08-2026", 1)
    with pytest.raises(SubscriptionError):
        add_months("", 1)


# --------------------------- what a payment buys ---------------------------
def test_a_first_payment_runs_from_today():
    # Nothing has been paid, so there is no term to extend. The month starts now.
    assert advance_paid_until(None, MONTHLY, TODAY) == "2026-09-06"
    assert advance_paid_until("", MONTHLY, TODAY) == "2026-09-06"


def test_paying_while_still_in_credit_extends_the_term():
    # Paid to the end of the year, paying again in August: the new month is added to
    # what is already owned, not started again from today. Otherwise every early payer
    # is quietly refunded the unused part of their term by losing it.
    assert advance_paid_until("2026-12-31", MONTHLY, TODAY) == "2027-01-31"


def test_paying_twice_in_one_day_buys_two_periods():
    # The same rule doing real work: a business settling two invoices at once must end
    # up two months forward, not one. This is what "extends rather than resets" means
    # when it is applied more than once.
    first = advance_paid_until(None, MONTHLY, TODAY)
    assert first == "2026-09-06"
    assert advance_paid_until(first, MONTHLY, TODAY) == "2026-10-06"


def test_paying_while_overdue_runs_from_today_not_from_the_stale_date():
    # Three months late. The stale date would give 2026-06-06 — a "payment" that leaves
    # them still two months overdue and charges them for May, June and July, months they
    # were never invoiced for and never had. The term they are buying starts today.
    assert advance_paid_until("2026-05-06", MONTHLY, TODAY) == "2026-09-06"


def test_a_hotel_a_year_late_pays_for_the_year_ahead_not_the_year_behind():
    assert advance_paid_until("2025-08-06", YEARLY, TODAY) == "2027-08-06"


def test_paid_until_today_is_not_stale_and_gives_the_same_answer_either_way():
    # The boundary. Paid through today is paid, not overdue — and both branches of the
    # rule agree here, which is what makes the boundary safe to move past.
    assert advance_paid_until(TODAY, MONTHLY, TODAY) == "2026-09-06"


def test_yesterday_is_already_stale():
    assert advance_paid_until("2026-08-05", MONTHLY, TODAY) == "2026-09-06"


def test_tomorrow_is_still_in_credit():
    assert advance_paid_until("2026-08-07", MONTHLY, TODAY) == "2026-09-07"


def test_a_quarter_from_an_overdue_date_still_starts_today():
    assert advance_paid_until("2026-01-31", QUARTERLY, TODAY) == "2026-11-06"


def test_the_period_covered_is_the_term_the_payment_bought():
    # What goes on the ledger line. From where it actually started, to where it now runs.
    assert period_covered(None, MONTHLY, TODAY) == (TODAY, "2026-09-06")
    assert period_covered("2026-12-31", MONTHLY, TODAY) == ("2026-12-31", "2027-01-31")
    assert period_covered("2026-05-06", MONTHLY, TODAY) == (TODAY, "2026-09-06")


def test_the_period_covered_and_the_new_paid_until_never_disagree():
    for start in (None, "2026-05-06", TODAY, "2026-12-31"):
        for period in BILLING_PERIODS:
            assert period_covered(start, period, TODAY)[1] == advance_paid_until(
                start, period, TODAY)


def test_advancing_with_an_unknown_period_is_refused():
    with pytest.raises(SubscriptionError):
        advance_paid_until(TODAY, "weekly", TODAY)


# ------------------------------ overdue, derived ------------------------------
def test_a_date_in_the_future_is_not_overdue():
    assert is_overdue("2026-12-31", TODAY) is False
    assert days_overdue("2026-12-31", TODAY) == 0


def test_paid_through_today_is_not_overdue():
    assert is_overdue(TODAY, TODAY) is False
    assert days_overdue(TODAY, TODAY) == 0


def test_yesterday_is_one_day_overdue():
    assert is_overdue("2026-08-05", TODAY) is True
    assert days_overdue("2026-08-05", TODAY) == 1


def test_three_months_late_is_counted_in_days():
    assert days_overdue("2026-05-06", TODAY) == 92


def test_a_property_that_has_never_paid_is_not_reported_overdue():
    # Nothing to be past. `never_paid` says so out loud on the state below, so the
    # console can tell "no invoice has come due" from "an invoice came due and was paid".
    assert is_overdue(None, TODAY) is False
    assert days_overdue(None, TODAY) == 0


def test_an_unreadable_paid_until_is_not_overdue():
    # A hand-edited record must not switch a red flag on beside a business's name; it is
    # a bug to fix, not a debt to chase.
    assert is_overdue("not a date", TODAY) is False
    assert days_overdue("2026-13-45", TODAY) == 0


# ------------------------------ the whole state ------------------------------
def test_an_unpriced_property_is_a_normal_state_not_an_error():
    state = subscription_state({}, TODAY)
    assert state["priced"] is False
    assert state["amount"] is None
    assert state["period"] is None
    assert state["paid_until"] is None
    assert state["overdue"] is False
    assert state["days_overdue"] == 0


def test_a_priced_property_in_credit_reads_as_paid():
    state = subscription_state({
        "subscription_amount": 12000.0, "billing_period": MONTHLY,
        "paid_until": "2026-09-06"}, TODAY)
    assert state["priced"] is True
    assert state["amount"] == 12000.0
    assert state["period"] == MONTHLY
    assert state["paid_until"] == "2026-09-06"
    assert state["overdue"] is False
    assert state["days_overdue"] == 0
    assert state["never_paid"] is False


def test_a_priced_property_past_its_date_reads_as_overdue_with_a_day_count():
    state = subscription_state({
        "subscription_amount": 12000.0, "billing_period": MONTHLY,
        "paid_until": "2026-05-06"}, TODAY)
    assert state["overdue"] is True
    assert state["days_overdue"] == 92


def test_a_priced_property_that_has_never_paid_says_so_without_being_overdue():
    state = subscription_state({
        "subscription_amount": 12000.0, "billing_period": MONTHLY}, TODAY)
    assert state["priced"] is True
    assert state["never_paid"] is True
    assert state["overdue"] is False


def test_an_unpriced_property_is_never_overdue_however_stale_its_date():
    # A price was withdrawn but a date was left behind. Chasing an invoice nobody agreed
    # is worse than missing one, and the operator sees the date either way.
    state = subscription_state({"paid_until": "2020-01-01"}, TODAY)
    assert state["priced"] is False
    assert state["overdue"] is False
    assert state["days_overdue"] == 0


def test_the_state_carries_no_stored_overdue_flag_to_go_stale():
    # The whole point: overdue is computed against the day it is asked about. The same
    # record answers differently tomorrow, with nothing rewritten in between.
    record = {"subscription_amount": 12000.0, "billing_period": MONTHLY,
              "paid_until": "2026-08-06"}
    assert subscription_state(record, "2026-08-06")["overdue"] is False
    assert subscription_state(record, "2026-08-07")["overdue"] is True
    assert subscription_state(record, "2026-08-07")["days_overdue"] == 1
    assert "overdue" not in record


def test_the_note_is_the_operators_and_is_not_in_the_state():
    # How they pay is the operator's memo — a bank account, a person's name. The state
    # is what the business itself is shown, so the note is deliberately absent from it.
    state = subscription_state({
        "subscription_amount": 12000.0, "billing_period": MONTHLY,
        "payment_note": "NEFT to HDFC 0001, ref BARFLOW"}, TODAY)
    assert "note" not in state and "payment_note" not in state
