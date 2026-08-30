"""What an outlet is, and what makes one valid.

A salon is not a new kind of thing. It is an outlet, which the restaurant and the bar
already are: a place with a catalogue that serves a guest, takes money, and posts to a
folio. This module holds the vocabulary and the rules; the database and the HTTP live
in routers/outlets.py, and nothing here imports either.

`outlet_problem` returns a message rather than raising, following services/password.py —
services/ is kept free of HTTP so that a rule can be tested without a server.
"""

# The kinds a hotel can choose from. Fixed rather than free text because `kind` decides
# which domain staff need and which reporting bucket the takings land in; a hotel that
# could invent a kind could invent one nobody can be assigned to.
#
# `name` is the free-text field, and the two are deliberately separate: a property may
# run two restaurants with different names and the same kind.
KINDS = ("restaurant", "bar", "salon", "gym", "laundry", "other")

# One domain for every non-food kind, rather than one per kind.
#
# A hotel that adds a salon and a gym staffs them from the same handful of people almost
# every time, and three domains would make the staff screen ask three questions to
# express one fact. If a property ever genuinely needs to keep salon staff out of the
# gym, `outlet_ids` on the user record already says exactly that — which is the narrower
# question, and the one this design added it for.
SERVICES = "services"

# Which work domain each kind sits behind. `restaurant` and `bar` keep the domains they
# have always had: production has waiters holding those strings, and remapping them would
# move every one of them out of the screens they work in.
KIND_DOMAIN = {
    "restaurant": "restaurant",
    "bar": "bar",
    "salon": SERVICES,
    "gym": SERVICES,
    "laundry": SERVICES,
    "other": SERVICES,
}

_DEFAULT_NAMES = {
    "restaurant": "Restaurant",
    "bar": "Bar",
    "salon": "Salon",
    "gym": "Gym",
    "laundry": "Laundry",
    "other": "Outlet",
}


def default_name(kind: str) -> str:
    """A name to prefill the form with, so the field is never blank.

    Answers for an unknown kind too: a caller that has already passed validation should
    not be able to crash a form by asking for a label.
    """
    return _DEFAULT_NAMES.get(kind, "Outlet")


def outlet_problem(name: str, kind: str, charges_to_folio: bool,
                   takes_direct_payment: bool) -> str | None:
    """What is wrong with this outlet, in words, or None if nothing is.

    Returns the message rather than raising, for the reason given in the module
    docstring. The caller turns it into a 400.
    """
    if not (name or "").strip():
        return "The outlet needs a name"
    if kind not in KINDS:
        return f"{kind} is not an outlet kind — expected one of: {', '.join(KINDS)}"
    if not charges_to_folio and not takes_direct_payment:
        # Caught here rather than discovered at the counter with a guest waiting.
        return ("An outlet must take money somehow: charge to a room folio, "
                "accept direct payment, or both")
    return None
