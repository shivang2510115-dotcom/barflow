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


async def main() -> None:
    users = await db.users.find({}, {"_id": 0}).to_list(10000)
    updated = skipped = 0
    for user in users:
        patch = {}
        if not user.get("domains"):
            patch["domains"] = list(DOMAINS)
        if "active" not in user:
            patch["active"] = True
        if not patch:
            skipped += 1
            continue
        await db.users.update_one({"id": user["id"]}, {"$set": patch})
        updated += 1
    print(f"users updated: {updated}, already current: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
