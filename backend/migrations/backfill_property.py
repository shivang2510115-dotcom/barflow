"""One-shot: make the hotel that is already running the first tenant.

Idempotent — a second run creates nothing and stamps nobody, so it is safe on every
restart, which is how it actually runs (see server.py's `on_startup`). This app deploys
as a container with no manual shell step, so a migration nobody runs is a migration that
never runs.

Two decisions worth stating:

* The property is created **live**, not pending. This hotel has been taking bookings for
  months; landing it in the state that refuses bookings would take a working install off
  the air on deploy, to fix a problem it does not have.
* A user with no `property_id` reaches nothing at all once `require_access` resolves the
  property, so this migration is what stands between the deploy and every existing login
  being locked out. It runs at startup for that reason, and failures propagate.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_property
"""
import asyncio
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from models.property import Property  # noqa: E402
from services.access import LIVE, PLATFORM_ADMIN  # noqa: E402


async def backfill() -> tuple[bool, str | None, int, int]:
    """Ensure one property exists and every hotel user names it.

    Returns (created, property_id, stamped, already current).

    `property_id` is None only when the database holds several properties: at that point
    tenancy is live and the answer to "which hotel is this user in" is not this
    migration's to guess — guessing hands one hotel's login the other hotel's data. It
    then does nothing at all, which is correct, because a database with several
    properties is one where signup has been creating them and every user arrived stamped.
    """
    existing = await unscoped_db.properties.find({}, {"_id": 0}).to_list(100)
    created = False
    if len(existing) > 1:
        return False, None, 0, 0
    if existing:
        property_id = existing[0]["id"]
    else:
        record = Property(
            # Named from the environment so the first tenant carries the operator's own
            # hotel name rather than a placeholder nobody edits. Everything else on the
            # record is filled in from the property screen.
            name=os.environ.get("PROPERTY_NAME", "BarFlow Hotel"),
            city=os.environ.get("PROPERTY_CITY") or None,
            status=LIVE,
        ).model_dump()
        await unscoped_db.properties.insert_one(record)
        property_id = record["id"]
        created = True

    users = await unscoped_db.users.find({}, {"_id": 0}).to_list(10000)
    stamped = current = 0
    for user in users:
        # The platform operator belongs to no hotel, by design. Stamping them would hand
        # the one login that can approve and suspend hotels a seat inside one of them —
        # precisely the master key `_property_usable` refuses to issue.
        if user.get("role") == PLATFORM_ADMIN:
            continue
        if user.get("property_id"):
            current += 1
            continue
        await unscoped_db.users.update_one({"id": user["id"]}, {"$set": {"property_id": property_id}})
        stamped += 1
    return created, property_id, stamped, current


async def main() -> None:
    created, property_id, stamped, current = await backfill()
    print(f"property {'created' if created else 'already present'}: {property_id}, "
          f"users stamped: {stamped}, already current: {current}")


if __name__ == "__main__":
    asyncio.run(main())
