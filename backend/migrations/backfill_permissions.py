"""One-shot: give every existing user the screens their role already reached.

Idempotent — a user that already has a `permissions` field is left alone, so re-running
is safe and the second run is a no-op.

Backfilling from the role, rather than from nothing, is the whole point: screens are
enforced at the API, so deploying with an empty field would lock every existing account
out of everything mid-service. Nobody loses access on deploy; the owner narrows people
afterwards, deliberately, from the staff screen.

The set is intersected with each user's work domains (see
`services.access.default_permissions`), so a hotel-only manager is not stored holding
outlet screens they could never open — a tick that does nothing is the lie the save-time
rule exists to prevent, and the staff screen would be refused for sending it back.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_permissions
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from services.access import default_permissions  # noqa: E402


async def backfill() -> tuple[int, int, int]:
    """Bring every user up to date. Returns (updated, already current, left empty).

    Also imported by server.py and run at startup: this app deploys as a container with
    no manual shell step, so a migration nobody runs is a migration that never runs.

    Key presence, NOT truthiness — the opposite of the domains backfill, deliberately.
    An account narrowed by the owner to two screens must not be widened back to its
    role's whole set on the next restart, and the only way to hold an empty list is for
    this migration to have produced one, which running it again would produce again.
    """
    users = await unscoped_db.users.find({}, {"_id": 0}).to_list(10000)
    updated = skipped = stranded = 0
    for user in users:
        if "permissions" in user:
            skipped += 1
            continue
        granted = default_permissions(user.get("role"), user.get("domains") or [])
        if not granted and user.get("role") != "admin":
            # Their role's screens all lie outside their domains — an account that
            # already reached nothing, so this takes nothing away. Counted and logged
            # rather than guessed at: the owner has to decide what this person does.
            stranded += 1
        await unscoped_db.users.update_one({"id": user["id"]}, {"$set": {"permissions": granted}})
        updated += 1
    return updated, skipped, stranded


async def main() -> None:
    updated, skipped, stranded = await backfill()
    print(f"users updated: {updated}, already current: {skipped}, "
          f"left with no screens: {stranded}")


if __name__ == "__main__":
    asyncio.run(main())
