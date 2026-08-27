"""One-shot: give every existing property an outlet GST rate and an inclusive flag.

Idempotent — a property that already carries both fields is left alone, so re-running is
safe.

Backfilling to the statutory default (5%, exclusive) rather than to the 10% these
records were actually billed at is deliberate. 10% is not an Indian GST rate; it is the
bug. A migration that preserved it would carry the wrong figure forward forever on the
grounds that it was there yesterday, and the first bill printed after this deploys would
still be one no guest could lawfully be charged.

**Key presence, not truthiness.** `not record.get("outlet_gst_rate")` would find an
unregistered business's deliberate 0% falsy and stamp 5% over it, and that business
would start collecting tax it has no registration to collect. The same reasoning as
`backfill_domains.py`'s `active` flag, and the same trap.

Historic orders are not touched, here or anywhere. A settled bill keeps the total it was
settled at — the guest paid what the printed bill said, and changing it retrospectively
puts the hotel's books out. See routers/orders.py::compute_totals.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_outlet_gst
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from services.tax import DEFAULT_GST_INCLUSIVE, DEFAULT_OUTLET_GST_RATE  # noqa: E402


async def backfill() -> tuple[int, int]:
    """Bring every property up to date. Returns (updated, already current).

    Also imported by server.py and run at startup: this app deploys as a container and
    as a Cloud Function, neither of which has a manual shell step, so a migration nobody
    runs is a migration that never runs.
    """
    properties = await unscoped_db.properties.find({}, {"_id": 0}).to_list(10000)
    updated = skipped = 0
    for record in properties:
        patch = {}
        if record.get("outlet_gst_rate") is None:
            patch["outlet_gst_rate"] = DEFAULT_OUTLET_GST_RATE
        if record.get("gst_inclusive") is None:
            patch["gst_inclusive"] = DEFAULT_GST_INCLUSIVE
        if not patch:
            skipped += 1
            continue
        await unscoped_db.properties.update_one({"id": record["id"]}, {"$set": patch})
        updated += 1
    return updated, skipped


async def main() -> None:
    updated, skipped = await backfill()
    print(f"properties updated: {updated}, already current: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
