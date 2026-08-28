"""One-shot: tell every existing property that it already sells meal plans.

Idempotent — a property that already carries `meal_plans_enabled`, either way round, is
left alone, so re-running is safe.

**Why `True` here when the field's default is `False`.** They answer different questions.
`services.pricing.DEFAULT_MEAL_PLANS_ENABLED` is what a hotel signing up *tomorrow* gets:
one all-inclusive rate, because that is the simpler model and what most small Indian
hotels actually run. This migration is about a property that has been trading since
*yesterday*, and yesterday it quoted EP, CP and MAP — a room type with a breakfast
supplement of ₹500 an adult a night was quoting ₹1,000 more on a two-adult booking than
its base rate. Stamping the new default onto it would silently drop that supplement out
of every quote the morning this deploys, and the first anyone would know is a guest
paying less for breakfast than the hotel buys it for.

The reverse mistake is cheap: a hotel that wanted the single rate all along sees three
plans until its owner opens the settings screen and turns them off, which is one click
and changes no money that has already been quoted. So the migration preserves behaviour
and the owner opts in to the new model, exactly as `backfill_property_type.py` stamps
`both` on the grounds that a record written before that field existed has been running
rooms and outlets all along.

**Key presence, NOT truthiness.** `if not record.get("meal_plans_enabled")` would find a
hotel's deliberate `False` falsy and switch its meal plans back on at every restart,
undoing the owner's decision from the settings screen on a schedule. Same trap as the
`active` flag in `backfill_domains.py` and the 0% rate in `backfill_outlet_gst.py`.

Existing bookings are not touched, here or anywhere. A booking keeps the plan it was
taken on and the quote the guest was given; see routers/bookings.py, where the plan on
the booking — not the setting on the property — is what prices that booking forever.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_meal_plans
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402

# What a property that predates the field was doing before it existed: quoting per meal
# plan, because there was no other way to quote.
PREDATES_THE_FIELD = True


async def backfill() -> tuple[int, int]:
    """Bring every property up to date. Returns (updated, already current).

    Also imported by server.py and run at startup: this app deploys as a container and
    as a Cloud Function, neither of which has a manual shell step, so a migration nobody
    runs is a migration that never runs.
    """
    properties = await unscoped_db.properties.find({}, {"_id": 0}).to_list(10000)
    updated = skipped = 0
    for record in properties:
        if record.get("meal_plans_enabled") is not None:
            skipped += 1
            continue
        await unscoped_db.properties.update_one(
            {"id": record["id"]}, {"$set": {"meal_plans_enabled": PREDATES_THE_FIELD}})
        updated += 1
    return updated, skipped


async def main() -> None:
    updated, skipped = await backfill()
    print(f"properties updated: {updated}, already current: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
