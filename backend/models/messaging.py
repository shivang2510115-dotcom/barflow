"""What a property has configured about messaging, and the ledger of what it sent.

Two records with opposite lifetimes. The settings are one row per property, rewritten
whenever the owner changes their mind. The log is append-only, following the reasoning in
services/folio.py and models/property.py::SubscriptionPayment: nothing edits a line and
nothing deletes one, because this is the record that has to settle the argument about
whether a birthday message went out — and a record that can be rewritten cannot settle
anything.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from services.messaging import (
    DEFAULT_FOLLOW_UP_DAYS, DEFAULT_TEMPLATE_LANGUAGE, FAILED, REFUSED, SENT)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MessagingSettingsIn(BaseModel):
    """The half of messaging a property's admin decides.

    Every field is a template *name* or a window, and none of them is message wording.
    That is the whole design: Meta approves templates, this stores which approved ones
    this property owns, and the words live in Meta's dashboard where they were reviewed.

    Defaults are the state a property that has never opened this screen is in — nothing
    configured, so nothing sendable, and the follow-up switched off. A default template
    name would be a name that exists in nobody's WhatsApp Business account: the send would
    fail at Meta rather than at the one place somebody could fix it.
    """
    # Per occasion label, lowercased — {"birthday": "birthday_wish_v3"}. A hotel with one
    # all-purpose template never opens this and fills in the default below instead.
    occasion_templates: Dict[str, str] = {}
    default_occasion_template: str = ""
    follow_up_template: str = ""
    # Meta files a template under a name *and* a language, and wants both at send time.
    template_language: str = DEFAULT_TEMPLATE_LANGUAGE

    # Off, deliberately, and not merely defaulted to a large number. A property that has
    # not thought about this must not start messaging its customers because the software
    # shipped with an opinion about how often a guest should hear from a restaurant.
    follow_up_enabled: bool = False
    follow_up_days: int = DEFAULT_FOLLOW_UP_DAYS


class MessagingSettings(MessagingSettingsIn):
    """The stored row. One per property, found by its fixed id.

    `id` is a constant rather than a uuid because there is exactly one of these per
    property and it is reached through a property-scoped handle — the scope is what makes
    a fixed id safe, and it is what makes "read the settings" one lookup rather than a
    find-first-and-hope.
    """
    id: str = "messaging"
    updated_at: str = Field(default_factory=_now)
    updated_by: Optional[str] = None


class MessageLogEntry(BaseModel):
    """One attempt to message one customer, and exactly what came back.

    Append-only. There is no update path for this collection anywhere in the application
    and tests/test_customer_messaging.py asserts there is not, because the two questions
    this exists to answer — "did the birthday message go out" and "why did it not" — are
    both worthless against a row somebody could have tidied up afterwards.

    Three statuses, and the middle one earns its place:

    * `sent` — Meta accepted it and handed back a message id that can be found in their
      dashboard;
    * `failed` — it was handed to Meta, or would have been, and did not go: the real error
      text, translated where `routers/reports.py` recognises the code and passed through
      verbatim where it does not;
    * `refused` — this property's own rules stopped it before Meta was involved at all.
      Almost always consent. Kept distinct from `failed` because "we chose not to" and
      "it broke" are different facts, and the first one is the one a regulator asks about.

    There is no `message` field and there never was one. The wording is Meta's, held
    against the template name; storing a rendered sentence here would be storing a guess
    at what was actually delivered.
    """
    id: str = Field(default_factory=_uuid)
    kind: Literal["occasion", "follow_up"]
    status: Literal[SENT, FAILED, REFUSED]

    guest_id: str
    guest_name: str = ""
    # E.164, as it was handed to Meta — or "" when the refusal was that there was no
    # usable number to hand over.
    to: str = ""

    # What was sent, in the only terms the API accepts one: a name Meta approved, the
    # language it was filed under, and the positional parameters.
    template: str = ""
    language: str = DEFAULT_TEMPLATE_LANGUAGE
    variables: List[str] = []

    # What this message was about, which is also what makes it unrepeatable. The occasion
    # row for a greeting; the date of the visit being followed up otherwise.
    subject: str = ""
    subject_day: str = ""
    occasion_label: str = ""

    # What the provider actually answered. `message_id` on success, the real error on
    # failure — never both, and never neither.
    message_id: Optional[str] = None
    error: str = ""
    provider_status: Optional[int] = None
    provider_error_code: Optional[int] = None

    sent_by: Optional[str] = None
    sent_at: str = Field(default_factory=_now)
