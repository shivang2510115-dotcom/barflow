"""The statutory reference data every property needs before it can quote a room.

Room GST bands and meal plans are not demo content — a hotel cannot price a single night
without a slab covering its tariff, and cannot take a booking without a meal plan to book
on. They were seeded once, globally, with no `property_id`, which worked for exactly as
long as there was one property: after tenancy landed, the founding hotel kept them through
the backfill and every hotel that registered afterwards got none. The symptom was a 500
out of `GET /availability` — `No GST slab covers tariff 4500.0` — reached only after the
owner had already created a room type, its rooms and a rate, which is a long way to walk
to find out the product does not work.

The rates below are India's, correct as of 2026-08: 12% at or under ₹7,500 a night, 18%
above. They are seeded rather than hardcoded because they change by statute and the hotel
edits them from its own Rates screen when that happens.
"""
import uuid


def gst_slabs() -> list[dict]:
    return [
        {"id": str(uuid.uuid4()), "min_tariff": 0.0, "max_tariff": 7500.0,
         "rate_percent": 12.0, "active": True},
        {"id": str(uuid.uuid4()), "min_tariff": 7500.0, "max_tariff": None,
         "rate_percent": 18.0, "active": True},
    ]


def meal_plans() -> list[dict]:
    """European, Continental and Modified American — the plans Indian hotels quote on.

    The prices are a starting point the hotel edits, not a claim about what breakfast
    costs. `EP` must exist and be free: it is "room only", the default a booking falls
    back to, and a property whose cheapest plan carries a charge would silently add one
    to every quote.
    """
    return [
        {"id": str(uuid.uuid4()), "code": "EP", "name": "Room only",
         "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0, "active": True},
        {"id": str(uuid.uuid4()), "code": "CP", "name": "With breakfast",
         "price_per_adult_per_night": 500.0, "price_per_child_per_night": 250.0, "active": True},
        {"id": str(uuid.uuid4()), "code": "MAP", "name": "Half board",
         "price_per_adult_per_night": 1200.0, "price_per_child_per_night": 600.0, "active": True},
    ]


async def seed_reference_data(db) -> dict:
    """Give one property its GST bands and meal plans, if it has none.

    `db` is a property-scoped handle, so the writes are stamped and the counts are that
    property's own. Idempotent by counting first: a hotel that has edited its slabs must
    not have the statutory defaults put back on top of its own.
    """
    created = {"tax_slabs": 0, "meal_plans": 0}
    if await db.tax_slabs.count_documents({}) == 0:
        rows = gst_slabs()
        await db.tax_slabs.insert_many(rows)
        created["tax_slabs"] = len(rows)
    if await db.meal_plans.count_documents({}) == 0:
        rows = meal_plans()
        await db.meal_plans.insert_many(rows)
        created["meal_plans"] = len(rows)
    return created
