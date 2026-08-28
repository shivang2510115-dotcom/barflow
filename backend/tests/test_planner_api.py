"""The planner through the routers: who may write, who may read, and which day it lands on.

No server. The endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, so both are called as what they are — the same style as test_housekeeping_api.py
and test_isolation.py. The authorization dependencies are called directly too, because
`require_access(...)` returns a checker that takes the user: that is the only way to assert
from in here that a waiter cannot create an event, and the declaration on the route is the
thing worth asserting about.

The date rules themselves live in tests/test_planner.py, against the pure module. What is
proved here is the wiring: that the router uses those rules, that it uses the property's
clock and not the server's, and that the screen key does what it claims.
"""
import asyncio
from datetime import datetime, timezone
from typing import get_args

import pytest
from fastapi import HTTPException

import db as db_module
import routers.planner as planner
import security
import services.clock as clock
from migrations.backfill_planner import backfill as backfill_planner
from models.planner import CalendarCategoryIn, CalendarEventIn, Repeat
from mock_db import MockDatabase
from scoped_db import PropertyScopedDatabase, tenant_db
from services.access import DOMAINS, LIVE, ROLE_SCREENS, SCREENS, SCREEN_KEYS
from services.planner import DEFAULT_CATEGORIES, REPEATS, seed_categories

SCREEN = "property.planner"


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


_UNSCOPED_HOLDERS = (db_module, security)


class _FrozenClock:
    """`datetime`, answering `now()` with one fixed instant.

    Stands in for `services.clock.datetime` so that "what day is it at the property" can
    be asked at a chosen moment. `now(tz)` converts, exactly as the real one does, so what
    is under test is the real timezone arithmetic and not a stubbed answer.
    """

    def __init__(self, instant: str):
        self.moment = datetime.fromisoformat(instant)

    def now(self, tz=None):
        return self.moment.astimezone(tz) if tz else self.moment.replace(tzinfo=None)


def at_utc(monkeypatch, instant: str) -> None:
    monkeypatch.setattr(clock, "datetime", _FrozenClock(instant))


@pytest.fixture
def hotel(tmp_path, monkeypatch):
    """One live property, one of each role, and the default categories."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    run(handle.properties.insert_one({
        "id": "p1", "name": "The Grand", "status": LIVE, "property_type": "both",
        "created_at": "2026-01-01T00:00:00+00:00"}))

    people = {}
    for tag, role, domains, permissions in (
            ("admin", "admin", DOMAINS, SCREEN_KEYS),
            ("manager", "manager", ("hotel",), (SCREEN, "hotel.front_desk")),
            # A manager the owner has not ticked for the planner. The role alone must not
            # be enough, or the screen key is decoration.
            ("manager_unticked", "manager", ("hotel",), ("hotel.front_desk",)),
            ("front_desk", "front_desk", ("hotel",), ("hotel.front_desk",)),
            ("waiter", "waiter", ("bar",), ("outlet.pos", "outlet.kot")),
            ("kitchen", "kitchen", ("restaurant",), ("outlet.kot",)),
    ):
        person = {"id": f"u-{tag}", "email": f"{tag}@grand.example.com", "name": tag.title(),
                  "role": role, "domains": list(domains), "permissions": list(permissions),
                  "active": True, "property_id": "p1"}
        run(handle.users.insert_one(person))
        people[tag] = person

    db = run(tenant_db(people["admin"]))
    run(seed_categories(db))
    return people, db, handle


def category(db, name="Meeting") -> dict:
    return run(db.calendar_categories.find_one({"name": name}, {"_id": 0}))


def make(db, people, actor="admin", **fields):
    body = {"title": "Staff briefing", "category_id": category(db)["id"]}
    body.update(fields)
    return call(planner.create_event, payload=CalendarEventIn(**body),
                user=people[actor], db=db)


# ------------------------------ the calendar reads ------------------------------
def test_an_event_on_a_date_appears_in_that_month_and_not_the_next(hotel):
    """The headline case, end to end through the router."""
    people, db, _ = hotel
    made = make(db, people, date="2026-08-05")
    assert made["date"] == "2026-08-05"

    august = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                  user=people["admin"], db=db)
    september = call(planner.list_events, start="2026-09-01", end="2026-09-30",
                     user=people["admin"], db=db)

    assert [e["occurrence_date"] for e in august["events"]] == ["2026-08-05"]
    assert august["events"][0]["id"] == made["id"]
    assert september["events"] == []


def test_an_all_day_event_and_a_timed_one_both_round_trip(hotel):
    """Both are ordinary states of the same record, and neither is an empty string
    pretending to be the other."""
    people, db, _ = hotel
    drill = make(db, people, title="Fire drill", date="2026-08-06")
    # The form for an untimed event posts empty strings. They must not survive as times.
    blanked = make(db, people, title="Stocktake", date="2026-08-06",
                   start_time="", end_time="")
    briefing = make(db, people, title="Briefing", date="2026-08-06",
                    start_time="16:00", end_time="17:30")

    assert drill["start_time"] is None and drill["end_time"] is None
    assert blanked["start_time"] is None and blanked["end_time"] is None
    assert briefing["start_time"] == "16:00" and briefing["end_time"] == "17:30"

    day = call(planner.list_events, start="2026-08-06", end="2026-08-06",
               user=people["admin"], db=db)
    by_id = {e["id"]: e for e in day["events"]}
    assert by_id[drill["id"]]["all_day"] is True
    assert by_id[blanked["id"]]["all_day"] is True
    assert by_id[briefing["id"]]["all_day"] is False
    # All-day first, then by time. That is the order a manager reads a day in.
    assert [e["title"] for e in day["events"]] == ["Fire drill", "Stocktake", "Briefing"]


def test_the_window_defaults_to_the_property_s_current_month(hotel, monkeypatch):
    people, db, _ = hotel
    at_utc(monkeypatch, "2026-08-14T09:00:00+00:00")
    seen = call(planner.list_events, start=None, end=None, user=people["admin"], db=db)
    assert (seen["start"], seen["end"]) == ("2026-08-01", "2026-08-31")
    # February, so the "step into next month and back" arithmetic is exercised on a
    # month whose length is not 30 or 31.
    at_utc(monkeypatch, "2026-02-14T09:00:00+00:00")
    seen = call(planner.list_events, start=None, end=None, user=people["admin"], db=db)
    assert (seen["start"], seen["end"]) == ("2026-02-01", "2026-02-28")


def test_a_window_that_is_backwards_or_enormous_is_refused(hotel):
    people, db, _ = hotel
    assert refused(planner.list_events, start="2026-08-31", end="2026-08-01",
                   user=people["admin"], db=db).status_code == 400
    assert refused(planner.list_events, start="2020-01-01", end="2030-01-01",
                   user=people["admin"], db=db).status_code == 400
    assert refused(planner.list_events, start="not-a-date", end="2026-08-01",
                   user=people["admin"], db=db).status_code == 400


def test_the_categories_ride_along_with_the_month(hotel):
    """One call draws the grid. Two would be two chances to disagree about a category
    that was renamed between them."""
    people, db, _ = hotel
    seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                user=people["admin"], db=db)
    assert [c["name"] for c in seen["categories"]] == sorted(
        [name for name, _ in DEFAULT_CATEGORIES], key=str.lower)


# --------------------------------- recurrence ---------------------------------
def test_a_weekly_repeat_is_drawn_on_every_week_of_the_month(hotel):
    people, db, _ = hotel
    made = make(db, people, title="Monday briefing", date="2026-03-02",
                start_time="16:00", repeat="weekly", repeat_until="2026-12-31")
    august = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                  user=people["admin"], db=db)
    assert [e["occurrence_date"] for e in august["events"]] == [
        "2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]
    # One stored row, five drawn cells, and every cell names the row an edit would change.
    assert {e["id"] for e in august["events"]} == {made["id"]}
    assert all(e["recurring"] is True for e in august["events"])
    assert run(db.calendar_events.count_documents({})) == 1


def test_a_recurring_event_is_not_drawn_twice_on_its_own_first_day(hotel):
    """It is found by both of the router's two queries — by its date and as a repeat —
    so the de-duplication is load-bearing, not defensive."""
    people, db, _ = hotel
    make(db, people, date="2026-08-03", repeat="weekly", repeat_until="2026-08-03")
    seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                user=people["admin"], db=db)
    assert [e["occurrence_date"] for e in seen["events"]] == ["2026-08-03"]


def test_a_repeat_must_say_when_it_ends(hotel):
    people, db, _ = hotel
    refusal = refused(planner.create_event,
                      payload=CalendarEventIn(title="Forever", date="2026-08-03",
                                              category_id=category(db)["id"],
                                              repeat="weekly"),
                      user=people["admin"], db=db)
    assert refusal.status_code == 400
    assert "repeat until" in str(refusal.detail)


def test_an_end_date_with_no_repeat_is_refused(hotel):
    people, db, _ = hotel
    assert refused(planner.create_event,
                   payload=CalendarEventIn(title="Odd", date="2026-08-03",
                                           category_id=category(db)["id"],
                                           repeat_until="2026-09-03"),
                   user=people["admin"], db=db).status_code == 400


def test_a_repeat_may_not_end_before_it_starts(hotel):
    people, db, _ = hotel
    assert refused(planner.create_event,
                   payload=CalendarEventIn(title="Backwards", date="2026-08-03",
                                           category_id=category(db)["id"],
                                           repeat="weekly", repeat_until="2026-07-03"),
                   user=people["admin"], db=db).status_code == 400


def test_the_models_repeat_literal_agrees_with_the_service(hotel):
    """Two lists of the same two words, kept apart because a Literal needs its members at
    type-check time. This is what stops them drifting in silence."""
    assert set(get_args(Repeat)) == set(REPEATS)


# ---------------------------- the day it lands on ----------------------------
def test_an_event_created_near_midnight_lands_on_the_property_s_own_day(hotel, monkeypatch):
    """**The one that matters.**

    01:00 on the 6th of August at the property (Asia/Kolkata, UTC+5:30) is
    2026-08-05T19:30:00Z. A manager writing "fire drill" into the planner at that moment
    means the 6th — that is the date on the wall clock in the building, and the date every
    person there would say. Slicing the UTC timestamp for its date answers "what day is it
    in Greenwich", which is the 5th: a day early, every night, for the first five and a
    half hours of every Indian morning. This codebase has had that bug in its money once
    already; `services/clock.py` exists because of it.
    """
    people, db, _ = hotel
    instant = "2026-08-05T19:30:00+00:00"
    assert instant[:10] == "2026-08-05"  # what the naive slice would have said
    at_utc(monkeypatch, instant)

    made = call(planner.create_event,
                payload=CalendarEventIn(title="Fire drill",
                                        category_id=category(db)["id"]),
                user=people["admin"], db=db)
    assert made["date"] == "2026-08-06"
    # And it is stored that way, not merely echoed that way.
    assert run(db.calendar_events.find_one({"id": made["id"]}))["date"] == "2026-08-06"

    # It is on the 6th when the calendar is drawn, and on nothing else.
    seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                user=people["admin"], db=db)
    assert [e["occurrence_date"] for e in seen["events"]] == ["2026-08-06"]
    # ...and the grid highlights the 6th as today, for the same reason.
    assert seen["today"] == "2026-08-06"


def test_the_same_instant_from_a_server_running_anywhere_gives_the_same_day(
        hotel, monkeypatch):
    """The property's day is a fact about the property, not about where the process runs.

    Two clients naming the same instant in two different offsets are the same moment, so
    the calendar has to put the event on the same day for both.
    """
    people, db, _ = hotel
    for instant in ("2026-08-05T19:30:00+00:00",  # UTC
                    "2026-08-05T12:30:00-07:00",  # a laptop in California
                    "2026-08-06T04:30:00+09:00"):  # a server in Tokyo
        at_utc(monkeypatch, instant)
        made = call(planner.create_event,
                    payload=CalendarEventIn(title="Fire drill",
                                            category_id=category(db)["id"]),
                    user=people["admin"], db=db)
        assert made["date"] == "2026-08-06", instant


def test_a_date_that_was_given_is_stored_exactly_as_given(hotel, monkeypatch):
    """A calendar date is already the property's own day, so it is never converted —
    `services/clock.py` says the same about `charge_date`. Converting it again is what
    moves it."""
    people, db, _ = hotel
    at_utc(monkeypatch, "2026-08-05T19:30:00+00:00")
    made = make(db, people, date="2026-12-25")
    assert made["date"] == "2026-12-25"


# -------------------------------- who may write --------------------------------
def test_a_manager_may_create_and_a_waiter_may_not(hotel):
    people, db, _ = hotel
    made = call(planner.create_event,
                payload=CalendarEventIn(title="Shift briefing", date="2026-08-04",
                                        start_time="16:00",
                                        category_id=category(db)["id"]),
                user=people["manager"], db=db)
    assert made["created_by"] == "u-manager"
    assert made["created_by_name"] == "Manager"

    # The refusal is the dependency's, so it is the dependency that is asked. Calling a
    # handler coroutine with `user=` supplies the argument the dependency would have
    # produced and therefore proves nothing about who may reach the route — the
    # declaration on the route is the thing worth asserting about.
    for tag in ("waiter", "kitchen", "front_desk"):
        assert refused(planner.PLAN, user=people[tag]).status_code == 403


def test_the_screen_key_is_what_lets_a_manager_write_and_not_the_role_alone(hotel):
    """Otherwise the tick on the staff screen is decoration."""
    people, _db, _ = hotel
    assert call(planner.PLAN, user=people["manager"])["id"] == "u-manager"
    assert refused(planner.PLAN, user=people["manager_unticked"]).status_code == 403


def test_an_admin_is_never_locked_out_of_the_planner(hotel):
    """"admin" is in the role tuple like every other role tuple here: the role check runs
    before the admin bypass, so an admin left out of the list is an admin refused."""
    people, _db, _ = hotel
    assert "admin" in planner.EDIT_ROLES
    assert call(planner.PLAN, user=people["admin"])["id"] == "u-admin"


def test_everybody_in_the_property_may_read_the_calendar(hotel):
    """A waiter has to be able to see that Tuesday's briefing is at 4pm."""
    people, db, _ = hotel
    make(db, people, title="Tuesday briefing", date="2026-08-04", start_time="16:00")
    for tag in ("waiter", "kitchen", "front_desk", "manager_unticked", "manager", "admin"):
        # The dependency lets them through...
        assert call(planner.READ, user=people[tag])["id"] == people[tag]["id"]
        # ...and what they get is the briefing, at the time it is at.
        seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                    user=people[tag], db=db)
        assert [e["title"] for e in seen["events"]] == ["Tuesday briefing"]
        assert seen["events"][0]["start_time"] == "16:00"


def test_the_screen_is_told_whether_this_person_may_edit(hotel):
    """Computed from `can_access` with the arguments the write routes declare, not
    re-derived — a second copy of the rule is how a button appears for somebody who then
    gets a 403 when they press it."""
    people, db, _ = hotel
    for tag, expected in (("admin", True), ("manager", True), ("manager_unticked", False),
                          ("front_desk", False), ("waiter", False), ("kitchen", False)):
        seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                    user=people[tag], db=db)
        assert seen["can_edit"] is expected, tag


def test_a_deactivated_account_reaches_nothing(hotel):
    people, _db, handle = hotel
    run(handle.users.update_one({"id": "u-manager"}, {"$set": {"active": False}}))
    stale = run(handle.users.find_one({"id": "u-manager"}, {"_id": 0}))
    assert refused(planner.PLAN, user=stale).status_code == 403
    assert refused(planner.READ, user=stale).status_code == 403


def test_a_suspended_property_locks_its_own_admin_out_of_the_planner(hotel):
    people, _db, handle = hotel
    run(handle.properties.update_one({"id": "p1"}, {"$set": {"status": "suspended"}}))
    assert refused(planner.PLAN, user=people["admin"]).status_code == 403
    assert refused(planner.READ, user=people["waiter"]).status_code == 403


# ---------------------------------- editing ----------------------------------
def test_an_event_can_be_edited_and_keeps_who_wrote_it(hotel):
    people, db, _ = hotel
    made = make(db, people, actor="manager", title="Briefing", date="2026-08-04")
    changed = call(planner.update_event, event_id=made["id"],
                   payload=CalendarEventIn(title="Briefing (moved)", date="2026-08-05",
                                           start_time="17:00",
                                           category_id=category(db)["id"]),
                   user=people["admin"], db=db)
    assert changed["title"] == "Briefing (moved)"
    assert changed["date"] == "2026-08-05"
    assert changed["start_time"] == "17:00"
    # Who wrote a thing down is a fact about the past; an edit by somebody else does not
    # change it.
    assert changed["created_by"] == "u-manager"
    assert changed["updated_at"]


def test_an_event_can_be_edited_back_to_all_day(hotel):
    people, db, _ = hotel
    made = make(db, people, date="2026-08-04", start_time="16:00", end_time="17:00")
    changed = call(planner.update_event, event_id=made["id"],
                   payload=CalendarEventIn(title="Fire drill", date="2026-08-04",
                                           start_time="", end_time="",
                                           category_id=category(db)["id"]),
                   user=people["admin"], db=db)
    assert changed["start_time"] is None and changed["end_time"] is None


def test_editing_cannot_produce_a_shape_that_could_not_have_been_created(hotel):
    """One validator for both routes. Two is how a checked field stops being checked."""
    people, db, _ = hotel
    made = make(db, people, date="2026-08-04")
    assert refused(planner.update_event, event_id=made["id"],
                   payload=CalendarEventIn(title="Bad", date="2026-08-04",
                                           end_time="17:00",
                                           category_id=category(db)["id"]),
                   user=people["admin"], db=db).status_code == 400


def test_an_event_can_be_deleted(hotel):
    people, db, _ = hotel
    made = make(db, people, date="2026-08-04")
    call(planner.delete_event, event_id=made["id"], user=people["admin"], db=db)
    assert run(db.calendar_events.count_documents({})) == 0
    assert refused(planner.delete_event, event_id=made["id"],
                   user=people["admin"], db=db).status_code == 404


def test_an_event_needs_a_title(hotel):
    people, db, _ = hotel
    assert refused(planner.create_event,
                   payload=CalendarEventIn(title="   ", date="2026-08-04",
                                           category_id=category(db)["id"]),
                   user=people["admin"], db=db).status_code == 400


# --------------------------------- categories ---------------------------------
def test_a_property_starts_with_the_default_categories(hotel):
    _people, db, _ = hotel
    rows = run(db.calendar_categories.find({}, {"_id": 0}).to_list(50))
    assert {c["name"] for c in rows} == {name for name, _ in DEFAULT_CATEGORIES}
    assert all(c["property_id"] == "p1" for c in rows)


def test_seeding_twice_does_not_duplicate_a_renamed_category(hotel):
    """A property that renamed "Event" to "Banquet" must not have "Event" put back on top
    of it by a restart."""
    _people, db, _ = hotel
    before = run(db.calendar_categories.count_documents({}))
    assert run(seed_categories(db)) == 0
    assert run(db.calendar_categories.count_documents({})) == before


def test_a_property_can_name_a_category_of_its_own(hotel):
    """The whole point: a category list hardcoded in our source is one the hotel cannot
    fix."""
    people, db, _ = hotel
    made = call(planner.create_category,
                payload=CalendarCategoryIn(name="Banquet", colour="#FF00AA"),
                user=people["manager"], db=db)
    assert made["name"] == "Banquet"
    assert made["colour"] == "#ff00aa"
    assert made["active"] is True

    made2 = make(db, people, date="2026-08-04", category_id=made["id"])
    assert made2["category_id"] == made["id"]


def test_every_write_route_sits_behind_the_key_and_every_read_route_does_not(hotel):
    """The guard that outlives the tests above.

    Calling a handler with `user=` supplies the argument the dependency would have
    produced, so no direct call can prove which dependency a route actually declares.
    This reads the declarations, and it fails the day somebody adds a seventh write route
    to this file and reaches for `READ` because it was nearer the top.
    """
    import inspect
    from fastapi.params import Depends as DependsMarker

    def gate(fn):
        for p in inspect.signature(fn).parameters.values():
            if p.name == "user" and isinstance(p.default, DependsMarker):
                return p.default.dependency
        return None

    for route in (planner.create_event, planner.update_event, planner.delete_event,
                  planner.create_category, planner.update_category,
                  planner.delete_category):
        assert gate(route) is planner.PLAN, route.__name__
    for route in (planner.list_events, planner.list_categories):
        assert gate(route) is planner.READ, route.__name__

    people, _db, _ = hotel
    assert refused(planner.PLAN, user=people["waiter"]).status_code == 403


def test_a_category_colour_is_a_colour_and_not_a_stylesheet(hotel):
    people, db, _ = hotel
    assert refused(planner.create_category,
                   payload=CalendarCategoryIn(
                       name="Injected", colour="#fff; background:url(//evil)"),
                   user=people["admin"], db=db).status_code == 400


def test_two_categories_cannot_share_a_name(hotel):
    people, db, _ = hotel
    assert refused(planner.create_category,
                   payload=CalendarCategoryIn(name="meeting", colour="#ffffff"),
                   user=people["admin"], db=db).status_code == 409


def test_a_category_in_use_is_switched_off_rather_than_deleted(hotel):
    people, db, _ = hotel
    meeting = category(db)
    make(db, people, date="2026-08-04", category_id=meeting["id"])

    refusal = refused(planner.delete_category, category_id=meeting["id"],
                      user=people["admin"], db=db)
    assert refusal.status_code == 409
    assert refusal.detail["events"] == 1

    off = call(planner.update_category, category_id=meeting["id"],
               payload=CalendarCategoryIn(name="Meeting", colour=meeting["colour"],
                                          active=False),
               user=people["admin"], db=db)
    assert off["active"] is False

    # Nothing new is filed under it...
    assert refused(planner.create_event,
                   payload=CalendarEventIn(title="Another", date="2026-08-05",
                                           category_id=meeting["id"]),
                   user=people["admin"], db=db).status_code == 400
    # ...and the event that already carries it still knows its colour.
    seen = call(planner.list_events, start="2026-08-01", end="2026-08-31",
                user=people["admin"], db=db)
    assert meeting["id"] in {c["id"] for c in seen["categories"]}


def test_an_unused_category_can_be_deleted(hotel):
    people, db, _ = hotel
    meeting = category(db)
    call(planner.delete_category, category_id=meeting["id"], user=people["admin"], db=db)
    assert run(db.calendar_categories.find_one({"id": meeting["id"]})) is None


def test_an_event_cannot_be_filed_under_a_category_that_does_not_exist(hotel):
    people, db, _ = hotel
    assert refused(planner.create_event,
                   payload=CalendarEventIn(title="Orphan", date="2026-08-04",
                                           category_id="nope"),
                   user=people["admin"], db=db).status_code == 400


# ------------------------- the screen key and its migration -------------------------
def test_the_catalogue_files_the_planner_under_its_own_section(hotel):
    assert SCREENS[SCREEN]["label"] == "Planner"
    assert SCREENS[SCREEN]["section"] == "Property"
    assert SCREENS[SCREEN]["domains"] == DOMAINS


def test_the_roles_that_plan_start_with_the_key_and_the_others_do_not(hotel):
    """Wired like `admin.analytics`: admin and manager compute their screens from the
    whole catalogue, so they gain it; the hand-written role lists are frozen and do not."""
    assert SCREEN in ROLE_SCREENS["admin"]
    assert SCREEN in ROLE_SCREENS["manager"]
    for role in ("front_desk", "waiter", "kitchen", "housekeeping"):
        assert SCREEN not in ROLE_SCREENS[role], role


def test_the_migration_grants_the_key_and_seeds_a_property_that_predates_it(
        hotel, monkeypatch):
    """A key invented after a deployment has been running reaches nobody: every account
    already has a `permissions` list and it predates the key. This is what reaches them."""
    people, db, handle = hotel
    # An older property, with a manager who was never given the key and no categories.
    run(handle.properties.insert_one({
        "id": "p2", "name": "The Old One", "status": LIVE, "property_type": "both",
        "created_at": "2025-01-01T00:00:00+00:00"}))
    run(handle.users.insert_one({
        "id": "u-old-mgr", "email": "old@x.example.com", "name": "Old", "role": "manager",
        "domains": ["hotel"], "permissions": ["hotel.front_desk"], "active": True,
        "property_id": "p2"}))
    run(handle.users.insert_one({
        "id": "u-old-waiter", "email": "w@x.example.com", "name": "W", "role": "waiter",
        "domains": ["bar"], "permissions": ["outlet.pos"], "active": True,
        "property_id": "p2"}))

    granted, held, seeded, current = run(backfill_planner())

    old = run(handle.users.find_one({"id": "u-old-mgr"}, {"_id": 0}))
    assert SCREEN in old["permissions"]
    # A waiter is not granted it, and does not need it: the read routes declare no key.
    waiter = run(handle.users.find_one({"id": "u-old-waiter"}, {"_id": 0}))
    assert SCREEN not in waiter["permissions"]
    assert granted >= 1
    # p2 had no categories and now has the defaults; p1 already had its own.
    assert seeded == 1 and current == 1
    p2 = PropertyScopedDatabase("p2")
    assert run(p2.calendar_categories.count_documents({})) == len(DEFAULT_CATEGORIES)


def test_the_migration_is_a_no_op_the_second_time(hotel):
    """A restart must not widen an account the owner narrowed, nor re-seed a property that
    renamed its own categories."""
    _people, _db, handle = hotel
    run(backfill_planner())
    granted, held, seeded, current = run(backfill_planner())
    assert granted == 0 and seeded == 0
    assert held >= 1 and current >= 1


def test_the_migration_leaves_an_account_with_no_permissions_field_alone(hotel):
    """That one belongs to `backfill_permissions`, which runs first at startup and derives
    the whole set from the role — including this key. Touching it here would grant it one
    screen and hide it from the migration whose job that is."""
    _people, _db, handle = hotel
    run(handle.users.insert_one({
        "id": "u-unmigrated", "email": "u@x.example.com", "name": "U", "role": "manager",
        "domains": ["hotel"], "active": True, "property_id": "p1"}))
    run(backfill_planner())
    row = run(handle.users.find_one({"id": "u-unmigrated"}, {"_id": 0}))
    assert "permissions" not in row
