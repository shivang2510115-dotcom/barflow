"""Room availability arithmetic.

Pure functions over supplied rooms and bookings — no database access, so the rules are
testable in isolation. Every range is half-open [from, to): a stay ending on the 5th and
one starting on the 5th do not conflict.
"""

# Statuses that hold inventory. Cancelled and no-show release it.
CONSUMING_STATUSES = frozenset({"tentative", "confirmed", "checked_in"})


def ranges_overlap(a_from: str, a_to: str, b_from: str, b_to: str) -> bool:
    """True when two half-open date ranges share at least one night."""
    return a_from < b_to and a_to > b_from


def room_is_available(room: dict, check_in: str, check_out: str) -> bool:
    """False when the room is inactive or out of order for any night in the window."""
    if not room.get("active", True):
        return False
    for block in room.get("out_of_order") or []:
        if ranges_overlap(check_in, check_out, block["from"], block["to"]):
            return False
    return True


def count_available(
    room_type_id: str,
    check_in: str,
    check_out: str,
    rooms: list[dict],
    bookings: list[dict],
) -> int:
    """Rooms of a type free for the whole window. Never negative."""
    usable = sum(
        1 for r in rooms
        if r.get("room_type_id") == room_type_id
        and room_is_available(r, check_in, check_out)
    )
    taken = sum(
        1 for b in bookings
        if b.get("room_type_id") == room_type_id
        and b.get("status") in CONSUMING_STATUSES
        and ranges_overlap(check_in, check_out, b["check_in"], b["check_out"])
    )
    return max(0, usable - taken)
