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


def blocking_out_of_order(room: dict, check_in: str, check_out: str) -> dict | None:
    """The first out-of-order block covering any night of [check_in, check_out).

    The block itself, not a bool, because the caller that refuses an assignment has to
    tell the receptionist *why* the room cannot be used — "out of order 6th to 7th,
    burst pipe" is something they can act on; "unavailable" sends them to ask someone.
    """
    for block in room.get("out_of_order") or []:
        if ranges_overlap(check_in, check_out, block["from"], block["to"]):
            return block
    return None


def room_is_available(room: dict, check_in: str, check_out: str) -> bool:
    """False when the room is inactive or out of order for any night in the window."""
    if not room.get("active", True):
        return False
    return blocking_out_of_order(room, check_in, check_out) is None


def booking_holding_room(
    room_id: str,
    check_in: str,
    check_out: str,
    bookings: list[dict],
    exclude_booking_id: str | None = None,
) -> dict | None:
    """The live booking already holding this physical room across [check_in, check_out).

    `count_available` answers a different question — how many rooms of a *type* are
    left — and a type with two rooms free still cannot put two guests behind one door.
    This is the per-room question, and it is the one an assignment has to ask.

    The clashing booking is returned rather than a bool so the refusal can name it. The
    same three statuses that consume type inventory hold a room here: a cancelled or
    no-show booking, and a departed one, release the door even though the record still
    carries the `assigned_room_id` it was given — the status is what frees it, exactly
    as it is for inventory.

    `exclude_booking_id` is the booking being assigned. Without it, re-checking a room a
    booking already holds — reassigning it to itself, or checking in to the room it was
    pre-assigned — would find that booking and refuse it against its own reservation.
    """
    for b in bookings:
        if b.get("assigned_room_id") != room_id:
            continue
        if exclude_booking_id is not None and b.get("id") == exclude_booking_id:
            continue
        if b.get("status") not in CONSUMING_STATUSES:
            continue
        if ranges_overlap(check_in, check_out, b["check_in"], b["check_out"]):
            return b
    return None


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
