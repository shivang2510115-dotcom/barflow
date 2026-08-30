"""Everything that happened during one stay, as a single readable history.

Four collections record different halves of a stay and none of them knows about the
others: the folio has the money, `entitlement_uses` has what the package covered,
`housekeeping_events` has who made the room up, and the booking itself has the arrival
and the departure. Merging them is this module's whole job, and it is pure so the
merging can be tested without any of the four.

**This is not the bill, and the difference is deliberate.** A bill shows what a guest
owes, so a charge that was keyed wrongly and voided leaves it entirely — the guest reads
what they owe, not the hotel's correction history. A timeline answers "what happened",
and a mis-keyed charge followed by a void IS what happened. Hiding it here would make
the history useless for the one question it exists to answer.
"""

# How a folio entry reads on a history, as opposed to on a bill.
_ENTRY_WORDS = {
    "room_night": "Room night",
    "outlet": "Charge",
    "misc_charge": "Charge",
    "payment": "Payment",
    "discount": "Discount",
    "refund": "Refund",
    "void": "Cancelled",
}


def _row(kind: str, at: str | None, description: str, **extra) -> dict | None:
    """One event, or None if it cannot be placed in time.

    An undated row sorts to one end and lands somewhere that reads as a lie about when
    it happened. On a history somebody is trusting to settle a dispute, absent is better
    than wrongly placed.
    """
    if not at:
        return None
    return {"kind": kind, "at": at, "description": description, **extra}


def merge_events(folio_entries: list[dict], uses: list[dict],
                 housekeeping: list[dict], booking: dict) -> list[dict]:
    """Every event of one stay, newest first."""
    rows: list[dict | None] = []

    rows.append(_row("checked_in", booking.get("checked_in_at"), "Checked in"))
    rows.append(_row("checked_out", booking.get("checked_out_at"), "Checked out"))

    for e in folio_entries:
        word = _ENTRY_WORDS.get(e.get("kind"), "Entry")
        rows.append(_row(
            e.get("kind") or "entry",
            e.get("posted_at"),
            f"{word} · {e.get('description') or ''}".strip(" ·"),
            amount=e.get("amount"),
        ))

    for u in uses:
        rows.append(_row("included", u.get("used_at"), "Included in package",
                         inclusion_id=u.get("inclusion_id")))

    for h in housekeeping:
        by = h.get("changed_by")
        rows.append(_row(
            "housekeeping", h.get("changed_at"),
            f"Room marked {h.get('to_status') or 'changed'}" + (f" by {by}" if by else ""),
        ))

    kept = [r for r in rows if r]
    kept.sort(key=lambda r: r["at"], reverse=True)
    return kept
