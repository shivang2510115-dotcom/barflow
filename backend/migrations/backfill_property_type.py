"""One-shot: say out loud what every existing property has been operating as.

Idempotent — a property that already names a type is left alone, so re-running is safe,
which is how it actually runs (see server.py's `on_startup`). This app deploys as a
container with no manual shell step, so a migration nobody runs is a migration that never
runs.

`both`, stated rather than guessed. Every property that predates this field has had
rooms *and* outlets — that is the only shape the product had — so `both` is not a
cautious default, it is what those tenants are. Writing it down means a reader of a
record, a support engineer and the platform console all get the same answer, instead of
each inferring one from a missing key. `services.access.property_domains` reads an absent
key the same way for the window before this has run; the two agree on purpose.

Nothing is narrowed here. A tenant that is really only a restaurant says so by being
signed up as one; taking the hotel away from a property that has been using it would
lock its front desk out mid-service, which is exactly the failure backfill_domains exists
to avoid at the other end.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_property_type
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from services.access import DEFAULT_PROPERTY_TYPE  # noqa: E402


async def backfill() -> tuple[int, int]:
    """Stamp every untyped property. Returns (updated, already current).

    `not record.get(...)` rather than a missing-key check, so a property left holding an
    empty string is repaired too — an empty type is not a type, and reading one as
    "unknown" would take the whole domain-scoped half of the app away from that tenant.
    """
    records = await unscoped_db.properties.find({}, {"_id": 0}).to_list(10000)
    updated = current = 0
    for record in records:
        if record.get("property_type"):
            current += 1
            continue
        await unscoped_db.properties.update_one(
            {"id": record["id"]}, {"$set": {"property_type": DEFAULT_PROPERTY_TYPE}})
        updated += 1
    return updated, current


async def main() -> None:
    updated, current = await backfill()
    print(f"properties updated: {updated}, already current: {current}")


if __name__ == "__main__":
    asyncio.run(main())
