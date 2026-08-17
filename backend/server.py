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

from db import db, client, check_connection, using_mock
from security import hash_password
from routers import auth, staff, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings, frontdesk, folios, analytics, permissions, property as property_router
from routers.tables import Table
from routers.menu import MenuItem
from routers.inventory import InventoryItem
from routers.reports import daily_brief_scheduler
from models.hotel import Rate, Room, RoomType
from services.access import DOMAINS
from migrations.backfill_domains import backfill as backfill_domains
from migrations.backfill_permissions import backfill as backfill_permissions
from migrations.backfill_property import backfill as backfill_property

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

app = FastAPI(title="BarFlow API")
api_router = APIRouter(prefix="/api")

for module in (auth, staff, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings, frontdesk, folios, analytics, permissions, property_router):
    api_router.include_router(module.router)


@api_router.get("/")
async def root():
    return {"service": "BarFlow API", "status": "ok"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
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


async def seed_data():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.tables.create_index("label")
    await db.reservations.create_index("date")
    await db.menu.create_index("category")
    await db.guests.create_index("phone", unique=True)
    await db.bookings.create_index([("room_type_id", 1), ("check_in", 1), ("check_out", 1), ("status", 1)])
    await db.bookings.create_index("reference", unique=True)
    await db.rooms.create_index("room_type_id")
    await db.folios.create_index("booking_id", unique=True)
    await db.folio_entries.create_index("folio_id")
    # Unique against real MongoDB; mocked create_index is a no-op.
    # Actual idempotency comes from services/folio.py::unposted_nights.
    # Partial, not sparse: a compound sparse index only skips a document missing ALL of
    # its keys, and folio_id/kind are always present, so a plain sparse index would still
    # cover every payment/misc_charge/outlet/void entry (all with charge_date: null) and
    # the second such entry on a folio would raise a duplicate-key error. The partial
    # filter limits uniqueness to the room-night rows that actually need it.
    await db.folio_entries.create_index(
        [("folio_id", 1), ("charge_date", 1)], unique=True,
        partialFilterExpression={"kind": "room_night"})

    # Seed admin + staff
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@barflow.io").lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

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

    for u in default_users:
        existing = await db.users.find_one({"email": u["email"]})
        if existing is None:
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": u["email"],
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
    if await db.tax_slabs.count_documents({}) == 0:
        await db.tax_slabs.insert_many([
            {"id": str(uuid.uuid4()), "min_tariff": 0.0, "max_tariff": 7500.0,
             "rate_percent": 12.0, "active": True},
            {"id": str(uuid.uuid4()), "min_tariff": 7500.0, "max_tariff": None,
             "rate_percent": 18.0, "active": True},
        ])

    if await db.meal_plans.count_documents({}) == 0:
        await db.meal_plans.insert_many([
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
    if await db.tables.count_documents({}) == 0:
        zones = [("Bar", 6, 4), ("Lounge", 4, 4), ("Patio", 3, 6)]
        seq = 1
        for zone, count, cap in zones:
            for i in range(count):
                t = Table(label=f"T{seq:02d}", capacity=cap, zone=zone).model_dump()
                await db.tables.insert_one(t)
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
    if await db.menu.count_documents({}) == 0:
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
            await db.menu.insert_one(item)
    else:
        # Backfill images on existing docs that don't have one
        for name, url in menu_images.items():
            await db.menu.update_one(
                {"name": name, "$or": [{"image": ""}, {"image": None}, {"image": {"$exists": False}}]},
                {"$set": {"image": url}},
            )

    # Seed inventory
    if await db.inventory.count_documents({}) == 0:
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
            await db.inventory.insert_one(item)

    # Demo room types, rooms and a default rate for each. Rates use period_id=None (the
    # year-round default) so every type is immediately bookable; a type with no rate
    # is refused rather than priced at zero (see services/pricing.py).
    #
    # A real property starts with none of this and builds its own from the Rooms and
    # Rates screens, which are open while it is still pending approval.
    if await db.room_types.count_documents({}) == 0:
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
            await db.room_types.insert_one(room_type)

            for i in range(1, room_count + 1):
                room = Room(
                    number=f"{room_type['code']}-{i:02d}",
                    room_type_id=room_type["id"],
                    floor=str(floor),
                ).model_dump()
                await db.rooms.insert_one(room)

            rate = Rate(
                room_type_id=room_type["id"], period_id=None,
                base_rate=base_rate, extra_adult_rate=extra_adult_rate,
                extra_child_rate=extra_child_rate,
            ).model_dump()
            await db.rates.insert_one(rate)


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
    if os.environ.get("DAILY_BRIEF_ENABLED", "true").lower() == "true":
        asyncio.create_task(daily_brief_scheduler())
        logger.info("Daily brief scheduler started (%s).", os.environ.get("OWNER_BRIEF_TIME", "23:00"))


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
