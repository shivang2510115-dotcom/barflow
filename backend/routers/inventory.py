"""Inventory items, stock adjustments, and the reviewed bulk upload."""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, require_configuration
from services.access import SHARED
from services.inventory_import import (
    MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL, ImportRefused, plan_apply, plan_upload,
    template_csv)

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


# ----------------- Bulk import -----------------
# Three endpoints, and the order they come in is the feature: download the shape, upload
# a file and be told what it *would* do, then — after a human has corrected it — apply
# it. Nothing is written by the first two. See services/inventory_import.py for why the
# review step in the middle is the point rather than a nicety.
#
# All three are CONFIG: admin only, exactly like creating and editing an item by hand,
# because that is what this is a faster way of doing. A manager may look at the Inventory
# screen and adjust stock on a shift; creating two hundred items is configuration.

class InventoryImportRowIn(BaseModel):
    """One row of the review screen, as the admin left it.

    Every field is loose on purpose. These values come back from a browser after a human
    has edited them, so a quantity may well arrive as the text somebody typed rather than
    as a number — and a 422 from the framework naming `body -> rows -> 3 -> stock` is
    worse than the message `plan_apply` produces from the same input, which names the row
    and the cell in the owner's own words. Parsing and validation happen in one place, by
    the same rules the file was read with.
    """
    row: int = 0
    name: str = ""
    unit: str = ""
    stock: float | str = ""
    threshold: float | str = ""
    cost_per_unit: float | str = ""
    category: str = ""
    # create, update or skip. Re-decided in plan_apply rather than trusted — see there.
    action: str = "skip"
    item_id: str | None = None


class InventoryImportApplyIn(BaseModel):
    rows: list[InventoryImportRowIn] = Field(default_factory=list)


async def _read_bounded(file: UploadFile) -> bytes:
    """The upload, read in chunks and abandoned the moment it exceeds the bound.

    Not `await file.read()` then a length check: that has already spooled the whole thing
    before anyone asks how big it was, which on a serverless function is exactly the bill
    the bound exists to prevent. 413 is the honest status — the request is well formed
    and too large — and the message says the limit rather than only that one was hit.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, (
                f"That file is larger than {MAX_UPLOAD_LABEL}, which is the most this "
                f"import will read. Export just the stock rows as CSV and try again."))
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/inventory/import/template")
async def inventory_import_template(user: dict = Depends(CONFIG)):
    """The file to fill in. A published format, not one the owner has to guess."""
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="barflow-stock-template.csv"'},
    )


@router.post("/inventory/import/preview")
async def preview_inventory_import(file: UploadFile = File(...),
                                   user: dict = Depends(CONFIG),
                                   db: PropertyScopedDatabase = Depends(tenant_db)):
    """Read the file and report what importing it would do. **Writes nothing.**

    The property's own stock is read through the scoped handle and handed to the planner,
    so a name can only ever be matched against an item this property holds — an upload in
    one hotel must not find, let alone update, another hotel's Gin.
    """
    raw = await _read_bounded(file)
    existing = await db.inventory.find({}, {"_id": 0}).to_list(10000)
    try:
        return plan_upload(raw, file.filename or "", existing)
    except ImportRefused as refusal:
        raise HTTPException(refusal.status_code, refusal.detail) from None


@router.post("/inventory/import/apply")
async def apply_inventory_import(payload: InventoryImportApplyIn,
                                 user: dict = Depends(CONFIG),
                                 db: PropertyScopedDatabase = Depends(tenant_db)):
    """Write the reviewed rows, and report honestly what happened to each.

    Two failure modes, kept apart because they mean different things to the person
    reading the result:

    * a row that cannot be applied — an unreadable number still in it, an update naming
      an item that has since been deleted, two rows fighting over one item — refuses the
      **whole** request with a 400 naming those rows, and nothing is written. Half an
      import is a store room in a state nobody chose.
    * a write that fails once the writing has started is not something this can undo:
      there is no transaction spanning the mock, MongoDB and Firestore. So the remaining
      rows are still attempted and the response says exactly which ones landed and which
      did not, with `complete: false`. That is a 200 carrying bad news rather than a 500,
      because a 500 reads as "nothing happened" and some of it did.
    """
    rows = [row.model_dump() for row in payload.rows]
    existing = await db.inventory.find({}, {"_id": 0}).to_list(10000)
    operations, refusals = plan_apply(rows, existing)

    if refusals:
        raise HTTPException(400, {
            "message": (f"{len(refusals)} row(s) cannot be imported as they are, so "
                        f"none of this file was written. Fix or drop them and apply "
                        f"again."),
            "rows": refusals,
        })

    created = updated = 0
    failed: list[dict] = []
    for operation in operations:
        item = operation["item"]
        try:
            if operation["action"] == "create":
                await db.inventory.insert_one(InventoryItem(**item).model_dump())
                created += 1
            else:
                await db.inventory.update_one(
                    {"id": operation["item_id"]},
                    {"$set": InventoryItemIn(**item).model_dump()})
                updated += 1
        except Exception as exc:  # noqa: BLE001 — whichever driver is underneath
            failed.append({"row": operation["row"], "name": item["name"],
                           "action": operation["action"], "message": str(exc)})

    return {
        "created": created,
        "updated": updated,
        "skipped": len(rows) - len(operations),
        "failed": failed,
        "complete": not failed,
    }
