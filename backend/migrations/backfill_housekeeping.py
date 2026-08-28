"""One-shot: the housekeeping screen for the people who run the hotel.

Idempotent — a user who already holds the key is left alone, so re-running is a no-op.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_housekeeping

**Why this exists at all.** `backfill_permissions` fills in a `permissions` field that is
*missing*; it deliberately never touches one that is present, so an account the owner
narrowed to two screens is not widened back on the next restart. That is the right rule
and it has one consequence: a screen key added after a deployment has been running
reaches nobody, because every account already has a `permissions` list and it predates the
key. `hotel.housekeeping` is the first such key, so it is the first migration of this
shape.

**Who is granted it, and who is not.** Managers and admins, and anybody already carrying
the new `housekeeping` role — the people whose roles mean "all of this property's
operations" — provided they hold the `hotel` domain, because a tick outside somebody's
domains is refused at request time anyway and storing one would be a lie on the staff
screen (see `services.access.permission_in_domains`).

`front_desk` is **not** granted it, even though the design has the desk reading the
housekeeping list. Their entry in `ROLE_SCREENS` is an explicit short list rather than a
computed "everything", so adding to it here would overrule a decision somebody made about
what a receptionist sees, on every property at once. Nothing is lost by waiting to be
ticked: the desk's real need is the status label on the check-in room picker, which comes
from `GET /api/rooms` — a screen they already hold, now carrying the new fields. An owner
who wants the desk on the housekeeping screen ticks it, once, on the staff screen.

Rooms are handled by `seed_room_status` below, in the same run and for the same reason:
a room whose record predates the field would otherwise read as "nobody has said", and
`is_ready` answers no to that — every existing room would show as not ready on the morning
this deploys.
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from services.housekeeping import DEFAULT_STATUS  # noqa: E402

SCREEN = "hotel.housekeeping"

# The roles whose stored screens are a computed "everything this role does" rather than a
# hand-written list. See the module docstring for why front_desk is absent.
GRANT_TO = ("admin", "manager", "housekeeping")


async def grant_screen() -> tuple[int, int]:
    """Give the housekeeping screen to the roles that run the property.

    Returns (granted, already current).

    An account with no `permissions` key at all is left to `backfill_permissions`, which
    runs first at startup and derives the whole set from the role — including this key,
    since `ROLE_SCREENS` now carries it. Touching such an account here would grant it one
    screen and hide it from the migration whose job that is.
    """
    users = await unscoped_db.users.find({}, {"_id": 0}).to_list(10000)
    granted = current = 0
    for user in users:
        held = user.get("permissions")
        if held is None:
            continue
        if user.get("role") not in GRANT_TO or "hotel" not in (user.get("domains") or ()):
            continue
        if SCREEN in held:
            current += 1
            continue
        await unscoped_db.users.update_one(
            {"id": user["id"]}, {"$set": {"permissions": [*held, SCREEN]}})
        granted += 1
    return granted, current


async def seed_room_status() -> tuple[int, int]:
    """Stamp every room that predates the status with `clean`. Returns (stamped, current).

    `clean` rather than `dirty`, and the choice is not neutral: these are rooms the
    property has been letting all along, and calling them all dirty would hand
    housekeeping a list of a hundred rooms that do not need doing on the morning of the
    deploy. The first real check-out dirties the ones that are actually dirty.

    Key presence, not truthiness. A room deliberately marked `out_of_order` by an
    attendant must not be quietly cleaned by a restart.

    Reached through `unscoped_db` — this is a migration, it crosses every tenant on
    purpose, and each room carries the `property_id` it already had. Nothing here reads a
    property from a request; there is no request.
    """
    rooms = await unscoped_db.rooms.find({}, {"_id": 0}).to_list(50000)
    stamped = current = 0
    for room in rooms:
        if "housekeeping_status" in room:
            current += 1
            continue
        await unscoped_db.rooms.update_one({"id": room["id"]}, {"$set": {
            "housekeeping_status": DEFAULT_STATUS,
            "housekeeping_note": None,
            "housekeeping_updated_at": None,
            "housekeeping_updated_by": None,
        }})
        stamped += 1
    return stamped, current


async def backfill() -> tuple[int, int, int, int]:
    """Both halves, in the order they are wanted. Returns
    (screens granted, already held, rooms stamped, rooms current)."""
    granted, held = await grant_screen()
    stamped, current = await seed_room_status()
    return granted, held, stamped, current


async def main() -> None:
    granted, held, stamped, current = await backfill()
    print(f"housekeeping screen granted to {granted} user(s), {held} already held it; "
          f"{stamped} room(s) stamped {DEFAULT_STATUS}, {current} already current")


if __name__ == "__main__":
    asyncio.run(main())
