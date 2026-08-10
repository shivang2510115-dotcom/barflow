"""Staff administration: who works here, what they do, and where they do it.

Admin-only. Leavers are deactivated rather than deleted, because posted_by and
created_by on orders and folio entries must still resolve to a name — deleting a user
would orphan the audit trail the ledger exists to keep.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from db import db
from security import hash_password, require_access
from services.access import SHARED

router = APIRouter()

# Staff administration is admin-only, and is not tied to any one area of the business.
ADMIN = require_access(SHARED, "admin")

Domain = Literal["hotel", "restaurant", "bar"]
StaffRole = Literal["admin", "manager", "waiter", "kitchen", "front_desk"]


class StaffIn(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: StaffRole
    domains: List[Domain] = []


class StaffUpdateIn(BaseModel):
    name: str
    role: StaffRole
    domains: List[Domain] = []


class ActiveIn(BaseModel):
    active: bool


class PasswordIn(BaseModel):
    password: str


def _public(user: dict) -> dict:
    """Never return password_hash. Building the response explicitly rather than
    deleting keys means a new sensitive field cannot leak by being forgotten."""
    return {
        "id": user["id"],
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "domains": user.get("domains") or [],
        "active": user.get("active", True),
        "created_at": user.get("created_at"),
    }


async def _count_other_active_admins(exclude_id: str) -> int:
    admins = await db.users.find(
        {"role": "admin", "id": {"$ne": exclude_id}}, {"_id": 0}).to_list(1000)
    return sum(1 for a in admins if a.get("active", True))


@router.get("/staff")
async def list_staff(user: dict = Depends(ADMIN)):
    users = await db.users.find({}, {"_id": 0}).to_list(1000)
    return [_public(u) for u in sorted(users, key=lambda x: x.get("name") or "")]


@router.post("/staff")
async def create_staff(payload: StaffIn, user: dict = Depends(ADMIN)):
    # An account that can reach nothing is a mistake, not a state worth storing.
    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "A staff member with this email already exists")

    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "email": email,
        "role": payload.role,
        "domains": list(dict.fromkeys(payload.domains)),
        "active": True,
        "password_hash": hash_password(payload.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    return _public(doc)


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, payload: StaffUpdateIn, user: dict = Depends(ADMIN)):
    target = await db.users.find_one({"id": staff_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")

    # Without this, one edit locks the owner out of their own system with no recovery
    # short of editing the database by hand.
    if staff_id == user.get("id"):
        raise HTTPException(409, "You cannot change your own role or domains")

    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")

    if (target.get("role") == "admin" and payload.role != "admin"
            and await _count_other_active_admins(staff_id) == 0):
        raise HTTPException(409, "This is the last active admin and cannot be demoted")

    await db.users.update_one({"id": staff_id}, {"$set": {
        "name": payload.name.strip(),
        "role": payload.role,
        "domains": list(dict.fromkeys(payload.domains)),
    }})
    return _public(await db.users.find_one({"id": staff_id}, {"_id": 0}))


@router.post("/staff/{staff_id}/active")
async def set_active(staff_id: str, payload: ActiveIn, user: dict = Depends(ADMIN)):
    target = await db.users.find_one({"id": staff_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")
    if staff_id == user.get("id"):
        raise HTTPException(409, "You cannot deactivate yourself")

    if (not payload.active and target.get("role") == "admin"
            and await _count_other_active_admins(staff_id) == 0):
        raise HTTPException(409, "This is the last active admin and cannot be deactivated")

    await db.users.update_one({"id": staff_id}, {"$set": {"active": payload.active}})
    return _public(await db.users.find_one({"id": staff_id}, {"_id": 0}))


@router.post("/staff/{staff_id}/password")
async def reset_password(staff_id: str, payload: PasswordIn, user: dict = Depends(ADMIN)):
    if not await db.users.find_one({"id": staff_id}):
        raise HTTPException(404, "Staff member not found")
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    await db.users.update_one({"id": staff_id}, {"$set": {
        "password_hash": hash_password(payload.password)}})
    return {"ok": True}
