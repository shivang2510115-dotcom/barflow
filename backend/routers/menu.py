"""Menu items."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db import db
from security import require_configuration
from services.access import OUTLET

# The menu itself is configuration — prices on it become money on a bill — so only the
# admin edits it. Reading it is deliberately unauthenticated below: the QR code on the
# table is a guest's menu, not a staff screen.
CONFIG = require_configuration(OUTLET)

router = APIRouter()


class MenuItemIn(BaseModel):
    name: str
    category: str
    price: float
    description: str = ""
    image: str = ""
    station: Literal["bar", "kitchen"] = "bar"
    available: bool = True


class MenuItem(MenuItemIn):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))


# ----------------- Menu -----------------
@router.get("/menu")
async def list_menu():
    return await db.menu.find({}, {"_id": 0}).sort("category", 1).to_list(1000)


@router.post("/menu")
async def create_menu_item(payload: MenuItemIn, user: dict = Depends(CONFIG)):
    m = MenuItem(**payload.model_dump()).model_dump()
    await db.menu.insert_one(m)
    m.pop("_id", None)
    return m


@router.put("/menu/{item_id}")
async def update_menu_item(item_id: str, payload: MenuItemIn, user: dict = Depends(CONFIG)):
    await db.menu.update_one({"id": item_id}, {"$set": payload.model_dump()})
    doc = await db.menu.find_one({"id": item_id}, {"_id": 0})
    return doc


@router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str, user: dict = Depends(CONFIG)):
    await db.menu.delete_one({"id": item_id})
    return {"ok": True}
