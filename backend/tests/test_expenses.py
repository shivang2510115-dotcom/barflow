"""Expenditure attribution — pure, no database.

The twin of test_revenue.py, and deliberately shaped like it: the day money lands on and
the arithmetic that turns rows into a report, tested without a request, a server or a
database, so the rules are readable in one place.
"""
import pytest

from services.expenses import (
    DEFAULT_CATEGORIES, ExpenseError, combine, default_categories, expense_day, in_range,
    is_counted, normalise_category_name, same_category_name, summarise)


def x(amount, *, id="x1", category_id="c1", spent_on="2026-03-05",
      recorded_at="2026-03-05T10:00:00+00:00", voided_at=None):
    return {"id": id, "amount": amount, "category_id": category_id, "spent_on": spent_on,
            "recorded_at": recorded_at, "voided_at": voided_at}


NAMES = {"c1": "Salaries and Wages", "c2": "Utilities", "c3": "Supplies"}


# ------------------------------- the day money lands on -------------------------------
def test_the_day_is_the_one_the_property_named_not_the_one_it_was_typed_on():
    # Three weeks' worth of bills entered in one sitting must still land on their own
    # days, exactly as three room nights posted at check-out do.
    assert expense_day(x(100, spent_on="2026-03-01",
                         recorded_at="2026-03-21T09:00:00+00:00")) == "2026-03-01"


def test_an_expense_recorded_near_midnight_lands_on_the_propertys_local_day():
    """01:00 on the 6th at the property (Asia/Kolkata, UTC+5:30) is 19:30 UTC on the 5th.

    This is the bug this codebase has already had once. A bar manager settling the night's
    bills at 1am has a UTC date of the day before, so slicing the timestamp files the
    whole late session on the wrong day — every night — and pushes the first five and a
    half hours of the 1st of a month into the previous month's figures.
    """
    late = {"id": "late", "amount": 400, "category_id": "c1", "spent_on": None,
            "recorded_at": "2026-03-05T19:30:00+00:00"}
    assert expense_day(late) == "2026-03-06"

    report = summarise([late], "2026-03-01", "2026-03-31", NAMES)
    by = {d["date"]: d["amount"] for d in report["by_day"]}
    assert by["2026-03-06"] == 400.0
    assert by["2026-03-05"] == 0.0


def test_a_spent_on_date_is_never_run_through_the_clock_a_second_time():
    # It is already a local calendar date, not an instant. Converting it again would
    # shift it — the mistake `charge_date` is protected from on the revenue side.
    assert expense_day(x(100, spent_on="2026-03-06",
                         recorded_at="2026-03-05T19:30:00+00:00")) == "2026-03-06"


def test_an_expense_with_no_usable_date_is_ignored_not_crashed_on():
    orphan = {"id": "o", "amount": 100, "category_id": "c1", "spent_on": None,
              "recorded_at": None}
    assert expense_day(orphan) is None
    assert summarise([orphan], "2026-03-01", "2026-03-31", NAMES)["total"] == 0.0


# ---------------------------------- the arithmetic ----------------------------------
def test_the_category_breakdown_sums_to_the_total():
    rows = [x(1234.56, id="a", category_id="c1"),
            x(78.9, id="b", category_id="c2"),
            x(0.01, id="c", category_id="c2"),
            x(4321.05, id="d", category_id="c3")]
    report = summarise(rows, "2026-03-01", "2026-03-31", NAMES)
    assert report["total"] == 5634.52
    # To the paise, not to within a rounding error: the sums are accumulated as integer
    # paise precisely so this is true by construction.
    assert sum(c["amount"] for c in report["by_category"]) == report["total"]
    assert sum(d["amount"] for d in report["by_day"]) == report["total"]


def test_the_breakdown_still_sums_when_the_naive_float_sum_would_not():
    # 0.1 + 0.2 != 0.3 in binary floating point. Three of these in three categories is
    # the smallest case where rounding each part separately misses the total.
    rows = [x(0.1, id="a", category_id="c1"), x(0.2, id="b", category_id="c2"),
            x(0.7, id="c", category_id="c3")]
    report = summarise(rows, "2026-03-05", "2026-03-05", NAMES)
    assert report["total"] == 1.0
    assert sum(c["amount"] for c in report["by_category"]) == 1.0


def test_each_category_carries_its_share_and_the_largest_comes_first():
    rows = [x(250, id="a", category_id="c1"), x(750, id="b", category_id="c2")]
    report = summarise(rows, "2026-03-01", "2026-03-31", NAMES)
    assert [c["name"] for c in report["by_category"]] == ["Utilities", "Salaries and Wages"]
    assert [c["share"] for c in report["by_category"]] == [75.0, 25.0]


def test_a_category_is_named_by_what_it_is_called_now():
    # The rename reaches last month's chart too, because the name is resolved at read
    # time rather than stamped on the row when it was recorded.
    report = summarise([x(100, category_id="c2")], "2026-03-01", "2026-03-31",
                       {"c2": "Power and water"})
    assert report["by_category"][0]["name"] == "Power and water"


def test_money_whose_category_has_vanished_is_still_money():
    report = summarise([x(100, category_id="gone")], "2026-03-01", "2026-03-31", NAMES)
    assert report["total"] == 100.0
    assert report["by_category"][0]["name"] == "Uncategorised"


def test_a_range_excludes_what_falls_outside_it():
    rows = [x(100, id="a", spent_on="2026-02-28"), x(100, id="b", spent_on="2026-03-01"),
            x(100, id="c", spent_on="2026-03-31"), x(100, id="d", spent_on="2026-04-01")]
    report = summarise(rows, "2026-03-01", "2026-03-31", NAMES)
    assert report["total"] == 200.0
    assert report["count"] == 2
    assert {e["id"] for e in in_range(rows, "2026-03-01", "2026-03-31")} == {"b", "c"}


def test_the_range_is_inclusive_of_both_ends():
    # A report range names the days the user picked, unlike a stay, whose departure night
    # is not slept in. Same rule as hotel_revenue.
    assert summarise([x(100, spent_on="2026-03-01")],
                     "2026-03-01", "2026-03-01", NAMES)["total"] == 100.0


def test_by_day_covers_every_day_in_range_including_the_empty_ones():
    report = summarise([], "2026-03-01", "2026-03-03", NAMES)
    assert [d["date"] for d in report["by_day"]] == ["2026-03-01", "2026-03-02", "2026-03-03"]
    assert all(d["amount"] == 0.0 for d in report["by_day"])


def test_an_empty_range_has_no_shares_rather_than_a_division_by_zero():
    assert summarise([], "2026-03-01", "2026-03-03", NAMES)["by_category"] == []


# ------------------------------------ reversals ------------------------------------
def test_a_reversed_expense_is_not_spent_money():
    rows = [x(1000, id="wrong", voided_at="2026-03-06T10:00:00+00:00"),
            x(100, id="right")]
    assert is_counted(rows[0]) is False
    report = summarise(rows, "2026-03-01", "2026-03-31", NAMES)
    assert report["total"] == 100.0
    assert report["count"] == 1


def test_a_reversal_leaves_nothing_behind_on_the_day_it_was_reversed():
    # Excluded from the day it covers, not negated on the day somebody noticed — the
    # same choice `hotel_revenue` makes for a voided room night.
    rows = [x(1000, id="wrong", spent_on="2026-03-05",
              voided_at="2026-03-20T10:00:00+00:00")]
    report = summarise(rows, "2026-03-01", "2026-03-31", NAMES)
    assert all(d["amount"] == 0.0 for d in report["by_day"])
    assert in_range(rows, "2026-03-01", "2026-03-31") == []


# -------------------------------- income against it --------------------------------
def _revenue(by_day, total):
    return {"total": total, "by_day": [{"date": d, "total": v} for d, v in by_day]}


def test_what_is_left_is_income_minus_expenditure():
    revenue = _revenue([("2026-03-01", 10000.0), ("2026-03-02", 5000.0)], 15000.0)
    expenses = summarise([x(2000, id="a", spent_on="2026-03-01"),
                          x(500, id="b", spent_on="2026-03-02")],
                         "2026-03-01", "2026-03-02", NAMES)
    out = combine(revenue, expenses)
    assert out["net"] == 12500.0
    assert out["by_day"] == [
        {"date": "2026-03-01", "income": 10000.0, "expenditure": 2000.0, "net": 8000.0},
        {"date": "2026-03-02", "income": 5000.0, "expenditure": 500.0, "net": 4500.0}]


def test_breaking_even_reads_as_zero_and_not_as_a_floating_point_crumb():
    # 12345.67 - 12345.60 - 0.07 is not 0.0 in binary floating point, and a profit line
    # reading ₹0.00000000001 is the sort of number an owner rings up about.
    revenue = _revenue([("2026-03-01", 12345.67)], 12345.67)
    expenses = summarise([x(12345.60, id="a", spent_on="2026-03-01"),
                          x(0.07, id="b", spent_on="2026-03-01")],
                         "2026-03-01", "2026-03-01", NAMES)
    assert combine(revenue, expenses)["net"] == 0.0


def test_spending_more_than_was_earned_is_a_negative_answer_not_an_error():
    revenue = _revenue([("2026-03-01", 1000.0)], 1000.0)
    expenses = summarise([x(2500, spent_on="2026-03-01")], "2026-03-01", "2026-03-01", NAMES)
    assert combine(revenue, expenses)["net"] == -1500.0


def test_the_two_sides_are_joined_on_the_date_not_on_position():
    # The expense range is wider than the revenue one here. Joined by index, the 400
    # would be drawn against the wrong day; joined by date it lands where it belongs and
    # the extra day simply does not appear.
    revenue = _revenue([("2026-03-02", 900.0)], 900.0)
    expenses = summarise([x(400, spent_on="2026-03-02")], "2026-03-01", "2026-03-02", NAMES)
    assert combine(revenue, expenses)["by_day"] == [
        {"date": "2026-03-02", "income": 900.0, "expenditure": 400.0, "net": 500.0}]


# ------------------------------------ categories ------------------------------------
def test_the_defaults_are_the_indian_hospitality_ones_and_each_gets_its_own_id():
    rows = default_categories()
    assert [r["name"] for r in rows] == list(DEFAULT_CATEGORIES)
    assert "Salaries and Wages" in DEFAULT_CATEGORIES and "Licences and Taxes" in DEFAULT_CATEGORIES
    assert len({r["id"] for r in rows}) == len(rows)
    # Two properties get different ids for the same name: these are each property's own
    # categories, not a shared table, so one renaming "Supplies" must not touch the other.
    assert not ({r["id"] for r in rows} & {r["id"] for r in default_categories()})


def test_a_category_name_is_trimmed_and_its_whitespace_collapsed():
    assert normalise_category_name("  Salaries   and Wages ") == "Salaries and Wages"


def test_an_empty_or_overlong_category_name_is_refused():
    with pytest.raises(ExpenseError):
        normalise_category_name("   ")
    with pytest.raises(ExpenseError):
        normalise_category_name("x" * 200)


def test_two_spellings_of_one_name_are_one_category():
    assert same_category_name("utilities", "Utilities ") is True
    assert same_category_name("Utilities", "Supplies") is False
