"""One-shot: the expenses screen for the people who already read the money, and the
default categories for every property that predates them.

Idempotent in both halves — a user who already holds the key is left alone, and a
property that already has categories is never given the defaults on top of its own — so
re-running is a no-op.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_expenses

**Why the grant is needed at all.** `backfill_permissions` fills in a `permissions` field
that is *missing* and deliberately never touches one that is present, which is what stops
an account the owner narrowed to two screens being widened back on every restart. The
consequence is that a screen key invented after a deployment has been running reaches
nobody: every account already has a list, and the list predates the key.
`migrations/backfill_housekeeping.py` is the same shape for the same reason.

**Who is granted it, and why exactly those two.** Admins and managers, provided they hold
a domain — a tick outside somebody's domains is refused at request time anyway, and
storing one would be a lie on the staff screen (see
`services.access.permission_in_domains`).

That is the audience `admin.analytics` already has, and the match is the point. This
screen shows revenue as well as spending, because "what is left" is not answerable
without both; granting it more widely than the revenue screen would hand the property's
turnover to people the owner had already decided should not see it, through a screen
whose name says "expenses". The owner widens it deliberately from the staff screen — an
accountant or a front-desk supervisor entering bills is exactly the case, and the read
endpoints name no role at all so the tick is the whole decision for them.

`front_desk`, `waiter`, `kitchen` and `housekeeping` are not granted it, for the same
reason `backfill_housekeeping` did not grant its key to the desk: their entries in
`ROLE_SCREENS` are hand-written short lists describing what those jobs reach, and
overruling one here would change that decision on every property at once.
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import db as _db_module  # noqa: E402
from scoped_db import PropertyScopedDatabase  # noqa: E402
from services.expenses import seed_expense_categories  # noqa: E402

SCREEN = "admin.expenses"

# The roles whose stored screens are a computed "everything this role does" rather than a
# hand-written list — `ROLE_SCREENS` gives admin the whole catalogue and manager all of it
# but `admin.staff`, so a new key belongs to both by that table's own rule. This migration
# only has to reach the accounts that already existed when the key was added.
GRANT_TO = ("admin", "manager")


async def grant_screen() -> tuple[int, int]:
    """Give the expenses screen to the roles that read the property's money.

    Returns (granted, already current).

    An account with no `permissions` key at all is left to `backfill_permissions`, which
    runs first at startup and derives the whole set from the role — including this key,
    since `SCREENS` now carries it and `ROLE_SCREENS` computes admin's and manager's sets
    from `SCREEN_KEYS`. Touching such an account here would grant it one screen and hide
    it from the migration whose job that is.
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


async def seed_categories() -> tuple[int, int]:
    """Give every property the default categories, if it has none.

    Returns (properties seeded, already current). Signup seeds these at the moment a
    tenant comes into existence; this is for the ones that came into existence first.
    Reached through a scoped handle per property, so the rows are stamped with the
    property they belong to rather than written loose — the mistake
    `services/reference_data.py` was written to undo.
    """
    properties = await _db_module.unscoped_db.properties.find({}, {"_id": 0}).to_list(10000)
    seeded = current = 0
    for record in properties:
        if await seed_expense_categories(PropertyScopedDatabase(record["id"])):
            seeded += 1
        else:
            current += 1
    return seeded, current


async def backfill() -> tuple[int, int, int, int]:
    """Both halves. Returns (screens granted, already held, properties seeded, current)."""
    granted, held = await grant_screen()
    seeded, current = await seed_categories()
    return granted, held, seeded, current


async def main() -> None:
    granted, held, seeded, current = await backfill()
    print(f"expenses screen granted to {granted} user(s), {held} already held it; "
          f"{seeded} propert(ies) seeded with categories, {current} already current")


if __name__ == "__main__":
    asyncio.run(main())
