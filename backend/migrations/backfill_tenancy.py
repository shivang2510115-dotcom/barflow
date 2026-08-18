"""One-shot: give every existing record the hotel it has always belonged to.

`backfill_property` makes the running hotel the first tenant and stamps its users. This
does the same for everything the hotel owns — its bookings, rooms, rates, folios, orders,
tables, menu, stock and guests — because from the moment the routers take a scoped handle,
a document with no `property_id` matches no query: the front desk opens on an empty
arrivals board, the menu is blank, and nothing says why. Unstamped data is not lost, it is
invisible, which is worse.

Idempotent. It stamps only documents that have no `property_id`, so a second run reports
zero and changes nothing — which is how it actually runs, on every startup, because this
app deploys as a container with no manual shell step.

It does nothing at all when the database holds no property, or several. Several means
tenancy is already live and signup has been creating hotels; at that point "which hotel
does this row belong to" is not this migration's to guess, and a guess hands one hotel's
data to another.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_tenancy
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import unscoped_db  # noqa: E402
from scoped_db import PROPERTY_FIELD, SCOPED_COLLECTIONS  # noqa: E402


async def backfill() -> tuple[str | None, int, dict[str, int]]:
    """Stamp every unscoped document. Returns (property_id, stamped, per collection).

    The collection list comes from `scoped_db`, not from a copy kept here: the module
    that decides what "scoped" means is the one that has to name the collections, or the
    day somebody adds one they update a list that this migration never reads.
    """
    properties = await unscoped_db.properties.find({}, {"_id": 0}).to_list(100)
    if len(properties) != 1:
        return None, 0, {}
    property_id = properties[0]["id"]

    per_collection: dict[str, int] = {}
    for name in SCOPED_COLLECTIONS:
        # `$exists: False` rather than a null test, so a row stamped by an earlier run is
        # not rewritten — that is what makes the second run free rather than merely
        # harmless.
        result = await unscoped_db[name].update_many(
            {PROPERTY_FIELD: {"$exists": False}},
            {"$set": {PROPERTY_FIELD: property_id}})
        if result.modified_count:
            per_collection[name] = result.modified_count
    return property_id, sum(per_collection.values()), per_collection


async def main() -> None:
    property_id, stamped, per_collection = await backfill()
    if property_id is None:
        print("no single property to stamp against — nothing done")
        return
    detail = ", ".join(f"{name} {count}" for name, count in sorted(per_collection.items()))
    print(f"property {property_id}: {stamped} document(s) stamped"
          + (f" ({detail})" if detail else ""))


if __name__ == "__main__":
    asyncio.run(main())
