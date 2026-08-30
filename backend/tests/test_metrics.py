"""ADR, RevPAR and occupancy — the three numbers a hotelier reads first.

Pure arithmetic over numbers somebody else fetched, so every edge can be exercised
without a database. The edges are the point: a hotel with no rooms yet, a day with
nothing sold, a period longer than one night.
"""
from services.metrics import occupancy_metrics


def test_the_textbook_case():
    # 10 rooms, 1 day, 6 sold for 12,000 total.
    m = occupancy_metrics(room_revenue=12000, nights_sold=6, rooms=10, days=1)
    assert m["adr"] == 2000.0            # 12000 / 6 sold
    assert m["revpar"] == 1200.0         # 12000 / 10 available
    assert m["occupancy"] == 60.0        # 6 / 10


def test_revpar_is_adr_times_occupancy():
    """The identity every hotelier checks the software against.

    Held to a tolerance rather than exactly, because each figure is rounded from the
    unrounded quotient independently — RevPAR is revenue / available, not the product of
    two already-rounded numbers. Deriving it from a rounded ADR would compound the error
    instead of containing it, so the small disagreement here is the correct behaviour and
    a exact assertion would be pressure to introduce the bug.
    """
    m = occupancy_metrics(room_revenue=45000, nights_sold=18, rooms=23, days=3)
    assert abs(m["adr"] * m["occupancy"] / 100 - m["revpar"]) < 0.5


def test_a_multi_day_period_multiplies_the_rooms_by_the_days():
    # 10 rooms over 3 days is 30 available room-nights, not 10. Getting this wrong
    # inflates occupancy threefold and is the most common way RevPAR is misreported.
    m = occupancy_metrics(room_revenue=30000, nights_sold=15, rooms=10, days=3)
    assert m["occupancy"] == 50.0        # 15 / 30
    assert m["revpar"] == 1000.0         # 30000 / 30


def test_nothing_sold_reports_no_adr_rather_than_zero():
    # An ADR of zero claims rooms were given away free. None says nothing was sold,
    # which is what happened, and the screen renders it as a dash.
    m = occupancy_metrics(room_revenue=0, nights_sold=0, rooms=10, days=1)
    assert m["adr"] is None
    assert m["occupancy"] == 0.0
    assert m["revpar"] == 0.0


def test_a_property_with_no_rooms_yet_does_not_divide_by_zero():
    # A hotel on its first day, before anyone has built the room list.
    m = occupancy_metrics(room_revenue=0, nights_sold=0, rooms=0, days=1)
    assert m["occupancy"] is None
    assert m["revpar"] is None
    assert m["adr"] is None


def test_zero_days_is_refused_the_same_way():
    m = occupancy_metrics(room_revenue=1000, nights_sold=1, rooms=10, days=0)
    assert m["occupancy"] is None
    assert m["revpar"] is None
    # ADR survives: it never depended on the period at all.
    assert m["adr"] == 1000.0


def test_occupancy_can_exceed_nothing_it_should_not_but_is_reported_honestly():
    # Overbooking, or a room sold twice in one night by mistake. Reporting 120% is how
    # somebody notices; silently capping at 100 would hide the error that caused it.
    m = occupancy_metrics(room_revenue=12000, nights_sold=12, rooms=10, days=1)
    assert m["occupancy"] == 120.0


def test_a_refunded_night_can_push_revenue_negative_without_breaking_adr():
    # Revenue arrives already void-corrected; a heavy refund can still leave it below
    # zero for a small period. That is a real number and it is reported, not clamped.
    m = occupancy_metrics(room_revenue=-500, nights_sold=1, rooms=10, days=1)
    assert m["adr"] == -500.0
    assert m["revpar"] == -50.0
