"""One-shot: the Bills screen for the people who read a property's takings.

`hotel.bills` is a screen key invented after these deployments started running, and
`backfill_permissions` deliberately fills in a permissions field that is *missing* and
never touches one that is present — which is what stops an account an owner narrowed to
two screens being widened back on the next restart. The consequence is that a new key
reaches nobody already hired, so it needs a migration of its own. `backfill_housekeeping`
was the first of this shape and `backfill_planner` the most recent.

Idempotent: a user who already holds the key is left alone. Runs from `on_startup()` on
every boot, because the deployment has no shell step to run a script from.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_bills
"""
import logging

import db as _db_module

logger = logging.getLogger(__name__)

SCREEN = "hotel.bills"

# Admins and managers. Drawing a bill for the guest in front of you rides on
# `hotel.front_desk` and reaches a receptionist already; this key is the wider question —
# every bill the property has issued — which is a manager's to ask.
GRANT_TO = ("admin", "manager")


async def backfill() -> tuple[int, int]:
    """Give the bills key to the roles that already read this property's money.

    Returns (granted, already held).

    An account with no `permissions` key at all is left to `backfill_permissions`, which
    runs first at startup and derives the whole set from the role — including this key,
    since ROLE_SCREENS computes admin's and manager's from SCREEN_KEYS. Touching such an
    account here would grant it one screen and hide it from the migration whose job that
    is.

    A user holding no domain is skipped: `permission_in_domains` refuses such a tick when
    it is saved, so storing one would put a mark on the staff screen that does nothing.
    """
    users = await _db_module.unscoped_db.users.find({}, {"_id": 0}).to_list(10000)
    granted = current = 0
    for user in users:
        held = user.get("permissions")
        if held is None:
            continue
        if user.get("role") not in GRANT_TO or not (user.get("domains") or ()):
            continue
        if SCREEN in held:
            current += 1
            continue
        await _db_module.unscoped_db.users.update_one(
            {"id": user["id"]}, {"$set": {"permissions": [*held, SCREEN]}})
        granted += 1
    return granted, current
