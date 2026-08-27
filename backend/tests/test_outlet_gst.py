"""Outlet GST: the arithmetic on a restaurant bill, with no database and no server.

Written before `services/tax.py` existed. The two branches are not variations on one
sum — an exclusive rate is *added* to the menu price and an inclusive one is *extracted*
from it — and getting the second wrong overcharges every guest by the tax on the tax,
silently, on a figure they have already agreed to pay.

The figures below are the ones from the design: a ₹100 dish at 5% bills ₹105 exclusive
and ₹100 inclusive, with ₹4.76 of tax inside it.
"""
import pytest

from services.tax import (
    DEFAULT_OUTLET_GST_RATE, MAX_GST_RATE, TaxRateError, normalise_rate,
    outlet_gst_settings, outlet_totals, split_tax,
)


# ------------------------------ the rate itself ------------------------------
def test_the_default_is_five_percent_not_ten():
    """The bug this whole piece exists to fix. 10% is not an Indian GST rate."""
    assert DEFAULT_OUTLET_GST_RATE == 5.0


@pytest.mark.parametrize("given, expected", [
    (5, 5.0), ("5", 5.0), ("5.0", 5.0), (0, 0.0), (18.0, 18.0), (2.5, 2.5),
])
def test_a_rate_is_read_as_a_number(given, expected):
    assert normalise_rate(given) == expected


@pytest.mark.parametrize("bad", [-1, -0.01, MAX_GST_RATE + 0.01, 100, "five", None, ""])
def test_a_rate_that_cannot_be_charged_is_refused(bad):
    """A raise rather than a fallback. Every silent default here bills somebody the
    wrong tax, and the wrong tax on a printed bill is not something a later release
    can put right."""
    with pytest.raises(TaxRateError):
        normalise_rate(bad)


# --------------------------- exclusive: tax on top ---------------------------
def test_a_hundred_rupee_dish_at_five_percent_exclusive_bills_one_hundred_and_five():
    out = outlet_totals(100.0, 5.0, inclusive=False)
    assert out["taxable_value"] == 100.00
    assert out["tax"] == 5.00
    assert out["total"] == 105.00


def test_exclusive_rounds_at_the_paise():
    # 33.33 * 5% = 1.6665, which is 1.67 and not 1.66: a bill is printed in paise.
    out = outlet_totals(33.33, 5.0, inclusive=False)
    assert out["tax"] == 1.67
    assert out["total"] == 35.00


# ------------------------- inclusive: tax taken out --------------------------
def test_a_hundred_rupee_dish_at_five_percent_inclusive_bills_one_hundred():
    """The printed menu price is what the guest pays. Adding 5% on top of a price that
    already contains it would take ₹105 for a dish the card says is ₹100."""
    out = outlet_totals(100.0, 5.0, inclusive=True)
    assert out["taxable_value"] == 95.24
    assert out["tax"] == 4.76
    assert out["total"] == 100.00


def test_an_inclusive_bill_always_adds_back_up_to_the_menu_price():
    """The property that makes the extraction right: whatever the rounding does, the two
    halves shown on the bill have to equal the number at the bottom of it."""
    for price in (99.0, 149.0, 275.50, 1_249.99, 7.5, 0.99):
        for rate in (0.0, 5.0, 12.0, 18.0, 28.0):
            out = outlet_totals(price, rate, inclusive=True)
            assert round(out["taxable_value"] + out["tax"], 2) == out["total"], (price, rate)


def test_inclusive_at_eighteen_percent():
    out = outlet_totals(149.0, 18.0, inclusive=True)
    assert out["taxable_value"] == 126.27
    assert out["tax"] == 22.73
    assert out["total"] == 149.00


# ------------------------------ a zero rate ---------------------------------
@pytest.mark.parametrize("inclusive", [False, True])
def test_an_unregistered_business_charges_no_tax_either_way(inclusive):
    """A business under the registration threshold charges 0%, and then the two branches
    have to agree: there is no tax to add and none to take out."""
    out = outlet_totals(240.0, 0.0, inclusive=inclusive)
    assert out["tax"] == 0.0
    assert out["taxable_value"] == 240.0
    assert out["total"] == 240.0


# ------------------------------- the discount -------------------------------
def test_a_discount_comes_off_the_total():
    """Unchanged from what this POS has always done — the discount is taken off the
    figure at the bottom, not off the taxable value. Stated by a test rather than left
    to be inferred, because the alternative reading (discount the taxable value, then
    tax it) produces a different number and somebody will one day assume it."""
    assert outlet_totals(100.0, 5.0, inclusive=False, discount=10.0)["total"] == 95.00
    assert outlet_totals(100.0, 5.0, inclusive=True, discount=10.0)["total"] == 90.00


def test_the_rate_it_was_billed_at_comes_back_with_the_figures():
    """So the order can record it. A bill settled at 5% must still say 5% after the
    hotel moves to 18% — see test_settled_orders.py."""
    out = outlet_totals(100.0, 18.0, inclusive=True)
    assert out["gst_rate"] == 18.0
    assert out["gst_inclusive"] is True


# ------------------- reading the two settings off a property -----------------
def test_a_property_that_predates_this_field_gets_the_default():
    """The migration stamps every record, but a read must not depend on having run it:
    an unmigrated property bills at 5% exclusive rather than raising at the till."""
    assert outlet_gst_settings({}) == (5.0, False)
    assert outlet_gst_settings(None) == (5.0, False)


def test_a_property_that_has_set_them_gets_what_it_set():
    assert outlet_gst_settings(
        {"outlet_gst_rate": 18.0, "gst_inclusive": True}) == (18.0, True)


def test_a_zero_rate_on_the_record_is_honoured_not_defaulted():
    """`or 5.0` would turn an unregistered business's deliberate 0% back into 5% and
    charge tax it has no registration to collect."""
    assert outlet_gst_settings({"outlet_gst_rate": 0.0}) == (0.0, False)


def test_a_hand_edited_rate_that_makes_no_sense_falls_back_rather_than_500s():
    """Read paths do not raise — the same rule `services/subscription.py::_parse_or_none`
    follows. A till that will not open because somebody typed "five" into a settings
    field is worse than one that bills the statutory default."""
    assert outlet_gst_settings({"outlet_gst_rate": "five"}) == (5.0, False)
    assert outlet_gst_settings({"outlet_gst_rate": 900}) == (5.0, False)


# ------------------- the primitive the platform invoice reuses ---------------
def test_split_tax_is_the_same_sum_for_the_platforms_own_invoice():
    """`split_tax` is what both pieces are built from: an outlet bill at 5% and a
    subscription invoice at 18% are the same arithmetic on different numbers, and two
    copies of it would eventually disagree."""
    out = split_tax(12000.0, 18.0, inclusive=True)
    assert out["taxable_value"] == 10169.49
    assert out["tax"] == 1830.51
    assert out["gross"] == 12000.00

    out = split_tax(12000.0, 18.0, inclusive=False)
    assert out["taxable_value"] == 12000.00
    assert out["tax"] == 2160.00
    assert out["gross"] == 14160.00
