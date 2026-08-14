"""Password hashing, JWT issuing, and role-based access dependencies."""
import os
from datetime import datetime, timezone, timedelta
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from db import db
from services.access import can_access, normalise_domains, normalise_permissions

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


# What a non-admin is told when they try to change configuration. Not a bare
# "Forbidden": the person reading it is a manager who can take a booking and post a
# payment, so the useful information is that this particular thing is the owner's to
# change, not that they are locked out generally.
CONFIGURATION_ONLY_ADMIN = "Editing this is restricted to an administrator"


def require_access(domains: str | tuple[str, ...], *roles: str,
                   permission: str | tuple[str, ...] | None = None):
    """Dependency: the caller must be active, hold one of `roles`, hold a domain, and
    hold the screen this endpoint sits behind.

    The only authorization dependency. `require_roles` — role-only, no domain — is gone
    rather than deprecated: leaving both in place means the next endpoint gets written
    with the weaker one. Declaring the domain at each call site keeps authorization
    greppable — you can read any route and see exactly who reaches it. Inferring it from
    the router or the URL would make a misfiled endpoint silently inherit the wrong
    permission.

    `permission` names the screen key, or keys, this endpoint serves. Several endpoints
    behind one screen share its key; one endpoint read by two screens names both, and
    holding either is enough.
    """
    # Validated where this is called (route declaration, i.e. import time), not inside
    # the checker: a typo in a domain or a screen key must break startup loudly, not
    # silently deny every user at request time.
    domains = normalise_domains(domains)
    if permission is not None:
        permission = normalise_permissions(permission)

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if not can_access(user, domains, roles, permission=permission):
            raise HTTPException(status_code=403, detail="Not permitted")
        return user

    return checker


def require_configuration(domains: str | tuple[str, ...]):
    """Dependency for a write that changes how the property is *configured*.

    Room types, rooms, rates, rate periods, meal plans, tax slabs, menu items and
    inventory items: the settings every operational number is derived from. Admin only.

    Declared per endpoint rather than inferred from the HTTP verb, because the verb does
    not carry the distinction: `POST /bookings` and `POST /rates` are both writes and
    only one of them is configuration. Taking a booking, checking a guest in or out,
    posting a charge, opening and settling an order and seating a table stay on
    `require_access` — a receptionist who cannot take a booking is not a receptionist.

    No `permission` argument: the screens are already reachable by anyone ticked for
    them, and what this adds is that only the admin may change what is on them.
    """
    domains = normalise_domains(domains)

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if not can_access(user, domains, ("admin",)):
            raise HTTPException(status_code=403, detail=CONFIGURATION_ONLY_ADMIN)
        return user

    return checker
