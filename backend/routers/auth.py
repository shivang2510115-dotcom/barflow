"""Authentication: logging in and reading your own identity."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from db import db
from security import (
    verify_password, create_access_token,
    get_current_user, Role,
)

router = APIRouter()


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: Role
    domains: list[str] = []
    active: bool = True


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
        },
    }


# POST /auth/register is gone: it created users with no domains and no password rules,
# which under domain-based access is an account that can reach nothing. POST /api/staff
# replaces it and is the only way to create a user.


@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "domains": user.get("domains", []),
        "active": user.get("active", True),
    }


# GET /auth/staff is gone. It returned the whole roster — every id, name, email, role,
# domains and active flag — behind require_roles("admin", "manager"), which let any
# manager, including a restaurant-only one, enumerate all staff around the admin-only
# gate on GET /api/staff. That route is now the only roster.
