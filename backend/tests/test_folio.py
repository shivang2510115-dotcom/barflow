"""Pure folio tests — no server, no database."""
import pytest
from services.folio import (
    ENTRY_DIRECTION, FolioError, direction_for, void_direction,
    folio_balance, nights_due, unposted_nights,
)


def e(kind, direction, amount, **kw):
    return {"id": kw.get("id", kind), "kind": kind, "direction": direction,
            "amount": amount, **kw}


def booking(status="checked_in", ci="2026-08-10", co="2026-08-14"):
    return {"status": status, "check_in": ci, "check_out": co}


def test_charges_are_debits_payments_are_credits():
    assert direction_for("room_night") == "debit"
    assert direction_for("outlet") == "debit"
    assert direction_for("misc_charge") == "debit"
    assert direction_for("payment") == "credit"
    assert direction_for("discount") == "credit"


def test_refund_is_a_debit_not_a_credit():
    # Handing money back increases what the guest owes again.
    assert direction_for("refund") == "debit"


def test_unknown_kind_raises():
    with pytest.raises(FolioError):
        direction_for("gratuity")


def test_void_reverses_direction():
    assert void_direction("debit") == "credit"
    assert void_direction("credit") == "debit"


def test_empty_ledger_is_zero():
    assert folio_balance([]) == 0.0


def test_balance_mixes_debits_and_credits():
    entries = [
        e("room_night", "debit", 5000.0),
        e("room_night", "debit", 5000.0),
        e("outlet", "debit", 1200.0),
        e("payment", "credit", 4000.0),
    ]
    assert folio_balance(entries) == 7200.0


def test_void_pair_cancels_to_zero():
    entries = [
        e("outlet", "debit", 1200.0, id="a"),
        e("void", "credit", 1200.0, ref_entry_id="a"),
    ]
    assert folio_balance(entries) == 0.0


def test_overpayment_gives_negative_balance():
    entries = [e("room_night", "debit", 5000.0), e("payment", "credit", 6000.0)]
    assert folio_balance(entries) == -1000.0


def test_refund_increases_balance():
    entries = [
        e("room_night", "debit", 5000.0),
        e("payment", "credit", 5000.0),
        e("refund", "debit", 2000.0),
    ]
    assert folio_balance(entries) == 2000.0


def test_entry_without_direction_raises():
    with pytest.raises(FolioError):
        folio_balance([{"id": "x", "kind": "outlet", "amount": 100.0}])


def test_no_nights_due_before_check_in():
    assert nights_due(booking(status="confirmed"), "2026-08-12") == []


def test_nights_due_mid_stay_excludes_today_onward():
    # Arrived the 10th, it is now the 12th: nights of the 10th and 11th have been slept.
    assert nights_due(booking(), "2026-08-12") == ["2026-08-10", "2026-08-11"]


def test_nights_due_on_departure_day_covers_whole_stay():
    assert nights_due(booking(), "2026-08-14") == [
        "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]


def test_nights_due_never_exceeds_check_out():
    # as_of well past departure must not invent extra nights
    assert nights_due(booking(), "2026-09-01") == [
        "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]


def test_early_departure_stops_posting():
    b = booking(status="checked_out", co="2026-08-12")
    assert nights_due(b, "2026-08-20") == ["2026-08-10", "2026-08-11"]


def test_unposted_nights_skips_already_posted():
    entries = [e("room_night", "debit", 5000.0, charge_date="2026-08-10")]
    assert unposted_nights(booking(), "2026-08-12", entries) == ["2026-08-11"]


def test_unposted_nights_is_empty_when_all_posted():
    entries = [
        e("room_night", "debit", 5000.0, charge_date="2026-08-10"),
        e("room_night", "debit", 5000.0, charge_date="2026-08-11"),
    ]
    assert unposted_nights(booking(), "2026-08-12", entries) == []
