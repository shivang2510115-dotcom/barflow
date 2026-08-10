"""Password hashing, JWT issuing, and role-based access dependencies."""
import os
from datetime import datetime, timezone, timedelta
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status

from db import db
from services.access import can_access, normalise_domains

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ.get("JWT_SECRET", "supersecret-key-123456789")

Role = Literal["admin", "manager", "waiter", "kitchen", "front_desk"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request) -> dict:
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        # Tokens live for seven days, so refusing at login is not enough — a leaver
        # deactivated an hour ago would otherwise keep working until their token expired.
        # Checked here rather than only in require_access so that deactivation takes
        # effect on every authenticated route at once. 403, not 401: the token is
        # genuine, the account is not permitted.
        if not user.get("active", True):
            raise HTTPException(status_code=403, detail="Account is deactivated")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_roles(*roles: str):
    async def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return checker


def require_access(domains: str | tuple[str, ...], *roles: str):
    """Dependency: the caller must be active, hold one of `roles`, and hold a domain.

    Replaces require_roles. Declaring the domain at each call site keeps authorization
    greppable — you can read any route and see exactly who reaches it. Inferring it from
    the router or the URL would make a misfiled endpoint silently inherit the wrong
    permission.
    """
    # Validated where this is called (route declaration, i.e. import time), not inside
    # the checker: a typo in a domain must break startup loudly, not silently deny every
    # user at request time.
    domains = normalise_domains(domains)

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if not can_access(user, domains, roles):
            raise HTTPException(status_code=403, detail="Not permitted")
        return user

    return checker
