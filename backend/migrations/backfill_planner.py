"""One-shot: the planner screen for the people who plan, and a vocabulary to plan in.

Idempotent in both halves — a user who already holds the key is left alone, and a property
that already has categories is not given the defaults on top of its own — so re-running is
a no-op.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_planner

**Why the screen half exists at all.** `backfill_permissions` fills in a `permissions`
field that is *missing* and deliberately never touches one that is present, which is what
stops an account the owner narrowed to two screens being widened back on the next restart.
That is the right rule, and it has the consequence that a screen key added after a
deployment has been running reaches nobody: every account already has a `permissions` list
and it predates the key. `hotel.housekeeping` was the first key of that shape and
`migrations/backfill_housekeeping.py` was the first migration of it; this is the same
migration for `property.planner`.

**Who is granted it.** Admins and managers, provided they hold a domain at all — the two
roles whose stored screens are a computed "everything this role does" rather than a
hand-written list, and exactly the audience `admin.analytics` has. Planning the property's
week is their job.

**Nobody else is granted it, and nobody else needs it.** The key guards *writing* to the
calendar. Reading it is open to anyone signed in who works in this property — see
`routers/planner.py::READ`, which declares the domain and neither a role nor a key — so a
waiter sees Tuesday's briefing without holding anything, and no migration has to reach
them for that to be true. Granting the key to a waiter would not widen what they see; it
would let them edit the property's plan, which is not what "a waiter should be able to
read it" asked for.

**The categories half** is here rather than left to signup for the reason
`migrations/backfill_reference_data.py` exists: signup seeds them now, but every property
that registered before this feature has none, and a planner whose category picker is empty
is a screen on which no event can be created at all. Seeded per property, through that
property's own scoped handle, because the whole point of the collection is that the names
are the hotel's own.
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import db as _db_module  # noqa: E402
from scoped_db import PropertyScopedDatabase  # noqa: E402
from services.planner import seed_categories  # noqa: E402

SCREEN = "property.planner"

# The roles whose stored screens are a computed "everything this role does" rather than a
# hand-written list. See the module docstring for why nobody else appears here.
GRANT_TO = ("admin", "manager")


async def grant_screen() -> tuple[int, int]:
    """Give the planner key to the people who plan. Returns (granted, already current).

    An account with no `permissions` key at all is left to `backfill_permissions`, which
    runs first at startup and derives the whole set from the role — including this key,
    since `ROLE_SCREENS` computes admin's and manager's from `SCREEN_KEYS`. Touching such
    an account here would grant it one screen and hide it from the migration whose job
    that is.

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


async def seed_every_property() -> tuple[int, int]:
    """Give each property the default categories, if it has none.

    Returns (properties seeded, properties already current). Reached through an unscoped
    read of `properties` and then a *scoped* handle per property — this is a migration, it
    crosses every tenant on purpose, and each write is still stamped with exactly one of
    them.
    """
    properties = await _db_module.unscoped_db.properties.find({}, {"_id": 0}).to_list(10000)
    seeded = current = 0
    for record in properties:
        if await seed_categories(PropertyScopedDatabase(record["id"])):
            seeded += 1
        else:
            current += 1
    return seeded, current


async def backfill() -> tuple[int, int, int, int]:
    """Both halves. Returns (granted, already held, properties seeded, already current)."""
    granted, held = await grant_screen()
    seeded, current = await seed_every_property()
    return granted, held, seeded, current


async def main() -> None:
    granted, held, seeded, current = await backfill()
    print(f"planner screen granted to {granted} user(s), {held} already held it; "
          f"{seeded} propert(ies) seeded with categories, {current} already current")


if __name__ == "__main__":
    asyncio.run(main())
