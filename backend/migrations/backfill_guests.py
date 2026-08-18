"""One-shot: create a guest for every distinct customer_phone in orders.

Idempotent — existing guests are skipped, so re-running is safe. Run once after
deploying the hotel module:

    cd backend && MONGO_URL=... python3 -m migrations.backfill_guests

Run per property, through a scoped handle. Orders and guests both belong to a hotel, and
the same phone number can be a regular at two of them; one pass over the whole database
would file the first hotel's customer under whichever hotel it happened to reach first.
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from scoped_db import PropertyScopedDatabase  # noqa: E402


async def backfill_property_guests(db) -> tuple[int, int]:
    orders = await db.orders.find(
        {"customer_phone": {"$nin": [None, ""]}}, {"_id": 0}
    ).to_list(100000)

    # Most recent name wins, so a corrected spelling beats an older typo.
    latest: dict[str, dict] = {}
    for o in sorted(orders, key=lambda o: o.get("created_at") or ""):
        phone = (o.get("customer_phone") or "").strip()
        if phone:
            latest[phone] = o

    created = skipped = 0
    for phone, order in latest.items():
        if await db.guests.find_one({"phone": phone}):
            skipped += 1
            continue
        await db.guests.insert_one({
            "id": str(uuid.uuid4()),
            "name": (order.get("customer_name") or "Guest").strip(),
            "phone": phone,
            "email": None, "address": None, "nationality": None,
            "id_proof_type": None, "id_proof_number": None,
            "notes": "Imported from outlet order history",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        created += 1

    return created, skipped


async def main() -> None:
    properties = await unscoped_db.properties.find({}, {"_id": 0}).to_list(1000)
    for record in properties:
        created, skipped = await backfill_property_guests(
            PropertyScopedDatabase(record["id"]))
        print(f"{record.get('name')}: guests created: {created}, "
              f"already present: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
