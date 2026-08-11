"""Hotel revenue attribution — pure, no database."""
import pytest

from models.folio import ENTRY_KINDS
from services.revenue import REVENUE_SIGN, entry_revenue_date, hotel_revenue


def e(kind, amount, *, id="e1", charge_date=None, posted_at="2026-03-05T10:00:00+00:00",
      ref_entry_id=None):
    return {"id": id, "kind": kind, "amount": amount, "charge_date": charge_date,
            "posted_at": posted_at, "ref_entry_id": ref_entry_id}


def test_room_nights_and_misc_charges_are_revenue():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("misc_charge", 300, id="e2")], "2026-03-01", "2026-03-31")
    assert r["total"] == 5300.0


def test_a_discount_reduces_revenue():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("discount", 500, id="e2")], "2026-03-01", "2026-03-31")
    assert r["total"] == 4500.0


def test_an_outlet_charge_is_not_hotel_revenue():
    # The bar already recognised this when the order settled. Counting it again here
    # would report the same rupee twice across the combined view.
    r = hotel_revenue([e("outlet", 800)], "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_a_payment_is_not_revenue():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("payment", 5000, id="e2")], "2026-03-01", "2026-03-31")
    assert r["total"] == 5000.0


def test_a_refund_is_not_revenue():
    r = hotel_revenue([e("refund", 900)], "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_a_voided_night_is_excluded_not_negated():
    entries = [
        e("room_night", 5000, id="n1", charge_date="2026-03-05"),
        e("void", 5000, id="v1", ref_entry_id="n1", posted_at="2026-03-09T10:00:00+00:00"),
    ]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0
    # and it leaves nothing behind on either day
    assert all(d["revenue"] == 0.0 for d in r["by_day"])


def test_a_night_is_counted_on_the_night_it_covers_not_when_posted():
    # Three nights posted in one go at check-out must still land on three days.
    entries = [e("room_night", 1000, id=f"n{i}", charge_date=f"2026-03-0{i}",
                 posted_at="2026-03-04T09:00:00+00:00") for i in (1, 2, 3)]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    by = {d["date"]: d["revenue"] for d in r["by_day"]}
    assert by["2026-03-01"] == 1000.0
    assert by["2026-03-02"] == 1000.0
    assert by["2026-03-03"] == 1000.0
    assert by["2026-03-04"] == 0.0


def test_entries_outside_the_range_are_ignored():
    entries = [e("room_night", 1000, id="a", charge_date="2026-02-28"),
               e("room_night", 1000, id="b", charge_date="2026-03-01"),
               e("room_night", 1000, id="c", charge_date="2026-03-31"),
               e("room_night", 1000, id="d", charge_date="2026-04-01")]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    assert r["total"] == 2000.0


def test_the_range_is_inclusive_of_both_ends():
    # Unlike a stay, a report range names the days the user picked. Both are included.
    r = hotel_revenue([e("room_night", 100, charge_date="2026-03-01")],
                      "2026-03-01", "2026-03-01")
    assert r["total"] == 100.0


def test_by_day_covers_every_day_in_range_including_empty_ones():
    r = hotel_revenue([], "2026-03-01", "2026-03-03")
    assert [d["date"] for d in r["by_day"]] == ["2026-03-01", "2026-03-02", "2026-03-03"]
    assert all(d["revenue"] == 0.0 for d in r["by_day"])


def test_room_nights_are_reported_separately_from_extras():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("misc_charge", 300, id="e2")], "2026-03-01", "2026-03-31")
    assert r["room_nights"] == 5000.0
    assert r["extras"] == 300.0


def test_an_entry_with_no_usable_date_is_ignored_not_crashed_on():
    # Legacy rows predate charge_date. Losing one from a report beats a 500 on the
    # whole analytics screen.
    assert entry_revenue_date({"kind": "misc_charge", "posted_at": None}) is None
    r = hotel_revenue([{"id": "x", "kind": "misc_charge", "amount": 100,
                        "posted_at": None}], "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_a_posted_entry_lands_on_the_propertys_local_day_not_the_utc_one():
    """19:30 UTC is 01:00 the next morning at the property (Asia/Kolkata, UTC+5:30).

    A bar bill or a late charge recorded between 00:00 and 05:29:59 local time has a
    UTC date of the day before. Slicing the timestamp reports the whole late session on
    the wrong day, and pushes the first hours of the 1st into last month's figures.
    """
    assert entry_revenue_date({"kind": "misc_charge",
                               "posted_at": "2026-03-05T19:30:00+00:00"}) == "2026-03-06"

    r = hotel_revenue([e("misc_charge", 400, posted_at="2026-03-05T19:30:00+00:00")],
                      "2026-03-01", "2026-03-31")
    by = {d["date"]: d["revenue"] for d in r["by_day"]}
    assert by["2026-03-06"] == 400.0
    assert by["2026-03-05"] == 0.0


def test_a_charge_date_is_a_local_date_and_is_never_shifted():
    """The counterpart: charge_date is already the calendar night, not an instant.
    Running it through the timezone conversion as well would move it."""
    assert entry_revenue_date({"kind": "room_night", "charge_date": "2026-03-05",
                               "posted_at": "2026-03-08T19:30:00+00:00"}) == "2026-03-05"


def test_every_kind_revenue_sign_names_is_a_real_entry_kind():
    """A typo, or a kind renamed in models/folio.py, would otherwise be worth zero in
    silence. revenue.py refuses to import at all if this is broken; assert it here too
    so the reason is named."""
    assert set(REVENUE_SIGN) <= set(ENTRY_KINDS)


def test_the_full_set_of_entry_kinds_is_pinned():
    """Adding a kind to models/folio.py must fail here until someone decides how it is
    treated: revenue (add it to REVENUE_SIGN) or not (say so, and update this list).
    Silently worth zero is not a decision."""
    assert set(ENTRY_KINDS) == {
        "room_night", "outlet", "misc_charge", "payment", "refund", "discount", "void",
    }
    # And the standing decision for each one that is deliberately not revenue.
    assert set(ENTRY_KINDS) - set(REVENUE_SIGN) == {"outlet", "payment", "refund", "void"}


def test_a_missing_amount_is_treated_as_zero():
    r = hotel_revenue([{"id": "x", "kind": "room_night", "charge_date": "2026-03-05"}],
                      "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_start_after_end_raises():
    with pytest.raises(ValueError):
        hotel_revenue([], "2026-03-31", "2026-03-01")


def test_mixed_ledger_totals_extras_minus_discount_only():
    # One list with a bit of everything: a room night, a misc charge, an outlet charge,
    # a payment, a discount, and a void of the room night. The room night is cancelled
    # out by its void, the outlet charge never counted in the first place, and payments
    # never counted either — so what's left is exactly misc_charge - discount.
    entries = [
        e("room_night", 5000, id="n1", charge_date="2026-03-05"),
        e("misc_charge", 300, id="m1"),
        e("outlet", 800, id="o1"),
        e("payment", 5300, id="p1"),
        e("discount", 100, id="d1"),
        e("void", 5000, id="v1", ref_entry_id="n1", posted_at="2026-03-06T10:00:00+00:00"),
    ]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    assert r["total"] == 300.0 - 100.0
