"""The tenant record: one hotel, and the state that decides what it may do.

A property is not settings. It is the thing every other record belongs to, so its
`status` is read on every request by `services.access.can_access` — which is why the
three states are imported from there rather than spelled out again here. The module that
enforces a value owns its vocabulary; a second copy of the strings is a second thing to
keep in step.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

from services.access import (
    DEFAULT_PROPERTY_TYPE, LIVE, PENDING, PROPERTY_TYPES, SUSPENDED)
from services.pricing import DEFAULT_MEAL_PLANS_ENABLED
from services.registration import (
    FSSAI_SHAPE, GSTIN_SHAPE, validate_fssai, validate_gstin,
)
from services.subscription import BILLING_PERIODS, PAYMENT_METHODS
from services.tax import DEFAULT_GST_INCLUSIVE, DEFAULT_OUTLET_GST_RATE

# Exactly the three the access rule knows. Written as a Literal so an unknown status is
# refused where the record is built, not discovered later by `_property_usable` treating
# it as "not live" and switching a working hotel off.
PropertyStatus = Literal[PENDING, LIVE, SUSPENDED]

# What kind of business this is: a hotel, an outlet with no rooms, or both. A Literal for
# the same reason `PropertyStatus` is one — and because signup takes this straight off a
# request body, where an unknown value has to come back as a 422 naming the field rather
# than being stored and read later as a property with no domains at all.
#
# A Literal cannot be built from a runtime tuple, so the vocabulary is spelled out once
# more here and pinned to the central one below. This is the same arrangement, and the
# same guard, that routers/staff.py uses for its domain Literal.
PropertyType = Literal["hotel", "outlet", "both"]

if set(get_args(PropertyType)) != set(PROPERTY_TYPES):
    raise RuntimeError(
        f"models.property.PropertyType {get_args(PropertyType)} has drifted from "
        f"services.access.PROPERTY_TYPES {PROPERTY_TYPES} — update the Literal above")

# What was agreed, and how the money arrives. Literals for the third time in this file
# and for the reason given above each of the others: these come off a request body, and
# an unknown period stored is a period `advance_paid_until` cannot advance by — a payment
# that would be taken and then refused, or worse, taken and silently mis-dated.
BillingPeriod = Literal["monthly", "quarterly", "yearly"]
PaymentMethod = Literal["bank_transfer", "upi", "cash", "cheque"]

for _literal, _central, _name in (
    (BillingPeriod, BILLING_PERIODS, "BILLING_PERIODS"),
    (PaymentMethod, PAYMENT_METHODS, "PAYMENT_METHODS"),
):
    if set(get_args(_literal)) != set(_central):
        raise RuntimeError(
            f"models.property {get_args(_literal)} has drifted from "
            f"services.subscription.{_name} {_central} — update the Literal above")


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PropertyFields(BaseModel):
    """What a hotel's admin may edit about their own property.

    Unvalidated on purpose — `PropertyIn` below adds the format checks. The two exist
    separately because a Pydantic failure on a request body is a 422 whose shape the
    hotel's form cannot rely on, and the design asks for a **400 naming the field**. The
    router takes these fields, then runs the same two checks itself so it can say which
    identifier is wrong and what shape was expected.
    """
    name: str
    legal_name: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    phone: Optional[str] = None
    # A plain string, not EmailStr: the record is saved long before every field is
    # filled, and blank must remain saveable.
    email: Optional[str] = None
    gstin: Optional[str] = None
    fssai_licence: Optional[str] = None
    # The Indian norm, and the two values every folio's night count is worked out
    # against. Defaulted rather than required so a hotel that never opens the settings
    # screen still bills correctly.
    check_in_time: str = "14:00"
    check_out_time: str = "11:00"
    # A data URI. Stored on the property so a bill printed from the POS carries the
    # hotel's own header rather than the platform's.
    logo: Optional[str] = None
    # What the outlet charges on a bill, and whether the menu prices already contain it.
    #
    # Here on `PropertyFields` — the body of `PUT /api/property`, which is admin-only —
    # and deliberately not on `Property` beside the subscription block: this is the
    # hotel's own statutory rate, known by the owner who holds the registration, and it
    # is not the platform's to set. A waiter cannot reach it because the route they would
    # reach it through names "admin".
    #
    # 5% is restaurant service without input tax credit. Defaulted rather than required
    # so a hotel that never opens the settings screen still bills a lawful figure —
    # which is more than the 10% these two replace ever did. See services/tax.py.
    outlet_gst_rate: float = DEFAULT_OUTLET_GST_RATE
    gst_inclusive: bool = DEFAULT_GST_INCLUSIVE

    # Whether a room is sold split three ways — EP, CP, MAP — or at one all-inclusive
    # rate with anything extra billed to the folio as it is consumed.
    #
    # Here on `PropertyFields`, beside the two GST settings above and for the same
    # reason: this is how the owner has decided to sell their own rooms, it is edited
    # from the property settings screen, and the route it arrives on names "admin". It
    # is emphatically not a platform decision — one deployment serves several hotels and
    # a resort selling breakfast-inclusive packages needs plans as much as a ten-room
    # guest house does not.
    #
    # **`None` here means "not mentioned", not "off".** Alone among these fields, this one
    # is optional on the *body* and defaulted only on the stored record below.
    #
    # `PUT /api/property` replaces the editable half wholesale, which is fine for a field
    # every client has always sent and quietly catastrophic for one added afterwards: a
    # settings form built before this existed — or any script that reads the record,
    # changes the address and puts it back — would omit the key, Pydantic would fill in
    # `False`, and a hotel that sells breakfast-inclusive packages would find its meal
    # plans switched off by somebody correcting a postcode. `outlet_gst_rate` has exactly
    # this shape of hazard today and gets away with it because nothing reads it back;
    # this one decides what every new booking is quoted.
    #
    # So the router drops it when it arrives as `None` and leaves whatever is stored.
    # Saying "off" still works and still means off — it just has to be said.
    meal_plans_enabled: Optional[bool] = None


class PropertyIn(PropertyFields):
    """The same fields with the statutory identifiers format-checked.

    Format only, and blank stays legal — see `services/registration.py`, whose functions
    these validators are, rather than a second regex that could disagree with them.
    """

    @field_validator("gstin")
    @classmethod
    def _check_gstin(cls, value):
        if not validate_gstin(value):
            raise ValueError(f"gstin is not a valid GSTIN — expected {GSTIN_SHAPE}")
        return value

    @field_validator("fssai_licence")
    @classmethod
    def _check_fssai(cls, value):
        if not validate_fssai(value):
            raise ValueError(
                f"fssai_licence is not a valid FSSAI licence number — "
                f"expected {FSSAI_SHAPE}")
        return value


class Property(PropertyIn):
    """A stored tenant.

    `id` is a per-tenant UUID and there is no singleton constant anywhere: the moment one
    exists, the second hotel to sign up is the bug, and it is the kind that is found by a
    guest seeing another hotel's booking.
    """
    id: str = Field(default_factory=_uuid)
    # A stored property always carries a real boolean — the `None` on `PropertyFields`
    # means "the request did not mention it", which is a fact about a request body and
    # never a state a record is left in. A hotel signing up today gets the single
    # all-inclusive rate; see services/pricing.py.
    meal_plans_enabled: bool = DEFAULT_MEAL_PLANS_ENABLED
    # Pending is the safe default: a hotel nobody has approved must not be one that can
    # take a booking. The startup migration is the one place that creates a `live`
    # property, because there the hotel has been operating for months already.
    status: PropertyStatus = PENDING
    # Deliberately here and not on `PropertyFields`, so it is nowhere in the body of
    # `PUT /api/property`. An admin who could set their own type would grant their
    # restaurant a hotel it does not have; the reverse is worse — narrowing to `outlet`
    # would strand every hotel-domain staff member on a domain the property no longer
    # has, with no route back through the staff screen, which now refuses that domain.
    # Changing what a business is belongs with the operator, not inside the tenant.
    #
    # `both` is the default for the same reason the migration stamps it: a record written
    # before this field existed has been running rooms and outlets all along.
    property_type: PropertyType = DEFAULT_PROPERTY_TYPE
    created_at: str = Field(default_factory=_now)
    # The audit trail of the operator's decisions. Empty until someone decides.
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    suspended_at: Optional[str] = None
    suspension_reason: Optional[str] = None

    # ------------------------------ the subscription ------------------------------
    # What this business agreed to pay, agreed offline and recorded here. All four are
    # optional because a property nobody has priced yet is a normal state, not an error:
    # businesses are approved before a figure is agreed, and every one of them spends
    # some time in this shape.
    #
    # Here and not on `PropertyFields`, exactly like `property_type` above and for the
    # same reason: `PropertyFields` is the body of `PUT /api/property`, so an admin who
    # could send these would set their own price to zero and their own `paid_until` to
    # the next century. Only `/api/platform/*` writes them.
    #
    # There is deliberately no `overdue` field. A stored flag is wrong the moment nobody
    # recomputes it, and wrong in both directions — a business chased for an invoice it
    # settled, or one trading free because a nightly job stopped. It is derived from
    # `paid_until` against the property's local day, in services/subscription.py.
    subscription_amount: Optional[float] = None
    billing_period: Optional[BillingPeriod] = None
    # A plain local calendar date, not a timestamp: the last day this business is paid
    # through. Advanced only by a recorded payment.
    paid_until: Optional[str] = None
    # How they pay — a bank account, a UPI handle, a person to ring. The operator's memo,
    # and not shown to the tenant; see routers/property.py, which builds the tenant's view
    # explicitly rather than handing the record over whole.
    payment_note: Optional[str] = None


class SubscriptionPayment(BaseModel):
    """One line of the platform's own money ledger: what arrived, when, and for what.

    Append-only, following the folio ledger in services/folio.py — nothing edits a line
    and nothing deletes one. A correction is a new entry, and both stay. That is not
    fastidiousness: this is money changing hands outside any gateway, reconciled by hand
    against a bank statement, and a record that can be rewritten is a record that cannot
    settle an argument about whether ₹12,000 was ever received.

    `property_id` is carried on the row rather than the row being reached through a
    scoped handle. The collection stands outside tenancy for the same reason `properties`
    does — only the operator, who belongs to no property, ever reads or writes it — and
    scoped_db refuses it by name so no router can believe otherwise.
    """
    id: str = Field(default_factory=_uuid)
    property_id: str
    amount: float
    # The day the money actually arrived at the bank, which is not always the day it was
    # typed in: the transfer lands on Friday and the operator reconciles on Monday.
    received_on: str
    # The term this payment bought, worked out by services.subscription.period_covered.
    # Written down rather than recomputed later, because the rule that produced it reads
    # the property's state at the moment of payment and that state has since moved on.
    covers_from: str
    covers_to: str
    method: PaymentMethod
    # A UTR, a UPI reference, a cheque number — or, for a correction, why. Free text
    # because that is what a bank statement gives you.
    reference: str = ""
    # The operator's user id and the instant they recorded it. The audit half of the
    # line: `received_on` is when the money moved, this is when somebody said so.
    recorded_by: str
    recorded_at: str = Field(default_factory=_now)
