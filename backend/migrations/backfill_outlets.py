"""One-shot: the outlets every existing property has been running all along.

Before outlets were rows, a property's restaurant and bar existed only as work domains
on its record. This creates the matching rows, so a property that has been taking
restaurant orders for months has a restaurant to point them at.

Idempotent, and it has to be: it runs from `on_startup()` on every boot forever, because
the deployment has no shell step from which to run a script once. A property that already
has an outlet of a kind is left alone — including one whose admin has renamed it, which
is why the check is on `kind` and never on `name`.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_outlets
"""
import logging
import uuid
from datetime import datetime, timezone

import db as _db_module
from scoped_db import PropertyScopedDatabase
from services.access import DEFAULT_PROPERTY_TYPE, domains_for_property_type
from services.outlets import KIND_DOMAIN, default_name

logger = logging.getLogger(__name__)

# The domains that used to mean "this property has one of these". `services` is
# deliberately absent: it is new in the same change that added it, so no existing
# property has ever run a salon, and creating one here would invent an outlet the hotel
# never had.
_EXISTING_KINDS = ("restaurant", "bar")


def outlets_for_domains(domains: list[str]) -> list[dict]:
    """What outlets a property holding these domains has been operating.

    Pure — no uuid, no clock — so the decision can be tested for the stability this
    migration depends on. Ids and timestamps are added by the caller.
    """
    held = set(domains or [])
    return [
        {
            "name": default_name(kind),
            "kind": kind,
            "domain": KIND_DOMAIN[kind],
            # What a restaurant in this product has always been able to do: charge a
            # resident to their room, or take payment at the table.
            "charges_to_folio": True,
            "takes_direct_payment": True,
            "active": True,
        }
        for kind in _EXISTING_KINDS if kind in held
    ]


async def backfill() -> tuple[int, int, int]:
    """Create missing outlet rows and point existing staff at them.

    Returns (outlets_created, users_pointed, properties_already_current).
    """
    created = pointed = current = 0
    properties = await _db_module.unscoped_db.properties.find({}, {"_id": 0}).to_list(5000)

    for prop in properties:
        pid = prop["id"]
        scoped = PropertyScopedDatabase(pid)

        existing = await scoped.outlets.find({}, {"_id": 0}).to_list(200)
        by_kind = {o.get("kind"): o["id"] for o in existing}
        made_here = 0

        domains = list(domains_for_property_type(
            prop.get("property_type") or DEFAULT_PROPERTY_TYPE))

        for row in outlets_for_domains(domains):
            if row["kind"] in by_kind:
                continue
            made = {**row, "id": str(uuid.uuid4()),
                    "created_at": datetime.now(timezone.utc).isoformat()}
            await scoped.outlets.insert_one(made)
            by_kind[row["kind"]] = made["id"]
            made_here += 1

        created += made_here
        if not made_here:
            current += 1

        # Point each staff member at the outlets matching the domains they already hold.
        #
        # Only users whose `outlet_ids` is *missing* are touched, never one that is
        # present and empty. `backfill_permissions` learned this rule the hard way: an
        # account an owner deliberately narrowed must not be widened again on the next
        # restart. Here it matters twice over, because empty already means "not
        # narrowed" to require_outlet — so an absent list and an empty one mean the same
        # thing at read time, and filling the absent one is only about making the record
        # say what it holds.
        users = await _db_module.unscoped_db.users.find(
            {"property_id": pid}, {"_id": 0}).to_list(5000)
        for u in users:
            if "outlet_ids" in u:
                continue
            held = set(u.get("domains") or [])
            ids = [oid for kind, oid in by_kind.items() if kind in held]
            await _db_module.unscoped_db.users.update_one(
                {"id": u["id"]}, {"$set": {"outlet_ids": ids}})
            pointed += 1

    return created, pointed, current
