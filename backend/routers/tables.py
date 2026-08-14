"""Tables and reservations."""
import uuid
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import db
from security import require_access
from services.access import OUTLET

router = APIRouter()


class TableIn(BaseModel):
    label: str
    capacity: int = 4
    zone: str = "Main"


class Table(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str
    capacity: int = 4
    zone: str = "Main"
    status: Literal["free", "occupied", "billed", "reserved"] = "free"
    current_order_id: Optional[str] = None


class ReservationIn(BaseModel):
    guest_name: str
    phone: Optional[str] = None
    party_size: int = 2
    date: str          # "YYYY-MM-DD"
    time: str          # "HH:MM"
    table_id: Optional[str] = None
    table_label: Optional[str] = None
    hold_table: bool = False
    notes: Optional[str] = None


class StatusIn(BaseModel):
    status: Literal["booked", "seated", "no_show", "cancelled"]


class Reservation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    guest_name: str
    phone: Optional[str] = None
    party_size: int = 2
    date: str
    time: str
    table_id: Optional[str] = None
    table_label: Optional[str] = None
    status: Literal["booked", "seated", "no_show", "cancelled"] = "booked"
    notes: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# The floor plan is read by three screens — Tables, the POS and Reservations all need
# to know what the tables are — so the one endpoint names all three.
FLOOR = require_access(OUTLET, permission=("outlet.tables", "outlet.pos", "outlet.reservations"))
# Adding or removing a table is not configuration in the sense the admin lock covers:
# nothing is priced off it, and a floor that cannot be rearranged by the manager on duty
# is a floor plan that goes stale. Room types, rates and menu items are the settings that
# turn into money on a bill; a table is furniture.
FLOOR_EDIT = require_access(OUTLET, "admin", "manager", permission="outlet.tables")
BOOK_TABLE = require_access(OUTLET, "admin", "manager", "waiter", permission="outlet.reservations")
READ_RESERVATIONS = require_access(OUTLET, permission="outlet.reservations")
CANCEL_RESERVATION = require_access(OUTLET, "admin", "manager", permission="outlet.reservations")


# ----------------- Tables -----------------
@router.get("/tables")
async def list_tables(user: dict = Depends(FLOOR)):
    return await db.tables.find({}, {"_id": 0}).sort("label", 1).to_list(500)


@router.get("/tables/public/{table_id}")
async def get_table_public(table_id: str):
    t = await db.tables.find_one({"id": table_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Table not found")
    return t


@router.post("/tables")
async def create_table(payload: TableIn, user: dict = Depends(FLOOR_EDIT)):
    t = Table(**payload.model_dump()).model_dump()
    await db.tables.insert_one(t)
    t.pop("_id", None)
    return t


@router.delete("/tables/{table_id}")
async def delete_table(table_id: str, user: dict = Depends(FLOOR_EDIT)):
    await db.tables.delete_one({"id": table_id})
    return {"ok": True}


# ----------------- Reservations -----------------
@router.get("/reservations")
async def list_reservations(date: Optional[str] = None, user: dict = Depends(READ_RESERVATIONS)):
    query = {"date": date} if date else {}
    return await db.reservations.find(query, {"_id": 0}).sort("time", 1).to_list(500)


@router.post("/reservations")
async def create_reservation(
    payload: ReservationIn,
    user: dict = Depends(BOOK_TABLE),
):
    data = payload.model_dump()
    hold = data.pop("hold_table", False)
    r = Reservation(**data).model_dump()
    await db.reservations.insert_one(r)
    r.pop("_id", None)

    # Hold the physical table if requested and it's currently free.
    if hold and r.get("table_id"):
        table = await db.tables.find_one({"id": r["table_id"]}, {"_id": 0})
        if table and table.get("status") == "free":
            await db.tables.update_one(
                {"id": r["table_id"]}, {"$set": {"status": "reserved"}}
            )
    return r


@router.put("/reservations/{reservation_id}/status")
async def set_reservation_status(
    reservation_id: str,
    payload: StatusIn,
    user: dict = Depends(BOOK_TABLE),
):
    res = await db.reservations.find_one({"id": reservation_id}, {"_id": 0})
    if not res:
        raise HTTPException(status_code=404, detail="Reservation not found")

    await db.reservations.update_one(
        {"id": reservation_id}, {"$set": {"status": payload.status}}
    )

    # Release a held table when the booking is done/void, but never stomp a
    # table that has since been occupied by a live order.
    if res.get("table_id") and payload.status in ("cancelled", "no_show", "seated"):
        table = await db.tables.find_one({"id": res["table_id"]}, {"_id": 0})
        if table and table.get("status") == "reserved":
            await db.tables.update_one(
                {"id": res["table_id"]}, {"$set": {"status": "free"}}
            )
    return {"ok": True, "status": payload.status}


@router.delete("/reservations/{reservation_id}")
async def delete_reservation(
    reservation_id: str,
    user: dict = Depends(CANCEL_RESERVATION),
):
    res = await db.reservations.find_one({"id": reservation_id}, {"_id": 0})
    if res and res.get("table_id"):
        table = await db.tables.find_one({"id": res["table_id"]}, {"_id": 0})
        if table and table.get("status") == "reserved":
            await db.tables.update_one(
                {"id": res["table_id"]}, {"$set": {"status": "free"}}
            )
    await db.reservations.delete_one({"id": reservation_id})
    return {"ok": True}
