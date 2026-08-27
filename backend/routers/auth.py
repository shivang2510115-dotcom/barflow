"""Authentication: logging in, reading your own identity, and changing your own password."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from db import unscoped_db
from security import (
    create_access_token, get_current_user, hash_password, require_access,
    resolve_property, verify_password)
from services.access import SCREENS, SHARED, SUSPENDED
from services.password import password_problem
from services.ratelimit import RateLimiter, client_ip

router = APIRouter()

# Both count *failures*, not attempts. A hotel's staff all sign in within a minute of
# each other at the start of service and a busy front desk signs in on three devices;
# counting successes would throttle the thing the door is for.
#
# Two limiters because each covers the other's weakness, and neither is enough alone:
#
# * per address stops one client spraying one password across every account on the
#   platform — the attack the per-account limit cannot see, because each account is only
#   tried once. It is the weaker of the two: `client_ip` prefers X-Forwarded-For, which
#   the client sets, so an attacker who knows that can hand themselves a fresh
#   allowance. See services/ratelimit.py for why reading the socket instead is worse.
# * per email address is the half no header can move, and it is what actually bounds
#   guessing at one known account — an owner's address is on the hotel's website.
#
# The cost of the second one is stated rather than hidden: an attacker who knows an
# email can hold that one account out of its own system for as long as they keep failing
# against it. That is a fifteen-minute nuisance that needs sustained traffic to maintain,
# against unlimited guessing at every account on the platform, and it is the trade every
# password door makes. It is a throttle, not a lockout: nothing is disabled, and the
# window lifts by itself.
LOGIN_FAILURES_PER_ADDRESS = RateLimiter(limit=50, window_seconds=900, name="login_ip")
LOGIN_FAILURES_PER_EMAIL = RateLimiter(limit=10, window_seconds=900, name="login_email")

# One message for both limits. Which of the two stopped you is not information a caller
# is owed — per-email throttling that announced itself would confirm that an address is
# a real account here.
TOO_MANY_ATTEMPTS = "Too many sign-in attempts. Try again in a few minutes."


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/login")
async def login(payload: LoginIn, request: Request = None):
    email = payload.email.lower()
    if (await LOGIN_FAILURES_PER_ADDRESS.blocked(client_ip(request))
            or await LOGIN_FAILURES_PER_EMAIL.blocked(email)):
        # Before the password is checked, never after: a throttle that still verifies is
        # not a throttle, it only changes the status code the guesser reads.
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)

    async def refuse():
        """Every way this door says no, counted identically.

        Deliberately not "count only wrong passwords": a deactivated leaver and a
        suspended hotel get the same 401 as a bad guess precisely so that the three
        cannot be told apart, and an unthrottled path among them would tell them apart
        by which one starts returning 429.
        """
        await LOGIN_FAILURES_PER_ADDRESS.record(client_ip(request))
        await LOGIN_FAILURES_PER_EMAIL.record(email)
        return HTTPException(status_code=401, detail="Invalid email or password")

    user = await unscoped_db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise await refuse()
    # Refused at the door rather than on the first request. The message is identical to
    # a wrong password on purpose: revealing that an account exists but is disabled tells
    # a former employee their guess was right.
    if not user.get("active", True):
        raise await refuse()
    # And the same one level up: a suspended hotel refuses its whole staff, its admin
    # included. Byte-identical to the two refusals above, deliberately — "this hotel is
    # suspended" tells whoever typed the address that the hotel is on this platform and
    # that this email is one of its logins, which is more than a wrong password reveals.
    # A pending hotel logs in normally: setting the place up is exactly what it is for.
    property_record = await resolve_property(user)
    if property_record and property_record.get("status") == SUSPENDED:
        raise await refuse()

    # This address keeps whatever failures it has accumulated, and only the account
    # forgets. An attacker holding one valid login of their own would otherwise clear
    # their address's counter between every fifty guesses simply by signing into it;
    # clearing the *email* needs the password to that email, which is the thing they are
    # trying to find out. The person it helps is the front desk who mistyped twice
    # before getting it right.
    await LOGIN_FAILURES_PER_EMAIL.forget(email)
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


# Setup-time: this is the caller reading their own identity, and it carries nothing
# about the hotel's guests or money. A pending property whose staff could log in but not
# then be told who they are would be a hotel that cannot open its own app to set itself
# up — the state approval is supposed to allow.
@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    """Who the caller is — their own record, and nothing about any hotel.

    Deliberately `get_current_user` rather than `require_access`: this is the one route
    the platform operator needs, and `require_access` refuses them by design because they
    belong to no property. Without it the client has no verified way to learn who it is
    signed in as, and a page refresh would either sign the operator out or push the
    frontend into trusting an unverified claim off the token.

    `get_current_user` still refuses a deactivated account, so this is not a way around
    that. It carries no guest, money or staff data, so there is nothing here to gate on a
    property being live.
    """
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "domains": user.get("domains", []),
        "active": user.get("active", True),
        "permissions": [k for k in (user.get("permissions") or []) if k in SCREENS],
    }


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


# What a wrong current password is told. Deliberately about the password and not about
# the account: the caller is already authenticated, so there is nothing here to keep from
# them — what they need to know is which of the two fields to retype.
WRONG_CURRENT_PASSWORD = "That is not your current password"

# And what re-typing the one you already have is told. Not a refusal about strength: the
# password may be a perfectly good one, it is simply not a change, and a form that
# answered "changed" would have told somebody something untrue about their own account.
SAME_AS_CURRENT = "That is the password you already have. Choose a different one."


@router.post("/auth/password")
async def change_own_password(payload: ChangePasswordIn,
                              user: dict = Depends(get_current_user)):
    """The caller changes their own password, having proved they know it.

    `POST /api/staff/{id}/password` is the other password route and it is a different
    thing: an admin resetting *somebody else's*, admin-only, scoped to their own hotel.
    It is untouched. This one takes no id at all — the account changed is the account the
    token names, so there is no request that can point at anybody else and therefore no
    filter here to get wrong.

    **`get_current_user`, not `require_access`, and that is the whole design decision.**
    `require_access` resolves the caller's property and refuses anyone who has none, which
    is the platform operator's permanent and deliberate state — so the obvious dependency
    would lock the one account that approves hotels out of its own password, and would
    also refuse a hotel user whose property is suspended or merely pending. None of those
    are reasons to stop somebody securing their own login. `/auth/me` is here for exactly
    this reason and made exactly this call. `get_current_user` still refuses a deactivated
    account and an expired or forged token, so this is not a way around either.

    **The current password is required.** Without it a session is a takeover: a laptop
    left unlocked at the end of a shift, or a token copied out of local storage, becomes
    permanent ownership of the account rather than access that expires in seven days. The
    order of the checks matters too — the current password is verified *before* the new
    one is judged, so that a 400 about strength cannot be used as an oracle telling a
    guesser that their guess at the current one was right.

    One thing this deliberately does not do: it does not sign other sessions out. Tokens
    are signed statements about who you are and carry nothing derived from the password,
    so an already-issued one keeps working until it expires. Revoking them needs a
    per-user token version on the record and a check in `get_current_user`, which is a
    change to every authenticated request and is worth doing on its own.
    """
    # `get_current_user` projects `password_hash` away — correctly, so that no handler
    # can return it by accident. This is the one place that needs it, so it is read back
    # here, by the caller's own id and nothing from the request.
    row = await unscoped_db.users.find_one({"id": user["id"]})
    if not row or not row.get("password_hash") \
            or not verify_password(payload.current_password, row["password_hash"]):
        # 401, not 403: what failed is the proof of identity, not the caller's role.
        # Everyone who reaches this route may change their own password.
        raise HTTPException(status_code=401, detail=WRONG_CURRENT_PASSWORD)

    if verify_password(payload.new_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail=SAME_AS_CURRENT)

    # The same rule as hiring somebody and as an admin reset — services/password.py, the
    # one place it is written — checked against this account's own address, because this
    # account is the one that will be typing it.
    problem = password_problem(payload.new_password, row.get("email"))
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    await unscoped_db.users.update_one(
        {"id": user["id"]}, {"$set": {"password_hash": hash_password(payload.new_password)}})
    return {"ok": True}


# GET /auth/staff is gone. It returned the whole roster — every id, name, email, role,
# domains and active flag — behind require_roles("admin", "manager"), which let any
# manager, including a restaurant-only one, enumerate all staff around the admin-only
# gate on GET /api/staff. That route is now the only roster.
