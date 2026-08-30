"""Everything that happened during one stay, merged into one readable history.

Pure: rows in, ordered events out. The merging is the whole job — four collections
record different halves of a stay and none of them knows about the others.
"""
from services.timeline import merge_events


def test_events_come_back_newest_first():
    rows = merge_events(
        folio_entries=[{"id": "a", "kind": "outlet", "amount": 400,
                        "description": "Dinner", "posted_at": "2026-08-28T19:00:00Z"}],
        uses=[{"id": "u", "inclusion_id": "i", "used_at": "2026-08-29T08:00:00Z"}],
        housekeeping=[], booking={},
    )
    assert [e["at"][:10] for e in rows] == ["2026-08-29", "2026-08-28"]


def test_a_voided_charge_and_its_void_both_stay():
    """The opposite of the bill, deliberately.

    A bill shows what a guest owes; this shows what happened. A charge that was keyed
    wrongly and then voided IS what happened, and hiding it here would make the timeline
    useless for the one question it exists to answer.
    """
    rows = merge_events(
        folio_entries=[
            {"id": "a", "kind": "outlet", "amount": 999, "description": "Wrong",
             "posted_at": "2026-08-28T19:00:00Z"},
            {"id": "b", "kind": "void", "ref_entry_id": "a", "amount": 999,
             "description": "Void: Wrong — keyed twice",
             "posted_at": "2026-08-28T19:05:00Z"},
        ],
        uses=[], housekeeping=[], booking={},
    )
    assert len(rows) == 2


def test_check_in_and_check_out_bookend_the_stay():
    rows = merge_events(
        folio_entries=[], uses=[], housekeeping=[],
        booking={"checked_in_at": "2026-08-28T14:00:00Z",
                 "checked_out_at": "2026-08-30T11:00:00Z"},
    )
    kinds = [e["kind"] for e in rows]
    assert "checked_out" in kinds and "checked_in" in kinds
    # Newest first, so the departure leads.
    assert kinds[0] == "checked_out"


def test_a_stay_still_in_progress_has_no_departure():
    rows = merge_events(folio_entries=[], uses=[], housekeeping=[],
                        booking={"checked_in_at": "2026-08-28T14:00:00Z"})
    assert [e["kind"] for e in rows] == ["checked_in"]


def test_an_event_with_no_timestamp_is_dropped_rather_than_sorted_first():
    # An undated row sorts to one end and lands somewhere that reads as a lie about when
    # it happened. Better absent than wrongly placed on a history somebody trusts.
    rows = merge_events(
        folio_entries=[{"id": "a", "kind": "outlet", "amount": 1, "description": "?"}],
        uses=[], housekeeping=[], booking={},
    )
    assert rows == []


def test_housekeeping_events_join_the_same_history():
    rows = merge_events(
        folio_entries=[], uses=[], booking={},
        housekeeping=[{"id": "h", "to_status": "clean", "changed_at": "2026-08-29T10:00:00Z",
                       "changed_by": "Meena"}],
    )
    assert rows[0]["kind"] == "housekeeping"
    assert "clean" in rows[0]["description"]


def test_everything_carries_a_kind_a_time_and_a_description():
    """What the screen renders. A row missing any of the three cannot be drawn."""
    rows = merge_events(
        folio_entries=[{"id": "a", "kind": "payment", "amount": 500, "description": "Cash",
                        "posted_at": "2026-08-30T11:00:00Z"}],
        uses=[{"id": "u", "inclusion_id": "i", "used_at": "2026-08-29T08:00:00Z"}],
        housekeeping=[{"id": "h", "to_status": "dirty", "changed_at": "2026-08-29T09:00:00Z"}],
        booking={"checked_in_at": "2026-08-28T14:00:00Z"},
    )
    assert len(rows) == 4
    for e in rows:
        assert e["kind"] and e["at"] and e["description"]
