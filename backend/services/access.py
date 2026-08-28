"""Authorization: property state and shape, then role, then work domain.

Pure functions over plain dicts — no database, no request — so every access rule is
testable in isolation and readable in one place.

A role says what someone does; a domain says which part of the business they do it in.
Both must pass, except for admin, who is never domain-checked.

Ahead of both sits the hotel itself, twice over. A suspended property refuses its own
staff exactly as a deactivated staff member is refused; and a property that is not a
hotel — a restaurant or a bar, with no rooms — refuses the hotel endpoints to everybody
in it, its owner included. Both live in this one function rather than in a second gate
that the next endpoint forgets to apply.
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


# What kind of business a tenant is. Chosen at signup and stored on the property record,
# because "does this place have rooms" is not a preference — it decides which half of the
# product exists for them at all.
#
# `hotel` and `both` grant the same domains on purpose. They are kept apart because the
# signup wording differs and the platform operator wants to see which is which; folding
# them into one value would throw that away to save a dictionary entry.
PROPERTY_HOTEL = "hotel"
PROPERTY_OUTLET = "outlet"
PROPERTY_BOTH = "both"

PROPERTY_TYPES = (PROPERTY_HOTEL, PROPERTY_OUTLET, PROPERTY_BOTH)

# What a property that has never said is taken to be. Every property that existed before
# the type did has been running rooms *and* outlets, so `both` is what they have actually
# been operating as — see migrations/backfill_property_type.py, which stamps it rather
# than leaving the reader of a record to guess.
DEFAULT_PROPERTY_TYPE = PROPERTY_BOTH

_TYPE_DOMAINS: dict[str, tuple[str, ...]] = {
    PROPERTY_HOTEL: DOMAINS,
    PROPERTY_OUTLET: OUTLET,
    PROPERTY_BOTH: DOMAINS,
}


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


def domains_for_property_type(property_type: str) -> tuple[str, ...]:
    """The work domains a property of this type has, and therefore the most its staff
    may hold.

    A pure function beside DOMAINS rather than a branch in the signup router, because it
    is a rule about what a business *is*: the API bounds a user's domains by it and the
    staff screen draws its pickers from it, and two copies of that mapping is how a
    restaurant ends up being offered a rooms tick on the day the third caller is written.

    An unknown type raises, the same stance `normalise_domains` takes towards an unknown
    domain: a typo must fail where it is written rather than quietly hand somebody the
    wrong half of the business.
    """
    try:
        return _TYPE_DOMAINS[property_type]
    except (KeyError, TypeError):
        raise AccessError(f"unknown property type: {property_type!r}") from None


def property_domains(property_record) -> tuple[str, ...]:
    """The domains a *stored* property has — the same rule, applied to a record.

    Three answers, and the difference between them matters:

    * no record at all is the empty tuple. A user naming a property that does not exist
      is a broken tenant, and `_property_usable` already refuses them; granting the whole
      vocabulary here would make the one place that reads it disagree.
    * a record with no `property_type` is `both`. That is what every property predating
      the field has been operating as, and the rule has to give the same answer whether
      or not the startup migration has run yet.
    * a record with a type this module has never been taught is the empty tuple, not
      `both`. An unrecognised value is a bug or a hand-edited record; "all of it" is the
      permissive guess and therefore the wrong one. The shared endpoints are unaffected,
      so such a property can still be looked at while somebody fixes it.
    """
    if not property_record:
        return ()
    try:
        return domains_for_property_type(
            property_record.get("property_type") or DEFAULT_PROPERTY_TYPE)
    except AccessError:
        return ()


# The screens an owner ticks per staff member, and what each one takes to reach.
#
# These keys are STABLE IDENTIFIERS. They are stored on user records, so renaming one
# does not move anybody across: it silently revokes that screen from everyone who held
# it, because the tick stays ticked on a key no endpoint answers to any more. Add keys,
# retire keys, but never rename one that has shipped.
#
# `section` is presentation only — `hotel.guests` and `outlet.inventory` sit behind
# `shared` endpoints and are filed under a section so the sidebar has somewhere to put
# them, not because they belong to that half of the business.
#
# `domains` is what the endpoints behind the screen declare. It is here so that a tick
# outside somebody's work domains can be refused when it is saved (see
# `permission_in_domains`): the domain check runs first at request time, so such a tick
# could never take effect, and storing it silently would be a lie the owner would rely on.
SCREENS: dict[str, dict] = {
    "hotel.front_desk":   {"label": "Front desk",   "section": "Hotel",      "domains": ("hotel",)},
    "hotel.bookings":     {"label": "Bookings",     "section": "Hotel",      "domains": ("hotel",)},
    "hotel.calendar":     {"label": "Occupancy",    "section": "Hotel",      "domains": ("hotel",)},
    "hotel.rooms":        {"label": "Rooms",        "section": "Hotel",      "domains": ("hotel",)},
    "hotel.rates":        {"label": "Rates",        "section": "Hotel",      "domains": ("hotel",)},
    "hotel.guests":       {"label": "Guests",       "section": "Hotel",      "domains": (SHARED,)},
    # The attendant's phone screen: room statuses and the requests raised against them.
    # Declared `hotel` like the rest of this section — an outlet has no rooms to clean.
    "hotel.housekeeping": {"label": "Housekeeping", "section": "Hotel",      "domains": ("hotel",)},
    "outlet.tables":      {"label": "Tables",       "section": "Restaurant", "domains": OUTLET},
    "outlet.pos":         {"label": "POS / Bill",   "section": "Restaurant", "domains": OUTLET},
    "outlet.kot":         {"label": "KOT board",    "section": "Restaurant", "domains": OUTLET},
    "outlet.reservations": {"label": "Reservations", "section": "Restaurant", "domains": OUTLET},
    "outlet.menu":        {"label": "Menu",         "section": "Restaurant", "domains": OUTLET},
    "outlet.inventory":   {"label": "Inventory",    "section": "Restaurant", "domains": (SHARED,)},
    "outlet.reports":     {"label": "Reports",      "section": "Restaurant", "domains": OUTLET},
    # Grantable rather than implied by the role, so a manager can be given the analytics
    # screen without being made an admin.
    "admin.staff":        {"label": "Staff",        "section": "Admin",      "domains": (SHARED,)},
    "admin.analytics":    {"label": "Analytics",    "section": "Admin",      "domains": DOMAINS},
    # What the property spends, and the profit that falls out of it. `DOMAINS` for the
    # same reason analytics has it: expenditure spans the business — salaries belong to
    # the place, not to the restaurant or the hotel — and holding any one domain is
    # enough, so an outlet property's staff reach it exactly as a hotel's do.
    #
    # Grantable rather than implied by the role, again like analytics: an owner who wants
    # the accountant or the front-desk supervisor entering bills ticks this, without
    # having to make them an admin. Note that holding it reveals revenue as well as
    # spending — there is no honest way to show what is left without showing what came
    # in — which is why the migration hands it to the audience analytics already had.
    "admin.expenses":     {"label": "Expenses",     "section": "Admin",      "domains": DOMAINS},
}

SCREEN_KEYS = tuple(SCREENS)


# What each role reached before the ticks existed — read off the sidebar in
# frontend/src/components/app/AppLayout.jsx, which is what actually decided it, rather
# than from anybody's memory of what a role name ought to mean.
#
# This is NOT consulted at request time: permissions decide what a person reaches, roles
# decide who may edit. It is used twice, both times to answer "what should this account
# start with" — by the startup backfill, so nobody loses access on deploy, and as the
# default when a staff member is created without ticks. Frozen: it describes the old
# navigation, so it does not grow when a new screen is added.
ROLE_SCREENS: dict[str, tuple[str, ...]] = {
    "admin": SCREEN_KEYS,
    # Everything the admin console has except staff administration, which stays admin-only.
    "manager": tuple(k for k in SCREEN_KEYS if k != "admin.staff"),
    "front_desk": ("hotel.front_desk", "hotel.bookings", "hotel.calendar", "hotel.guests"),
    "waiter": ("outlet.tables", "outlet.reservations", "outlet.pos", "outlet.kot"),
    "kitchen": ("outlet.kot", "outlet.inventory"),
    # One screen, and the shortest entry here on purpose. An attendant reaches the
    # corridor they work in and nothing else — not bookings, not rates, not the POS, not
    # the guest list. Adding a *role* key is not the growth this table is frozen against:
    # what stays fixed is the set of screens each existing role reached, so that a new
    # screen does not quietly widen everybody. A role that has never existed has no old
    # navigation to describe, and giving it an empty tuple would create accounts that
    # cannot open the one screen they were hired for.
    "housekeeping": ("hotel.housekeeping",),
}


def default_permissions(role: str, held: list[str] | tuple[str, ...]) -> list[str]:
    """The screens an account of this role, working in these domains, should start with.

    Intersected with the domains rather than handed over whole: a screen outside
    somebody's domains is refused at request time anyway, so storing it would put a tick
    on the staff screen that does nothing — the exact lie `permission_in_domains` exists
    to prevent — and the next save of that record would be refused for containing it.
    """
    if role == "admin":
        # An admin is never permission-checked, so this costs them nothing today. It is
        # stored for the same reason `_stored_domains` stores every domain for them: a
        # later demotion to manager must not silently produce an account reaching nothing.
        held = held or DOMAINS
    return [k for k in ROLE_SCREENS.get(role, ()) if permission_in_domains(k, held)]


def normalise_permissions(permission: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Accept one screen key or several, and reject an unknown one loudly.

    Several because one endpoint can sit behind more than one screen — GET /rooms is
    read by the Rooms screen and by the front desk's room picker — and holding any one
    of them is enough, the same any-of rule the declared domains already use.

    A typo must fail where the endpoint declares it (import time), not silently deny
    every user at runtime; this is `normalise_domains`' reasoning applied to the ticks.
    """
    if isinstance(permission, str):
        permission = (permission,)
    try:
        out = tuple(permission)
    except TypeError:
        raise AccessError(f"invalid permission: {permission!r}") from None
    if not out:
        raise AccessError("an endpoint that declares a permission must name one")
    for key in out:
        if key not in SCREENS:
            raise AccessError(f"unknown screen key: {key}")
    return out


def permission_in_domains(key: str, held: list[str] | tuple[str, ...]) -> bool:
    """Whether ticking this screen for someone holding `held` could ever take effect.

    Used when permissions are saved. The request-time rule is the one in `can_access`;
    this is the same question asked ahead of time, so the owner is told at the moment
    they tick rather than discovering later that the screen never opened.
    """
    if key not in SCREENS:
        raise AccessError(f"unknown screen key: {key}")
    held = tuple(held or ())
    if not held:
        return False
    required = SCREENS[key]["domains"]
    if tuple(required) == (SHARED,):
        return True
    return any(d in held for d in required)


def narrow_to_domains(user: dict, allowed: tuple[str, ...]) -> dict:
    """The patch that brings one staff record inside the domains its property now has.

    Called when the platform operator changes what a business *is* — the one correction
    that could not be made from inside the app before, and the one that can strand
    people. Narrowing `both` to `outlet` takes the hotel away; every receptionist would
    otherwise be left holding a domain the property no longer has and screens that would
    403, which is exactly the lie `permission_in_domains` exists to prevent when the same
    thing is ticked by hand on the staff screen.

    Returns only what changed, and `{}` when nothing did, so re-typing a property to the
    type it already holds writes nothing at all.

    Three cases, and the third is the decision worth stating out loud:

    * **an admin** is re-stamped with the whole of `allowed`. Same rule as
      `routers/staff.py::_stored_domains`, and it is what keeps the property's
      last-active-admin invariant true through a retype: the account that has to be able
      to fix everything afterwards is never the one this leaves with nothing.
    * **anyone whose domains survive** keeps the intersection, and keeps the screens that
      intersection can still reach. A manager over the restaurant and the front desk goes
      on managing the restaurant.
    * **a non-admin left with nothing** — a front-desk-only receptionist at a business
      that no longer has rooms — is **deactivated**. The job they were hired for does not
      exist here any more, and the alternatives are worse: leaving them holding `hotel`
      is the stranding this function exists to stop, and refusing the retype outright
      strands the *operator*, who cannot edit another business's staff at all and would
      have no way to proceed. They are handed `allowed` rather than an empty list because
      empty is not a stable state — `migrations/backfill_domains.py` repairs an empty
      `domains` on every startup by granting all three, including the one the property
      just gave up. Deactivation is what actually holds them (`can_access` refuses an
      inactive account before it looks at anything else), and their own admin brings them
      back deliberately, from the staff screen, where `_within_the_property` still holds.

    Destructive in effect and it deletes nothing, exactly like suspension: rooms, rates
    and bookings sit untouched, so a type set wrong at signup and corrected twice loses
    no data.
    """
    if not allowed:
        # Emptying a property's whole roster is never the right answer to a type nobody
        # recognises. The caller has been handed a property whose type this module cannot
        # read, which is a bug to fix, not a licence to lock everybody out.
        raise AccessError(
            "cannot narrow a staff record to no domains at all — the property's type "
            "could not be read")

    role = user.get("role")
    held = list(user.get("domains") or ())
    if role == "admin":
        domains = list(allowed)
    else:
        domains = [d for d in held if d in allowed]

    stranded = not domains
    if stranded:
        domains = list(allowed)

    permissions = [k for k in (user.get("permissions") or ())
                   if k in SCREENS and permission_in_domains(k, domains)]
    if role == "admin" and not permissions:
        permissions = default_permissions(role, domains)

    patch: dict = {}
    if domains != held:
        patch["domains"] = domains
    if permissions != list(user.get("permissions") or ()):
        patch["permissions"] = permissions
    if stranded and user.get("active", True):
        patch["active"] = False
    return patch


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
    permission: str | tuple[str, ...] | list[str] | None = None,
) -> bool:
    """True when this user may reach an endpoint requiring these roles and domains.

    `property_record` is the caller's hotel, resolved once per request. Pass `None` when
    the user's `property_id` names no record; omit it only outside tenancy (see UNSCOPED).
    It decides two things, both ahead of the role and the admin bypass: whether the hotel
    may trade at all, and which half of the business it *has* — an outlet property's
    hotel endpoints are refused to everybody there, its owner included.

    `setup_time` marks an endpoint a `pending` hotel may still reach — property details,
    room types, rooms, rates, meal plans, tax slabs, menu, tables and staff. It is a
    keyword argument, and it defaults to False, so that every operating endpoint is
    locked while pending without anyone having to remember to say so, and the handful
    that unlock read that way at the call site — the same explicitness the declared
    domain already has.

    `permission` is the screen this endpoint sits behind, one key or several. It is
    checked last, after the domain, because a tick is only meaningful inside a domain
    the user holds — checking it first, or instead, would let a ticked screen widen
    somebody past the part of the business they work in.
    """
    required = normalise_domains(domains)
    # Validated before anything is decided, so a typo cannot pass unnoticed on the
    # requests that are refused earlier for some other reason.
    wanted = normalise_permissions(permission) if permission is not None else ()

    # A bare string is convenient shorthand for a single role, same as for domains —
    # but left unwrapped, `role not in roles` becomes substring matching on the str,
    # so "man" would pass a check for roles="manager".
    if isinstance(roles, str):
        roles = (roles,)

    # First, and ahead of the admin bypass below: suspending a hotel must lock out its
    # admin too, or the account worth the most is the one suspension does not reach.
    if not _property_usable(user, property_record, setup_time):
        return False

    # Then what the property *is*. A restaurant with no rooms has no hotel side, so its
    # hotel endpoints are refused outright rather than merely unlinked in the menu.
    #
    # This is a backstop and it is not redundant. The staff routes already bound a user's
    # domains by the property's, so no non-admin here can hold `hotel` — but the founding
    # admin of an outlet *is* an admin, and an admin is never domain-checked, so without a
    # check on the property itself `GET /api/bookings` would answer the one account every
    # such property has. Which is why it sits here, ahead of the bypass, exactly where the
    # suspension check sits and for the same reason.
    #
    # `shared` is exempt: guests, inventory and the property record are not the hotel's,
    # and a bar regular is still a guest.
    if (property_record is not UNSCOPED and required != (SHARED,)
            and not any(d in property_domains(property_record) for d in required)):
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

    # A shared endpoint is satisfied by any domain at all; otherwise one of the declared
    # ones has to be held.
    if required != (SHARED,) and not any(d in held for d in required):
        return False

    if not wanted:
        return True

    # Ticks are what the owner actually decided, so a user the migration has not reached
    # — no `permissions` key at all — fails closed rather than open.
    return any(key in (user.get("permissions") or ()) for key in wanted)
