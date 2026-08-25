"""Public hotel registration: the one way a new tenant comes into existence.

This is the only unauthenticated endpoint in the application that creates records, which
makes it the only one an anonymous caller can use to fill the database. It is rate-limited
by IP for that reason.

It uses `unscoped_db` because there is no tenant to scope to yet — the request is what
creates one. Every other router that touches `unscoped_db` does so for a collection that
sits outside tenancy; this one does it because tenancy has not begun.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr

from db import unscoped_db
from models.property import PropertyType
from security import hash_password
from services.access import (
    DEFAULT_PROPERTY_TYPE, PENDING, default_permissions, domains_for_property_type)
from services.password import password_problem
from services.ratelimit import RateLimiter, client_ip
from services.registration import GSTIN_SHAPE, validate_gstin

router = APIRouter()

# Ten signups an hour from one address. High enough that a hotel retrying a mistyped form
# never notices, low enough that filling the properties collection takes weeks.
#
# The counting itself now lives in services/ratelimit.py, because the login door and the
# QR order route need the same thing and a second copy is how two of them end up with
# different behaviour. Its docstring carries the caveat that used to be here: in-process,
# so the effective limit multiplies by the worker count.
SIGNUPS_PER_ADDRESS = RateLimiter(limit=10, window_seconds=3600, name="signup_ip")


class SignupIn(BaseModel):
    hotel_name: str
    city: str = ""
    # What the business actually is. A Literal, so an unknown value never reaches this
    # handler: FastAPI answers 422 naming the field, which is the shape the signup form
    # already renders for a malformed email address.
    #
    # Defaulted rather than required, and defaulted to `both`, because that is what every
    # property signed up before this field existed got — an omitted type has to keep
    # giving the answer it always gave, or the field becomes a breaking change to a public
    # endpoint. The form always sends one; this is for everything that does not.
    property_type: PropertyType = DEFAULT_PROPERTY_TYPE
    # Optional here on purpose. A hotel signing up in the evening should not be blocked
    # because the GST certificate is in a drawer at the office; the property screen asks
    # again, and the operator sees whether it is filled before approving.
    gstin: str = ""
    admin_name: str
    admin_email: EmailStr
    admin_password: str


@router.post("/signup")
async def signup(payload: SignupIn, request: Request):
    """Create a pending hotel and the admin who will run it, together.

    Together, because a property with no login is unreachable and a login with no
    property reaches nothing. Neither half is useful alone, so neither is created alone.
    """
    if await SIGNUPS_PER_ADDRESS.limited(client_ip(request)):
        raise HTTPException(429, "Too many signups from this address. Try again later.")

    name = payload.hotel_name.strip()
    if not name:
        raise HTTPException(400, "The hotel needs a name")
    # The first account of a new hotel, and the one that can reach every screen in it.
    # Checked against the email too: `thegrand@…` / `thegrand` is a real thing people do
    # on a signup form.
    problem = password_problem(payload.admin_password, str(payload.admin_email))
    if problem:
        raise HTTPException(400, problem)
    gstin = payload.gstin.strip().upper()
    if not validate_gstin(gstin):
        raise HTTPException(400, f"gstin is not a valid GSTIN — expected {GSTIN_SHAPE}")

    email = str(payload.admin_email).strip().lower()
    if await unscoped_db.users.find_one({"email": email}):
        # Deliberately explicit rather than a vague failure: the person is trying to
        # register their own hotel and needs to know the address is already in use.
        raise HTTPException(409, "An account with this email already exists")

    # The whole of what this property is allowed to do, decided once from what it says it
    # is. Read from `services.access` rather than branched on here: the staff routes bound
    # every later hire against the same rule, and a second reading of it in this router is
    # how the founding admin ends up with a domain nobody else in the property can hold.
    domains = domains_for_property_type(payload.property_type)

    now = datetime.now(timezone.utc).isoformat()
    property_id = str(uuid.uuid4())
    await unscoped_db.properties.insert_one({
        "id": property_id,
        "name": name,
        "legal_name": "",
        "address_line1": "", "address_line2": "",
        "city": payload.city.strip(), "state": "", "pincode": "",
        "phone": "", "email": email,
        "gstin": gstin, "fssai_licence": "",
        "check_in_time": "14:00", "check_out_time": "11:00",
        "logo": None,
        "property_type": payload.property_type,
        "status": PENDING,
        "created_at": now,
        "approved_at": None, "approved_by": None,
        "suspended_at": None, "suspension_reason": None,
    })

    try:
        await unscoped_db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": email,
            "name": payload.admin_name.strip() or "Owner",
            "role": "admin",
            "password_hash": hash_password(payload.admin_password),
            # Their own property, entirely — and no more than it. An owner who cannot
            # reach half their own place would be filing a support ticket on day one; an
            # owner handed a half their place does not have gets a Hotel section leading
            # to screens the API refuses, which is the same ticket from the other side.
            "domains": list(domains),
            # Intersected with those domains rather than handed the whole catalogue, by
            # the same function that decides a new hire's: a restaurant's owner has no
            # rooms screen to tick, so storing the tick would be a grant that does
            # nothing. `hotel.guests` survives — it sits behind a shared endpoint.
            "permissions": default_permissions("admin", domains),
            "active": True,
            "property_id": property_id,
            "created_at": now,
        })
    except Exception:
        # Two writes, no transaction. A property with no admin can never be logged into
        # and can never be deleted through the app, so it would sit in the operator's
        # pending list forever with nobody able to explain it. Undo rather than orphan.
        await unscoped_db.properties.delete_one({"id": property_id})
        raise

    return {
        "property_id": property_id,
        "status": PENDING,
        "message": "Your hotel is registered and waiting for approval. "
                   "You can sign in now and set it up.",
    }
