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

# Spelled out rather than built from `services.housekeeping.STATUSES`, because a Literal
# needs its members at type-check time and a tuple unpacked into one is unreadable to
# every tool that reads this file. tests/test_housekeeping_api.py asserts the two lists
# are the same set, so they cannot drift in silence.
HousekeepingStatus = Literal["clean", "dirty", "inspected", "out_of_order"]


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
    # What comes with this kind of room. A Suite that includes breakfast and two spa
    # treatments says so here, once, and every rate for it inherits that.
    #
    # A rate may still name its own package, and it wins — that is how a hotel sells the
    # same Deluxe as Room Only and as Bed & Breakfast at two prices. Most properties never
    # need it, which is why the room type is the place the owner is asked.
    package_id: Optional[str] = None
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

    # ---- housekeeping ----
    # Four fields the owner never types. They live on `Room` and not on `RoomIn` for the
    # same reason `out_of_order` does, and it is load-bearing: `PUT /api/rooms/{id}` sets
    # `payload.model_dump()` wholesale, so a field on the input model would be reset to
    # its default every time somebody corrected a room's floor — an attendant's morning
    # of work undone by an edit on the Rooms screen.
    #
    # **`housekeeping_status = "out_of_order"` is not the `out_of_order` list above.**
    # That list is date ranges and controls what the booking engine will *sell*; this
    # says the room is not usable *right now* and stops the desk assigning it. Two axes,
    # two owners, deliberately never merged — see services/housekeeping.py.
    housekeeping_status: HousekeepingStatus = "clean"
    # Free text, and required by the API when the status is `out_of_order`. It describes
    # the state the room is in *now*; the history of what it used to say is in
    # `housekeeping_events`, which is never updated.
    housekeeping_note: Optional[str] = None
    housekeeping_updated_at: Optional[str] = None
    housekeeping_updated_by: Optional[str] = None


# --------------------------- housekeeping --------------------------
class HousekeepingStatusIn(BaseModel):
    """What an attendant taps on a room card.

    An unknown status is a 422 from here rather than a hand-written 400, which is what
    the design asks for and is also the honest code: the request is malformed, not
    refused. `services.housekeeping.STATUSES` is the same list, and a test asserts they
    have not drifted.
    """
    status: HousekeepingStatus
    note: Optional[str] = None


class HousekeepingEvent(BaseModel):
    """One line of the append-only log. Never updated, never deleted.

    This is what individual logins are for. When a guest says their room was filthy, the
    question is who marked it clean and when, and a status field on the room can only
    ever answer the last half of it.
    """
    id: str = Field(default_factory=_uuid)
    room_id: str
    from_status: str
    to_status: str
    note: Optional[str] = None
    # A user id. `None` is not used: even the automatic transition at check-out records
    # the person who checked the guest out, because somebody did press that button.
    changed_by: Optional[str] = None
    changed_at: str = Field(default_factory=_now)


# Same reasoning as HousekeepingStatus above: one list of strings, asserted equal to
# `services.housekeeping.PRIORITIES` by a test rather than trusted.
HousekeepingPriority = Literal["low", "normal", "high"]


class HousekeepingJobIn(BaseModel):
    """A request raised by a member of staff: this room needs attention.

    Separate from the room's status and with a life of its own — raised, picked up, done.
    "This room is dirty" and "the guest in 204 has asked for towels" are different facts
    and only one of them is answered by the room being cleaned.

    An empty reason is allowed. "Something is wrong in 204" is still worth knowing, and
    the alternative is a required field that gets filled in with a full stop.
    """
    room_id: str
    priority: HousekeepingPriority = "normal"
    reason: str = ""


class HousekeepingJob(BaseModel):
    id: str = Field(default_factory=_uuid)
    room_id: str
    # Denormalised, on purpose, and the one thing in this model that is not simply the
    # design's field list. The alert endpoint is polled every fifteen seconds by every
    # signed-in hotel user, and naming the room from the job means that poll is one query
    # instead of a query plus the whole rooms collection. It is what the room was called
    # when the request was raised, which is also the honest thing for a record of what
    # somebody asked for — the same reasoning that stores a price on an order line.
    room_number: Optional[str] = None
    priority: HousekeepingPriority = "normal"
    reason: str = ""
    # A staff id, or **None when a guest raised it from the in-room QR**. The two are told
    # apart by `source` rather than by the absence, because "nobody is recorded" and "a
    # guest, who has no account" are different facts and only one of them is a gap.
    raised_by: Optional[str] = None
    source: Literal["staff", "guest"] = "staff"
    status: Literal["open", "in_progress", "done", "cancelled"] = "open"
    created_at: str = Field(default_factory=_now)
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    completed_at: Optional[str] = None
    completed_by: Optional[str] = None
    # Cancelling has its own three fields rather than reusing the completed ones. Folding
    # them together would leave a record saying a job was completed when it was called
    # off, which is exactly the fact cancelling-as-a-status exists to keep.
    cancelled_at: Optional[str] = None
    cancelled_by: Optional[str] = None
    cancel_reason: Optional[str] = None
    # Every hand this job passed through, appended and never rewritten. Two staff
    # acknowledging at once is last-write-wins on the fields above and both lines here,
    # so neither of them sees an error and neither of them is erased. A guest's second
    # press lands here too, which is how a merged request stays legible afterwards.
    history: List[dict] = []


class HousekeepingCancelIn(BaseModel):
    reason: Optional[str] = None


class GuestRequestIn(BaseModel):
    """What a guest types into the card in their room. One box.

    No priority: the guest does not triage their own request, and a field the hotel
    cannot verify would only ever be set to `high`. No room either — that comes from the
    QR code in the URL, so nothing a guest can type names another room or another hotel.
    """
    reason: str = ""


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
    # What this rate includes beyond the room, if anything. Optional and defaulted to
    # None, because that is what every rate that existed before packages did has: a
    # rate with no package sells a room and nothing else, which is what they have all
    # been doing. This one field is the entire mechanism by which an elite room differs
    # from a normal one — see services/packages.py.
    package_id: Optional[str] = None


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
    # What this stay was sold with, copied from the rate that priced it at the moment
    # of sale. NOT a reference to the rate: a rate is editable, and a price change next
    # month must not retroactively change what a guest was entitled to. Same reasoning
    # as the bill being a snapshot — what was bought is fixed when it is bought.
    package_id: Optional[str] = None
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
