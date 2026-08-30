"""Whose number a message goes out from.

**The restaurant's own, always. There is no platform fallback.** That is the owner's
decision, stated plainly, and it is enforced by the shape of this module rather than by
a rule somebody has to remember: `credentials_for` takes a property record and nothing
else, so there is nowhere for an environment variable to enter. A test reads the source
of that function and fails if `environ` appears in it.

The reasoning is worth keeping: a fallback would put the platform's name on a
restaurant's relationship with its own customers, and would quietly train a hotel never
to set up its own account. Refusing is the honest answer, and the screens say exactly
what is missing.

The token is stored encrypted — see services/crypto.py — and is never returned by any
endpoint. The phone number id is not secret and may be shown, which matters because it
is the field people get wrong: Meta's "Phone number ID" is a numeric identifier, not the
phone number itself, and a property that has pasted the phone number will fail every
send with an error that does not say so.
"""


def _clean(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def credentials_for(property_record: dict | None) -> tuple[str | None, str | None]:
    """This property's (phone_number_id, token), or (None, None).

    Takes the property and nothing else. There is deliberately no environment lookup
    here and there must never be one — see the module docstring.
    """
    p = property_record or {}
    phone_id = _clean(p.get("whatsapp_phone_id"))
    token = _clean(p.get("whatsapp_token"))
    if not phone_id or not token:
        # Half-configured is not configured. A phone id with no token is the state an
        # onboarding call leaves behind when it is interrupted, and it must not read as
        # ready — the send would fail at Meta with a less useful message.
        return None, None
    return phone_id, token


def can_send(property_record: dict | None) -> bool:
    """Whether this property can send anything at all."""
    return credentials_for(property_record) != (None, None)


def missing_for(property_record: dict | None,
                need_owner_phone: bool = False) -> list[str]:
    """What this property still needs, in words somebody can act on.

    `need_owner_phone` is False for a customer message and True for the nightly brief:
    the brief goes to the owner, so it needs a number to go to, and a customer follow-up
    already has the customer's. Reporting a missing owner phone as the reason a customer
    could not be messaged sends whoever reads it to the wrong field entirely.
    """
    p = property_record or {}
    missing = []
    if not _clean(p.get("whatsapp_phone_id")):
        missing.append(
            "WhatsApp phone number ID — the numeric id from Meta, not the phone number")
    if not _clean(p.get("whatsapp_token")):
        missing.append("WhatsApp access token from Meta")
    if need_owner_phone and not _clean(p.get("owner_phone")):
        missing.append("Owner's phone number, with country code, digits only")
    return missing
