"""The planner's rules, with no database and no server under them.

`services/planner.py` is pure, so the questions that decide what a manager sees on a
month grid — which day an event falls on, what "no time" means, where a weekly repeat
lands — are answered here in milliseconds. The router's own tests
(tests/test_planner_api.py) then only have to prove the wiring.
"""
import pytest

from services.planner import (
    DEFAULT_CATEGORIES, MONTHLY, PlannerError, WEEKLY, check_times, clean_colour,
    clean_name, clean_time, default_categories, expand, is_all_day, occurrences,
    parse_date)


def event(**kwargs) -> dict:
    base = {"id": "e1", "title": "Staff briefing", "date": "2026-08-04",
            "start_time": None, "end_time": None, "repeat": None, "repeat_until": None}
    base.update(kwargs)
    return base


# ------------------------------- dates and windows -------------------------------
def test_an_event_appears_in_its_own_month_and_not_the_next():
    """The headline case. A date that is only *sliced* out of a timestamp, or compared as
    a string against the wrong month, fails exactly here."""
    august = occurrences(event(date="2026-08-05"), "2026-08-01", "2026-08-31")
    september = occurrences(event(date="2026-08-05"), "2026-09-01", "2026-09-30")
    assert august == ["2026-08-05"]
    assert september == []


def test_the_first_and_last_day_of_a_window_are_inside_it():
    assert occurrences(event(date="2026-08-01"), "2026-08-01", "2026-08-31") == ["2026-08-01"]
    assert occurrences(event(date="2026-08-31"), "2026-08-01", "2026-08-31") == ["2026-08-31"]
    assert occurrences(event(date="2026-07-31"), "2026-08-01", "2026-08-31") == []
    assert occurrences(event(date="2026-09-01"), "2026-08-01", "2026-08-31") == []


def test_a_backwards_window_holds_nothing_rather_than_raising():
    assert occurrences(event(date="2026-08-05"), "2026-08-31", "2026-08-01") == []


def test_a_date_that_is_not_a_date_is_refused_where_it_is_written():
    assert parse_date("2026-08-05") == "2026-08-05"
    # A day February does not have. Refused here rather than stored and then sorted into
    # a month with no such cell.
    with pytest.raises(PlannerError):
        parse_date("2026-02-30")
    with pytest.raises(PlannerError):
        parse_date("05/08/2026")
    with pytest.raises(PlannerError):
        parse_date(None)


# ------------------------------------- times -------------------------------------
def test_an_all_day_event_and_a_timed_one_are_different_states():
    assert is_all_day(event()) is True
    assert is_all_day(event(start_time="16:00")) is False


def test_an_empty_string_is_no_time_at_all_rather_than_a_time():
    """The bug this exists to stop: a form posting `start_time: ""` for a fire drill,
    producing a row that is neither timed nor all-day."""
    assert clean_time("") is None
    assert clean_time("   ") is None
    assert clean_time(None) is None
    assert is_all_day({"start_time": ""}) is True


def test_a_time_is_normalised_so_two_four_oclocks_compare_equal():
    assert clean_time("16:00") == "16:00"
    assert clean_time("16:00:00") == "16:00"
    assert clean_time("9:05") == "09:05"


def test_a_value_that_is_not_a_time_is_refused():
    for bad in ("16", "16:60", "24:00", "four", "16:00:00:00"):
        with pytest.raises(PlannerError):
            clean_time(bad)


def test_an_end_time_without_a_start_is_refused():
    with pytest.raises(PlannerError):
        check_times(None, "17:00")


def test_an_event_may_not_end_before_it_begins():
    with pytest.raises(PlannerError):
        check_times("17:00", "16:00")
    with pytest.raises(PlannerError):
        check_times("16:00", "16:00")
    assert check_times("16:00", "17:00") == ("16:00", "17:00")


def test_both_times_empty_is_the_all_day_case_and_is_allowed():
    assert check_times("", "") == (None, None)
    assert check_times(None, None) == (None, None)


# ----------------------------------- recurrence -----------------------------------
def test_a_weekly_repeat_lands_on_the_same_weekday_every_week():
    # 2026-08-03 is a Monday.
    days = occurrences(
        event(date="2026-08-03", repeat=WEEKLY, repeat_until="2026-08-31"),
        "2026-08-01", "2026-08-31")
    assert days == ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]


def test_a_weekly_repeat_that_began_months_ago_still_shows_in_this_month():
    """The reason expansion happens at read time: August must not have to know that the
    briefing was first written down in March."""
    days = occurrences(
        event(date="2026-03-02", repeat=WEEKLY, repeat_until="2026-12-31"),
        "2026-08-01", "2026-08-31")
    assert days == ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]


def test_a_repeat_stops_on_its_end_date():
    days = occurrences(
        event(date="2026-08-03", repeat=WEEKLY, repeat_until="2026-08-17"),
        "2026-08-01", "2026-09-30")
    assert days == ["2026-08-03", "2026-08-10", "2026-08-17"]


def test_a_monthly_repeat_keeps_the_day_of_the_month():
    days = occurrences(
        event(date="2026-01-15", repeat=MONTHLY, repeat_until="2026-04-30"),
        "2026-01-01", "2026-04-30")
    assert days == ["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"]


def test_a_monthly_repeat_skips_a_month_that_has_no_such_day():
    """The 31st means the 31st. Moving it to the 30th or rolling it into the 1st would
    announce a stocktake on a day nobody agreed to, and the series must not then drag the
    rest of the year along behind the shortened month."""
    days = occurrences(
        event(date="2026-01-31", repeat=MONTHLY, repeat_until="2026-05-31"),
        "2026-01-01", "2026-05-31")
    assert days == ["2026-01-31", "2026-03-31", "2026-05-31"]


def test_a_repeat_with_no_end_date_is_bounded_by_the_window():
    days = occurrences(
        event(date="2026-08-03", repeat=WEEKLY, repeat_until=None),
        "2026-08-01", "2026-08-14")
    assert days == ["2026-08-03", "2026-08-10"]


def test_a_repeat_this_module_has_never_heard_of_is_a_single_event():
    """Not guessed at. A value from a later version of this file must put the event on
    its own day and nowhere else, rather than on days nobody chose."""
    days = occurrences(
        event(date="2026-08-03", repeat="fortnightly", repeat_until="2026-12-31"),
        "2026-08-01", "2026-08-31")
    assert days == ["2026-08-03"]


# ------------------------------------ expansion ------------------------------------
def test_expansion_puts_one_row_on_every_day_an_event_falls_on():
    rows = expand([event(id="e1", date="2026-08-03", repeat=WEEKLY,
                         repeat_until="2026-08-17")], "2026-08-01", "2026-08-31")
    assert [r["occurrence_date"] for r in rows] == ["2026-08-03", "2026-08-10", "2026-08-17"]
    # The id stays the event's own: that is what an edit or a delete names.
    assert {r["id"] for r in rows} == {"e1"}
    assert [r["occurrence_id"] for r in rows] == ["e1:2026-08-03", "e1:2026-08-10",
                                                  "e1:2026-08-17"]
    assert all(r["recurring"] is True for r in rows)


def test_all_day_events_sort_above_timed_ones_on_the_same_day():
    rows = expand([
        event(id="timed", title="Briefing", date="2026-08-04", start_time="16:00"),
        event(id="early", title="Handover", date="2026-08-04", start_time="09:00"),
        event(id="drill", title="Fire drill", date="2026-08-04"),
    ], "2026-08-01", "2026-08-31")
    assert [r["id"] for r in rows] == ["drill", "early", "timed"]
    assert [r["all_day"] for r in rows] == [True, False, False]


def test_expansion_orders_by_day_first():
    rows = expand([
        event(id="b", date="2026-08-09"),
        event(id="a", date="2026-08-02"),
    ], "2026-08-01", "2026-08-31")
    assert [r["id"] for r in rows] == ["a", "b"]


def test_expansion_leaves_the_stored_event_alone():
    stored = event(date="2026-08-04")
    expand([stored], "2026-08-01", "2026-08-31")
    assert "occurrence_date" not in stored and "all_day" not in stored


# ------------------------------------ categories ------------------------------------
def test_the_property_starts_with_the_categories_the_design_named():
    names = [name for name, _colour in DEFAULT_CATEGORIES]
    assert names == ["Training", "Meeting", "Guest service", "Maintenance", "Event"]


def test_seeded_categories_belong_to_the_property_that_gets_them():
    """Fresh ids each call. They are stored per property, not shared, which is what lets
    one hotel rename "Event" to "Banquet" without touching anybody else's."""
    first, second = default_categories(), default_categories()
    assert {c["id"] for c in first}.isdisjoint({c["id"] for c in second})
    assert all(c["active"] is True and c["colour"].startswith("#") for c in first)


def test_a_colour_is_a_colour_and_not_a_stylesheet():
    """It is written into a style attribute on the month grid, so a category coloured
    `red; background: url(...)` is stored cross-site scripting with a colour picker in
    front of it."""
    assert clean_colour("#F97316") == "#f97316"
    assert clean_colour("#abc") == "#aabbcc"
    for bad in ("red", "#12345", "", None, "#f97316; background:url(x)"):
        with pytest.raises(PlannerError):
            clean_colour(bad)


def test_a_category_needs_a_name():
    assert clean_name("  Banquet ") == "Banquet"
    with pytest.raises(PlannerError):
        clean_name("   ")
