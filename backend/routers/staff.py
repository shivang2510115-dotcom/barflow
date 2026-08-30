"""Staff administration: who works here, what they do, and where they do it.

Admin-only. Leavers are deactivated rather than deleted, because posted_by and
created_by on orders and folio entries must still resolve to a name — deleting a user
would orphan the audit trail the ledger exists to keep.

This is the one router that reaches `unscoped_db`, because `users` stands outside
tenancy: a login has to be findable by its identifier before anyone knows which hotel it
belongs to, so the collection cannot be filtered by property in the handle. The roster is
still one hotel's, so every query here says `_mine(user)` out loud instead — the
explicitness the scoped handle buys everywhere else, paid for by hand in the one place it
cannot. The identifier uniqueness checks are the deliberate exception: they are global,
because two hotels cannot share a login.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional, get_args

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from pymongo.errors import DuplicateKeyError

from db import unscoped_db
from scoped_db import PropertyScopedDatabase, tenant_db
from security import Role, hash_password, require_access, resolve_property
from services.access import (
    DOMAINS, SCREENS, SHARED, default_permissions, permission_in_domains,
    property_domains)
from services.identity import (
    NEITHER_IDENTIFIER, PHONE_SHAPE, normalise_email, normalise_phone)
from services.password import password_problem

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
#
# This is the vocabulary, not the permission. Every one of these is a real domain, and
# `_within_the_property` still refuses the ones the caller's own property does not have:
# a well-formed request for something that does not apply to this business is a 400, not
# a 422, so the two checks are deliberately in different places.
Domain = Literal["hotel", "restaurant", "bar", "services"]

# Import-time check, so adding a domain to services.access.DOMAINS without updating the
# line above breaks startup loudly instead of silently 422-ing input that is now valid.
# An explicit raise rather than `assert`, which python -O would strip.
if get_args(Domain) != DOMAINS:
    raise RuntimeError(
        f"staff.Domain {get_args(Domain)} has drifted from services.access.DOMAINS "
        f"{DOMAINS} — update the Literal above to match")

# The message a duplicate gets, whether the pre-check or the unique index caught it. One
# per identifier, because the owner has two fields in front of them and needs to know
# which one to change.
DUPLICATE_EMAIL = "A staff member with this email already exists"
DUPLICATE_PHONE = "A staff member with this phone number already exists"


class StaffIn(BaseModel):
    name: str
    # Both optional, and at least one required — the rule is `login_identifiers` below,
    # not this declaration, because "one of these two" is not something a field type can
    # say. A great many waiters and kitchen hands in India have no email address and
    # always have a phone; requiring an email is what produced `waiter1@fake.com`, which
    # cannot receive a password reset and collides across properties.
    #
    # `EmailStr` stays on the address: where a form is being filled in, a malformed one is
    # a typo worth naming. The phone is a plain `str` and is validated by
    # `services.identity.normalise_phone`, which has to run anyway to produce the stored
    # form — a Pydantic pattern beside it would be the same rule written twice.
    email: EmailStr | None = None
    phone: str | None = None
    password: str
    # security.Role is the one list of roles; duplicating it here would let the two drift.
    role: Role
    domains: List[Domain] = []
    # Which outlets this person works in. Empty means "not narrowed" — they work
    # wherever their domains reach, which is what every account predating outlets has.
    # Enforced in exactly one place: scoped_db.py::require_outlet.
    outlet_ids: List[str] = []
    # Not a Literal, so an unknown key reaches the handler and is refused with a 422 that
    # names it — "screen key 'hotel.spa' does not exist" is what an owner can act on,
    # where pydantic's enum error would recite all fifteen valid keys instead.
    #
    # None is not the empty list. Omitted means "the screens this role has always had"
    # (see _stored_permissions); an explicit `[]` is somebody deliberately ticking
    # nothing, which is an account that reaches nothing, and is refused.
    # ---- employment ----
    # All optional and all defaulted: eighty-eight staff records already exist without
    # them, and an account missing a joining date has to keep working exactly as it does.
    #
    # `document_number` is a number as typed — Aadhaar or PAN — and never a scan. A
    # document vault is a different product with different obligations.
    joined_on: Optional[str] = None
    designation: Optional[str] = None
    salary_monthly: Optional[float] = None
    # How many days a month are paid without being worked. Used by the salary run; a
    # property that does not give paid leave leaves it at zero.
    paid_leave_days: int = 0
    emergency_contact: Optional[str] = None
    document_number: Optional[str] = None
    permissions: List[str] | None = None


class StaffUpdateIn(BaseModel):
    name: str
    role: Role
    domains: List[Domain] = []
    # Which outlets this person works in. Empty means "not narrowed" — they work
    # wherever their domains reach, which is what every account predating outlets has.
    # Enforced in exactly one place: scoped_db.py::require_outlet.
    outlet_ids: List[str] = []
    # Omitted means "leave their screens alone" here rather than "reset to the role's",
    # so that an edit which only renames somebody cannot quietly widen them back out.
    # ---- employment ----
    # All optional and all defaulted: eighty-eight staff records already exist without
    # them, and an account missing a joining date has to keep working exactly as it does.
    #
    # `document_number` is a number as typed — Aadhaar or PAN — and never a scan. A
    # document vault is a different product with different obligations.
    joined_on: Optional[str] = None
    designation: Optional[str] = None
    salary_monthly: Optional[float] = None
    # How many days a month are paid without being worked. Used by the salary run; a
    # property that does not give paid leave leaves it at zero.
    paid_leave_days: int = 0
    emergency_contact: Optional[str] = None
    document_number: Optional[str] = None
    permissions: List[str] | None = None


class ActiveIn(BaseModel):
    active: bool


class PasswordIn(BaseModel):
    password: str


def login_identifiers(email, phone) -> tuple[str | None, str | None]:
    """The two identifiers to store, in their canonical forms — and the refusals.

    Shared by `POST /api/staff` and `POST /api/signup`, which is the whole reason it is a
    function: the founding admin of a property and the waiter hired a week later are the
    same kind of record, and two copies of this rule is how one of them ends up storing
    `9876543210` while the other stores `+919876543210` and neither can find the other's.

    Three refusals, all 400 and never 422, because in each case the request is perfectly
    well formed and what is wrong is what the resulting account could do:

    * **neither identifier** — the rule this change exists for. Both fields are
      individually optional, and an account holding neither has nothing to type at the
      sign-in box, so it can never be used. Stored, it would sit in the roster looking
      exactly like a working account until the waiter is standing at the till.
    * **a phone that is not a phone** — refused by shape rather than stored as typed.
      `1234567890` is what somebody enters to get past a field they do not want to fill
      in, which is `waiter1@fake.com` in its new spelling.
    * a blank string in either field, which is not an identifier and must not be stored
      as one: two accounts both holding `""` would read as two accounts holding the same
      address, and the uniqueness check would refuse the second.
    """
    stored_email = normalise_email(str(email) if email is not None else None)
    # The order matters: a number that was typed and cannot be read is a refusal, not a
    # reason to fall through to "you gave neither". The owner did give one, and telling
    # them otherwise sends them looking for a field they have already filled in.
    if phone is not None and str(phone).strip():
        stored_phone = normalise_phone(str(phone))
        if stored_phone is None:
            raise HTTPException(400, f"That is not a phone number this can store. Give "
                                     f"{PHONE_SHAPE}.")
    else:
        stored_phone = None

    if not stored_email and not stored_phone:
        raise HTTPException(400, NEITHER_IDENTIFIER)
    return stored_email, stored_phone


async def identifier_taken(email: str | None, phone: str | None, *,
                           email_message: str = DUPLICATE_EMAIL,
                           phone_message: str = DUPLICATE_PHONE) -> None:
    """Refuse an identifier that already belongs to somebody, anywhere on the platform.

    The manual pre-check that has always guarded `email`, extended to cover `phone` and
    to look for each value in *both* columns. Firestore has no unique indexes at all and
    the JSON mock's `create_index` is a no-op, so this read-then-write is the only thing
    enforcing uniqueness on two of the three backends — which is why the existing pattern
    is extended rather than a second mechanism invented beside it. `create_staff` still
    catches `DuplicateKeyError` underneath for the concurrent case on real MongoDB.

    Across both columns rather than within each: nothing can produce that clash today,
    since a canonical number starts `+91` and an address needs an `@`. It costs one extra
    query on a route an owner uses a handful of times a year, and the day either format
    loosens is not the day to find out that the check was narrower than the sentence
    "a phone must not collide with anything".

    Global, not `_mine(user)`. Two hotels cannot share a login, because the login is
    resolved before anyone knows which hotel it belongs to.

    The two messages are arguments because signup shares this check and does not share
    the words for it: somebody registering their own hotel is not "a staff member", and
    that endpoint's existing wording is what its screen is written against.
    """
    for value, message in ((email, email_message), (phone, phone_message)):
        if not value:
            continue
        if await unscoped_db.users.find_one({"email": value}) \
                or await unscoped_db.users.find_one({"phone": value}):
            raise HTTPException(409, message)


def _public(user: dict, *, with_salary: bool = False) -> dict:
    """Never return password_hash. Building the response explicitly rather than
    deleting keys means a new sensitive field cannot leak by being forgotten."""
    return {
        "id": user["id"],
        "name": user.get("name"),
        "email": user.get("email"),
        # `.get` and not `["phone"]`: every account created before this change is stored
        # without the key, and it has to read as "no number" rather than raise.
        "phone": user.get("phone"),
        "role": user.get("role"),
        "domains": user.get("domains") or [],
        "outlet_ids": user.get("outlet_ids") or [],
        "joined_on": user.get("joined_on"),
        "designation": user.get("designation"),
        "paid_leave_days": user.get("paid_leave_days") or 0,
        "emergency_contact": user.get("emergency_contact"),
        "document_number": user.get("document_number"),
        # Filtered to the catalogue on the way out: a key retired from the code is
        # ignored on read rather than shown as a tick for a screen that no longer exists.
        "permissions": [k for k in (user.get("permissions") or []) if k in SCREENS],
        "active": user.get("active", True),
        "created_at": user.get("created_at"),
        # Money, and therefore opt-in. The roster is already admin-only, so this rides on
        # the same gate rather than inventing a screen key — but a route that ever widens
        # who may read the roster must not accidentally widen who may read what everybody
        # earns. Absent by default is the safe direction.
        **({"salary_monthly": user.get("salary_monthly")} if with_salary else {}),
    }


async def _checked_outlet_ids(db, outlet_ids: list[str]) -> list[str]:
    """Every id must name an outlet of *this* property.

    The scoped handle means an id belonging to another hotel simply is not found, so
    this one check covers both a typo and a caller reaching across tenants — and it
    answers 400 rather than 404, because the request is about a staff member who does
    exist and it is the body that is wrong.
    """
    wanted = [o for o in dict.fromkeys(outlet_ids) if o]
    if not wanted:
        return []
    rows = await db.outlets.find({"id": {"$in": wanted}}, {"_id": 0, "id": 1}).to_list(200)
    found = {r["id"] for r in rows}
    missing = [o for o in wanted if o not in found]
    if missing:
        raise HTTPException(400, f"No such outlet in this property: {missing[0]}")
    return wanted


def _mine(user: dict) -> dict:
    """The filter that keeps this roster to the caller's own hotel.

    `property_id` comes from the admin's own record, never from the request — there is
    no path by which an admin can name another hotel, and a user of another hotel is
    simply not found, which is a 404 rather than a 403 for the same reason it is
    everywhere else: a 403 would confirm that the account exists.
    """
    return {"property_id": user.get("property_id")}


async def _count_active_admins(property_id: str | None) -> int:
    """Active admins in *this* hotel. Counting globally would let a property demote its
    own last admin because some other hotel still has one."""
    admins = await unscoped_db.users.find(
        {"role": "admin", "property_id": property_id}, {"_id": 0}).to_list(10000)
    return sum(1 for a in admins if a.get("active", True))


async def _property_domains(user: dict) -> tuple[str, ...]:
    """The work domains the caller's own property has — the ceiling on everyone in it.

    Resolved from the admin's own record, never from the request, exactly as `_mine` is:
    there is no path by which an admin can name another property, so there is none by
    which they can borrow another property's domains either.
    """
    return property_domains(await resolve_property(user))


def _within_the_property(picked: List[str], allowed: tuple[str, ...]) -> None:
    """Refuse a domain this property does not have, and say which one and why.

    A restaurant with no rooms must not be able to hold a hotel-domain user by any route,
    and this is the one function all three of them go through. 400 rather than 422: the
    value is a real domain and the request is well-formed — what is wrong is that it does
    not apply to *this* business, which is a fact about the property, not the payload.

    Refusing rather than silently dropping. A staff screen that quietly stores less than
    was ticked tells the owner they granted something they did not, which is the same lie
    `_stored_permissions` refuses to tell one line further down.
    """
    if not allowed:
        # No property record, or one whose type nothing recognises. Refused whatever was
        # asked for, including nothing at all: an admin stored against a property with no
        # domains is the account this check exists to stop being created. Naming an empty
        # list would read as a bug in the message rather than in the record.
        raise HTTPException(
            400, "This property's record cannot be read, so there is no work area to "
                 "assign anybody to. Ask the platform operator to look at it.")
    outside = [d for d in dict.fromkeys(picked) if d not in allowed]
    if not outside:
        return
    raise HTTPException(
        400,
        f"This property does not run a {' or a '.join(outside)}, so nobody here can work "
        f"in {'that area' if len(outside) == 1 else 'those areas'}. It runs "
        f"{', '.join(allowed)}.")


def _stored_domains(role: str, domains: List[str], allowed: tuple[str, ...]) -> List[str]:
    """The domain list to persist for this role, de-duplicated.

    An admin is never domain-checked, so an empty list costs them nothing today — but it
    is the state the startup backfill exists to repair, and it makes a later demotion to
    manager silently produce an account that can reach nothing. Storing the full list
    means the row already says what the admin can actually do, and there is no longer any
    route that writes an empty `domains`.

    `allowed` and not `DOMAINS`: the whole vocabulary is not this property's to give. An
    outlet's second admin is stored holding the outlet's two, so the demotion that list
    exists to survive leaves them with what the property actually has.
    """
    picked = list(dict.fromkeys(domains))
    if role == "admin" and not picked:
        return list(allowed)
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
    users = await unscoped_db.users.find(_mine(user), {"_id": 0}).to_list(10000)
    # Every route in this router is admin-only, so salary is included at all four call
    # sites. The parameter defaults to False anyway, so a route added later that reuses
    # this projection on a wider gate does not leak what everybody earns by omission.
    return [_public(u, with_salary=True)
            for u in sorted(users, key=lambda x: x.get("name") or "")]


@router.post("/staff")
async def create_staff(payload: StaffIn, user: dict = Depends(ADMIN),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    # An account that can reach nothing is a mistake, not a state worth storing.
    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")

    # Before the password rule, so that an account with no way in is refused for the
    # reason that actually stops it rather than for a weak password the owner would then
    # fix and be refused again.
    email, phone = login_identifiers(payload.email, payload.phone)

    # Still checked against the address and not the number. A password that is somebody's
    # own phone number is just as guessable, and services/password.py should learn that —
    # but it is a rule about passwords, it applies to the two other routes that set one
    # as well, and bolting it on here would leave those two disagreeing with this one.
    problem = password_problem(payload.password, email)
    if problem:
        raise HTTPException(400, problem)

    await identifier_taken(email, phone)

    allowed = await _property_domains(user)
    _within_the_property(payload.domains, allowed)
    domains = _stored_domains(payload.role, payload.domains, allowed)
    outlet_ids = await _checked_outlet_ids(db, payload.outlet_ids)
    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        # Both written explicitly, `None` and never omitted, so that every user document
        # says what it holds. A key that is sometimes absent and sometimes null is two
        # shapes for one fact, and the uniqueness pre-check above reads this column.
        "email": email,
        "phone": phone,
        "role": payload.role,
        "domains": domains,
        "outlet_ids": outlet_ids,
        "joined_on": payload.joined_on,
        "designation": payload.designation,
        "salary_monthly": payload.salary_monthly,
        "paid_leave_days": payload.paid_leave_days,
        "emergency_contact": payload.emergency_contact,
        "document_number": payload.document_number,
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
        await unscoped_db.users.insert_one(doc)
    except DuplicateKeyError as exc:
        # server.py declares email and phone unique. Locally the pre-check above is what
        # does the work, because the mock database's create_index is a no-op and its
        # insert never raises. Against real MongoDB two concurrent creates both pass the
        # pre-check and the loser lands here — without this it would reach the client as
        # a 500. The driver names the index it violated, so the owner is told which of
        # the two fields to change rather than being told "email" about a number.
        raise HTTPException(
            409, DUPLICATE_PHONE if "phone" in str(exc) else DUPLICATE_EMAIL)
    return _public(doc, with_salary=True)


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, payload: StaffUpdateIn,
                       user: dict = Depends(ADMIN),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    target = await unscoped_db.users.find_one({"id": staff_id, **_mine(user)}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")

    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")

    allowed = await _property_domains(user)
    _within_the_property(payload.domains, allowed)
    domains = _stored_domains(payload.role, payload.domains, allowed)
    outlet_ids = await _checked_outlet_ids(db, payload.outlet_ids)
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

    await unscoped_db.users.update_one({"id": staff_id, **_mine(user)}, {"$set": {
        "name": payload.name.strip(),
        "role": payload.role,
        "domains": domains,
        "outlet_ids": outlet_ids,
        "joined_on": payload.joined_on,
        "designation": payload.designation,
        "salary_monthly": payload.salary_monthly,
        "paid_leave_days": payload.paid_leave_days,
        "emergency_contact": payload.emergency_contact,
        "document_number": payload.document_number,
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
            and await _count_active_admins(user.get("property_id")) == 0:
        await unscoped_db.users.update_one({"id": staff_id, **_mine(user)},
                                           {"$set": previous})
        raise HTTPException(409, "This would leave the property with no active admin")

    return _public(await unscoped_db.users.find_one({"id": staff_id}, {"_id": 0}),
                   with_salary=True)


@router.post("/staff/{staff_id}/active")
async def set_active(staff_id: str, payload: ActiveIn, user: dict = Depends(ADMIN)):
    target = await unscoped_db.users.find_one({"id": staff_id, **_mine(user)}, {"_id": 0})
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
    await unscoped_db.users.update_one({"id": staff_id, **_mine(user)},
                                       {"$set": {"active": payload.active}})

    # A compensating check, not a transaction — there are no transactions here, the mock
    # database has none. Sequentially the self-guard above is enough, but two admins
    # deactivating each other at the same instant each see the other still active and
    # both writes land, leaving zero active admins: /api/staff needs admin *and* active,
    # so nobody can undo it without editing the database by hand. Re-counting after the
    # write and putting the target back narrows that window rather than closing it —
    # both requests can still read a count of 1 before either restores.
    if not payload.active and await _count_active_admins(user.get("property_id")) == 0:
        await unscoped_db.users.update_one({"id": staff_id, **_mine(user)},
                                           {"$set": {"active": previous_active}})
        raise HTTPException(409, "This would leave the property with no active admin")

    return _public(await unscoped_db.users.find_one({"id": staff_id}, {"_id": 0}),
                   with_salary=True)


@router.post("/staff/{staff_id}/password")
async def reset_password(staff_id: str, payload: PasswordIn, user: dict = Depends(ADMIN)):
    target = await unscoped_db.users.find_one({"id": staff_id, **_mine(user)}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")
    # The same rule as creating them, against the account being reset rather than the
    # admin doing it: a reset is the other half of "wherever a password is set", and it
    # is the half that gets typed in a hurry because somebody is locked out and waiting.
    problem = password_problem(payload.password, target.get("email"))
    if problem:
        raise HTTPException(400, problem)

    await unscoped_db.users.update_one({"id": staff_id, **_mine(user)}, {"$set": {
        "password_hash": hash_password(payload.password)}})
    return {"ok": True}
