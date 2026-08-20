"""The tenant record: one hotel, and the state that decides what it may do.

A property is not settings. It is the thing every other record belongs to, so its
`status` is read on every request by `services.access.can_access` — which is why the
three states are imported from there rather than spelled out again here. The module that
enforces a value owns its vocabulary; a second copy of the strings is a second thing to
keep in step.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from services.access import LIVE, PENDING, SUSPENDED
from services.registration import (
    FSSAI_SHAPE, GSTIN_SHAPE, validate_fssai, validate_gstin,
)

# Exactly the three the access rule knows. Written as a Literal so an unknown status is
# refused where the record is built, not discovered later by `_property_usable` treating
# it as "not live" and switching a working hotel off.
PropertyStatus = Literal[PENDING, LIVE, SUSPENDED]


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
    # Pending is the safe default: a hotel nobody has approved must not be one that can
    # take a booking. The startup migration is the one place that creates a `live`
    # property, because there the hotel has been operating for months already.
    status: PropertyStatus = PENDING
    created_at: str = Field(default_factory=_now)
    # The audit trail of the operator's decisions. Empty until someone decides.
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    suspended_at: Optional[str] = None
    suspension_reason: Optional[str] = None
