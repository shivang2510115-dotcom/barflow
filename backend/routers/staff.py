"""Staff administration: who works here, what they do, and where they do it.

Admin-only. Leavers are deactivated rather than deleted, because posted_by and
created_by on orders and folio entries must still resolve to a name — deleting a user
would orphan the audit trail the ledger exists to keep.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from pymongo.errors import DuplicateKeyError

from db import db
from security import Role, hash_password, require_access
from services.access import DOMAINS, SHARED

router = APIRouter()

# Staff administration is admin-only, and is not tied to any one area of the business.
ADMIN = require_access(SHARED, "admin")

# Pydantic needs a Literal to refuse an unknown domain with a 422, and a Literal cannot
# be built from a runtime tuple — so the vocabulary is spelled out once more here. It is
# pinned to the central one below rather than left to drift.
Domain = Literal["hotel", "restaurant", "bar"]

# Import-time check, so adding a domain to services.access.DOMAINS without updating the
# line above breaks startup loudly instead of silently 422-ing input that is now valid.
# An explicit raise rather than `assert`, which python -O would strip.
if get_args(Domain) != DOMAINS:
    raise RuntimeError(
        f"staff.Domain {get_args(Domain)} has drifted from services.access.DOMAINS "
        f"{DOMAINS} — update the Literal above to match")

# The message a duplicate email gets, whether the pre-check or the unique index caught it.
DUPLICATE_EMAIL = "A staff member with this email already exists"


class StaffIn(BaseModel):
    name: str
    email: EmailStr
    password: str
    # security.Role is the one list of roles; duplicating it here would let the two drift.
    role: Role
    domains: List[Domain] = []


class StaffUpdateIn(BaseModel):
    name: str
    role: Role
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


async def _count_active_admins() -> int:
    admins = await db.users.find({"role": "admin"}, {"_id": 0}).to_list(10000)
    return sum(1 for a in admins if a.get("active", True))


@router.get("/staff")
async def list_staff(user: dict = Depends(ADMIN)):
    users = await db.users.find({}, {"_id": 0}).to_list(10000)
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
        raise HTTPException(409, DUPLICATE_EMAIL)

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
    try:
        await db.users.insert_one(doc)
    except DuplicateKeyError:
        # server.py declares email unique. Locally the pre-check above is what does the
        # work, because the mock database's create_index is a no-op and its insert never
        # raises. Against real MongoDB two concurrent creates both pass the pre-check and
        # the loser lands here — without this it would reach the client as a 500.
        raise HTTPException(409, DUPLICATE_EMAIL)
    return _public(doc)


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, payload: StaffUpdateIn, user: dict = Depends(ADMIN)):
    target = await db.users.find_one({"id": staff_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")

    # Without this, one edit locks the owner out of their own system with no recovery
    # short of editing the database by hand.
    #
    # This guard is also what upholds "the property always has at least one active
    # admin". Only an admin reaches this route, and this line stops them targeting
    # themselves — so a demotion can only ever remove *another* admin, and the actor
    # remains an active admin afterwards. A separate last-admin count would be dead
    # code: the caller is always in it, so it could never reach zero.
    if staff_id == user.get("id"):
        raise HTTPException(409, "You cannot change your own role or domains")

    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")

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
    # As on the edit route, this self-guard is what upholds "the property always has at
    # least one active admin": only an admin gets here, and they cannot target
    # themselves, so a deactivation only ever removes *another* admin and the actor
    # stays active. A last-admin count before the write would be dead code — the caller
    # is always in it. The message names what was actually asked for, so reactivating
    # yourself is not refused with "you cannot deactivate yourself".
    if staff_id == user.get("id"):
        verb = "deactivate" if not payload.active else "reactivate"
        raise HTTPException(409, f"You cannot {verb} yourself")

    previous_active = target.get("active", True)
    await db.users.update_one({"id": staff_id}, {"$set": {"active": payload.active}})

    # A compensating check, not a transaction — there are no transactions here, the mock
    # database has none. Sequentially the self-guard above is enough, but two admins
    # deactivating each other at the same instant each see the other still active and
    # both writes land, leaving zero active admins: /api/staff needs admin *and* active,
    # so nobody can undo it without editing the database by hand. Re-counting after the
    # write and putting the target back narrows that window rather than closing it —
    # both requests can still read a count of 1 before either restores.
    if not payload.active and await _count_active_admins() == 0:
        await db.users.update_one({"id": staff_id}, {"$set": {"active": previous_active}})
        raise HTTPException(409, "This would leave the property with no active admin")

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
