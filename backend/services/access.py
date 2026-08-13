"""Authorization: property state, then role, then work domain.

Pure functions over plain dicts — no database, no request — so every access rule is
testable in isolation and readable in one place.

A role says what someone does; a domain says which part of the business they do it in.
Both must pass, except for admin, who is never domain-checked.

Ahead of both sits the hotel itself. A suspended property refuses its own staff exactly
as a deactivated staff member is refused, and for the same reason — so tenancy lives in
this one function rather than in a second gate that the next endpoint forgets to apply.
"""

# The areas a staff member can be assigned to.
DOMAINS = ("hotel", "restaurant", "bar")

# Endpoints serving more than one area declare this instead. A bar regular and a hotel
# guest are the same person, so splitting guest records by domain would stop the desk
# seeing an arrival's bar history — which is the product's whole claim.
SHARED = "shared"

# This property's restaurant and bar share the order, menu, table and reservation
# screens, so those endpoints declare both domains: holding either one grants access.
# Declaring "restaurant" alone would lock a bar-only waiter out of the POS.
OUTLET = ("restaurant", "bar")


# The three states a hotel can be in. Named here, in the module that enforces them,
# because the enforcement is what gives them meaning; the model and the platform routes
# import these rather than each spelling the strings out again.
PENDING = "pending"
LIVE = "live"
SUSPENDED = "suspended"

# The platform operator. Belongs to no hotel by design — see _property_usable.
PLATFORM_ADMIN = "platform_admin"

# Distinguishes "the caller passed no property" from "the caller's property is None".
# They must not mean the same thing: a property that could not be found is a refusal,
# while an omitted argument is a call made before tenancy existed and has to keep its
# old answer. `require_access` is the only authorization dependency, so there is exactly
# one call site that has to supply the property — one place to get right, not 244.
UNSCOPED = object()


class AccessError(Exception):
    """Raised when an access rule is configured with something meaningless."""


def normalise_domains(domains: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Accept a single domain or several, and reject unknown values loudly.

    A typo in an endpoint's declared domain must fail at import, not silently deny
    every user at runtime.
    """
    if isinstance(domains, str):
        domains = (domains,)
    # A missing or malformed declaration is a configuration mistake, not a Python
    # TypeError — callers are told AccessError is the one thing to catch.
    try:
        out = tuple(domains)
    except TypeError:
        raise AccessError(f"invalid domains: {domains!r}") from None
    if not out:
        raise AccessError("an endpoint must declare at least one domain")
    for d in out:
        if d != SHARED and d not in DOMAINS:
            raise AccessError(f"unknown domain: {d}")
    if SHARED in out and len(out) > 1:
        raise AccessError("shared cannot be combined with a specific domain")
    return out


def _property_usable(user: dict, property_record, setup_time: bool) -> bool:
    """Whether this user's hotel is in a state that permits this endpoint at all.

    Ahead of role and domain for the same reason `active` is: a hotel that has not been
    approved, or has been switched off, is not a hotel whose staff list matters yet. One
    rule in one place, so a new endpoint cannot be written that forgets to consult it.
    """
    # The operator belongs to no hotel, so no hotel endpoint is theirs. Refused on the
    # role rather than on the absent property record, because "has no property" is the
    # operator's normal state — reading it as "nothing to check" would turn the one
    # login that can approve hotels into a key to all of their guest data.
    if user.get("role") == PLATFORM_ADMIN:
        return False

    if property_record is UNSCOPED:
        return True

    # A hotel login that names no hotel cannot be placed, and an unplaceable request is
    # exactly what tenancy exists to stop. A startup migration stamps the existing users,
    # but the rule must hold whether or not that migration has run.
    if not user.get("property_id"):
        return False

    # An id pointing at no record is a broken tenant, not an unrestricted one.
    if not property_record:
        return False

    status = property_record.get("status")
    if status == LIVE:
        return True
    # Pending hotels configure but do not operate: they evaluate the product with their
    # own rooms and rates, while no guest money moves through an unvetted property.
    if status == PENDING:
        return setup_time
    # Suspended, or a status this function has never been taught. Both refuse: an unknown
    # state is a bug or a hand-edited record, and neither is a licence to operate.
    return False


def can_access(
    user: dict,
    domains: str | tuple[str, ...] | list[str],
    roles: str | tuple[str, ...] | list[str],
    property_record=UNSCOPED,
    *,
    setup_time: bool = False,
) -> bool:
    """True when this user may reach an endpoint requiring these roles and domains.

    `property_record` is the caller's hotel, resolved once per request. Pass `None` when
    the user's `property_id` names no record; omit it only outside tenancy (see UNSCOPED).

    `setup_time` marks an endpoint a `pending` hotel may still reach — property details,
    room types, rooms, rates, meal plans, tax slabs, menu, tables and staff. It is a
    keyword argument, and it defaults to False, so that every operating endpoint is
    locked while pending without anyone having to remember to say so, and the handful
    that unlock read that way at the call site — the same explicitness the declared
    domain already has.
    """
    required = normalise_domains(domains)

    # A bare string is convenient shorthand for a single role, same as for domains —
    # but left unwrapped, `role not in roles` becomes substring matching on the str,
    # so "man" would pass a check for roles="manager".
    if isinstance(roles, str):
        roles = (roles,)

    # First, and ahead of the admin bypass below: suspending a hotel must lock out its
    # admin too, or the account worth the most is the one suspension does not reach.
    if not _property_usable(user, property_record, setup_time):
        return False

    # Deactivated accounts are refused regardless of role. This applies to admin too —
    # otherwise deactivating a compromised admin would do nothing.
    if not user.get("active", True):
        return False

    role = user.get("role")
    if roles and role not in roles:
        return False

    # Admin is never domain-checked: one admin sees the whole property.
    if role == "admin":
        return True

    held = tuple(user.get("domains") or ())
    if not held:
        return False

    if required == (SHARED,):
        return True

    return any(d in held for d in required)
