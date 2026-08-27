"""Outlet GST: the tax on a restaurant bill, added on top or taken back out.

Pure functions over plain values — no database, no framework — for the same reason
`pricing.py` beside it is: this is money, and money rules have to be readable in one
place and testable without a server. Rooms are not this module's business at all;
`pricing.py` already computes the statutory hotel slab (12% at or under ₹7,500 a night,
18% above) and nothing here touches it.

**The bug this replaces.** `routers/orders.py::compute_totals` charged a flat 10%. Ten
percent is not an Indian GST rate: restaurant service is 5% without input tax credit, or
18% in specified cases, and packaged goods vary. Every bill this POS printed carried a
tax figure that matched nothing a guest could lawfully be charged. So the rate is the
hotel's own setting now, because the person who knows what their registration says is
the owner and they cannot edit a deployment.

**The two branches are not variations on one sum.**

* *exclusive* — the menu price is before tax, and the tax is added: ₹100 at 5% is ₹105.
* *inclusive* — the menu price already contains the tax, which is what most Indian
  restaurants print, and the tax is *extracted*: `price − price ÷ (1 + rate)`. ₹100 at
  5% is ₹100, of which ₹4.76 is tax. Charging 5% on top of an inclusive price takes
  ₹105 for a dish the card says is ₹100 — the guest is overcharged by the tax on the
  tax, on a figure they have already agreed to.

**Rounding is at the paise and the bill always adds up.** The tax on an inclusive price
is derived from the *rounded* taxable value rather than computed independently, so the
two lines printed on the bill sum to the number at the bottom of it. The alternative
rounds each half separately and is out by a paisa often enough that somebody eventually
files it as a bug — and a bill that does not add up is not a bill.

`Decimal(str(v))` rather than `Decimal(v)` throughout: binary floats hold 33.33 as
33.32999…, and 33.33 × 5% then rounds to 1.66 where a printed bill says 1.67.
"""
from decimal import Decimal, ROUND_HALF_UP

# Restaurant service without input tax credit. What a new property gets, and what the
# startup migration stamps on every property that predates the field.
DEFAULT_OUTLET_GST_RATE = 5.0

# Whether menu prices already contain the tax. False is the safer default for a record
# nobody has looked at: it is what this POS did before the field existed, so a hotel that
# never opens the settings screen keeps billing the way it already was.
DEFAULT_GST_INCLUSIVE = False

# The top GST slab. A rate above it is a typo, not a rate — there is nothing in the
# schedule at 50%, and a hotel that fat-fingers one would add half again to every bill.
MAX_GST_RATE = 28.0

_PAISE = Decimal("0.01")


class TaxRateError(Exception):
    """Raised when a rate cannot mean anything — negative, above the top slab, or not a
    number at all.

    A raise rather than a default, on the write path. Every silent fallback available
    here bills somebody the wrong tax, and the wrong tax on a bill that has already been
    printed and paid is not something a later release can put right.
    """


def _money(value) -> Decimal:
    """A plain number as an exact decimal, via its written form. See the module note."""
    return Decimal(str(value))


def _paise(value: Decimal) -> float:
    """Rounded to the paise, half up — the way a printed bill rounds, and the way a
    person checking one with a calculator expects."""
    return float(value.quantize(_PAISE, rounding=ROUND_HALF_UP))


def normalise_rate(rate) -> float:
    """A GST percentage, or a refusal naming what was given.

    Zero is legal and is not "unset": a business below the registration threshold
    charges no GST, and that is a rate it has chosen rather than one it is missing.
    """
    try:
        value = float(rate)
    except (TypeError, ValueError):
        raise TaxRateError(
            f"not a GST rate: {rate!r} — expected a percentage between 0 and "
            f"{MAX_GST_RATE:g}") from None
    if value != value or value in (float("inf"), float("-inf")):  # NaN, ±inf
        raise TaxRateError(f"not a GST rate: {rate!r}")
    if value < 0 or value > MAX_GST_RATE:
        raise TaxRateError(
            f"{value:g}% is not a GST rate that can be charged — expected between 0 and "
            f"{MAX_GST_RATE:g}")
    return round(value, 2)


def split_tax(amount: float, rate_percent: float, inclusive: bool = False) -> dict:
    """Split one amount into its taxable value and its tax.

    The primitive both pieces of this design are built from: an outlet bill at the
    hotel's own rate and the platform's own subscription invoice at 18% are the same
    arithmetic on different numbers, and two copies of it would eventually disagree
    about a paisa in front of an auditor.

    Returns `taxable_value`, `tax` and `gross`, where `gross` is what actually changes
    hands. Inclusive: `gross` is the amount given. Exclusive: `gross` is the amount plus
    the tax. Both branches satisfy `taxable_value + tax == gross`, exactly.
    """
    rate = _money(normalise_rate(rate_percent))
    value = _money(amount)

    if inclusive:
        taxable = _paise(value / (1 + rate / 100))
        # From the rounded taxable value, never independently: this is what makes the two
        # printed lines add up to the printed total.
        return {
            "taxable_value": taxable,
            "tax": _paise(value - _money(taxable)),
            "gross": _paise(value),
        }

    taxable = _paise(value)
    tax = _paise(_money(taxable) * rate / 100)
    return {
        "taxable_value": taxable,
        "tax": tax,
        "gross": _paise(_money(taxable) + _money(tax)),
    }


def outlet_totals(items_total: float, rate_percent: float, inclusive: bool = False,
                  discount: float = 0.0) -> dict:
    """The whole foot of a restaurant bill, from the sum of its lines.

    `subtotal` is what the lines add up to — the menu prices as printed on the card, so
    it is the taxable value when the rate is exclusive and the gross when it is
    inclusive. `taxable_value` is always the figure the tax was worked out on, which is
    the one that belongs on a GST bill.

    The discount comes off the total, which is what this POS has always done. It is not
    applied to the taxable value first: that produces a different number, and quietly
    changing which one a settled bill meant would put a hotel's own books out.

    `gst_rate` and `gst_inclusive` come back with the figures so the order can record
    what it was billed at. A bill settled at 5% has to still say 5% after the hotel
    moves to 18% — the guest paid what the printed bill said.
    """
    split = split_tax(items_total, rate_percent, inclusive)
    subtotal = _paise(_money(items_total))
    total = _paise(_money(split["gross"]) - _money(discount or 0))
    return {
        "subtotal": subtotal,
        "taxable_value": split["taxable_value"],
        "tax": split["tax"],
        "total": total,
        "gst_rate": normalise_rate(rate_percent),
        "gst_inclusive": bool(inclusive),
    }


def outlet_gst_settings(property_record) -> tuple[float, bool]:
    """The rate and the inclusive flag for one property, as at this moment.

    A *read* path, so it never raises — the same rule
    `services/subscription.py::_parse_or_none` follows. A property that predates the
    field bills at the statutory default; a record somebody hand-edited into nonsense
    does too, and the settings screen is where that gets found. A till that will not
    open because a field holds the word "five" is worse than one that bills 5%.

    Key presence is not what is tested, the value is — but zero is honoured. `or 5.0`
    would turn an unregistered business's deliberate 0% back into 5% and have it collect
    tax it has no registration to collect.
    """
    record = property_record or {}
    try:
        rate = normalise_rate(record.get("outlet_gst_rate", DEFAULT_OUTLET_GST_RATE))
    except TaxRateError:
        rate = DEFAULT_OUTLET_GST_RATE
    if record.get("outlet_gst_rate") is None:
        rate = DEFAULT_OUTLET_GST_RATE
    return rate, bool(record.get("gst_inclusive", DEFAULT_GST_INCLUSIVE))
