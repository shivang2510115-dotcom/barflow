"""Password hashing, JWT issuing, and role-based access dependencies."""
import os
from datetime import datetime, timezone, timedelta
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from db import unscoped_db, using_mock
from services.access import (
    PENDING, can_access, normalise_domains, normalise_permissions)

JWT_ALGORITHM = "HS256"

# This value is published in a public repository. Anyone holding it can mint a token for
# any user of any hotel — not guess a password, forge an admin session outright. It stays
# as a convenience for local work against the JSON mock, and is fatal anywhere else.
_DEFAULT_JWT_SECRET = "supersecret-key-123456789"
JWT_SECRET = os.environ.get("JWT_SECRET", _DEFAULT_JWT_SECRET)

if JWT_SECRET == _DEFAULT_JWT_SECRET and not using_mock:
    raise RuntimeError(
        "JWT_SECRET is still the default published in this repository, and MONGO_URL "
        "points at a real database. Every login token would be forgeable by anyone who "
        "has read the source. Set JWT_SECRET to a long random value and restart."
    )

# The one list of roles. `routers/staff.py` types its request bodies on this rather than
# repeating the strings, so a role that is not here cannot be created or assigned.
#
# `housekeeping` is an attendant with a phone, updating the room they have just finished.
# Individual logins rather than one shared "housekeeping" account, because the append-only
# event log is what answers "who marked this room clean and when" — and it only answers it
# if the accounts are people. What they reach is `hotel.housekeeping` and nothing else:
# not bookings, not rates, not the POS. See services/access.py::ROLE_SCREENS.
Role = Literal["admin", "manager", "waiter", "kitchen", "front_desk", "housekeeping"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str | None, role: str) -> str:
    """A signed statement of who the caller is.

    `email` is now optional, because a phone-only staff account has none. The claim is
    left as `None` rather than being filled in with the phone number: nothing reads it —
    every authenticated route resolves the user from `sub` — and quietly putting a
    different kind of value under a key called `email` is how a claim starts being
    trusted for something it does not mean.
    """
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
        user = await unscoped_db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
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

# The ordinary refusal: the caller lacks the role, the domain or the screen.
NOT_PERMITTED = "Not permitted"

# The refusal a hotel gets while it is waiting to be approved. Distinct wording because
# the situation is: nothing is wrong with this account, and there is nothing for the
# admin to fix. "Forbidden" would read as a permissions bug and produce a support ticket
# for a hotel that only has to wait.
PENDING_APPROVAL = (
    "Your property is pending approval. Setup — property details, rooms, rates, menu, "
    "tables and staff — is open; bookings, check-in and billing unlock once the "
    "property is approved."
)


async def resolve_property(user: dict) -> dict | None:
    """The caller's hotel, read once per request.

    `None` means the user names no hotel, or names one that no longer exists. Both are
    refusals in `can_access`, and deliberately not the same value as its `UNSCOPED`
    sentinel: "we looked and found nothing" must not be able to arrive at the predicate
    looking like "this caller is outside tenancy", because only one of those is
    permissive and it is the wrong one to guess.
    """
    property_id = user.get("property_id")
    if not property_id:
        return None
    return await unscoped_db.properties.find_one({"id": property_id}, {"_id": 0})


def _refusal(user: dict, domains, roles, permission, setup_time: bool,
             property_record: dict | None, default_detail: str) -> str:
    """Which 403 message this refusal deserves.

    `can_access` answers yes or no, and a bool cannot carry a reason — so the property
    already in hand (never a second read) is re-inspected here, at the only call site
    that has it. The pending wording is used only when the pending state is the *sole*
    thing in the way: asked again as though the endpoint were setup-time, would this
    caller pass? If they would still be refused for their role, their domain or their
    screen, telling them to wait for approval would be a lie that sends the admin to
    chase the platform operator over a permission they simply do not hold.
    """
    if (property_record and property_record.get("status") == PENDING and not setup_time
            and can_access(user, domains, roles, property_record,
                           setup_time=True, permission=permission)):
        return PENDING_APPROVAL
    return default_detail


def require_access(domains: str | tuple[str, ...], *roles: str,
                   permission: str | tuple[str, ...] | None = None,
                   setup_time: bool = False):
    """Dependency: the caller's property must be usable, and the caller must be active,
    hold one of `roles`, hold a domain, and hold the screen this endpoint sits behind.

    The only authorization dependency. `require_roles` — role-only, no domain — is gone
    rather than deprecated: leaving both in place means the next endpoint gets written
    with the weaker one. Declaring the domain at each call site keeps authorization
    greppable — you can read any route and see exactly who reaches it. Inferring it from
    the router or the URL would make a misfiled endpoint silently inherit the wrong
    permission.

    `permission` names the screen key, or keys, this endpoint serves. Several endpoints
    behind one screen share its key; one endpoint read by two screens names both, and
    holding either is enough.

    `setup_time` declares that a hotel still awaiting approval may reach this endpoint —
    property details, room types, rooms, rates, meal plans, tax slabs, menu, tables and
    staff. It defaults to False so that every operating endpoint locks while pending
    without anyone having to remember to say so, and it is checked per request, so a
    hotel suspended mid-session is stopped on its next call rather than when its
    seven-day token expires.
    """
    # Validated where this is called (route declaration, i.e. import time), not inside
    # the checker: a typo in a domain or a screen key must break startup loudly, not
    # silently deny every user at request time.
    domains = normalise_domains(domains)
    if permission is not None:
        permission = normalise_permissions(permission)

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        property_record = await resolve_property(user)
        if not can_access(user, domains, roles, property_record,
                          setup_time=setup_time, permission=permission):
            raise HTTPException(status_code=403, detail=_refusal(
                user, domains, roles, permission, setup_time, property_record,
                NOT_PERMITTED))
        return user

    return checker


def require_configuration(domains: str | tuple[str, ...], *, setup_time: bool = False):
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

    `setup_time` means the same thing it does on `require_access`, and most configuration
    carries it: a hotel awaiting approval is expected to be building its rooms, rates and
    menu. It is still declared per call site rather than assumed for every configuration
    endpoint, because "configuration" and "reachable while pending" are different
    questions — inventory is configuration, and stock levels are not part of signing up.
    """
    domains = normalise_domains(domains)

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        property_record = await resolve_property(user)
        if not can_access(user, domains, ("admin",), property_record,
                          setup_time=setup_time):
            raise HTTPException(status_code=403, detail=_refusal(
                user, domains, ("admin",), None, setup_time, property_record,
                CONFIGURATION_ONLY_ADMIN))
        return user

    return checker
