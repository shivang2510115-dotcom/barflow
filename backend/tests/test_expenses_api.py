"""Expenses through the routers: recording, the categories, the boundary, and the report
that sets what was spent against what the analytics endpoint says was earned.

No server. The endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, so both are called as what they are — the same style as test_isolation.py,
test_tenancy.py and test_housekeeping_api.py. The authorization dependencies are called
directly too, because `require_access(...)` returns a checker that takes the user: that
is the only way to assert from in here that somebody without the screen key is refused,
and the declaration on the route is the thing worth asserting about.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import db as db_module
import routers.analytics as analytics
import routers.expenses as expenses
import security
from mock_db import MockDatabase
from scoped_db import PropertyScopedDatabase, tenant_db
from services.access import DOMAINS, LIVE, SCREEN_KEYS
from services.clock import LOCAL_TZ
from services.expenses import DEFAULT_CATEGORIES, seed_expense_categories

SCREEN = "admin.expenses"


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


_UNSCOPED_HOLDERS = (db_module, security)


@pytest.fixture
def hotel(tmp_path, monkeypatch):
    """One live property with the default categories seeded, and one of each interesting
    account: an admin, a manager, a receptionist who has been ticked for the screen, and a
    waiter who has not."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)

    run(handle.properties.insert_one({
        "id": "p1", "name": "The Grand", "status": LIVE, "property_type": "both",
        "created_at": "2026-01-01T00:00:00+00:00"}))

    people = {}
    for role, domains, permissions in (
            ("admin", DOMAINS, SCREEN_KEYS),
            ("manager", DOMAINS, (SCREEN, "admin.analytics")),
            # Ticked for the screen but holding no management role — the "everyone who
            # has access" case the read endpoints name no role for.
            ("front_desk", ("hotel",), (SCREEN, "hotel.front_desk")),
            # Not ticked. Holds a domain and a job, and must still be refused.
            ("waiter", ("bar",), ("outlet.pos", "outlet.kot")),
    ):
        person = {"id": f"u-{role}", "email": f"{role}@grand.example.com", "name": role,
                  "role": role, "domains": list(domains),
                  "permissions": list(permissions), "active": True, "property_id": "p1"}
        run(handle.users.insert_one(person))
        people[role] = person

    db = run(tenant_db(people["admin"]))
    run(seed_expense_categories(db))
    categories = {c["name"]: c["id"]
                  for c in run(db.expense_categories.find({}, {"_id": 0}).to_list(50))}
    return {"db": db, "people": people, "categories": categories, "handle": handle}


def record(hotel, amount, category, day, **kw):
    return call(expenses.record_expense,
                payload=expenses.ExpenseIn(amount=amount,
                                           category_id=hotel["categories"][category],
                                           spent_on=day, **kw),
                user=hotel["people"]["manager"], db=hotel["db"])


# ------------------------------------ the boundary ------------------------------------
def test_a_user_without_the_screen_key_is_refused(hotel):
    """The waiter holds a domain, an active account and a live property. The only thing
    they do not have is the tick, and the tick is the whole decision."""
    for dependency in (expenses.READ, expenses.RECORD):
        assert refused(dependency, user=hotel["people"]["waiter"]).status_code == 403


def test_reading_is_whoever_holds_the_key_whatever_their_role(hotel):
    # The owner's brief was "everyone who has access", so the read endpoints name no
    # role: a receptionist ticked for the screen reads it, and nothing else had to change.
    assert call(expenses.READ, user=hotel["people"]["front_desk"])["id"] == "u-front_desk"


def test_recording_is_admin_and_manager_only(hotel):
    for role in ("admin", "manager"):
        assert call(expenses.RECORD, user=hotel["people"][role])["role"] == role
    # Ticked for the screen, so they read it — but entering the property's bills is not
    # the front desk's job unless somebody makes them a manager.
    assert refused(expenses.RECORD, user=hotel["people"]["front_desk"]).status_code == 403


def test_naming_the_categories_is_admin_only(hotel):
    assert call(expenses.CONFIG, user=hotel["people"]["admin"])["role"] == "admin"
    assert refused(expenses.CONFIG, user=hotel["people"]["manager"]).status_code == 403


def test_the_screen_key_is_in_the_catalogue_so_it_can_be_ticked(hotel):
    from routers.permissions import CATALOGUE
    entry = next(c for c in CATALOGUE if c["key"] == SCREEN)
    assert entry["label"] == "Expenses" and entry["section"] == "Admin"
    assert entry["domains"] == list(DOMAINS)


# ------------------------------------ categories ------------------------------------
def test_a_new_property_starts_with_the_indian_hospitality_defaults(hotel):
    rows = call(expenses.list_expense_categories, include_inactive=False,
                user=hotel["people"]["manager"], db=hotel["db"])
    assert {r["name"] for r in rows} == set(DEFAULT_CATEGORIES)
    # Name order, so the picker is not in insertion order.
    assert [r["name"] for r in rows] == sorted(DEFAULT_CATEGORIES)


def test_seeding_twice_does_not_double_the_list(hotel):
    assert run(seed_expense_categories(hotel["db"])) == 0
    assert run(hotel["db"].expense_categories.count_documents({})) == len(DEFAULT_CATEGORIES)


def test_the_property_can_name_its_own_category(hotel):
    made = call(expenses.create_expense_category,
                payload=expenses.CategoryIn(name="  Diesel for the   genset "),
                user=hotel["people"]["admin"], db=hotel["db"])
    assert made["name"] == "Diesel for the genset"


def test_a_second_category_of_the_same_name_is_refused(hotel):
    refusal = refused(expenses.create_expense_category,
                      payload=expenses.CategoryIn(name="utilities"),
                      user=hotel["people"]["admin"], db=hotel["db"])
    assert refusal.status_code == 409
    assert "Utilities" in str(refusal.detail)


def test_renaming_a_category_renames_it_on_last_months_report_too(hotel):
    record(hotel, 900, "Utilities", "2026-03-04")
    call(expenses.update_expense_category,
         category_id=hotel["categories"]["Utilities"],
         payload=expenses.CategoryIn(name="Power and water"),
         user=hotel["people"]["admin"], db=hotel["db"])
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["expenses"]["by_category"][0]["name"] == "Power and water"


def test_a_category_with_money_against_it_cannot_be_deleted(hotel):
    record(hotel, 900, "Supplies", "2026-03-04")
    refusal = refused(expenses.delete_expense_category,
                      category_id=hotel["categories"]["Supplies"],
                      user=hotel["people"]["admin"], db=hotel["db"])
    assert refusal.status_code == 409
    assert refusal.detail["expenses"] == 1
    assert "Retire it instead" in refusal.detail["message"]


def test_an_unused_category_can_be_deleted(hotel):
    out = call(expenses.delete_expense_category, category_id=hotel["categories"]["Rent"],
               user=hotel["people"]["admin"], db=hotel["db"])
    assert out["deleted"] == hotel["categories"]["Rent"]


def test_a_retired_category_leaves_the_picker_but_keeps_naming_its_money(hotel):
    record(hotel, 500, "Marketing", "2026-03-04")
    call(expenses.update_expense_category, category_id=hotel["categories"]["Marketing"],
         payload=expenses.CategoryIn(name="Marketing", active=False),
         user=hotel["people"]["admin"], db=hotel["db"])

    offered = call(expenses.list_expense_categories, include_inactive=False,
                   user=hotel["people"]["manager"], db=hotel["db"])
    assert "Marketing" not in {r["name"] for r in offered}
    # ...and nothing new can be filed under it.
    assert refused(expenses.record_expense, payload=expenses.ExpenseIn(
        amount=1, category_id=hotel["categories"]["Marketing"], spent_on="2026-03-05"),
        user=hotel["people"]["manager"], db=hotel["db"]).status_code == 400
    # ...but the ₹500 already spent is still labelled.
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["expenses"]["by_category"][0]["name"] == "Marketing"


def test_a_category_from_another_property_does_not_exist_here(hotel):
    other = PropertyScopedDatabase("p2")
    run(other.expense_categories.insert_one({"id": "theirs", "name": "Theirs",
                                             "active": True}))
    assert refused(expenses.record_expense, payload=expenses.ExpenseIn(
        amount=100, category_id="theirs", spent_on="2026-03-05"),
        user=hotel["people"]["admin"], db=hotel["db"]).status_code == 404


# ------------------------------------ recording ------------------------------------
def test_an_expense_carries_everything_the_property_needs_to_recognise_it(hotel):
    made = record(hotel, 12500.5, "Salaries and Wages", "2026-03-04",
                  description="March advance", payment_method="bank_transfer",
                  payee="Ramesh Kumar", reference="ADV-2026-03-11")
    assert made["amount"] == 12500.5
    assert made["spent_on"] == "2026-03-04"
    assert made["description"] == "March advance"
    assert made["payment_method"] == "bank_transfer"
    assert made["payee"] == "Ramesh Kumar"
    assert made["reference"] == "ADV-2026-03-11"
    assert made["category_name"] == "Salaries and Wages"
    # Who recorded it, by id and by the name the book has to read under later.
    assert made["recorded_by"] == "u-manager" and made["recorded_by_name"] == "manager"
    assert made["voided_at"] is None
    # And it is this property's.
    assert run(hotel["db"].expenses.find_one({"id": made["id"]}))["property_id"] == "p1"


def test_an_expense_recorded_at_1am_lands_on_the_day_the_property_calls_it(hotel,
                                                                           monkeypatch):
    """The whole of the date trap, through the router.

    At 01:00 local time the UTC date is still yesterday's. A server that defaulted the
    date from `datetime.utcnow()` — or that sliced its own `recorded_at` — would file the
    night's bills under the day before, every night, and push the first five and a half
    hours of the 1st of a month into last month's figures.
    """
    one_am = datetime(2026, 3, 6, 1, 0, tzinfo=LOCAL_TZ)
    assert one_am.astimezone(timezone.utc).date().isoformat() == "2026-03-05"

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return one_am.astimezone(tz) if tz else one_am

    monkeypatch.setattr("services.clock.datetime", FrozenClock)

    made = call(expenses.record_expense,
                payload=expenses.ExpenseIn(
                    amount=800, category_id=hotel["categories"]["Supplies"],
                    description="Late milk delivery"),
                user=hotel["people"]["manager"], db=hotel["db"])
    assert made["spent_on"] == "2026-03-06"

    # ...and the report agrees: the money is on the 6th, and the 5th is empty.
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    by = {d["date"]: d["expenditure"] for d in report["by_day"]}
    assert by["2026-03-06"] == 800.0 and by["2026-03-05"] == 0.0


def test_an_expense_of_nothing_or_less_is_refused(hotel):
    for amount in (0, -500):
        assert refused(expenses.record_expense, payload=expenses.ExpenseIn(
            amount=amount, category_id=hotel["categories"]["Supplies"],
            spent_on="2026-03-05"),
            user=hotel["people"]["manager"], db=hotel["db"]).status_code == 400


def test_a_date_that_is_not_a_date_is_refused(hotel):
    assert refused(expenses.record_expense, payload=expenses.ExpenseIn(
        amount=100, category_id=hotel["categories"]["Supplies"], spent_on="last tuesday"),
        user=hotel["people"]["manager"], db=hotel["db"]).status_code == 400


# ------------------------------------ reversals ------------------------------------
def test_an_expense_cannot_be_edited_or_deleted_only_reversed(hotel):
    """The decision, asserted rather than only written down: this router exposes no PUT
    and no DELETE for an expense. Money is append-only here as it is on a folio."""
    paths = {(m, r.path) for r in expenses.router.routes for m in r.methods
             if r.path.startswith("/expenses")}
    assert not [p for p in paths if p[0] in ("PUT", "PATCH", "DELETE")]
    assert ("POST", "/expenses/{expense_id}/void") in paths


def test_a_reversal_leaves_the_original_readable_and_takes_it_out_of_the_total(hotel):
    wrong = record(hotel, 45000, "Salaries and Wages", "2026-03-04",
                   description="Typo: meant 4500")
    record(hotel, 4500, "Salaries and Wages", "2026-03-04", description="March wages")

    reversed_ = call(expenses.void_expense, expense_id=wrong["id"],
                     payload=expenses.VoidIn(reason="Amount typed with an extra zero"),
                     user=hotel["people"]["admin"], db=hotel["db"])
    assert reversed_["voided_by_name"] == "admin"
    assert reversed_["void_reason"] == "Amount typed with an extra zero"
    # Nothing about what was spent has been rewritten.
    assert reversed_["amount"] == 45000 and reversed_["spent_on"] == "2026-03-04"

    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["expenses"]["total"] == 4500.0
    # ...and both rows are still on the transactions list, so the correction is visible.
    rows = call(expenses.list_expenses, start=None, end=None, category_id=None,
                payment_method=None, q=None, include_voided=True, sort="date",
                direction=-1, user=hotel["people"]["manager"], db=hotel["db"])
    assert len(rows) == 2
    assert {r["amount"] for r in rows} == {45000.0, 4500.0}


def test_reversing_twice_is_refused_rather_than_silently_accepted(hotel):
    made = record(hotel, 100, "Supplies", "2026-03-04")
    call(expenses.void_expense, expense_id=made["id"], payload=expenses.VoidIn(),
         user=hotel["people"]["admin"], db=hotel["db"])
    assert refused(expenses.void_expense, expense_id=made["id"],
                   payload=expenses.VoidIn(), user=hotel["people"]["admin"],
                   db=hotel["db"]).status_code == 409


def test_reversing_something_that_is_not_this_propertys_is_a_404(hotel):
    other = PropertyScopedDatabase("p2")
    run(other.expenses.insert_one({"id": "theirs", "amount": 999.0, "spent_on": "2026-03-04",
                                   "category_id": "x", "voided_at": None}))
    assert refused(expenses.void_expense, expense_id="theirs", payload=expenses.VoidIn(),
                   user=hotel["people"]["admin"], db=hotel["db"]).status_code == 404
    assert run(other.expenses.find_one({"id": "theirs"}))["voided_at"] is None


# --------------------------------- the transactions ---------------------------------
def _list(hotel, **kw):
    params = {"start": None, "end": None, "category_id": None, "payment_method": None,
              "q": None, "include_voided": True, "sort": "date", "direction": -1}
    params.update(kw)
    return call(expenses.list_expenses, user=hotel["people"]["front_desk"],
                db=hotel["db"], **params)


def test_the_transaction_list_is_filterable_and_sorted(hotel):
    record(hotel, 300, "Supplies", "2026-03-02", payment_method="cash",
           description="Vegetables", payee="Anand Traders")
    record(hotel, 9000, "Utilities", "2026-03-05", payment_method="bank_transfer",
           description="Electricity", reference="TSSPDCL-8891")
    record(hotel, 1200, "Supplies", "2026-03-09", payment_method="upi",
           description="Cleaning cloths")

    assert [r["spent_on"] for r in _list(hotel)] == ["2026-03-09", "2026-03-05", "2026-03-02"]
    assert [r["amount"] for r in _list(hotel, sort="amount", direction=-1)] == [9000, 1200, 300]
    assert len(_list(hotel, start="2026-03-03", end="2026-03-06")) == 1
    assert {r["amount"] for r in _list(hotel, category_id=hotel["categories"]["Supplies"])} == {300, 1200}
    assert len(_list(hotel, payment_method="upi")) == 1
    # Free text reaches the description, the payee and the bill number alike.
    assert _list(hotel, q="anand tra")[0]["payee"] == "Anand Traders"
    assert _list(hotel, q="TSSPDCL")[0]["reference"] == "TSSPDCL-8891"


def test_every_row_carries_the_category_name_it_goes_by_now(hotel):
    record(hotel, 300, "Supplies", "2026-03-02")
    assert _list(hotel)[0]["category_name"] == "Supplies"


def test_a_backwards_range_is_refused_rather_than_answered_emptily(hotel):
    assert refused(expenses.list_expenses, start="2026-03-31", end="2026-03-01",
                   category_id=None, payment_method=None, q=None, include_voided=True,
                   sort="date", direction=-1, user=hotel["people"]["manager"],
                   db=hotel["db"]).status_code == 400
    assert refused(expenses.expenses_report, start="2026-03-31", end="2026-03-01",
                   user=hotel["people"]["manager"], db=hotel["db"]).status_code == 400


# ------------------------------------ the report ------------------------------------
def _earn(hotel, amount, day, order_id):
    """One settled outlet order. Timed at noon local so the day is unambiguous under any
    reading, which keeps this helper out of the timezone argument the tests above make."""
    settled = datetime(*[int(p) for p in day.split("-")], 12, 0,
                       tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()
    run(hotel["db"].orders.insert_one({
        "id": order_id, "status": "settled", "total": amount, "items": [],
        "settled_at": settled, "payment_method": "cash"}))


def test_the_report_sums_its_own_breakdown_to_its_own_total(hotel):
    record(hotel, 45000, "Salaries and Wages", "2026-03-03")
    record(hotel, 9812.4, "Utilities", "2026-03-07")
    record(hotel, 3187.65, "Supplies", "2026-03-11")

    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    spent = report["expenses"]
    assert spent["total"] == 58000.05
    assert sum(c["amount"] for c in spent["by_category"]) == spent["total"]
    assert sum(d["amount"] for d in spent["by_day"]) == spent["total"]
    assert round(sum(c["share"] for c in spent["by_category"])) == 100
    assert report["totals"]["spent"] == spent["total"]


def test_the_report_excludes_what_falls_outside_the_range(hotel):
    record(hotel, 1000, "Supplies", "2026-02-28")
    record(hotel, 1000, "Supplies", "2026-03-01")
    record(hotel, 1000, "Supplies", "2026-03-31")
    record(hotel, 1000, "Supplies", "2026-04-01")
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["expenses"]["total"] == 2000.0
    assert report["expenses"]["count"] == 2


def test_the_reports_revenue_is_the_analytics_endpoints_answer_to_the_paise(hotel):
    """The one number this feature must not invent a second version of.

    `routers/analytics.py` already knows that an `outlet` folio entry mirrors an order
    that was recognised at the till, and drops it so a bar bill charged to a room is not
    counted twice. The combined report calls that endpoint rather than re-deriving any of
    it, and this asserts the two agree — total, per day, and per block.
    """
    _earn(hotel, 12345.67, "2026-03-02", "o1")
    _earn(hotel, 8000.33, "2026-03-05", "o2")
    # A room night and a bar bill charged to the room: the double-count the analytics
    # endpoint exists to avoid. The `outlet` entry must not add to hotel revenue.
    run(hotel["db"].folios.insert_one({"id": "f1", "status": "closed", "balance": 0.0}))
    run(hotel["db"].folio_entries.insert_many([
        {"id": "n1", "folio_id": "f1", "kind": "room_night", "amount": 5000.0,
         "charge_date": "2026-03-02", "posted_at": "2026-03-02T06:30:00+00:00"},
        {"id": "o1e", "folio_id": "f1", "kind": "outlet", "amount": 12345.67,
         "charge_date": None, "posted_at": "2026-03-02T06:30:00+00:00"}]))

    record(hotel, 4000, "Supplies", "2026-03-02")

    mine = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                user=hotel["people"]["manager"], db=hotel["db"])
    theirs = call(analytics.revenue, start="2026-03-01", end="2026-03-31", domains=None,
                  user=hotel["people"]["manager"], db=hotel["db"])

    assert mine["revenue"] == theirs
    assert mine["totals"]["earned"] == theirs["total"] == 25346.0
    assert mine["revenue"]["hotel"]["total"] == 5000.0  # not 17345.67
    assert [d["income"] for d in mine["by_day"]] == [d["total"] for d in theirs["by_day"]]


def test_what_is_left_is_what_was_earned_minus_what_was_spent(hotel):
    _earn(hotel, 100000, "2026-03-02", "o1")
    record(hotel, 45000, "Salaries and Wages", "2026-03-03")
    record(hotel, 9812.4, "Utilities", "2026-03-07")
    record(hotel, 3187.65, "Supplies", "2026-03-11")

    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    totals = report["totals"]
    assert totals["earned"] == 100000.0
    assert totals["spent"] == 58000.05
    assert totals["net"] == 41999.95
    assert round(totals["earned"] - totals["spent"], 2) == totals["net"]


def test_the_daily_series_carries_both_sides_against_the_same_day(hotel):
    _earn(hotel, 9000, "2026-03-02", "o1")
    record(hotel, 2000, "Supplies", "2026-03-02")
    record(hotel, 500, "Utilities", "2026-03-03")
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-03",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["by_day"] == [
        {"date": "2026-03-01", "income": 0.0, "expenditure": 0.0, "net": 0.0},
        {"date": "2026-03-02", "income": 9000.0, "expenditure": 2000.0, "net": 7000.0},
        {"date": "2026-03-03", "income": 0.0, "expenditure": 500.0, "net": -500.0}]


def test_no_currency_symbol_reaches_the_client_from_here(hotel):
    """Money leaves this router as a number. Every symbol on the screen comes from
    `currency()` in the frontend, which does Indian digit grouping — a `$` has reached a
    screen in this codebase twice, and both times it was a symbol written somewhere that
    should have sent a figure."""
    _earn(hotel, 9000, "2026-03-02", "o1")
    record(hotel, 2000, "Supplies", "2026-03-02")
    body = str(call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                    user=hotel["people"]["manager"], db=hotel["db"]))
    assert "$" not in body and "₹" not in body and "Rs" not in body


def test_the_report_reads_only_this_propertys_spending(hotel):
    other = PropertyScopedDatabase("p2")
    run(other.expenses.insert_one({
        "id": "theirs", "amount": 999999.0, "category_id": "x", "spent_on": "2026-03-04",
        "recorded_at": "2026-03-04T10:00:00+00:00", "voided_at": None}))
    record(hotel, 50, "Supplies", "2026-03-04")
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-31",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["expenses"]["total"] == 50.0
    assert "999999" not in str(report)


def test_a_report_over_a_quiet_month_is_zeroes_rather_than_a_crash(hotel):
    report = call(expenses.expenses_report, start="2026-03-01", end="2026-03-03",
                  user=hotel["people"]["manager"], db=hotel["db"])
    assert report["totals"] == {"earned": 0.0, "spent": 0.0, "net": 0.0}
    assert report["expenses"]["by_category"] == []
    assert len(report["by_day"]) == 3


# ------------------------------------ the migration ------------------------------------
def test_the_migration_grants_the_screen_to_the_audience_analytics_already_had(hotel,
                                                                               monkeypatch):
    import migrations.backfill_expenses as migration
    monkeypatch.setattr(migration, "_db_module", db_module)

    handle = hotel["handle"]
    # Take the key away from everyone, as it was before the feature shipped.
    for person in hotel["people"].values():
        run(handle.users.update_one({"id": person["id"]}, {"$set": {"permissions": [
            k for k in person["permissions"] if k != SCREEN]}}))
    # And an account the earlier backfill has not reached at all.
    run(handle.users.insert_one({"id": "u-new", "role": "manager", "domains": ["hotel"],
                                 "active": True, "property_id": "p1"}))

    granted, held, seeded, current = run(migration.backfill())
    assert (granted, held) == (2, 0)

    held_now = {u["id"]: (u.get("permissions") or []) for u in
                run(handle.users.find({}, {"_id": 0}).to_list(50))}
    assert SCREEN in held_now["u-admin"] and SCREEN in held_now["u-manager"]
    assert SCREEN not in held_now["u-front_desk"] and SCREEN not in held_now["u-waiter"]
    # The one with no `permissions` key at all belongs to `backfill_permissions`, which
    # derives the whole set from the role. Granting it one screen here would hide it.
    assert held_now["u-new"] == []

    # Idempotent: a second run grants nothing and seeds nothing.
    assert run(migration.backfill()) == (0, 2, 0, seeded + current)


def test_the_migration_gives_a_property_that_predates_the_feature_its_categories(hotel,
                                                                                 monkeypatch):
    import migrations.backfill_expenses as migration
    monkeypatch.setattr(migration, "_db_module", db_module)

    run(hotel["handle"].properties.insert_one({
        "id": "p2", "name": "The Regent", "status": LIVE, "property_type": "outlet",
        "created_at": "2026-02-01T00:00:00+00:00"}))
    _granted, _held, seeded, current = run(migration.backfill())
    assert (seeded, current) == (1, 1)  # p2 seeded, p1 already had them
    theirs = run(PropertyScopedDatabase("p2").expense_categories.find({}, {"_id": 0})
                 .to_list(50))
    assert {c["name"] for c in theirs} == set(DEFAULT_CATEGORIES)
    # Each property's own rows, not a shared table.
    assert all(c["property_id"] == "p2" for c in theirs)


def test_signup_gives_a_brand_new_property_its_categories(hotel):
    # The seed runs at the moment the tenant comes into existence, so an owner never
    # reaches an empty picker on their first bill. Asserted through the service the
    # signup route calls, with a property that has never had one.
    fresh = PropertyScopedDatabase("p3")
    assert run(seed_expense_categories(fresh)) == len(DEFAULT_CATEGORIES)
    assert run(fresh.expense_categories.count_documents({})) == len(DEFAULT_CATEGORIES)
