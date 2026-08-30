"""ADR, RevPAR and occupancy: the three numbers a hotelier reads before anything else.

Pure arithmetic over figures somebody else fetched — no database, no request — so the
edges can be exercised without a server. The edges are most of the value here: a hotel
that has not built its room list yet, a day on which nothing sold, a period longer than
one night.

**Why these three and not a dashboard of twenty.** They are the vocabulary. A manager
who cannot see ADR concludes the software is not really for hotels, however much else it
shows them, and the arithmetic was always available — room revenue is already in the
folio ledger and the room count is already in `rooms`.

**The denominators are the whole difficulty.** ADR divides by rooms *sold*; RevPAR
divides by rooms *available*. Confusing the two is the standard way RevPAR gets
misreported, and it always reports too well, so nobody notices. `test_metrics.py` asserts
the identity RevPAR = ADR x occupancy, which is the check that catches it.
"""


def occupancy_metrics(room_revenue: float, nights_sold: int,
                      rooms: int, days: int) -> dict:
    """The three figures, from room revenue and two counts.

    `room_revenue` is revenue from room charges only — never the folio total, which
    includes restaurant and other extras charged to the room. Those belong to the outlet
    that sold them, and folding them in would flatter ADR with somebody else's takings.
    It arrives already void-corrected from `services.revenue.hotel_revenue`.

    `nights_sold` counts room-nights actually charged, and `rooms x days` is what was
    available to sell over the period. Ten rooms over three days is thirty available
    room-nights, not ten.

    A figure whose denominator is zero comes back as None rather than 0.0, and the
    difference is not pedantry: an ADR of zero claims rooms were given away free, where
    None says nothing was sold. The screen renders None as a dash.
    """
    available = rooms * days

    # ADR never depended on the period, so it survives days=0.
    adr = round(room_revenue / nights_sold, 2) if nights_sold else None

    if available <= 0:
        return {"adr": adr, "revpar": None, "occupancy": None,
                "nights_sold": nights_sold, "nights_available": 0}

    return {
        "adr": adr,
        "revpar": round(room_revenue / available, 2),
        # Not clamped at 100. Overbooking, or one room sold twice in a night, is a real
        # error and reporting 120% is how somebody notices; capping it would hide the
        # mistake that caused it.
        "occupancy": round(nights_sold / available * 100, 2),
        "nights_sold": nights_sold,
        "nights_available": available,
    }
