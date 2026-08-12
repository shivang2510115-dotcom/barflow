"""One-shot: give every existing user all domains and mark them active.

Idempotent — a user that already has both fields is left alone, so re-running is safe.

Backfilling wide rather than narrow is deliberate. Domains are enforced at the API, so
seeding empty would lock every existing account out the instant this deploys, mid-service,
with no route back except the admin. The admin narrows people afterwards, deliberately.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_domains
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import db  # noqa: E402
from services.access import DOMAINS  # noqa: E402


async def backfill() -> tuple[int, int]:
    """Bring every user up to date. Returns (updated, already current).

    Also imported by server.py and run at startup: this app deploys as a container
    with no manual shell step, so a migration nobody runs is a migration that never
    runs — and an account left without domains is locked out of the whole app.
    """
    users = await db.users.find({}, {"_id": 0}).to_list(10000)
    updated = skipped = 0
    for user in users:
        patch = {}
        # `not user.get(...)` rather than a missing-key check, so a user left with an
        # empty list is repaired too — empty domains denies access to everything.
        if not user.get("domains"):
            patch["domains"] = list(DOMAINS)
        # Key presence, NOT truthiness: a deliberately deactivated leaver must stay
        # deactivated. Testing `not user.get("active")` would reactivate all of them.
        if "active" not in user:
            patch["active"] = True
        if not patch:
            skipped += 1
            continue
        await db.users.update_one({"id": user["id"]}, {"$set": patch})
        updated += 1
    return updated, skipped


async def main() -> None:
    updated, skipped = await backfill()
    print(f"users updated: {updated}, already current: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
