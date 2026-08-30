"""What a guest's package includes, and how much of it is left.

A rate points at a package; a package holds inclusions; an inclusion says how much of
what, how often. **That is the entire difference between an elite room and a normal
one** — the elite rate points at a package with more in it. No code anywhere branches on
room class, which is what stops "elite" becoming a special case that leaks into a dozen
files and disagrees with itself in two of them.

Pure functions over plain dicts: no database, no request. The periods are the whole
difficulty, and they are the reason this is a module rather than a division at the call
site. "Two spa treatments" is two for the stay; "breakfast" is one a night; a gym pass is
unlimited every day and empty again tomorrow. Getting that wrong gives a guest five
breakfasts against an entitlement of two, and nobody notices until the month's food cost
lands.
"""

# How often an allowance refills.
PER_STAY = "per_stay"    # a flat total for the whole booking
PER_NIGHT = "per_night"  # quantity for each night booked
PER_DAY = "per_day"      # quantity each calendar day, empty again tomorrow

PERIODS = (PER_STAY, PER_NIGHT, PER_DAY)

# What an inclusion covers.
SCOPE_ITEM = "item"        # one catalogue item — "breakfast buffet"
SCOPE_CATEGORY = "category"  # a category within an outlet — "any soft drink"
SCOPE_OUTLET = "outlet"    # everything the outlet sells — "any treatment"

SCOPES = (SCOPE_ITEM, SCOPE_CATEGORY, SCOPE_OUTLET)


def allowance(inclusion: dict, nights: int) -> int:
    """How many this inclusion grants, for the period it is counted over.

    `per_day` returns the *daily* figure rather than the whole stay's, because that is
    the number a caller compares against today's uses. `remaining` is what pairs the two.

    An unrecognised period grants nothing. That is the safe direction: the guest is
    charged and somebody queries it, where guessing "everything" gives the hotel's stock
    away silently. A hand-edited or imported row is the realistic way this happens.
    """
    quantity = int(inclusion.get("quantity") or 0)
    period = inclusion.get("period")

    if period == PER_STAY:
        return quantity
    if period == PER_NIGHT:
        # A day-use booking has no nights, so a per-night inclusion covers nothing.
        return quantity * max(0, int(nights or 0))
    if period == PER_DAY:
        return quantity
    return 0


def remaining(inclusion: dict, uses: list[dict], nights: int, day: str) -> int:
    """How many of this inclusion are left, given what has already been taken.

    `uses` may contain uses of *other* inclusions; they are filtered out here rather
    than by every caller. Counting every use against every inclusion would exhaust a
    package on its first morning — two spa treatments and three breakfasts are separate
    allowances that happen to belong to the same guest.

    Never negative. Three taken against two included is a real state — a manual
    override, a late correction — and the answer a screen needs is "none left", not
    "minus one".
    """
    total = allowance(inclusion, nights)
    inclusion_id = inclusion.get("id")

    # Counted by distinct id, not by list length. A use IS its id — that is the whole
    # reason the id is derived from the consumption rather than random — so the same use
    # appearing twice in the list is one use, however it got there. Counting positions
    # instead made a caller who merged a freshly-written row into a list that already
    # contained it report the allowance as spent twice.
    seen = set()
    taken = 0
    for u in uses:
        if u.get("inclusion_id") != inclusion_id:
            continue
        uid = u.get("id")
        if uid is not None:
            if uid in seen:
                continue
            seen.add(uid)
        # A daily allowance is counted against one day. A stay or per-night allowance
        # is counted across the whole booking, so the date is not consulted at all.
        if inclusion.get("period") == PER_DAY and u.get("used_on") != day:
            continue
        taken += 1

    return max(0, total - taken)


def covers(inclusion: dict, outlet_id: str, item: dict | None) -> bool:
    """Whether this inclusion applies to what is being sold.

    Outlet first in every case: an inclusion belongs to one outlet, so a breakfast
    entitlement cannot be spent in the salon however the scopes are read.
    """
    if inclusion.get("outlet_id") != outlet_id:
        return False

    scope = inclusion.get("scope")
    if scope == SCOPE_OUTLET:
        return True
    if not item:
        return False
    if scope == SCOPE_ITEM:
        return inclusion.get("ref_id") == item.get("id")
    if scope == SCOPE_CATEGORY:
        return inclusion.get("ref_id") == item.get("category")
    # An unrecognised scope covers nothing, for the reason `allowance` returns zero.
    return False
