"""Authentication: logging in and reading your own identity."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from db import db
from security import (
    create_access_token, require_access, resolve_property, verify_password)
from services.access import SCREENS, SHARED, SUSPENDED

router = APIRouter()


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/login")
async def login(payload: LoginIn):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # Refused at the door rather than on the first request. The message is identical to
    # a wrong password on purpose: revealing that an account exists but is disabled tells
    # a former employee their guess was right.
    if not user.get("active", True):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # And the same one level up: a suspended hotel refuses its whole staff, its admin
    # included. Byte-identical to the two refusals above, deliberately — "this hotel is
    # suspended" tells whoever typed the address that the hotel is on this platform and
    # that this email is one of its logins, which is more than a wrong password reveals.
    # A pending hotel logs in normally: setting the place up is exactly what it is for.
    property_record = await resolve_property(user)
    if property_record and property_record.get("status") == SUSPENDED:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], user["email"], user["role"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "domains": user.get("domains", []),
            "active": user.get("active", True),
            # Filtered against the catalogue: a key retired in code must stop granting
            # a screen the moment it is retired, not linger on old user records.
            "permissions": [k for k in (user.get("permissions") or []) if k in SCREENS],
        },
    }


# POST /auth/register is gone: it created users with no domains and no password rules,
# which under domain-based access is an account that can reach nothing. POST /api/staff
# replaces it and is the only way to create a user.


@router.get("/auth/me")
async def me(user: dict = Depends(require_access(SHARED))):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "domains": user.get("domains", []),
        "active": user.get("active", True),
        "permissions": [k for k in (user.get("permissions") or []) if k in SCREENS],
    }


# GET /auth/staff is gone. It returned the whole roster — every id, name, email, role,
# domains and active flag — behind require_roles("admin", "manager"), which let any
# manager, including a restaurant-only one, enumerate all staff around the admin-only
# gate on GET /api/staff. That route is now the only roster.
