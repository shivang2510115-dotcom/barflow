"""Authorization: role plus work domain.

Pure functions over a user dict — no database, no request — so every access rule is
testable in isolation and readable in one place.

A role says what someone does; a domain says which part of the business they do it in.
Both must pass, except for admin, who is never domain-checked.
"""

# The areas a staff member can be assigned to.
DOMAINS = ("hotel", "restaurant", "bar")

# Endpoints serving more than one area declare this instead. A bar regular and a hotel
# guest are the same person, so splitting guest records by domain would stop the desk
# seeing an arrival's bar history — which is the product's whole claim.
SHARED = "shared"


class AccessError(Exception):
    """Raised when an access rule is configured with something meaningless."""


def normalise_domains(domains: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Accept a single domain or several, and reject unknown values loudly.

    A typo in an endpoint's declared domain must fail at import, not silently deny
    every user at runtime.
    """
    if isinstance(domains, str):
        domains = (domains,)
    out = tuple(domains)
    if not out:
        raise AccessError("an endpoint must declare at least one domain")
    for d in out:
        if d != SHARED and d not in DOMAINS:
            raise AccessError(f"unknown domain: {d}")
    if SHARED in out and len(out) > 1:
        raise AccessError("shared cannot be combined with a specific domain")
    return out


def can_access(
    user: dict,
    domains: str | tuple[str, ...] | list[str],
    roles: tuple[str, ...] | list[str],
) -> bool:
    """True when this user may reach an endpoint requiring these roles and domains."""
    required = normalise_domains(domains)

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
