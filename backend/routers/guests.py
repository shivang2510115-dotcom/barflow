"""Guest records. Phone is the identity key across bar, restaurant and rooms."""
from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import Guest, GuestIn
from security import require_access
from services.access import SHARED

router = APIRouter()

# Guests are shared: a bar regular and a hotel guest are the same person. The
# new-booking screen creates one mid-booking, so it reaches these too — hence both keys.
MANAGE = require_access(SHARED, "admin", "manager", "front_desk",
                        permission=("hotel.guests", "hotel.bookings"))


@router.get("/guests")
async def list_guests(q: str = "", limit: int = 50, user: dict = Depends(MANAGE)):
    query = {}
    if q:
        query = {"$or": [
            {"phone": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]}
    return await db.guests.find(query, {"_id": 0}).to_list(limit)


@router.post("/guests")
async def create_guest(payload: GuestIn, user: dict = Depends(MANAGE)):
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(400, "Phone is required")

    existing = await db.guests.find_one({"phone": phone}, {"_id": 0})
    if existing:
        # Return the existing record so the desk can open it instead of retyping.
        raise HTTPException(409, {"message": "A guest with this phone already exists",
                                  "guest": existing})

    guest = Guest(**{**payload.model_dump(), "phone": phone}).model_dump()
    await db.guests.insert_one(guest)
    guest.pop("_id", None)
    return guest


@router.get("/guests/{guest_id}")
async def get_guest(guest_id: str, user: dict = Depends(MANAGE)):
    guest = await db.guests.find_one({"id": guest_id}, {"_id": 0})
    if not guest:
        raise HTTPException(404, "Guest not found")

    stays = await db.bookings.find({"guest_id": guest_id}, {"_id": 0}).to_list(200)
    orders = await db.orders.find(
        {"customer_phone": guest["phone"], "status": "settled"}, {"_id": 0}
    ).to_list(200)

    return {
        **guest,
        "stays": stays,
        "outlet_orders": len(orders),
        "outlet_spend": round(sum(o.get("total", 0) for o in orders), 2),
    }


@router.put("/guests/{guest_id}")
async def update_guest(guest_id: str, payload: GuestIn, user: dict = Depends(MANAGE)):
    clash = await db.guests.find_one({"phone": payload.phone.strip(), "id": {"$ne": guest_id}})
    if clash:
        raise HTTPException(409, "Another guest already uses this phone")

    result = await db.guests.update_one({"id": guest_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Guest not found")
    return await db.guests.find_one({"id": guest_id}, {"_id": 0})
