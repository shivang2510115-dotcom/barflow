"""Give every property the GST bands and meal plans it needs to quote a room.

Signup seeds these now, but the properties that registered before it did have none, and
nothing in the app tells their owner why availability fails — they get a 500 only after
building a room type, its rooms and a rate.

Idempotent: a property that already has slabs is skipped, so a hotel that has edited its
own is never overwritten with the statutory defaults.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_reference_data
"""
import asyncio
import logging
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import db as _db_module  # noqa: E402
from scoped_db import PropertyScopedDatabase  # noqa: E402
from services.reference_data import seed_reference_data  # noqa: E402

logger = logging.getLogger(__name__)


async def backfill() -> tuple[int, int]:
    """Returns (properties given data, properties already current)."""
    properties = await _db_module.unscoped_db.properties.find(
        {}, {"_id": 0}).to_list(10000)
    seeded = skipped = 0
    for record in properties:
        created = await seed_reference_data(PropertyScopedDatabase(record["id"]))
        if created["tax_slabs"] or created["meal_plans"]:
            seeded += 1
        else:
            skipped += 1
    return seeded, skipped


async def main() -> None:
    seeded, skipped = await backfill()
    print(f"properties seeded: {seeded}, already current: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
