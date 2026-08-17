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
from services.access import (
    DOMAINS, SCREENS, SHARED, default_permissions, permission_in_domains)

router = APIRouter()

# Staff administration is admin-only, and is not tied to any one area of the business.
# The screen key is declared for completeness — an admin bypasses the permission check
# exactly as they bypass domains, so `admin.staff` gates nothing here; it exists so the
# catalogue and the sidebar agree that this screen is one of the fifteen.
# Setup-time: hiring the first receptionist is part of setting a hotel up, and a
# property still awaiting approval that could not create a login would have nobody to
# do the rest of the setup with.
ADMIN = require_access(SHARED, "admin", permission="admin.staff", setup_time=True)

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
    # Not a Literal, so an unknown key reaches the handler and is refused with a 422 that
    # names it — "screen key 'hotel.spa' does not exist" is what an owner can act on,
    # where pydantic's enum error would recite all fifteen valid keys instead.
    #
    # None is not the empty list. Omitted means "the screens this role has always had"
    # (see _stored_permissions); an explicit `[]` is somebody deliberately ticking
    # nothing, which is an account that reaches nothing, and is refused.
    permissions: List[str] | None = None


class StaffUpdateIn(BaseModel):
    name: str
    role: Role
    domains: List[Domain] = []
    # Omitted means "leave their screens alone" here rather than "reset to the role's",
    # so that an edit which only renames somebody cannot quietly widen them back out.
    permissions: List[str] | None = None


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
        # Filtered to the catalogue on the way out: a key retired from the code is
        # ignored on read rather than shown as a tick for a screen that no longer exists.
        "permissions": [k for k in (user.get("permissions") or []) if k in SCREENS],
        "active": user.get("active", True),
        "created_at": user.get("created_at"),
    }


async def _count_active_admins() -> int:
    admins = await db.users.find({"role": "admin"}, {"_id": 0}).to_list(10000)
    return sum(1 for a in admins if a.get("active", True))


def _stored_domains(role: str, domains: List[str]) -> List[str]:
    """The domain list to persist for this role, de-duplicated.

    An admin is never domain-checked, so an empty list costs them nothing today — but it
    is the state the startup backfill exists to repair, and it makes a later demotion to
    manager silently produce an account that can reach nothing. Storing the full list
    means the row already says what the admin can actually do, and there is no longer any
    route that writes an empty `domains`.
    """
    picked = list(dict.fromkeys(domains))
    if role == "admin" and not picked:
        return list(DOMAINS)
    return picked


def _stored_permissions(submitted: List[str] | None, role: str, domains: List[str],
                        existing: List[str] | None = None) -> List[str]:
    """The screen keys to persist, validated against the catalogue and the domains.

    Three refusals, each because the alternative is a stored record that lies about what
    somebody can reach:

    * an unknown key is a 422 naming it — a typo in a tick would otherwise be saved and
      quietly do nothing;
    * a key outside the person's work domains is a 400, because the domain check runs
      first at request time so that screen could never open, and the owner would go on
      believing they had granted it;
    * a non-admin with nothing ticked is a 400 — an account that reaches no screen at
      all is a mistake, not a state worth storing.
    """
    if submitted is None:
        # Nothing was sent. On a create that means the role's usual screens; on an edit
        # it means the ones already held, minus any that this edit's domains or a
        # retirement in the code have made unreachable — a save drops what it cannot keep
        # rather than refusing an edit the owner did not make.
        if existing is None:
            return default_permissions(role, domains)
        return [k for k in existing
                if k in SCREENS and permission_in_domains(k, domains)]

    picked = list(dict.fromkeys(submitted))
    for key in picked:
        if key not in SCREENS:
            raise HTTPException(422, f"Unknown screen: {key}")
        if not permission_in_domains(key, domains):
            raise HTTPException(
                400, f"{key} is outside this staff member's work domains, so it would "
                     f"never take effect")

    # Same reasoning as `_stored_domains`: an admin is never permission-checked, so an
    # empty list costs them nothing until the day they are demoted and it costs them
    # everything.
    if role == "admin" and not picked:
        return default_permissions(role, domains)
    if not picked:
        raise HTTPException(400, "A non-admin needs at least one screen")
    return picked


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

    domains = _stored_domains(payload.role, payload.domains)
    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "email": email,
        "role": payload.role,
        "domains": domains,
        "permissions": _stored_permissions(payload.permissions, payload.role, domains),
        # The hotel doing the hiring, taken from the admin's own record and never from
        # the request: a staff list is the one place where "which hotel" could otherwise
        # be typed in. Without it the new account would be refused every endpoint, since
        # a user naming no property is unplaceable and therefore refused.
        "property_id": user.get("property_id"),
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

    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")

    domains = _stored_domains(payload.role, payload.domains)
    permissions = _stored_permissions(payload.permissions, payload.role, domains,
                                      existing=list(target.get("permissions") or []))

    # Editing your own *permissions* is refused — without this, one edit locks the owner
    # out of their own system with no recovery short of editing the database by hand.
    # Your own name is not a permission, though: a sole admin with a typo in their name
    # had no way to fix it while this guard covered the whole payload. So the guard is
    # on the fields that decide access, and the message names the one that was refused
    # rather than reciting both.
    if staff_id == user.get("id"):
        blocked = []
        if payload.role != target.get("role"):
            blocked.append("role")
        if domains != list(target.get("domains") or []):
            blocked.append("domains")
        # Only when they actually sent a list: an edit that leaves the field out carries
        # the stored screens over, and refusing that would stop an admin renaming
        # themselves — the very thing this guard was narrowed to allow.
        if payload.permissions is not None and permissions != list(target.get("permissions") or []):
            blocked.append("screens")
        if blocked:
            raise HTTPException(409, f"You cannot change your own {' or '.join(blocked)}")

    previous = {"name": target.get("name"), "role": target.get("role"),
                "domains": list(target.get("domains") or []),
                "permissions": list(target.get("permissions") or [])}

    await db.users.update_one({"id": staff_id}, {"$set": {
        "name": payload.name.strip(),
        "role": payload.role,
        "domains": domains,
        "permissions": permissions,
    }})

    # The same compensating check `set_active` makes below, for the other way to reach
    # zero admins. The self-guard above is enough sequentially — a demotion can only
    # target somebody else, so the actor is still an active admin afterwards — but it is
    # not enough concurrently: admin A demotes B while B demotes A, both pass the
    # self-guard because each is looking at the other, both writes land, and no admin is
    # left. /api/staff requires admin, so nothing short of editing the database by hand
    # restores it. Re-counting after the write and putting the row back narrows that
    # window rather than closing it — there are no transactions here — which is exactly
    # what the deactivation path settles for, and for the same reason.
    if previous["role"] == "admin" and payload.role != "admin" \
            and await _count_active_admins() == 0:
        await db.users.update_one({"id": staff_id}, {"$set": previous})
        raise HTTPException(409, "This would leave the property with no active admin")

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
