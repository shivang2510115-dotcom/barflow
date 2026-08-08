"""One-shot: create a guest for every distinct customer_phone in orders.

Idempotent — existing guests are skipped, so re-running is safe. Run once after
deploying the hotel module:

    cd backend && MONGO_URL=... python3 -m migrations.backfill_guests
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import db  # noqa: E402


async def main() -> None:
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

    print(f"guests created: {created}, already present: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
