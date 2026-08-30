"""What a month's pay comes to.

Pure arithmetic over numbers somebody else gathered — no database, no request — so every
edge can be exercised without a server, and the edges are most of the value: an unmarked
day, a week-off, attendance that exceeds the month. Each of those is a way payroll goes
wrong quietly and is discovered on payday.

**This module does not know tax law.** It computes no PF, no ESI, no professional tax and
no TDS. Deductions arrive as named lines somebody entered, and the arithmetic here adds
them up. Those rates change, several are state-specific, and their thresholds move with
headcount; a statutory figure computed wrongly here would become a compliance problem the
hotel carries and an inspector finds. See the design document for the full reasoning —
this boundary is deliberate and is not a gap to fill in later.
"""


def _money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def payslip_figures(*, salary_monthly: float, days_in_month: int,
                    present: float, half_days: float, paid_leave: float,
                    week_offs: float, additions: list[dict],
                    deductions: list[dict], advance_recovered: float) -> dict:
    """One payslip's numbers, with the working kept so a screen can show it.

    The first question anybody asks about a payslip is "why is it this much", so every
    intermediate is returned rather than only the total.
    """
    salary = _money(salary_monthly)
    days = max(0, int(days_in_month or 0))

    # A month with no days is not a real month, but a caller can produce one from a
    # malformed date and a division here would take payroll down for everybody.
    #
    # Kept unrounded for the arithmetic below and rounded only for display. Rounding the
    # daily rate first and then multiplying compounds the error once per absent day —
    # 18,000 over 31 days is 580.6451, and two days of it is 1,161.29, not the 1,161.30
    # that 580.65 x 2 gives. A rupee a month on every payslip is exactly the kind of
    # discrepancy somebody notices and nobody can explain.
    exact_per_day = (salary / days) if days else 0.0
    per_day = round(exact_per_day, 2)

    # A week-off is paid: it is a day the person was not required, not a day they failed
    # to turn up, and treating it as unpaid would dock everybody four days a month.
    worked_credit = (float(present or 0) + float(half_days or 0) / 2
                     + float(paid_leave or 0) + float(week_offs or 0))

    # Floored at zero. Attendance can exceed the month — a correction, or a manager
    # marking a 31st in a 30-day month — and paying somebody *more* because the
    # arithmetic went negative is worse than paying the figure they were promised.
    unpaid = max(0.0, days - worked_credit)

    gross = round(salary - unpaid * exact_per_day, 2)

    added = sum(_money(a.get("amount")) for a in (additions or []))
    taken = sum(_money(d.get("amount")) for d in (deductions or []))
    advance = _money(advance_recovered)

    # Not clamped at zero. An advance larger than the month's pay is a real situation,
    # and clamping would lose the fact that the person still owes the hotel money.
    net = round(gross + added - taken - advance, 2)

    return {
        "per_day": per_day,
        "worked_credit": round(worked_credit, 2),
        "unpaid_absence": round(unpaid, 2),
        "gross": gross,
        "additions_total": round(added, 2),
        "deductions_total": round(taken, 2),
        "advance_recovered": advance,
        "net": net,
    }
