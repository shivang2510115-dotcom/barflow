"""Inventory items and stock adjustments."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, require_configuration
from services.access import SHARED

# One store room supplies the kitchen, the bar and housekeeping, so these are shared.
# Creating, renaming, repricing or deleting an item is configuration: admin only.
CONFIG = require_configuration(SHARED)

# Counting stock in and out is not — it is what the kitchen does on a shift, and an
# adjustment nobody can make is a stock figure nobody can trust.
READ = require_access(SHARED, permission="outlet.inventory")
ADJUST = require_access(SHARED, "admin", "manager", "kitchen", permission="outlet.inventory")

router = APIRouter()


class InventoryItemIn(BaseModel):
    name: str
    unit: str = "bottle"
    stock: float = 0
    threshold: float = 5
    cost_per_unit: float = 0
    category: str = "spirits"


class InventoryItem(InventoryItemIn):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class InventoryAdjustIn(BaseModel):
    delta: float
    reason: str = ""


# ----------------- Inventory -----------------
@router.get("/inventory")
async def list_inventory(user: dict = Depends(READ),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    return await db.inventory.find({}, {"_id": 0}).sort("name", 1).to_list(1000)


@router.post("/inventory")
async def create_inventory(payload: InventoryItemIn, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    item = InventoryItem(**payload.model_dump()).model_dump()
    await db.inventory.insert_one(item)
    item.pop("_id", None)
    return item


@router.put("/inventory/{item_id}")
async def update_inventory(item_id: str, payload: InventoryItemIn, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    await db.inventory.update_one({"id": item_id}, {"$set": payload.model_dump()})
    return await db.inventory.find_one({"id": item_id}, {"_id": 0})


@router.post("/inventory/{item_id}/adjust")
async def adjust_inventory(item_id: str, payload: InventoryAdjustIn, user: dict = Depends(ADJUST),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    item = await db.inventory.find_one({"id": item_id}, {"_id": 0})
    if not item:
        raise HTTPException(404, "Item not found")
    new_stock = max(0, item["stock"] + payload.delta)
    await db.inventory.update_one({"id": item_id}, {"$set": {"stock": new_stock}})
    return {**item, "stock": new_stock}


@router.delete("/inventory/{item_id}")
async def delete_inventory(item_id: str, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    await db.inventory.delete_one({"id": item_id})
    return {"ok": True}
