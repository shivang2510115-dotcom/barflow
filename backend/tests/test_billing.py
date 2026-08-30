"""Turning a folio ledger into the document a guest is handed at checkout.

Pure: a list of entries in, a set of lines and totals out. No database, no HTTP. The
cases below are the ones that decide whether the guest and the hotel agree about what
is owed, which is the only thing a bill is for.
"""
from services.billing import bill_lines, next_number


def _e(kind, amount, description, **kw):
    return {"id": kw.pop("id", f"e{amount}{kind}"), "kind": kind, "amount": amount,
            "description": description, **kw}


def test_charges_and_payments_are_separated():
    entries = [
        _e("room_night", 5600, "Room night 2026-08-28"),
        _e("outlet", 1240, "Dinner"),
        _e("payment", 4000, "Cash"),
    ]
    b = bill_lines(entries)
    assert [l["description"] for l in b["charges"]] == ["Room night 2026-08-28", "Dinner"]
    assert [l["description"] for l in b["payments"]] == ["Cash"]
    assert b["charges_total"] == 6840.0
    assert b["paid_total"] == 4000.0
    assert b["balance"] == 2840.0


def test_a_voided_charge_leaves_the_bill_entirely():
    # Not shown struck through, and not shown as a pair that cancels: a guest reading
    # their bill should see what they owe, not the hotel's correction history. The
    # ledger keeps both; the document shows neither.
    charge = _e("room_night", 5600, "Room night", id="n1")
    entries = [charge, _e("void", 5600, "Void: Room night — wrong rate", ref_entry_id="n1")]
    b = bill_lines(entries)
    assert b["charges"] == []
    assert b["charges_total"] == 0.0


def test_a_discount_reduces_what_is_owed_and_is_shown():
    entries = [_e("room_night", 5000, "Room night"), _e("discount", 500, "Loyalty")]
    b = bill_lines(entries)
    assert b["balance"] == 4500.0
    # Shown, not silently netted off the room rate. A guest who was given something
    # should see that they were given it.
    assert any(l["description"] == "Loyalty" for l in b["payments"])


def test_a_refund_increases_what_is_owed_again():
    # A refund hands money back, so it undoes a payment rather than a charge.
    entries = [_e("payment", 5000, "Card"), _e("refund", 1000, "Card refund")]
    b = bill_lines(entries)
    assert b["paid_total"] == 4000.0
    assert b["balance"] == -4000.0


def test_room_nights_are_grouped_and_counted():
    entries = [_e("room_night", 5600, "Room night 2026-08-28", charge_date="2026-08-28"),
               _e("room_night", 5600, "Room night 2026-08-29", charge_date="2026-08-29"),
               _e("outlet", 400, "Coffee")]
    b = bill_lines(entries)
    assert b["nights"] == 2
    assert b["room_total"] == 11200.0
    # Everything that is not a room night is an extra, whichever outlet sold it.
    assert b["extras_total"] == 400.0


def test_an_empty_folio_produces_a_bill_of_zero_rather_than_an_error():
    b = bill_lines([])
    assert b["charges"] == [] and b["payments"] == []
    assert b["charges_total"] == b["paid_total"] == b["balance"] == 0.0
    assert b["nights"] == 0


def test_the_number_sequence_is_gapless_and_per_property():
    # A tax document cannot explain a missing number by pointing at a race condition.
    assert next_number([], "2026-27") == "2026-27/0001"
    assert next_number(["2026-27/0001"], "2026-27") == "2026-27/0002"
    # The sequence restarts with the financial year, which is what Indian numbering
    # expects, and the year is part of the string so the two never collide.
    assert next_number(["2025-26/0009"], "2026-27") == "2026-27/0001"


def test_the_sequence_ignores_a_number_it_did_not_issue():
    # A hand-edited or imported value must not be able to derail the count.
    assert next_number(["2026-27/0001", "nonsense", ""], "2026-27") == "2026-27/0002"


def test_the_financial_year_runs_april_to_march():
    from services.billing import financial_year
    assert financial_year("2026-08-30") == "2026-27"
    assert financial_year("2026-04-01") == "2026-27"
    # A date in the first quarter of the calendar year belongs to the year before.
    # Getting this wrong restarts the sequence three months early and duplicates every
    # number already issued in January, February and March.
    assert financial_year("2026-03-31") == "2025-26"
    assert financial_year("2026-02-14") == "2025-26"
