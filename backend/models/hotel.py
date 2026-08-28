"""Pydantic models for the hotel domain.

All dates are YYYY-MM-DD strings, never datetimes — a check-in is a calendar date, and
storing it as an instant reintroduces timezone drift.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

BookingStatus = Literal[
    "tentative", "confirmed", "checked_in", "checked_out", "cancelled", "no_show"
]


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------- guests -----------------------------
class GuestIn(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    nationality: Optional[str] = None
    id_proof_type: Optional[str] = None
    id_proof_number: Optional[str] = None
    notes: Optional[str] = None


class Guest(GuestIn):
    id: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now)


# --------------------------- room types ---------------------------
class RoomTypeIn(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
    block: Optional[str] = None
    base_occupancy: int = 2
    max_occupancy: int = 3
    max_extra_beds: int = 1
    amenities: List[str] = []
    images: List[str] = []
    active: bool = True


class RoomType(RoomTypeIn):
    id: str = Field(default_factory=_uuid)


# ------------------------------ rooms -----------------------------
class OutOfOrderIn(BaseModel):
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    reason: Optional[str] = None

    model_config = {"populate_by_name": True}


class RoomIn(BaseModel):
    number: str
    room_type_id: str
    floor: Optional[str] = None
    block: Optional[str] = None
    active: bool = True


class Room(RoomIn):
    id: str = Field(default_factory=_uuid)
    out_of_order: List[dict] = []


# --------------------------- meal plans ---------------------------
class MealPlanIn(BaseModel):
    code: str
    name: str
    price_per_adult_per_night: float = 0.0
    price_per_child_per_night: float = 0.0
    active: bool = True


class MealPlan(MealPlanIn):
    id: str = Field(default_factory=_uuid)


# -------------------------- rate periods --------------------------
class RatePeriodIn(BaseModel):
    name: str
    start_date: str
    end_date: str
    priority: int = 0
    active: bool = True


class RatePeriod(RatePeriodIn):
    id: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now)


# ------------------------------ rates -----------------------------
class RateIn(BaseModel):
    room_type_id: str
    period_id: Optional[str] = None
    base_rate: float
    extra_adult_rate: float = 0.0
    extra_child_rate: float = 0.0


class Rate(RateIn):
    id: str = Field(default_factory=_uuid)


# ---------------------------- tax slabs ---------------------------
class TaxSlab(BaseModel):
    id: str = Field(default_factory=_uuid)
    min_tariff: float
    max_tariff: Optional[float] = None
    rate_percent: float
    active: bool = True


# ---------------------------- bookings ----------------------------
class BookingIn(BaseModel):
    guest_id: str
    room_type_id: str
    meal_plan_id: str
    check_in: str
    check_out: str
    adults: int = 2
    children: int = 0
    extra_beds: int = 0
    status: Literal["tentative", "confirmed"] = "confirmed"
    hold_expires_at: Optional[str] = None
    group_ref: Optional[str] = None
    source: Literal["front_desk", "phone", "walk_in"] = "front_desk"
    notes: Optional[str] = None


class BookingUpdateIn(BaseModel):
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    adults: Optional[int] = None
    children: Optional[int] = None
    extra_beds: Optional[int] = None
    meal_plan_id: Optional[str] = None
    notes: Optional[str] = None


class CancelIn(BaseModel):
    reason: Optional[str] = None


class RoomAssignmentIn(BaseModel):
    """Which physical room a booking holds. `None` clears it.

    One payload for assign, reassign and clear, because to the desk they are one
    action — "this booking's room is now 204", "…is now 205", "…is nothing yet" — and
    splitting them into three endpoints would mean three places for the clash check to
    be forgotten from.
    """
    room_id: Optional[str] = None


class Booking(BookingIn):
    id: str = Field(default_factory=_uuid)
    reference: str
    assigned_room_id: Optional[str] = None
    checked_in_at: Optional[str] = None
    checked_out_at: Optional[str] = None
    quote: dict = {}
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    cancelled_at: Optional[str] = None
    cancellation_reason: Optional[str] = None
