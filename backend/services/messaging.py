"""What may be sent to a customer, to whom, and what is missing when it cannot be.

Pure functions over plain values — no database, no request — the same shape as
services/identity.py and services/folio.py, so every rule here is testable on its own and
each router is left holding nothing but the HTTP status code it turns a refusal into.

**The constraint the whole feature is shaped around.** WhatsApp permits free-form text
only within 24 hours of the customer messaging the business. A birthday greeting, or a
note days after a visit, is by definition outside that window, so it can only ever be a
**template Meta has approved** — submitted, reviewed, and then sent by name with its
variables filled in. Free text there is not "less good", it is rejected by the API with
error 131047, which `routers/reports.py::_WHATSAPP_ERRORS` already translates.

So nothing in this module composes a sentence, and nothing anywhere else does either. A
message is a template name plus an ordered list of variables, and the template names
belong to the property: the hotel obtains them from Meta under its own business account,
and a second hotel on this deployment will have obtained different ones. A default
baked in here would be a name that does not exist in anybody's account — a send that
fails at Meta rather than at the point where somebody could fix it.

**A property that has configured nothing can send nothing, and says so.** Every "what is
missing" string below is modelled on `whatsapp_config_problem()`: it names the individual
piece rather than saying "not configured", because each piece is a different afternoon's
work and being told which one is the difference.

**Consent is a refusal like any other**, and deliberately the *first* one — see
`blocking_problem`. Unsolicited commercial messaging is regulated in India and it is the
property carrying that risk, so "this customer asked not to be messaged" outranks every
other reason a send might be refused, including the ones that would have refused it
anyway.
"""
import re
from datetime import date

from services.identity import PHONE_SHAPE, normalise_phone

# The two kinds of message this feature sends. Not an open vocabulary: each one has its
# own template setting, its own dedupe rule and its own variables, so a third kind is a
# change to this module rather than a string a caller invents.
OCCASION = "occasion"
FOLLOW_UP = "follow_up"
KINDS = (OCCASION, FOLLOW_UP)

# What a log row can say happened. `refused` is not `failed`: nothing was handed to Meta,
# and the difference matters when somebody is working out whether a greeting was blocked
# by this property's own rules or lost somewhere on the way to a phone.
SENT = "sent"
FAILED = "failed"
REFUSED = "refused"

# Two months is a long time not to have seen a regular and a short time to a hotel whose
# guests come once a year. It is only the *starting* number on a screen the owner has to
# open and switch on before anything is sent, which is why a figure can be picked at all.
DEFAULT_FOLLOW_UP_DAYS = 60

# Meta stores a template under a name *and* a language, and asks for both at send time.
# "en" is the safe start for an Indian property writing in English; a hotel messaging in
# Hindi sets `template_language` to the code its approved template was filed under.
DEFAULT_TEMPLATE_LANGUAGE = "en"

# Quoted verbatim to staff, and stored on the log row that records the refusal. Written
# as a statement about the customer rather than about the software, because that is what
# the person holding the phone at the front desk has to be able to repeat.
NO_CONSENT = "This customer has asked not to be messaged."

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MessagingError(Exception):
    """Raised when a message is described with something meaningless — an unknown kind,
    a label that is not a label. A programming error, not a request error."""


# ------------------------------- occasions --------------------------------
def normalise_label(value) -> str:
    """The stored form of what somebody typed, or "" if there is nothing there.

    Whitespace-collapsed and trimmed, and otherwise left exactly as written. The label is
    **free text on purpose**: a birthday and a wedding anniversary are the two everyone
    thinks of, but a property knows its guests better than this module does — "Ananya's
    first birthday", "the anniversary of the day they got engaged here". An enum would
    have to be extended by a deploy every time a hotel had an idea.

    Case is preserved because it is shown to a guest inside the message; matching is done
    on `label_key` below, which is not.
    """
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def label_key(value) -> str:
    """The form a label is matched on when looking up its template.

    Lowercased, so "Birthday" and "birthday" find the same template. Anything a property
    spells two ways is two entries, which is the honest answer — this cannot know that
    "Anniv." meant "Anniversary".
    """
    return normalise_label(value).lower()


def month_day(value) -> str | None:
    """The recurring part of a date — `MM-DD` — or None if there is no usable date.

    A birthday recurs and a birth year does not, so the year is stored (a hotel may like
    to know) and never matched. This is what puts a guest born in 1962 on today's list.
    """
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        return None
    text = value.strip()
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text[5:]


# ------------------------------- templates --------------------------------
def _settings_value(settings, key, default):
    value = (settings or {}).get(key)
    return default if value is None else value


def template_for(settings, kind: str, label: str = "") -> str:
    """The approved template name this message would be sent under, or "".

    Two levels for an occasion, and both are the property's: a name filed against the
    label itself — a birthday greeting does not read like an anniversary one — falling
    back to one default template for every occasion the hotel has not thought about
    separately. A hotel with one all-purpose template configures only the default and
    never opens the map.
    """
    if kind == FOLLOW_UP:
        return str(_settings_value(settings, "follow_up_template", "") or "").strip()
    if kind != OCCASION:
        raise MessagingError(f"unknown message kind: {kind!r}")
    per_label = _settings_value(settings, "occasion_templates", {}) or {}
    named = str(per_label.get(label_key(label), "") or "").strip()
    if named:
        return named
    return str(_settings_value(settings, "default_occasion_template", "") or "").strip()


def template_language(settings) -> str:
    return str(_settings_value(settings, "template_language",
                               DEFAULT_TEMPLATE_LANGUAGE) or
               DEFAULT_TEMPLATE_LANGUAGE).strip()


def template_problem(settings, kind: str, label: str = "") -> str:
    """What is missing before this message could be sent at all, or "".

    Deliberately shaped like `whatsapp_config_problem()`: the same "Not configured:"
    opening, and the same habit of naming the exact thing to go and fix rather than
    reporting a general unreadiness. The sentence about Meta's approval is in here because
    it is the part nobody expects — an owner who reads "no template" reasonably assumes
    they can type one in, and the truth is that it is a form on Meta's dashboard and a
    wait.
    """
    if template_for(settings, kind, label):
        return ""
    if kind == FOLLOW_UP:
        return ("Not configured: no WhatsApp template name for the visit follow-up "
                "(Admin -> Notifications -> follow-up template). Meta has to approve a "
                "template before it can carry a message sent outside the 24-hour window.")
    shown = normalise_label(label) or "this occasion"
    return (f'Not configured: no WhatsApp template name for the occasion "{shown}" '
            f"(Admin -> Notifications -> occasion templates, or the default template "
            f"used for every occasion without one of its own). Meta has to approve a "
            f"template before it can carry a greeting sent outside the 24-hour window.")


# ------------------------------ the recipient ------------------------------
def consent_problem(guest) -> str:
    """`NO_CONSENT` when this customer has asked not to be messaged, otherwise "".

    A missing field is consent, not refusal: every guest recorded before this existed was
    recorded by a property that had never asked, and reading absence as "opted out" would
    silently switch the feature off for the whole existing guest list. The opt-out is
    something somebody *did*, and it is stored as such.
    """
    return NO_CONSENT if (guest or {}).get("no_messages") else ""


def recipient_problem(guest) -> str:
    """Why this guest's number could not be messaged, or "".

    `normalise_phone` is the staff phone-login work's, reused rather than reimplemented:
    the bill says `09876500001`, the guest record says `+91 98765 00001`, and those are
    one customer or the follow-up sends twice. See services/identity.py.
    """
    raw = ((guest or {}).get("phone") or "").strip()
    if not raw:
        return ("This customer has no phone number on their record, so there is nowhere "
                "to send it.")
    if not normalise_phone(raw):
        return (f"'{raw}' is not a number WhatsApp can be given — expected {PHONE_SHAPE}.")
    return ""


def recipient(guest) -> str | None:
    """The number to hand Meta, in E.164. None when there is not one."""
    return normalise_phone(((guest or {}).get("phone") or "").strip())


def blocking_problem(settings, guest, kind: str, label: str = "") -> str:
    """The one reason this message cannot be sent, or "" — consent first.

    One function, so the list screen and the send endpoint cannot disagree about whether a
    row is sendable. A screen that offers a send the endpoint then refuses is how staff
    learn to ignore what the screen says.

    Ordering is not cosmetic. Consent is checked ahead of the template and the credentials
    because it is the answer that stays true after somebody fixes the configuration, and
    because the property needs to see "this person asked not to be messaged" rather than
    "no template" on the row for the one customer where the distinction carries legal
    weight.
    """
    return (consent_problem(guest)
            or recipient_problem(guest)
            or template_problem(settings, kind, label))


# ------------------------------- follow-ups --------------------------------
def follow_up_enabled(settings) -> bool:
    """Off unless the property has said otherwise, and `None` is not "yes".

    A property that has never opened the screen must not start messaging its customers
    because the software shipped with an opinion about how often a guest should hear from
    a restaurant.
    """
    return bool(_settings_value(settings, "follow_up_enabled", False))


def follow_up_days(settings) -> int:
    value = _settings_value(settings, "follow_up_days", DEFAULT_FOLLOW_UP_DAYS)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_FOLLOW_UP_DAYS


FOLLOW_UP_OFF = ("Follow-up messages are switched off for this property "
                 "(Admin -> Notifications -> follow up after a visit).")


def days_since(last_visit, as_of: str) -> int | None:
    """Whole days between two local calendar dates, or None if either is unreadable."""
    if not isinstance(last_visit, str) or not isinstance(as_of, str):
        return None
    try:
        return (date.fromisoformat(as_of) - date.fromisoformat(last_visit)).days
    except ValueError:
        return None


def due_for_follow_up(last_visit, as_of: str, days: int) -> bool:
    """Whether this customer has not been back for long enough to hear from the property.

    `>=`, so a 60-day window means "has not been back for 60 days" and fires on the 60th.
    A customer with no visit at all is not lapsed — they are not a customer — which is why
    None is False rather than "infinitely overdue".
    """
    gap = days_since(last_visit, as_of)
    if gap is None:
        return False
    try:
        return gap >= int(days)
    except (TypeError, ValueError):
        return False


# ------------------------------ sending once -------------------------------
def dedupe_key(kind: str, guest_id: str, subject: str, day: str) -> str:
    """The identity of one greeting: this kind, to this person, about this thing, today.

    A plain composed string rather than a hash, because it is read by a human looking at
    a stuck claim in the database and has to say what it is on sight.

    `subject` is the occasion's id for a greeting and the date of the visit being followed
    up for a follow-up. That is what makes "one follow-up per visit" fall out: a customer
    who comes back and lapses again has a different last visit and therefore a different
    key, while pressing the button twice about the same visit does not.
    """
    if kind not in KINDS:
        raise MessagingError(f"unknown message kind: {kind!r}")
    return f"{kind}:{guest_id}:{subject}:{day}"


def send_definitely_did_not_happen(result) -> bool:
    """Whether we *know* nothing reached the customer — the only case safe to retry.

    Three answers, not two, and the third is the reason this function exists:

    * refused before the request left us (`configured` False) — nothing was sent;
    * refused **by Meta**, with an HTTP status and usually one of their error codes —
      nothing was sent, and they have said so;
    * anything else — a socket that died, a timeout, a response we could not read. The
      message may well have been delivered. Releasing the claim there is how a guest gets
      two birthday messages, so the claim is kept and the log says what happened.
    """
    if not isinstance(result, dict) or result.get("sent"):
        return False
    if not result.get("configured", True):
        return True
    return result.get("status") is not None


# ------------------------------- variables ---------------------------------
# What goes into the template's {{1}}, {{2}}, {{3}} — positional, because that is what
# Meta's body component takes. The order is fixed here rather than at each call site so
# that the template a property submits for approval can be written against it once:
#
#     occasion:  {{1}} the customer's name, {{2}} the occasion, {{3}} the property
#     follow-up: {{1}} the customer's name, {{2}} the property
#
# Empty strings are never sent — Meta rejects a blank parameter — so a nameless guest
# becomes "there", which reads as a greeting rather than as a bug.
ANONYMOUS = "there"


def occasion_variables(guest_name, label, property_name) -> list[str]:
    return [str(guest_name or "").strip() or ANONYMOUS,
            normalise_label(label) or "your special day",
            str(property_name or "").strip() or "us"]


def follow_up_variables(guest_name, property_name) -> list[str]:
    return [str(guest_name or "").strip() or ANONYMOUS,
            str(property_name or "").strip() or "us"]
