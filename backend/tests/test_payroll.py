"""What a month's pay comes to.

Pure arithmetic over numbers somebody else gathered. The edges are the point: an
unmarked day, a week-off, attendance that exceeds the month. Each one is a way this
goes wrong quietly and is discovered on payday.
"""
from services.payroll import payslip_figures


def figures(**kw):
    base = dict(salary_monthly=18000, days_in_month=31, present=26, half_days=0,
                paid_leave=3, week_offs=0, additions=[], deductions=[],
                advance_recovered=0)
    base.update(kw)
    return payslip_figures(**base)


def test_the_worked_example_from_the_design():
    f = figures()
    assert f["per_day"] == 580.65
    assert f["unpaid_absence"] == 2
    assert f["gross"] == 16838.71
    assert f["net"] == 16838.71


def test_an_advance_comes_off_the_net_and_not_the_gross():
    f = figures(advance_recovered=5000)
    assert f["gross"] == 16838.71, "an advance is not a pay cut"
    assert f["net"] == 11838.71


def test_a_full_month_present_is_paid_in_full():
    f = figures(present=31, paid_leave=0)
    assert f["unpaid_absence"] == 0
    assert f["gross"] == 18000.0


def test_a_week_off_is_paid():
    # A day the person was not required, not a day they failed to turn up. Treating it
    # as unpaid would dock everybody four days a month.
    f = figures(present=22, paid_leave=0, week_offs=9)
    assert f["unpaid_absence"] == 0
    assert f["gross"] == 18000.0


def test_a_half_day_counts_as_half():
    f = figures(present=30, half_days=2, paid_leave=0)
    # 30 + 1 = 31 credit against 31 days.
    assert f["unpaid_absence"] == 0
    assert f["gross"] == 18000.0


def test_attendance_beyond_the_month_never_pays_more_than_the_salary():
    # A correction, or a manager marking a 31st in a 30-day month. Paying somebody more
    # because the arithmetic went negative is worse than paying what was promised.
    f = figures(days_in_month=30, present=31, paid_leave=3)
    assert f["unpaid_absence"] == 0
    assert f["gross"] == 18000.0


def test_additions_and_deductions_move_the_net_only():
    f = figures(additions=[{"label": "Overtime", "amount": 1200}],
                deductions=[{"label": "PF", "amount": 1800}])
    assert f["gross"] == 16838.71
    assert f["net"] == round(16838.71 + 1200 - 1800, 2)


def test_a_deduction_can_take_the_net_below_zero_and_it_is_reported():
    # An advance larger than the month's pay is a real situation. Clamping it to zero
    # would lose the fact that the person still owes the hotel money.
    f = figures(advance_recovered=20000)
    assert f["net"] < 0


def test_a_month_with_no_days_does_not_divide_by_zero():
    f = figures(days_in_month=0, present=0, paid_leave=0)
    assert f["per_day"] == 0
    assert f["gross"] == 18000.0


def test_a_person_with_no_salary_recorded_earns_nothing_rather_than_erroring():
    # Eighty-eight staff records predate these fields. A run must produce a payslip for
    # them rather than refusing, so the gap is visible instead of stopping payroll.
    f = figures(salary_monthly=0)
    assert f["gross"] == 0.0 and f["net"] == 0.0
