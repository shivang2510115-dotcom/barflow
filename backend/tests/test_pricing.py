"""Pure pricing tests — no server, no database."""
import pytest

from services.pricing import (
    daterange, resolve_rate, gst_for, quote_stay, MissingRateError,
)

RT = "type-deluxe"

DEFAULT_RATE = {"room_type_id": RT, "period_id": None,
                "base_rate": 5000.0, "extra_adult_rate": 1000.0, "extra_child_rate": 500.0}
PEAK_RATE = {"room_type_id": RT, "period_id": "peak",
             "base_rate": 9000.0, "extra_adult_rate": 1500.0, "extra_child_rate": 700.0}

PEAK = {"id": "peak", "name": "Peak", "start_date": "2026-12-20", "end_date": "2027-01-05",
        "priority": 10, "active": True}

SLABS = [
    {"min_tariff": 0.0, "max_tariff": 7500.0, "rate_percent": 12.0, "active": True},
    {"min_tariff": 7500.0, "max_tariff": None, "rate_percent": 18.0, "active": True},
]

EP = {"id": "ep", "code": "EP", "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0}
MAP_PLAN = {"id": "map", "code": "MAP", "price_per_adult_per_night": 800.0,
            "price_per_child_per_night": 400.0}


def test_daterange_is_half_open():
    # A 2-night stay arriving the 3rd, leaving the 5th, bills the 3rd and 4th only.
    assert daterange("2026-08-03", "2026-08-05") == ["2026-08-03", "2026-08-04"]


def test_daterange_rejects_zero_nights():
    with pytest.raises(ValueError):
        daterange("2026-08-03", "2026-08-03")


def test_resolve_rate_uses_default_outside_periods():
    r = resolve_rate("2026-08-03", RT, [DEFAULT_RATE, PEAK_RATE], [PEAK])
    assert r["base_rate"] == 5000.0


def test_resolve_rate_uses_period_inside_range():
    r = resolve_rate("2026-12-25", RT, [DEFAULT_RATE, PEAK_RATE], [PEAK])
    assert r["base_rate"] == 9000.0


def test_resolve_rate_period_end_is_exclusive():
    # Peak ends 2027-01-05, so the night of the 5th is back to default.
    r = resolve_rate("2027-01-05", RT, [DEFAULT_RATE, PEAK_RATE], [PEAK])
    assert r["base_rate"] == 5000.0


def test_resolve_rate_raises_when_uncovered():
    with pytest.raises(MissingRateError) as e:
        resolve_rate("2026-08-03", RT, [PEAK_RATE], [PEAK])
    assert e.value.dates == ["2026-08-03"]


def test_gst_slab_boundaries():
    assert gst_for(5000.0, SLABS) == 12.0
    assert gst_for(7500.0, SLABS) == 12.0     # upper bound is inclusive
    assert gst_for(7500.01, SLABS) == 18.0
    assert gst_for(20000.0, SLABS) == 18.0


def test_quote_simple_two_nights_ep():
    q = quote_stay("2026-08-03", "2026-08-05", RT, adults=2, children=0,
                   base_occupancy=2, meal_plan=EP,
                   rates=[DEFAULT_RATE], periods=[], slabs=SLABS)
    assert len(q["nights"]) == 2
    assert q["room_subtotal"] == 10000.0
    assert q["tax_total"] == 1200.0           # 12% of 5000, twice
    assert q["total"] == 11200.0


def test_quote_extra_adult_and_child():
    q = quote_stay("2026-08-03", "2026-08-04", RT, adults=3, children=1,
                   base_occupancy=2, meal_plan=EP,
                   rates=[DEFAULT_RATE], periods=[], slabs=SLABS)
    # 5000 base + 1000 extra adult + 500 child = 6500
    assert q["room_subtotal"] == 6500.0
    assert q["tax_total"] == 780.0            # 12%


def test_quote_meal_plan_is_per_person_per_night():
    q = quote_stay("2026-08-03", "2026-08-04", RT, adults=2, children=1,
                   base_occupancy=2, meal_plan=MAP_PLAN,
                   rates=[DEFAULT_RATE], periods=[], slabs=SLABS)
    # 5000 room + 500 child occupancy + (800*2 + 400*1) meals = 7500
    assert q["room_subtotal"] == 7500.0
    assert q["tax_total"] == 900.0            # 7500 is still the 12% slab


def test_quote_crosses_gst_slab_mid_stay():
    # One default night at 5000 (12%), one peak night at 9000 (18%).
    q = quote_stay("2026-12-19", "2026-12-21", RT, adults=2, children=0,
                   base_occupancy=2, meal_plan=EP,
                   rates=[DEFAULT_RATE, PEAK_RATE], periods=[PEAK], slabs=SLABS)
    assert [n["tariff"] for n in q["nights"]] == [5000.0, 9000.0]
    assert [n["gst_percent"] for n in q["nights"]] == [12.0, 18.0]
    assert q["room_subtotal"] == 14000.0
    assert q["tax_total"] == 600.0 + 1620.0
    assert q["total"] == 16220.0


def test_quote_raises_on_any_uncovered_night():
    with pytest.raises(MissingRateError) as e:
        quote_stay("2026-12-19", "2026-12-21", RT, adults=2, children=0,
                   base_occupancy=2, meal_plan=EP,
                   rates=[PEAK_RATE], periods=[PEAK], slabs=SLABS)
    assert e.value.dates == ["2026-12-19"]
