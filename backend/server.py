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

from db import db, client
from security import hash_password
from routers import auth, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings
from routers.tables import Table
from routers.menu import MenuItem
from routers.inventory import InventoryItem
from routers.reports import daily_brief_scheduler
from models.hotel import Rate, Room, RoomType

app = FastAPI(title="BarFlow API")
api_router = APIRouter(prefix="/api")

for module in (auth, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings):
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

    # Seed admin + staff
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@barflow.io").lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")

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
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        # An existing account keeps whatever password it currently has. Re-hashing the
        # seed value here would silently undo every password change on each restart.

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

    # Room types, rooms and a default rate for each — without this a fresh deploy has
    # zero hotel inventory, availability returns nothing, and no booking can be made
    # until someone creates it over the API by hand. Rates use period_id=None (the
    # year-round default) so every type is immediately bookable; a type with no rate
    # is refused rather than priced at zero (see services/pricing.py).
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
    await seed_data()
    logger.info("Seed complete.")
    if os.environ.get("DAILY_BRIEF_ENABLED", "true").lower() == "true":
        asyncio.create_task(daily_brief_scheduler())
        logger.info("Daily brief scheduler started (%s).", os.environ.get("OWNER_BRIEF_TIME", "23:00"))


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
