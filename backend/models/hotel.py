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

    # "Do not message this person." Unsolicited commercial messaging is regulated in
    # India and it is the property carrying that risk, so this is a field on the person
    # rather than a preference buried in a settings screen.
    #
    # **`None` here means "not mentioned", not "consenting."** Alone among these fields it
    # is optional on the *body* and defaulted only on the stored record below, exactly as
    # `meal_plans_enabled` is on models/property.py and for a sharper version of the same
    # reason: `PUT /api/guests/{id}` replaces the record wholesale, so a form written
    # before this field existed — or any script that reads a guest, corrects a spelling
    # and puts it back — would omit the key, Pydantic would fill in `False`, and somebody
    # who had asked not to be messaged would be silently re-consented by a typo fix.
    # The router drops it when it arrives as `None` and leaves whatever is stored. Saying
    # "may be messaged" still works and still means it — it just has to be said.
    no_messages: Optional[bool] = None


class Guest(GuestIn):
    id: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now)
    # A stored guest always carries a real boolean — the `None` above is a fact about a
    # request body and never a state a record is left in. A guest nobody has asked is
    # messageable: every record written before this field existed was written by a
    # property that had never asked, and reading absence as an opt-out would switch the
    # feature off for the whole existing list.
    no_messages: bool = False


# ---------------------------- occasions ---------------------------
class OccasionIn(BaseModel):
    """A date in a customer's year that the property would like to mark.

    **`label` is free text, not an enum.** A birthday and a wedding anniversary are the
    two everybody thinks of, and then a hotel says "Ananya's first birthday" or "the
    anniversary of the night they got engaged here". A Literal would need a deploy every
    time a property had an idea, and the label is only ever shown back to the guest —
    nothing branches on it except the lookup of which approved template carries it, which
    is a dictionary the property fills in itself.

    `date` is a full YYYY-MM-DD because that is what a date input gives and because a
    hotel likes to know a regular is turning sixty. Only the month and day are ever
    matched — see services/messaging.py::month_day — so the year can be wrong, or a
    guess, without putting the greeting on the wrong day.
    """
    label: str
    date: str


class Occasion(OccasionIn):
    id: str = Field(default_factory=_uuid)
    # Whose occasion it is. Occasions live in their own collection rather than as a list
    # on the guest, because the one query this feature is built around — "everybody whose
    # occasion is today" — has to run across the whole guest list, and neither the JSON
    # mock nor Firestore can match inside an array. A denormalised `month_day` makes it
    # one indexed equality filter instead of reading every guest a property has.
    guest_id: str
    # The recurring part of `date`, stored rather than computed at query time for the
    # reason above.
    month_day: str
    created_at: str = Field(default_factory=_now)
    created_by: Optional[str] = None


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
    # Optional at the model, required at the router when the property sells meal plans.
    #
    # The check moved rather than being dropped. A property with `meal_plans_enabled`
    # off has no plan to name — its rate is all-inclusive — and a required field would
    # force the desk to pick a fiction. A property with plans on is refused a booking
    # without one exactly as before, by `create_booking`, which is the only place that
    # knows which kind of property this is. Refusing it here would refuse both.
    meal_plan_id: Optional[str] = None
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


class ExtendStayIn(BaseModel):
    """"The guest would like two more nights."

    One field, and deliberately only one. An extension moves check-**out** and nothing
    else: the guest is already in the room, or is arriving on a date they have been
    told, so `check_in` has nothing to move to. Moving a future booking's arrival is an
    ordinary edit and `BookingUpdateIn` above already does it — this payload exists so
    that the extension cannot accidentally be one.
    """
    check_out: str


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
