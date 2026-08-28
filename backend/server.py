"""BarFlow API — application assembly."""
from dotenv import load_dotenv
from pathlib import Path
import os
import logging

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware

from db import unscoped_db, client, check_connection, using_mock
from security import hash_password
from routers import auth, staff, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings, frontdesk, folios, analytics, permissions, property as property_router, signup, platform, invoices, messaging
from routers.tables import Table
from routers.menu import MenuItem
from routers.inventory import InventoryItem
from routers.payments import webhook_secret
from routers.reports import daily_brief_scheduler, in_process_brief_enabled
from models.hotel import Rate, Room, RoomType
from services.access import DOMAINS
from services.password import password_problem
from migrations.backfill_domains import backfill as backfill_domains
from migrations.backfill_permissions import backfill as backfill_permissions
from migrations.backfill_property import backfill as backfill_property
from migrations.backfill_outlet_gst import backfill as backfill_outlet_gst
from migrations.backfill_meal_plans import backfill as backfill_meal_plans
from migrations.backfill_property_type import backfill as backfill_property_type
from migrations.backfill_tenancy import backfill as backfill_tenancy
from migrations.backfill_reference_data import backfill as backfill_reference_data
from migrations.encrypt_guest_ids import backfill as encrypt_guest_ids
from services.crypto import ENV_VAR as GUEST_ID_KEY_VAR, encryption_configured

# Same reasoning as JWT_SECRET in security.py: this password is published in a public
# repository, so seeding it against a real database hands the first admin account to
# anyone who has read the source. Checked at import, before any database call, so an
# unreachable database cannot mask it — the connection error would be the last thing
# anyone saw, and the account would be seeded wide open on the retry that succeeds.
DEFAULT_ADMIN_PASSWORD = "admin123"

if os.environ.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD) == DEFAULT_ADMIN_PASSWORD \
        and not using_mock:
    raise RuntimeError(
        "ADMIN_PASSWORD is still the default published in this repository, and MONGO_URL "
        "points at a real database. Set ADMIN_PASSWORD to something private and restart."
    )

# The origins a browser may call this API from while carrying a session, when nobody has
# said. Localhost only, and only ever reached under the JSON mock — see cors_origins().
# Both hostnames because `localhost` and `127.0.0.1` are different origins to a browser
# and the two dev servers are started either way; 3000 is Create React App's default and
# 3001 is what it falls back to when 3000 is taken.
CORS_DEV_ORIGINS = (
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:3001", "http://127.0.0.1:3001",
)


def cors_origins() -> list[str]:
    """The websites allowed to call this API with a logged-in user's credentials.

    `allow_credentials=True` is not negotiable here — the client sends a session — and
    that makes `*` the whole hole rather than a loose end: the browser attaches the
    session, the origin check passes for everybody, and any page a signed-in manager
    opens in another tab can read their hotel's guest list and post to their folios. So
    a wildcard is refused outright rather than narrowed or warned about. There is no
    deployment of this application for which it is the right answer.

    Unset splits on the same signal `JWT_SECRET` and `ADMIN_PASSWORD` already use:

    * against the JSON mock this is a laptop, and the answer is the two local dev server
      origins. A fresh clone runs `npm start` and works, which is the whole reason the
      old default existed — it just did not have to be `*` to achieve it;
    * against a real database this refuses to start, and says so by name. The alternative
      considered was defaulting to same-origin only (an empty list). Both leave the
      deployment needing the same one action, and they differ in how it is discovered:
      an empty list surfaces as a CORS error in a browser console on somebody else's
      machine, which reads like a frontend bug and is the classic afternoon lost; a
      named RuntimeError in the deploy log says which variable to set. The louder
      failure is the kinder one, and it matches the two guards already above.
    """
    configured = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",")
                  if o.strip()]
    if "*" in configured:
        raise RuntimeError(
            "CORS_ORIGINS contains '*' and this API allows credentials, so any website a "
            "signed-in user visits could call it with their session. List the frontend's "
            "full origins instead, comma-separated, e.g. https://barflow-web.onrender.com"
        )
    if configured:
        return configured
    if using_mock:
        return list(CORS_DEV_ORIGINS)
    raise RuntimeError(
        "CORS_ORIGINS is not set and MONGO_URL points at a real database. Set it to the "
        "frontend's full origin, with scheme and comma-separated if there is more than "
        f"one, e.g. https://barflow-web.onrender.com — locally it defaults to "
        f"{', '.join(CORS_DEV_ORIGINS)}."
    )


app = FastAPI(title="BarFlow API")
api_router = APIRouter(prefix="/api")

for module in (auth, staff, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings, frontdesk, folios, analytics, permissions, property_router, signup, platform, invoices, messaging):
    api_router.include_router(module.router)


@api_router.get("/")
async def root():
    return {"service": "BarFlow API", "status": "ok"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------- Seed -----------------
def demo_content_enabled() -> bool:
    """Whether to seed the showcase bar — its tables, menu, stock, rooms and rates.

    Off unless asked for, the opposite of `DEMO_LOGINS`' default, and for a reason the
    two do not share: a demo login is a door that a public deployment must not leave
    open, while demo content is furniture that a *real* hotel would have to delete by
    hand, item by item, before it could trust a single number on its own dashboard.
    Three room types it never had make its occupancy wrong on day one.

    So a fresh production start creates only what every property needs regardless of who
    they are: the admin account, the GST bands and the three meal plans. Those are
    reference data — statutory rates and the standard Indian board codes — not somebody
    else's inventory. Set SEED_DEMO_CONTENT=true for a clone you want usable in one
    command.
    """
    return os.environ.get("SEED_DEMO_CONTENT", "false").lower() == "true"


async def _unique_identifier_index(field: str) -> None:
    """`users.<field>` unique among the accounts that actually have one.

    **Partial, not plain, and that is the whole reason this is a function.** Either
    identifier may now be absent — a waiter has a phone and no email, an owner the other
    way round — and both are stored as an explicit `None`. A plain unique index treats
    every one of those nulls as the same value, so the *second* phone-only account on
    real MongoDB would be refused for duplicating an email address it does not have. The
    partial filter indexes only the documents where the field is a string, which is the
    same reasoning `folio_entries` already uses one for, one block down.

    Sparse would not do it either, for the same reason it does not there: `None` is a
    present key, so a sparse index still covers every account holding one.

    The catch is for an existing MongoDB deployment. `users.email` was declared plain
    unique before this change, and Mongo refuses to redefine an index whose options
    differ rather than replacing it. Dropping it here would mean a startup path that
    quietly deletes a uniqueness constraint on the users collection, which is not a thing
    that should happen without somebody deciding to — so it is said out loud instead,
    naming the exact command. Until it is run, that deployment keeps the old index and
    behaves exactly as it does today for email; the phone index is new and unaffected.
    Firestore and the JSON mock no-op `create_index` entirely, so neither sees any of
    this, and the routers' pre-checks are what enforce uniqueness there regardless.
    """
    try:
        await unscoped_db.users.create_index(
            field, unique=True,
            partialFilterExpression={field: {"$type": "string"}})
    except Exception as exc:  # noqa: BLE001 — surface anything the driver raises
        logger.warning(
            "users.%s could not be indexed as unique-when-present (%s). If this is "
            "MongoDB carrying the older plain index, drop it once and restart: "
            "db.users.dropIndex('%s_1'). Until then an account with no %s may be "
            "refused as a duplicate of another that has none either. The duplicate "
            "pre-checks in routers/staff.py and routers/signup.py are unaffected.",
            field, exc, field, field)


async def seed_data():
    # Indexes.
    #
    # Every one of these is now compound on property_id, and every unique one is unique
    # *per property* rather than globally. Two hotels genuinely do both have a room 101, a
    # guest whose phone number is the same person eating in both, and a booking numbered
    # BF-2608-0001 — a global unique index would let whichever hotel signed up first stop
    # the second from entering its own data, with a duplicate-key error naming a record
    # they cannot see. Leading with property_id also matches how every query now reads:
    # the scoped handle puts it in the filter, so an index that does not start there is
    # one the planner cannot use.
    #
    # `users.email` and `users.phone` stay globally unique on purpose. A login is found by
    # its identifier before anyone knows which hotel it belongs to, so two hotels cannot
    # share one.
    #
    # None of this takes effect against the JSON mock, whose create_index is a no-op —
    # locally the routers' own duplicate checks (now scoped) are what enforce it.
    await _unique_identifier_index("email")
    await _unique_identifier_index("phone")
    await unscoped_db.tables.create_index([("property_id", 1), ("label", 1)])
    await unscoped_db.reservations.create_index([("property_id", 1), ("date", 1)])
    await unscoped_db.menu.create_index([("property_id", 1), ("category", 1)])
    await unscoped_db.guests.create_index([("property_id", 1), ("phone", 1)], unique=True)
    await unscoped_db.bookings.create_index([("property_id", 1), ("room_type_id", 1), ("check_in", 1), ("check_out", 1), ("status", 1)])
    await unscoped_db.bookings.create_index([("property_id", 1), ("reference", 1)], unique=True)
    await unscoped_db.rooms.create_index([("property_id", 1), ("room_type_id", 1)])
    await unscoped_db.rooms.create_index([("property_id", 1), ("number", 1)], unique=True)
    await unscoped_db.folios.create_index([("property_id", 1), ("booking_id", 1)], unique=True)
    await unscoped_db.folio_entries.create_index([("property_id", 1), ("folio_id", 1)])
    # Unique against real MongoDB; mocked create_index is a no-op.
    # Actual idempotency comes from services/folio.py::unposted_nights.
    # Partial, not sparse: a compound sparse index only skips a document missing ALL of
    # its keys, and folio_id/kind are always present, so a plain sparse index would still
    # cover every payment/misc_charge/outlet/void entry (all with charge_date: null) and
    # the second such entry on a folio would raise a duplicate-key error. The partial
    # filter limits uniqueness to the room-night rows that actually need it.
    await unscoped_db.folio_entries.create_index(
        [("property_id", 1), ("folio_id", 1), ("charge_date", 1)], unique=True,
        partialFilterExpression={"kind": "room_night"})

    # The platform's own tax documents. `number` is globally unique — a series with two
    # documents under one number is not a series — and `payment_id` is unique among
    # invoices so that one recorded payment can never carry two of them. Partial, not
    # sparse, for the reason above: a credit note has no payment_id, and a plain sparse
    # index would still cover every one of them and refuse the second.
    #
    # Against MongoDB these are what hold when more than one process issues at once.
    # Both the JSON mock and Firestore no-op create_index, so there the in-process lock
    # in routers/invoices.py is the whole guarantee — the same limitation every other
    # unique index in this function already has.
    await unscoped_db.platform_invoices.create_index("number", unique=True)
    await unscoped_db.platform_invoices.create_index(
        "payment_id", unique=True,
        partialFilterExpression={"kind": "invoice"})

    # Customer messaging. The first is the query the occasions screen is: everybody in
    # this property whose birthday or anniversary falls on today's month and day.
    await unscoped_db.occasions.create_index([("property_id", 1), ("month_day", 1)])
    await unscoped_db.occasions.create_index([("property_id", 1), ("guest_id", 1)])
    await unscoped_db.message_log.create_index([("property_id", 1), ("sent_at", -1)])
    await unscoped_db.message_log.create_index([("property_id", 1), ("guest_id", 1)])
    # The one that makes a double press structurally impossible rather than merely
    # unlikely: the claim on one greeting, to one person, about one thing, on one day.
    # Against MongoDB this is what holds when two members of staff press send at the same
    # moment on two terminals. Both the JSON mock and Firestore no-op create_index, so
    # there the atomic upsert in routers/messaging.py::_claim is the whole guarantee —
    # the same limitation every other unique index in this function already has, and the
    # reason that claim is an upsert rather than a read followed by an insert.
    await unscoped_db.message_claims.create_index(
        [("property_id", 1), ("key", 1)], unique=True)

    # Seed admin + staff
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@barflow.io").lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

    # Warned about, not refused. The strength rule guards the doors a person types a
    # password into; this one comes from a deployment variable, and turning a weak value
    # here into a failed start would take a hotel offline on a restart for something
    # that was already true before it. Only against a real database, or every local
    # clone would say this about the "admin123" it is meant to have.
    if not using_mock:
        for label, value in (("ADMIN_PASSWORD", admin_pw),
                             ("PLATFORM_ADMIN_PASSWORD",
                              os.environ.get("PLATFORM_ADMIN_PASSWORD") or "")):
            if value and password_problem(value, admin_email if label ==
                                          "ADMIN_PASSWORD" else None):
                logger.warning(
                    "%s would be refused if it were typed into the app: %s", label,
                    password_problem(value))

    default_users = [
        {"email": admin_email, "name": "Alex Mercer", "role": "admin", "password": admin_pw},
    ]

    # The manager/waiter/kitchen logins below use passwords that are published in this
    # repo. They exist so a fresh clone is usable immediately, and must never be seeded
    # on a public deployment — set DEMO_LOGINS=false there.
    if os.environ.get("DEMO_LOGINS", "true").lower() == "true":
        default_users += [
            {"email": "manager@barflow.io", "name": "Jamie Rowe", "role": "manager", "password": "manager123"},
            {"email": "waiter@barflow.io", "name": "Riley Cole", "role": "waiter", "password": "waiter123"},
            {"email": "kitchen@barflow.io", "name": "Sam Ash", "role": "kitchen", "password": "kitchen123"},
            {"email": "frontdesk@barflow.io", "name": "Nina Patel", "role": "front_desk", "password": "desk123"},
        ]

    # The platform operator: belongs to no hotel, approves the ones that sign up. Seeded
    # only when both variables are set — inventing a default password here would repeat
    # exactly the mistake that JWT_SECRET and ADMIN_PASSWORD already guard against, and
    # this account can approve every hotel on the platform.
    op_email = (os.environ.get("PLATFORM_ADMIN_EMAIL") or "").strip().lower()
    op_pw = os.environ.get("PLATFORM_ADMIN_PASSWORD") or ""
    if op_email and op_pw:
        if await unscoped_db.users.find_one({"email": op_email}) is None:
            await unscoped_db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": op_email,
                # Seeded by email and only ever by email — PLATFORM_ADMIN_EMAIL is the
                # variable this account is configured by and nothing about that changes.
                # The key is written as an explicit None so the document has the same
                # shape as every other user record.
                "phone": None,
                "name": "Platform Operator",
                "role": "platform_admin",
                "password_hash": hash_password(op_pw),
                # No domains, no screens, and no property. Every hotel endpoint refuses
                # them; they reach /api/platform/* and nothing else.
                "domains": [], "permissions": [], "active": True,
                "property_id": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("Platform operator seeded (%s).", op_email)

    for u in default_users:
        existing = await unscoped_db.users.find_one({"email": u["email"]})
        if existing is None:
            await unscoped_db.users.insert_one({
                "id": str(uuid.uuid4()),
                # The seeded admin and the demo logins are created by email, exactly as
                # they always have been. ADMIN_EMAIL still decides the first account and
                # nothing here takes a phone number.
                "email": u["email"],
                "phone": None,
                "name": u["name"],
                "role": u["role"],
                "password_hash": hash_password(u["password"]),
                # Seeded staff work everywhere; the admin narrows them from the staff
                # screen. Seeding them narrow would lock a fresh install out of itself.
                "domains": list(DOMAINS),
                "active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        # An existing account keeps whatever password it currently has. Re-hashing the
        # seed value here would silently undo every password change on each restart.

    # Room GST bands. Editable, because these change by statute.
    if await unscoped_db.tax_slabs.count_documents({}) == 0:
        await unscoped_db.tax_slabs.insert_many([
            {"id": str(uuid.uuid4()), "min_tariff": 0.0, "max_tariff": 7500.0,
             "rate_percent": 12.0, "active": True},
            {"id": str(uuid.uuid4()), "min_tariff": 7500.0, "max_tariff": None,
             "rate_percent": 18.0, "active": True},
        ])

    if await unscoped_db.meal_plans.count_documents({}) == 0:
        await unscoped_db.meal_plans.insert_many([
            {"id": str(uuid.uuid4()), "code": "EP", "name": "Room only",
             "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0, "active": True},
            {"id": str(uuid.uuid4()), "code": "CP", "name": "With breakfast",
             "price_per_adult_per_night": 500.0, "price_per_child_per_night": 250.0, "active": True},
            {"id": str(uuid.uuid4()), "code": "MAP", "name": "Half board",
             "price_per_adult_per_night": 1200.0, "price_per_child_per_night": 600.0, "active": True},
        ])

    if demo_content_enabled():
        await seed_demo_content()


async def seed_demo_content():
    """The showcase bar-and-hotel: tables, a menu, stock, room types, rooms and rates.

    Only ever reached through `demo_content_enabled()`. Each block still checks that its
    collection is empty, so turning the flag on against a property that has already
    started entering its own data adds nothing on top of it.
    """
    # Seed tables
    if await unscoped_db.tables.count_documents({}) == 0:
        zones = [("Bar", 6, 4), ("Lounge", 4, 4), ("Patio", 3, 6)]
        seq = 1
        for zone, count, cap in zones:
            for i in range(count):
                t = Table(label=f"T{seq:02d}", capacity=cap, zone=zone).model_dump()
                await unscoped_db.tables.insert_one(t)
                seq += 1

    # Seed menu
    menu_images = {
        "Smoked Old Fashioned": "https://images.unsplash.com/photo-1536935338788-846bb9981813?w=800&q=80&auto=format&fit=crop",
        "Midnight Negroni": "https://images.unsplash.com/photo-1541546006121-5c3bc5e8c7b9?w=800&q=80&auto=format&fit=crop",
        "Copper Sour": "https://images.unsplash.com/photo-1514362545857-3bc16c4c7d1b?w=800&q=80&auto=format&fit=crop",
        "Neon Spritz": "https://images.unsplash.com/photo-1587223962930-cb7f31384c19?w=800&q=80&auto=format&fit=crop",
        "House Lager": "https://images.unsplash.com/photo-1608270586620-248524c67de9?w=800&q=80&auto=format&fit=crop",
        "Copper Ale": "https://images.unsplash.com/photo-1571613914406-6301c3b1f39f?w=800&q=80&auto=format&fit=crop",
        "Dark Stout": "https://images.unsplash.com/photo-1618885472179-5e474019f2a9?w=800&q=80&auto=format&fit=crop",
        "Malbec": "https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?w=800&q=80&auto=format&fit=crop",
        "Sauvignon Blanc": "https://images.unsplash.com/photo-1510812431401-41d2bd2722f3?w=800&q=80&auto=format&fit=crop",
        "Islay Single Malt": "https://images.unsplash.com/photo-1527281400683-1aae777175f8?w=800&q=80&auto=format&fit=crop",
        "Reposado Tequila": "https://images.unsplash.com/photo-1516997121675-4c2d1684aa3e?w=800&q=80&auto=format&fit=crop",
        "Truffle Fries": "https://images.unsplash.com/photo-1630384060421-cb20d0e0649d?w=800&q=80&auto=format&fit=crop",
        "Wagyu Sliders": "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=800&q=80&auto=format&fit=crop",
        "Charred Octopus": "https://images.unsplash.com/photo-1625944525200-2b241f0f6f8f?w=800&q=80&auto=format&fit=crop",
        "Bar Burger": "https://images.unsplash.com/photo-1571091718767-18b5b1457add?w=800&q=80&auto=format&fit=crop",
        "Crispy Chicken": "https://images.unsplash.com/photo-1562967914-608f82629710?w=800&q=80&auto=format&fit=crop",
    }
    if await unscoped_db.menu.count_documents({}) == 0:
        menu = [
            # Cocktails
            {"name": "Smoked Old Fashioned", "category": "Cocktails", "price": 14, "station": "bar", "description": "Bourbon, applewood smoke, orange bitters."},
            {"name": "Midnight Negroni", "category": "Cocktails", "price": 13, "station": "bar", "description": "Gin, Campari, sweet vermouth, charred orange."},
            {"name": "Copper Sour", "category": "Cocktails", "price": 12, "station": "bar", "description": "Mezcal, lime, agave, egg white."},
            {"name": "Neon Spritz", "category": "Cocktails", "price": 11, "station": "bar", "description": "Aperol, prosecco, blood orange."},
            # Beer
            {"name": "House Lager", "category": "Draft Beer", "price": 7, "station": "bar", "description": "Crisp pilsner on tap."},
            {"name": "Copper Ale", "category": "Draft Beer", "price": 8, "station": "bar", "description": "Amber ale, toasted malt."},
            {"name": "Dark Stout", "category": "Draft Beer", "price": 9, "station": "bar", "description": "Rich chocolate stout."},
            # Wine
            {"name": "Malbec", "category": "Wine", "price": 12, "station": "bar", "description": "Mendoza red, glass."},
            {"name": "Sauvignon Blanc", "category": "Wine", "price": 11, "station": "bar", "description": "Crisp white, glass."},
            # Spirits
            {"name": "Islay Single Malt", "category": "Spirits", "price": 18, "station": "bar", "description": "Peated, neat pour."},
            {"name": "Reposado Tequila", "category": "Spirits", "price": 14, "station": "bar", "description": "Aged 8 months, oak."},
            # Food
            {"name": "Truffle Fries", "category": "Small Plates", "price": 9, "station": "kitchen", "description": "Hand-cut, parmesan, truffle oil."},
            {"name": "Wagyu Sliders", "category": "Small Plates", "price": 15, "station": "kitchen", "description": "Three sliders, aged cheddar."},
            {"name": "Charred Octopus", "category": "Small Plates", "price": 18, "station": "kitchen", "description": "Smoked paprika, lemon."},
            {"name": "Bar Burger", "category": "Mains", "price": 17, "station": "kitchen", "description": "Dry-aged patty, brioche, gruyère."},
            {"name": "Crispy Chicken", "category": "Mains", "price": 15, "station": "kitchen", "description": "Buttermilk, hot honey."},
        ]
        for m in menu:
            m["image"] = menu_images.get(m["name"], "")
            item = MenuItem(**m).model_dump()
            await unscoped_db.menu.insert_one(item)
    else:
        # Backfill images on existing docs that don't have one
        for name, url in menu_images.items():
            await unscoped_db.menu.update_one(
                {"name": name, "$or": [{"image": ""}, {"image": None}, {"image": {"$exists": False}}]},
                {"$set": {"image": url}},
            )

    # Seed inventory
    if await unscoped_db.inventory.count_documents({}) == 0:
        inv = [
            {"name": "Bourbon 750ml", "unit": "bottle", "stock": 12, "threshold": 4, "cost_per_unit": 35, "category": "spirits"},
            {"name": "Gin 750ml", "unit": "bottle", "stock": 8, "threshold": 4, "cost_per_unit": 28, "category": "spirits"},
            {"name": "Mezcal 750ml", "unit": "bottle", "stock": 3, "threshold": 4, "cost_per_unit": 42, "category": "spirits"},
            {"name": "Malbec Case", "unit": "case", "stock": 5, "threshold": 2, "cost_per_unit": 120, "category": "wine"},
            {"name": "House Lager Keg", "unit": "keg", "stock": 4, "threshold": 2, "cost_per_unit": 180, "category": "beer"},
            {"name": "Dark Stout Keg", "unit": "keg", "stock": 1, "threshold": 2, "cost_per_unit": 210, "category": "beer"},
            {"name": "Wagyu Beef", "unit": "kg", "stock": 8, "threshold": 5, "cost_per_unit": 90, "category": "food"},
            {"name": "Potatoes", "unit": "kg", "stock": 22, "threshold": 10, "cost_per_unit": 3, "category": "food"},
            {"name": "Aperol 750ml", "unit": "bottle", "stock": 2, "threshold": 3, "cost_per_unit": 26, "category": "spirits"},
        ]
        for i in inv:
            item = InventoryItem(**i).model_dump()
            await unscoped_db.inventory.insert_one(item)

    # Demo room types, rooms and a default rate for each. Rates use period_id=None (the
    # year-round default) so every type is immediately bookable; a type with no rate
    # is refused rather than priced at zero (see services/pricing.py).
    #
    # A real property starts with none of this and builds its own from the Rooms and
    # Rates screens, which are open while it is still pending approval.
    if await unscoped_db.room_types.count_documents({}) == 0:
        room_type_specs = [
            {"name": "Deluxe Room", "code": "DLX", "block": "Main",
             "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1,
             "room_count": 6, "base_rate": 4500.0,
             "extra_adult_rate": 800.0, "extra_child_rate": 400.0},
            {"name": "Executive Suite", "code": "EXE", "block": "Main",
             "base_occupancy": 2, "max_occupancy": 4, "max_extra_beds": 2,
             "room_count": 4, "base_rate": 7500.0,
             "extra_adult_rate": 1200.0, "extra_child_rate": 600.0},
            {"name": "Garden Cottage", "code": "GDN", "block": "Garden",
             "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1,
             "room_count": 3, "base_rate": 5500.0,
             "extra_adult_rate": 900.0, "extra_child_rate": 450.0},
        ]
        for floor, spec in enumerate(room_type_specs, start=1):
            spec = dict(spec)
            room_count = spec.pop("room_count")
            base_rate = spec.pop("base_rate")
            extra_adult_rate = spec.pop("extra_adult_rate")
            extra_child_rate = spec.pop("extra_child_rate")

            room_type = RoomType(**spec).model_dump()
            await unscoped_db.room_types.insert_one(room_type)

            for i in range(1, room_count + 1):
                room = Room(
                    number=f"{room_type['code']}-{i:02d}",
                    room_type_id=room_type["id"],
                    floor=str(floor),
                ).model_dump()
                await unscoped_db.rooms.insert_one(room)

            rate = Rate(
                room_type_id=room_type["id"], period_id=None,
                base_rate=base_rate, extra_adult_rate=extra_adult_rate,
                extra_child_rate=extra_child_rate,
            ).model_dump()
            await unscoped_db.rates.insert_one(rate)


# ----------------- Startup -----------------
@app.on_event("startup")
async def on_startup():
    # Ping before seeding. seed_data() issues many queries, so an unreachable
    # database would otherwise fail slowly and opaquely somewhere in the middle.
    await check_connection()
    await seed_data()
    logger.info("Seed complete.")
    # After seeding, so an account seed_data() wrote without the fields is caught too.
    # Failures propagate on purpose, matching seed_data above: an app that starts with
    # unmigrated users is one where every pre-existing login silently reaches nothing,
    # which is far harder to diagnose than a refused start.
    updated, current = await backfill_domains()
    logger.info("Domain backfill: %d user(s) updated, %d already current.", updated, current)
    # After the domain backfill, never before it: the screens each account is given are
    # intersected with its domains, so a user whose domains this run repaired must be
    # holding them by the time their screens are worked out.
    updated, current, stranded = await backfill_permissions()
    logger.info(
        "Screen backfill: %d user(s) updated, %d already current, %d left with no screens.",
        updated, current, stranded)
    # Tenancy. After seed_data so the admin it may have just created is stamped too, and
    # before the first request either way: a user with no property_id now reaches
    # nothing, so an unmigrated database is one where every existing login is locked out.
    # Order against the two backfills above does not matter — they touch other fields.
    created, property_id, stamped, current = await backfill_property()
    logger.info(
        "Property backfill: property %s (%s), %d user(s) stamped, %d already current.",
        "created" if created else "already present", property_id, stamped, current)
    # Straight after the property exists, and before anything is served: every property
    # that predates the type field has been running rooms *and* outlets, so it is stamped
    # `both` rather than left for each reader of the record to infer. Until this has run,
    # `services.access.property_domains` reads an absent key the same way — the migration
    # is what stops that agreement from being load-bearing forever.
    typed, typed_current = await backfill_property_type()
    logger.info(
        "Property type backfill: %d propert(ies) stamped both, %d already current.",
        typed, typed_current)
    # And the outlet's GST rate, in the same breath and for a blunter reason: until this
    # has run, every property reads as "no rate set". `services.tax.outlet_gst_settings`
    # answers 5% for one, so nothing bills wrongly in the meantime — but a settings
    # screen that shows a rate the record does not hold is a screen whose first save
    # changes something the owner did not mean to change.
    gst_stamped, gst_current = await backfill_outlet_gst()
    logger.info(
        "Outlet GST backfill: %d propert(ies) stamped, %d already current.",
        gst_stamped, gst_current)
    # And whether the property quotes per meal plan. Beside the GST stamp because it is
    # the same kind of thing — a pricing setting the owner holds — and stamped `True`
    # rather than the field's `False` default, because a property that predates the field
    # has been quoting EP, CP and MAP all along and the alternative silently drops the
    # breakfast supplement out of every quote the morning this deploys. A hotel that
    # wants the single all-inclusive rate switches it off from its settings screen, and
    # the migration then leaves that decision alone. See the module docstring.
    plans_stamped, plans_current = await backfill_meal_plans()
    logger.info(
        "Meal plan setting: %d propert(ies) stamped, %d already current.",
        plans_stamped, plans_current)
    # Immediately after, never before: the records are stamped with the property that
    # migration has just made sure exists. Until this has run, every scoped read matches
    # nothing — the data is not lost, it is invisible, which is harder to diagnose.
    stamped_property, stamped_docs, per_collection = await backfill_tenancy()
    logger.info(
        "Tenancy backfill: %d document(s) stamped for property %s%s.",
        stamped_docs, stamped_property,
        f" ({', '.join(f'{k} {v}' for k, v in sorted(per_collection.items()))})"
        if per_collection else "")
    # After the tenancy stamp, because it seeds through a scoped handle and a property
    # has to exist to be scoped to.
    seeded_props, current_props = await backfill_reference_data()
    logger.info(
        "Reference data: %d propert(ies) given GST bands and meal plans, %d already current.",
        seeded_props, current_props)
    # Last of the migrations, and the only one that is allowed to do nothing: an unset
    # key means this deployment stores identity documents in plain text, which is what
    # it did yesterday and is not a reason to refuse a hotel its check-in screen. It is
    # said out loud instead, because the alternative is a deployment that believes it is
    # encrypting and is not. `encryption_configured()` raises here, at startup, if the
    # key is set but malformed — somebody meant to encrypt and mistyped.
    if encryption_configured():
        encrypted, already, nothing = await encrypt_guest_ids()
        logger.info(
            "Guest ID encryption: %d newly encrypted, %d already encrypted, "
            "%d with nothing recorded.", encrypted, already, nothing)
    else:
        logger.warning(
            "%s is not set. Guest identity-document numbers (id_proof_number) are "
            "stored in PLAIN TEXT, so anyone who obtains a copy of this database has "
            "every hotel's guests' Aadhaar, passport and licence numbers. Generate a "
            "key with: python3 -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"", GUEST_ID_KEY_VAR)
    # Said once, loudly, at startup rather than only on the first webhook that is
    # refused: the symptom of a missing signing secret is online payments quietly not
    # settling, which nobody notices until a guest disputes a bill.
    if not webhook_secret():
        logger.warning(
            "STRIPE_WEBHOOK_SECRET is not set. Every Stripe webhook will be refused "
            "(503) and online payments will not settle. Set it to the signing secret "
            "from the Stripe dashboard's webhook endpoint.")
    # The brief has exactly one sender per deployment, and which one it is depends on
    # whether this process can be relied on to still exist at OWNER_BRIEF_TIME. A
    # container can; a function instance is shut down as soon as it is idle. So under
    # Functions this loop stays quiet and `functions/main.py::daily_brief` — woken by
    # Cloud Scheduler — sends instead. See routers/reports.py::in_process_brief_enabled.
    if in_process_brief_enabled():
        asyncio.create_task(daily_brief_scheduler())
        logger.info("Daily brief scheduler started (%s).", os.environ.get("OWNER_BRIEF_TIME", "23:00"))


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
