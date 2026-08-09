# Front Desk & Guest Folio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Check a guest in, let them charge a bar or restaurant bill to their room, and settle one folio at departure.

**Architecture:** A `folios` collection with an append-only `folio_entries` ledger. `settle_order` gains a `room` payment method that closes the order exactly as today and additionally writes one folio debit — so orders keep their current shape and every existing report works untouched. Balance arithmetic and night-posting logic live in `backend/services/folio.py` as pure functions, following the pattern already established by `pricing.py` and `availability.py`.

**Tech Stack:** FastAPI, Motor/PyMongo with a JSON-file mock fallback, Pydantic v2, pytest + pytest-xdist, React 19 + CRA/craco, Tailwind, axios, react-router-dom v7, `sonner`.

**Spec:** `docs/superpowers/specs/2026-08-09-front-desk-folio-design.md`

---

## Global Constraints

- **The ledger is append-only.** Entries are never updated or deleted. Corrections are new reversing entries. Any code that mutates an existing entry is a defect.
- **`balance = sum(debits) − sum(credits)`.** The cached `folios.balance` is for list views only and must be **recomputed from entries before every decision** — every 409, every check-out, every void.
- **Direction per kind is fixed:** `room_night`, `outlet`, `misc_charge`, `refund` → **debit**; `payment`, `discount` → **credit**; `void` → the opposite of the entry it reverses. A refund is a debit because handing money back increases what the guest owes again.
- **A room-settled order is an ordinary settled order.** It counts in outlet revenue on the day served, exactly like a cash bill. No report gets a special case.
- **A folio payment is never revenue.** It is a credit settling a receivable. Counting it would book the same money twice.
- **Voiding an outlet entry reverses both sides:** credit the folio **and** set the order to `voided`.
- **Dates are `YYYY-MM-DD` strings, compared as strings.** Ranges are half-open `[check_in, check_out)` — the departure date is never a billable night.
- **Room-night amounts come from `booking["quote"]["nights"]`**, never from a fresh rate lookup. The folio must agree with the price the guest was quoted.
- **Only in-house guests are chargeable.** The POS room search returns checked-in bookings only.
- **Do NOT modify `backend/pytest.ini`.** Its `addopts` is pinned to `-n 2 --dist loadscope` with an explicit warning comment.
- Python: 4-space indent, type hints on signatures, `HTTPException` for errors. Match surrounding style.

### Test baselines (measured 2026-08-09, must be preserved)

| Suite | Command | Expected |
|---|---|---|
| Pure units | `python3 -m pytest tests/test_pricing.py tests/test_availability.py -q` | `26 passed` |
| Hotel API | `REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q` | `35 passed` |
| Regression | `REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q` | **`1 failed, 9 passed, 1 skipped`** |

The regression suite is **intentionally not green**. The failure is `TestStripeCheckout::test_create_checkout_session_returns_stripe_url` — environmental, because no real `STRIPE_API_KEY` is configured so the vendored stub returns a local URL. It predates all hotel work. **Do not fix it, skip it, or touch payments tests.** Matching that exact result is the success criterion.

Start the server for HTTP suites with:

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/
```

`backend/db.json` is gitignored runtime state — **never commit it**.

---

## File Structure

**Backend — new:**

| File | Responsibility |
|---|---|
| `backend/services/folio.py` | pure: direction per kind, balance, nights due, unposted nights |
| `backend/models/folio.py` | Pydantic models for folios, entries, and the request bodies |
| `backend/routers/folios.py` | folio read (with lazy night posting), charges, payments, void |
| `backend/routers/frontdesk.py` | check-in, check-out, `/front-desk`, `/in-house` |

**Backend — modified:**

| File | Change |
|---|---|
| `backend/models/hotel.py` | `Booking` gains `checked_in_at`, `checked_out_at` |
| `backend/routers/orders.py` | `SettleIn` accepts `payment_method="room"` + `folio_id`; order status gains `voided` |
| `backend/server.py` | register the two new routers; add folio indexes |

**Backend tests:**

| File | Responsibility |
|---|---|
| `backend/tests/test_folio.py` | pure unit tests, no server |
| `backend/tests/hotel_api_test.py` | append integration tests |

**Frontend:**

| File | Responsibility |
|---|---|
| `frontend/src/pages/hotel/FrontDesk.jsx` | arrivals, departures, in-house |
| `frontend/src/pages/hotel/Folio.jsx` | ledger, add charge, take payment, void |
| `frontend/src/pages/POS.jsx` | modify: "Room" settle option with in-house search |
| `frontend/src/pages/hotel/BookingDetail.jsx` | modify: check-in / check-out actions |
| `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx` | modify: routes and nav |

---

## Task 1: Folio service — pure functions

The money rules, testable with no database. Highest-value test surface in the plan.

**Files:**
- Create: `backend/services/folio.py`
- Test: `backend/tests/test_folio.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ENTRY_DIRECTION: dict[str, str]`
  - `FolioError` (exception)
  - `direction_for(kind: str) -> str`
  - `void_direction(original_direction: str) -> str`
  - `folio_balance(entries: list[dict]) -> float`
  - `nights_due(booking: dict, as_of: str) -> list[str]`
  - `unposted_nights(booking: dict, as_of: str, entries: list[dict]) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_folio.py`:

```python
"""Pure folio tests — no server, no database."""
import pytest
from services.folio import (
    ENTRY_DIRECTION, FolioError, direction_for, void_direction,
    folio_balance, nights_due, unposted_nights,
)


def e(kind, direction, amount, **kw):
    return {"id": kw.get("id", kind), "kind": kind, "direction": direction,
            "amount": amount, **kw}


def booking(status="checked_in", ci="2026-08-10", co="2026-08-14"):
    return {"status": status, "check_in": ci, "check_out": co}


def test_charges_are_debits_payments_are_credits():
    assert direction_for("room_night") == "debit"
    assert direction_for("outlet") == "debit"
    assert direction_for("misc_charge") == "debit"
    assert direction_for("payment") == "credit"
    assert direction_for("discount") == "credit"


def test_refund_is_a_debit_not_a_credit():
    # Handing money back increases what the guest owes again.
    assert direction_for("refund") == "debit"


def test_unknown_kind_raises():
    with pytest.raises(FolioError):
        direction_for("gratuity")


def test_void_reverses_direction():
    assert void_direction("debit") == "credit"
    assert void_direction("credit") == "debit"


def test_empty_ledger_is_zero():
    assert folio_balance([]) == 0.0


def test_balance_mixes_debits_and_credits():
    entries = [
        e("room_night", "debit", 5000.0),
        e("room_night", "debit", 5000.0),
        e("outlet", "debit", 1200.0),
        e("payment", "credit", 4000.0),
    ]
    assert folio_balance(entries) == 7200.0


def test_void_pair_cancels_to_zero():
    entries = [
        e("outlet", "debit", 1200.0, id="a"),
        e("void", "credit", 1200.0, ref_entry_id="a"),
    ]
    assert folio_balance(entries) == 0.0


def test_overpayment_gives_negative_balance():
    entries = [e("room_night", "debit", 5000.0), e("payment", "credit", 6000.0)]
    assert folio_balance(entries) == -1000.0


def test_refund_increases_balance():
    entries = [
        e("room_night", "debit", 5000.0),
        e("payment", "credit", 5000.0),
        e("refund", "debit", 2000.0),
    ]
    assert folio_balance(entries) == 2000.0


def test_entry_without_direction_raises():
    with pytest.raises(FolioError):
        folio_balance([{"id": "x", "kind": "outlet", "amount": 100.0}])


def test_no_nights_due_before_check_in():
    assert nights_due(booking(status="confirmed"), "2026-08-12") == []


def test_nights_due_mid_stay_excludes_today_onward():
    # Arrived the 10th, it is now the 12th: nights of the 10th and 11th have been slept.
    assert nights_due(booking(), "2026-08-12") == ["2026-08-10", "2026-08-11"]


def test_nights_due_on_departure_day_covers_whole_stay():
    assert nights_due(booking(), "2026-08-14") == [
        "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]


def test_nights_due_never_exceeds_check_out():
    # as_of well past departure must not invent extra nights
    assert nights_due(booking(), "2026-09-01") == [
        "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]


def test_early_departure_stops_posting():
    b = booking(status="checked_out", co="2026-08-12")
    assert nights_due(b, "2026-08-20") == ["2026-08-10", "2026-08-11"]


def test_unposted_nights_skips_already_posted():
    entries = [e("room_night", "debit", 5000.0, charge_date="2026-08-10")]
    assert unposted_nights(booking(), "2026-08-12", entries) == ["2026-08-11"]


def test_unposted_nights_is_empty_when_all_posted():
    entries = [
        e("room_night", "debit", 5000.0, charge_date="2026-08-10"),
        e("room_night", "debit", 5000.0, charge_date="2026-08-11"),
    ]
    assert unposted_nights(booking(), "2026-08-12", entries) == []
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && python3 -m pytest tests/test_folio.py -q`
Expected: collection error — `No module named 'services.folio'`

- [ ] **Step 3: Implement `backend/services/folio.py`**

```python
"""Guest folio arithmetic.

Pure functions over supplied entries and bookings — no database access, so the money
rules are testable in isolation. The ledger is append-only: nothing here mutates.
"""
from datetime import date, timedelta

# Which way each kind moves the balance. A refund hands money back to the guest, so it
# increases what they owe again — it is a debit, not a credit.
ENTRY_DIRECTION = {
    "room_night": "debit",
    "outlet": "debit",
    "misc_charge": "debit",
    "payment": "credit",
    "discount": "credit",
    "refund": "debit",
}


class FolioError(Exception):
    """Raised when an operation would corrupt the ledger."""


def direction_for(kind: str) -> str:
    """Direction a new entry of this kind takes. `void` is not valid here — a void's
    direction is the opposite of the entry it reverses, via `void_direction`."""
    try:
        return ENTRY_DIRECTION[kind]
    except KeyError:
        raise FolioError(f"unknown entry kind: {kind}")


def void_direction(original_direction: str) -> str:
    """Voiding a charge credits; voiding a payment debits."""
    if original_direction == "debit":
        return "credit"
    if original_direction == "credit":
        return "debit"
    raise FolioError(f"unknown direction: {original_direction}")


def folio_balance(entries: list[dict]) -> float:
    """What the guest owes. Positive means outstanding, negative means in credit."""
    total = 0.0
    for e in entries:
        amount = float(e.get("amount") or 0)
        if e.get("direction") == "debit":
            total += amount
        elif e.get("direction") == "credit":
            total -= amount
        else:
            raise FolioError(f"entry {e.get('id')} has no valid direction")
    return round(total, 2)


def nights_due(booking: dict, as_of: str) -> list[str]:
    """Night dates that should have posted by `as_of`.

    Half-open [check_in, check_out): the departure date is never a billable night.
    Returns nothing until the booking is checked in.
    """
    if booking.get("status") not in ("checked_in", "checked_out"):
        return []
    start = date.fromisoformat(booking["check_in"])
    end = date.fromisoformat(booking["check_out"])
    cutoff = date.fromisoformat(as_of)
    last = min(cutoff, end)
    out, cur = [], start
    while cur < last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def unposted_nights(booking: dict, as_of: str, entries: list[dict]) -> list[str]:
    """Nights due that have not already been posted. This is what makes lazy posting
    idempotent in application code; the unique index enforces it at the store."""
    posted = {
        e.get("charge_date") for e in entries
        if e.get("kind") == "room_night" and e.get("charge_date")
    }
    return [d for d in nights_due(booking, as_of) if d not in posted]
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python3 -m pytest tests/test_folio.py -q`
Expected: `17 passed`

- [ ] **Step 5: Confirm the other pure suites still pass**

Run: `cd backend && python3 -m pytest tests/test_pricing.py tests/test_availability.py -q`
Expected: `26 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/folio.py backend/tests/test_folio.py
git commit -m "feat: folio service with ledger arithmetic and night-due logic"
```

---

## Task 2: Folio models, indexes and router registration

**Files:**
- Create: `backend/models/folio.py`
- Modify: `backend/models/hotel.py` (Booking gains two fields), `backend/server.py` (indexes)

**Interfaces:**
- Consumes: nothing
- Produces: `Folio`, `FolioEntry`, `CheckInIn`, `CheckOutIn`, `ChargeIn`, `PaymentIn`, `VoidIn`, `FolioStatus`, `EntryKind`

- [ ] **Step 1: Create `backend/models/folio.py`**

```python
"""Pydantic models for the guest folio.

The ledger is append-only: there is deliberately no model for updating an entry.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

FolioStatus = Literal["open", "settled", "closed_unpaid"]
EntryKind = Literal[
    "room_night", "outlet", "misc_charge", "payment", "refund", "discount", "void"
]


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Folio(BaseModel):
    id: str = Field(default_factory=_uuid)
    booking_id: str
    guest_id: str
    status: FolioStatus = "open"
    # Cached for list views only. Every decision recomputes from entries.
    balance: float = 0.0
    opened_at: str = Field(default_factory=_now)
    closed_at: Optional[str] = None
    closed_reason: Optional[str] = None


class FolioEntry(BaseModel):
    id: str = Field(default_factory=_uuid)
    folio_id: str
    kind: EntryKind
    direction: Literal["debit", "credit"]
    amount: float
    description: str
    posted_at: str = Field(default_factory=_now)
    posted_by: Optional[str] = None
    ref_order_id: Optional[str] = None
    ref_entry_id: Optional[str] = None
    charge_date: Optional[str] = None


class CheckInIn(BaseModel):
    room_id: str
    id_proof_type: str
    id_proof_number: str


class CheckOutIn(BaseModel):
    force: bool = False
    reason: Optional[str] = None


class ChargeIn(BaseModel):
    amount: float
    description: str


class PaymentIn(BaseModel):
    amount: float
    method: Literal["cash", "card", "online"] = "cash"
    kind: Literal["payment", "refund", "discount"] = "payment"
    description: Optional[str] = None


class VoidIn(BaseModel):
    reason: str
```

- [ ] **Step 2: Add the two Booking fields**

In `backend/models/hotel.py`, inside `class Booking(BookingIn):`, after `assigned_room_id`:

```python
    checked_in_at: Optional[str] = None
    checked_out_at: Optional[str] = None
```

- [ ] **Step 3: Add folio indexes**

In `backend/server.py`, inside `seed_data`, alongside the existing `create_index` calls:

```python
    await db.folios.create_index("booking_id", unique=True)
    await db.folio_entries.create_index("folio_id")
    # Makes lazy night-posting idempotent at the store, not just in application code.
    await db.folio_entries.create_index(
        [("folio_id", 1), ("kind", 1), ("charge_date", 1)], unique=True, sparse=True)
```

- [ ] **Step 4: Verify the app still boots and models import**

```bash
cd ~/dev/bar-management-system/backend
MONGO_URL=mock python3 -c "
from models.folio import Folio, FolioEntry, CheckInIn, CheckOutIn, ChargeIn, PaymentIn, VoidIn
from models.hotel import Booking
import server
print('imports ok; Booking has checked_in_at:', 'checked_in_at' in Booking.model_fields)"
```

Expected: `imports ok; Booking has checked_in_at: True`

- [ ] **Step 5: Confirm the regression baseline is unchanged**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q 2>&1 | tail -1
```

Expected: `1 failed, 9 passed, 1 skipped`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/models/ backend/server.py
git commit -m "feat: folio models, booking check-in fields, folio indexes"
```

---

## Task 3: Check-in, front desk, and the folio read path

Check-in creates the folio, so folio *reading* must land in the same task — otherwise
neither half is testable on its own. Charges, payments and check-out follow in Task 4.

**Files:**
- Create: `backend/routers/frontdesk.py`, `backend/routers/folios.py`
- Modify: `backend/server.py` (register both routers)
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `services.folio.{folio_balance, unposted_nights}`, `models.folio.{Folio, FolioEntry, CheckInIn}`
- Produces:
  - `POST /api/bookings/{id}/check-in`, `GET /api/front-desk`, `GET /api/in-house`
  - `GET /api/folios`, `GET /api/folios/{folio_id}`
  - `folios.post_due_nights(folio_id: str) -> int`
  - `folios._entries(folio_id: str) -> list[dict]`
  - `folios._sync_balance(folio_id: str) -> float`
  - `folios._require_open(folio_id: str) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`. Each test builds its own room type, room,
rate, guest and booking, so it passes alone or in any order.

```python
def _stay(admin, ci, co, adults=2):
    """Create a bookable room type with 1 room, a rate, a guest and a confirmed booking."""
    code = f"F{uuid.uuid4().hex[:6].upper()}"
    rt = admin.post(f"{API}/room-types", json={
        "name": f"Folio {code}", "code": code,
        "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1}).json()
    room = admin.post(f"{API}/rooms", json={
        "number": f"F{uuid.uuid4().hex[:5]}", "room_type_id": rt["id"]}).json()
    admin.post(f"{API}/rates", json={
        "room_type_id": rt["id"], "period_id": None, "base_rate": 5000.0,
        "extra_adult_rate": 1000.0, "extra_child_rate": 500.0})
    ep = next(p for p in admin.get(f"{API}/meal-plans").json() if p["code"] == "EP")
    guest = admin.post(f"{API}/guests", json={
        "name": "Folio Guest", "phone": f"96{uuid.uuid4().int % 100000000:08d}"}).json()
    booking = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": rt["id"], "meal_plan_id": ep["id"],
        "check_in": ci, "check_out": co, "adults": adults, "children": 0}).json()
    return {"room_type": rt, "room": room, "guest": guest, "booking": booking}


def _checked_in(admin, ci, co):
    """A stay that has been checked in, with its folio id attached."""
    s = _stay(admin, ci, co)
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "9090-8080-7070"})
    assert r.status_code == 200, r.text
    s["folio_id"] = r.json()["folio"]["id"]
    return s


def test_check_in_assigns_room_and_opens_folio(admin):
    s = _stay(admin, "2029-01-05", "2029-01-08")
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1234-5678-9012"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booking"]["status"] == "checked_in"
    assert body["booking"]["assigned_room_id"] == s["room"]["id"]
    assert body["folio"]["status"] == "open"


def test_check_in_requires_id_proof(admin):
    s = _stay(admin, "2029-02-05", "2029-02-08")
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "", "id_proof_number": ""})
    assert r.status_code == 400, r.text


def test_double_check_in_is_refused(admin):
    s = _checked_in(admin, "2029-03-05", "2029-03-08")
    again = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1111-2222-3333"})
    assert again.status_code == 409, again.text


def test_check_in_refuses_a_room_of_the_wrong_type(admin):
    s = _stay(admin, "2029-04-05", "2029-04-08")
    other = _stay(admin, "2029-04-05", "2029-04-08")
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": other["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "4444-5555-6666"})
    assert r.status_code == 409, r.text


def test_in_house_lists_checked_in_guests_only(admin):
    s = _stay(admin, "2029-05-05", "2029-05-08")
    before = admin.get(f"{API}/in-house").json()
    assert all(x["booking"]["id"] != s["booking"]["id"] for x in before)

    admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "7777-8888-9999"})
    after = admin.get(f"{API}/in-house").json()
    assert any(x["booking"]["id"] == s["booking"]["id"] for x in after)


def test_front_desk_groups_arrivals_departures_and_in_house(admin):
    r = admin.get(f"{API}/front-desk")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("date", "arrivals", "departures", "in_house"):
        assert key in body, body


def test_room_nights_post_lazily_and_only_once(admin):
    # Stay already in the past, so every night is due the moment the folio is read.
    s = _checked_in(admin, "2024-01-05", "2024-01-08")
    first = admin.get(f"{API}/folios/{s['folio_id']}").json()
    nights = [e for e in first["entries"] if e["kind"] == "room_night"]
    assert len(nights) == 3, first
    assert first["balance"] == round(sum(n["amount"] for n in nights), 2)

    second = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert len([e for e in second["entries"] if e["kind"] == "room_night"]) == 3
    assert second["balance"] == first["balance"]


def test_room_night_amounts_come_from_the_booking_quote(admin):
    s = _checked_in(admin, "2024-02-05", "2024-02-08")
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()
    booked = admin.get(f"{API}/bookings/{s['booking']['id']}").json()
    quoted = {n["date"]: round(n["tariff"] + n["gst_amount"], 2)
              for n in booked["quote"]["nights"]}
    posted = [e for e in folio["entries"] if e["kind"] == "room_night"]
    assert posted, folio
    for entry in posted:
        assert entry["amount"] == quoted[entry["charge_date"]]


def test_future_stay_posts_no_nights_yet(admin):
    s = _checked_in(admin, "2031-01-05", "2031-01-08")
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert [e for e in folio["entries"] if e["kind"] == "room_night"] == []
    assert folio["balance"] == 0.0
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "check_in or in_house or front_desk or room_night or future_stay"`
Expected: FAIL — 404, routes do not exist.

- [ ] **Step 3: Create `backend/routers/folios.py` (read path only)**

```python
"""Guest folio: an append-only ledger of charges and payments.

Nothing in this module updates or deletes an entry. Corrections are new reversing
entries, so a folio can always be reconstructed and a disputed bill has an audit trail.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.folio import FolioEntry
from security import require_roles
from services.folio import folio_balance, unposted_nights

router = APIRouter()

DESK = require_roles("admin", "manager", "front_desk")
MANAGER = require_roles("admin", "manager")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _entries(folio_id: str) -> list[dict]:
    rows = await db.folio_entries.find({"folio_id": folio_id}, {"_id": 0}).to_list(5000)
    return sorted(rows, key=lambda e: e.get("posted_at") or "")


async def _sync_balance(folio_id: str) -> float:
    balance = folio_balance(await _entries(folio_id))
    await db.folios.update_one({"id": folio_id}, {"$set": {"balance": balance}})
    return balance


async def _require_open(folio_id: str) -> dict:
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "Folio not found")
    if folio["status"] != "open":
        raise HTTPException(409, f"This folio is {folio['status']} and cannot be changed")
    return folio


async def post_due_nights(folio_id: str) -> int:
    """Post every room night due but not yet posted. Called on every folio read.

    Lazy rather than scheduled: a server that slept cannot silently skip a night, and
    under real MongoDB the unique index on (folio_id, kind, charge_date) also guards
    this, but mock_db's create_index is a no-op, so unposted_nights is the real protection.
    Amounts come from the booking's quote snapshot so the folio agrees with the price
    the guest was actually quoted, even if rates have changed since.
    """
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio or folio["status"] != "open":
        return 0
    booking = await db.bookings.find_one({"id": folio["booking_id"]}, {"_id": 0})
    if not booking:
        return 0

    existing = await _entries(folio_id)
    due = unposted_nights(booking, _today(), existing)
    if not due:
        return 0

    by_date = {n["date"]: n for n in (booking.get("quote") or {}).get("nights", [])}
    posted = 0
    for night in due:
        priced = by_date.get(night)
        if not priced:
            continue
        amount = round(float(priced["tariff"]) + float(priced["gst_amount"]), 2)
        entry = FolioEntry(
            folio_id=folio_id, kind="room_night", direction="debit", amount=amount,
            description=f"Room night {night}", charge_date=night,
            posted_by="system").model_dump()
        await db.folio_entries.insert_one(entry)
        posted += 1

    if posted:
        await _sync_balance(folio_id)
    return posted


@router.get("/folios")
async def list_folios(status: str = "", user: dict = Depends(DESK)):
    query = {"status": status} if status else {}
    folios = await db.folios.find(query, {"_id": 0}).to_list(1000)
    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    bookings = {b["id"]: b for b in await db.bookings.find({}, {"_id": 0}).to_list(5000)}
    for f in folios:
        f["guest"] = guests.get(f["guest_id"])
        f["booking"] = bookings.get(f["booking_id"])
    return folios


@router.get("/folios/{folio_id}")
async def get_folio(folio_id: str, user: dict = Depends(DESK)):
    folio = await db.folios.find_one({"id": folio_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "Folio not found")

    await post_due_nights(folio_id)
    entries = await _entries(folio_id)
    balance = folio_balance(entries)
    await db.folios.update_one({"id": folio_id}, {"$set": {"balance": balance}})

    folio["balance"] = balance
    folio["entries"] = entries
    folio["guest"] = await db.guests.find_one({"id": folio["guest_id"]}, {"_id": 0})
    folio["booking"] = await db.bookings.find_one({"id": folio["booking_id"]}, {"_id": 0})
    return folio
```

- [ ] **Step 4: Create `backend/routers/frontdesk.py` (check-in only; check-out lands in Task 4)**

```python
"""Front desk: arrivals, departures, in-house and check-in."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.folio import CheckInIn, Folio
from security import require_roles

router = APIRouter()

DESK = require_roles("admin", "manager", "front_desk")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@router.get("/front-desk")
async def front_desk(user: dict = Depends(DESK)):
    today = _today()
    arrivals = await db.bookings.find(
        {"check_in": today, "status": {"$in": ["tentative", "confirmed"]}}, {"_id": 0}
    ).to_list(500)
    departures = await db.bookings.find(
        {"check_out": today, "status": "checked_in"}, {"_id": 0}).to_list(500)
    in_house_rows = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(500)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(500)}

    def decorate(rows: list[dict]) -> list[dict]:
        out = [{**b, "guest": guests.get(b["guest_id"]),
                "room": rooms.get(b.get("assigned_room_id"))} for b in rows]
        return sorted(out, key=lambda b: b["check_in"])

    return {"date": today,
            "arrivals": decorate(arrivals),
            "departures": decorate(departures),
            "in_house": decorate(in_house_rows)}


@router.get("/in-house")
async def in_house(q: str = "", user: dict = Depends(DESK)):
    """Checked-in guests, for the POS room search. Only in-house bookings are
    chargeable — a departed folio must never be reachable from the POS."""
    bookings = await db.bookings.find({"status": "checked_in"}, {"_id": 0}).to_list(500)
    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    rooms = {r["id"]: r for r in await db.rooms.find({}, {"_id": 0}).to_list(500)}
    folios = {f["booking_id"]: f for f in await db.folios.find(
        {"status": "open"}, {"_id": 0}).to_list(500)}

    rows = []
    for b in bookings:
        folio = folios.get(b["id"])
        if not folio:
            continue
        rows.append({"booking": b, "guest": guests.get(b["guest_id"]),
                     "room": rooms.get(b.get("assigned_room_id")), "folio": folio})

    if q:
        needle = q.lower()
        rows = [
            r for r in rows
            if needle in ((r["room"] or {}).get("number") or "").lower()
            or needle in ((r["guest"] or {}).get("name") or "").lower()
            or needle in ((r["guest"] or {}).get("phone") or "")
        ]
    return rows


@router.post("/bookings/{booking_id}/check-in")
async def check_in(booking_id: str, payload: CheckInIn, user: dict = Depends(DESK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] == "checked_in":
        raise HTTPException(409, "This booking is already checked in")
    if booking["status"] not in ("tentative", "confirmed"):
        raise HTTPException(409, f"A {booking['status']} booking cannot be checked in")

    # Legally required in India, and the guest is physically present — capture it here
    # rather than at booking, where often only a phone number is known.
    if not payload.id_proof_type.strip() or not payload.id_proof_number.strip():
        raise HTTPException(400, "ID proof type and number are required at check-in")

    room = await db.rooms.find_one({"id": payload.room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if room["room_type_id"] != booking["room_type_id"]:
        raise HTTPException(409, "That room is not of the booked room type")
    if not room.get("active", True):
        raise HTTPException(409, "That room is inactive")

    clash = await db.bookings.find_one({
        "assigned_room_id": payload.room_id, "status": "checked_in",
        "id": {"$ne": booking_id}})
    if clash:
        raise HTTPException(409, f"Room {room['number']} is occupied by {clash['reference']}")

    now = datetime.now(timezone.utc).isoformat()
    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "status": "checked_in", "assigned_room_id": payload.room_id, "checked_in_at": now}})
    await db.guests.update_one({"id": booking["guest_id"]}, {"$set": {
        "id_proof_type": payload.id_proof_type.strip(),
        "id_proof_number": payload.id_proof_number.strip()}})

    folio = await db.folios.find_one({"booking_id": booking_id}, {"_id": 0})
    if not folio:
        folio = Folio(booking_id=booking_id, guest_id=booking["guest_id"]).model_dump()
        await db.folios.insert_one(folio)
        folio.pop("_id", None)

    return {"booking": await db.bookings.find_one({"id": booking_id}, {"_id": 0}),
            "folio": folio,
            "room": room}
```

- [ ] **Step 5: Register both routers**

In `backend/server.py`, add `folios` and `frontdesk` to both the `from routers import ...`
line and the `for module in (...)` loop. Keep `bookings` registered before `frontdesk`;
`/bookings/{id}/check-in` is a literal suffix and does not collide with
`/bookings/{booking_id}`, but preserving the existing order avoids surprises.

- [ ] **Step 6: Restart and run**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
```

Expected: all pass, including the nine new tests.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/folios.py backend/routers/frontdesk.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: check-in, front desk, and folio read with lazy night posting"
```

---

## Task 4: Charges, payments and check-out

**Files:**
- Modify: `backend/routers/folios.py`, `backend/routers/frontdesk.py`
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `services.folio.{direction_for, folio_balance}`, `folios.{_require_open, _sync_balance, post_due_nights}`, `models.folio.{ChargeIn, PaymentIn, CheckOutIn}`
- Produces: `POST /api/folios/{id}/charges`, `POST /api/folios/{id}/payments`, `POST /api/bookings/{id}/check-out`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_charge_and_payment_move_the_balance(admin):
    s = _checked_in(admin, "2029-08-05", "2029-08-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1500.0, "description": "Spa"})
    after_charge = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after_charge["balance"] == 1500.0

    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 500.0, "method": "cash", "kind": "payment"})
    after_payment = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after_payment["balance"] == 1000.0


def test_refund_increases_the_balance(admin):
    s = _checked_in(admin, "2029-09-05", "2029-09-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1000.0, "description": "Minibar"})
    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 1000.0, "method": "cash", "kind": "payment"})
    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 400.0, "method": "cash", "kind": "refund"})
    assert admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"] == 400.0


def test_negative_or_zero_amounts_are_refused(admin):
    s = _checked_in(admin, "2029-10-05", "2029-10-08")
    bad = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                     json={"amount": 0, "description": "Nothing"})
    assert bad.status_code == 400, bad.text
    worse = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                       json={"amount": -50, "description": "Negative"})
    assert worse.status_code == 400, worse.text


def test_check_out_blocked_while_balance_outstanding(admin):
    s = _checked_in(admin, "2029-06-05", "2029-06-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 900.0, "description": "Laundry"})
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out", json={})
    assert r.status_code == 409, r.text


def test_force_check_out_requires_a_reason_and_closes_unpaid(admin):
    s = _checked_in(admin, "2029-07-05", "2029-07-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 700.0, "description": "Minibar"})

    no_reason = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
                           json={"force": True})
    assert no_reason.status_code == 400, no_reason.text

    forced = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
                        json={"force": True, "reason": "Company will settle"})
    assert forced.status_code == 200, forced.text
    assert forced.json()["folio"]["status"] == "closed_unpaid"


def test_check_out_settles_when_the_balance_is_paid(admin):
    s = _checked_in(admin, "2031-03-05", "2031-03-08")   # future stay: no nights due yet
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 600.0, "description": "Laundry"})
    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 600.0, "method": "card", "kind": "payment"})
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out", json={})
    assert r.status_code == 200, r.text
    assert r.json()["folio"]["status"] == "settled"
    assert r.json()["booking"]["status"] == "checked_out"


def test_charging_a_closed_folio_is_refused(admin):
    s = _checked_in(admin, "2029-11-05", "2029-11-08")
    admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
               json={"force": True, "reason": "test"})
    r = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                   json={"amount": 100.0, "description": "Too late"})
    assert r.status_code == 409, r.text
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "charge or payment or refund or check_out or closed_folio"`
Expected: FAIL — 404 on `/charges`, `/payments` and `/check-out`.

- [ ] **Step 3: Add charges and payments to `backend/routers/folios.py`**

Extend the existing imports — add `direction_for` to the `services.folio` import and
`ChargeIn, PaymentIn` to the `models.folio` import — then append:

```python
@router.post("/folios/{folio_id}/charges")
async def add_charge(folio_id: str, payload: ChargeIn, user: dict = Depends(DESK)):
    await _require_open(folio_id)
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    if not payload.description.strip():
        raise HTTPException(400, "A description is required")

    entry = FolioEntry(
        folio_id=folio_id, kind="misc_charge", direction=direction_for("misc_charge"),
        amount=round(payload.amount, 2), description=payload.description.strip(),
        posted_by=user.get("id")).model_dump()
    await db.folio_entries.insert_one(entry)
    entry.pop("_id", None)
    return {"entry": entry, "balance": await _sync_balance(folio_id)}


@router.post("/folios/{folio_id}/payments")
async def add_payment(folio_id: str, payload: PaymentIn, user: dict = Depends(DESK)):
    await _require_open(folio_id)
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be greater than zero")

    # A refund moves money back to the guest. Managers only.
    if payload.kind == "refund" and user.get("role") not in ("admin", "manager"):
        raise HTTPException(403, "Only a manager can issue a refund")

    default_text = {"payment": f"Payment ({payload.method})",
                    "refund": f"Refund ({payload.method})",
                    "discount": "Discount"}[payload.kind]
    entry = FolioEntry(
        folio_id=folio_id, kind=payload.kind, direction=direction_for(payload.kind),
        amount=round(payload.amount, 2),
        description=(payload.description or default_text).strip(),
        posted_by=user.get("id")).model_dump()
    await db.folio_entries.insert_one(entry)
    entry.pop("_id", None)
    return {"entry": entry, "balance": await _sync_balance(folio_id)}
```

- [ ] **Step 4: Add check-out to `backend/routers/frontdesk.py`**

Add `CheckOutIn` to the `models.folio` import, then append:

```python
@router.post("/bookings/{booking_id}/check-out")
async def check_out(booking_id: str, payload: CheckOutIn, user: dict = Depends(DESK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] != "checked_in":
        raise HTTPException(409, f"A {booking['status']} booking cannot be checked out")

    folio = await db.folios.find_one({"booking_id": booking_id}, {"_id": 0})
    if not folio:
        raise HTTPException(404, "No folio for this booking")

    # Post any nights still due before deciding, or a departing guest is undercharged.
    # Imported here rather than at module scope to keep the two routers independent.
    from routers.folios import post_due_nights, _sync_balance
    await post_due_nights(folio["id"])
    balance = await _sync_balance(folio["id"])

    if abs(balance) > 0.005:
        if not payload.force:
            raise HTTPException(409, {
                "message": "Folio has an outstanding balance",
                "balance": balance})
        if user.get("role") not in ("admin", "manager"):
            raise HTTPException(403, "Only a manager can check out an unsettled folio")
        if not (payload.reason or "").strip():
            raise HTTPException(400, "A reason is required to check out with a balance")

    now = datetime.now(timezone.utc).isoformat()
    settled = abs(balance) <= 0.005
    await db.folios.update_one({"id": folio["id"]}, {"$set": {
        "status": "settled" if settled else "closed_unpaid",
        "closed_at": now,
        "closed_reason": None if settled else payload.reason.strip()}})
    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "status": "checked_out", "checked_out_at": now}})

    return {"booking": await db.bookings.find_one({"id": booking_id}, {"_id": 0}),
            "folio": await db.folios.find_one({"id": folio["id"]}, {"_id": 0}),
            "balance": balance}
```

- [ ] **Step 5: Restart and run**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q 2>&1 | tail -1
```

Expected: hotel suite fully green; regression suite `1 failed, 9 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/folios.py backend/routers/frontdesk.py backend/tests/hotel_api_test.py
git commit -m "feat: folio charges, payments and check-out with balance guard"
```

---

## Task 5: Voids — reversing entries that also reverse the order

**Files:**
- Modify: `backend/routers/folios.py`
- Test: `backend/tests/hotel_api_test.py` (append)

The order is voided by writing to `db.orders` from `folios.py`; `orders.py` itself is
unchanged by this task.

**Interfaces:**
- Consumes: `services.folio.void_direction`, `models.folio.VoidIn`, `folios.{_require_open, _sync_balance}`
- Produces: `POST /api/folios/{folio_id}/entries/{entry_id}/void`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_void_credits_the_folio_and_zeroes_the_balance(admin):
    s = _checked_in(admin, "2029-12-05", "2029-12-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 800.0, "description": "Spa"}).json()
    before = admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"]

    r = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void",
        json={"reason": "Guest disputed"})
    assert r.status_code == 200, r.text

    after = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after["balance"] == round(before - 800.0, 2)
    voids = [e for e in after["entries"] if e["kind"] == "void"]
    assert len(voids) == 1
    assert voids[0]["direction"] == "credit"
    assert voids[0]["ref_entry_id"] == charge["entry"]["id"]


def test_voiding_a_payment_debits_the_folio(admin):
    s = _checked_in(admin, "2030-01-05", "2030-01-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1000.0, "description": "Spa"})
    pay = admin.post(f"{API}/folios/{s['folio_id']}/payments",
                     json={"amount": 1000.0, "method": "cash", "kind": "payment"}).json()
    assert admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"] == 0.0

    admin.post(f"{API}/folios/{s['folio_id']}/entries/{pay['entry']['id']}/void",
               json={"reason": "Payment reversed by bank"})
    after = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after["balance"] == 1000.0
    assert next(e for e in after["entries"] if e["kind"] == "void")["direction"] == "debit"


def test_voiding_twice_is_refused(admin):
    s = _checked_in(admin, "2030-02-05", "2030-02-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 300.0, "description": "Laundry"}).json()
    body = {"reason": "Duplicate"}
    first = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void", json=body)
    assert first.status_code == 200
    second = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void", json=body)
    assert second.status_code == 409, second.text


def test_void_requires_a_reason(admin):
    s = _checked_in(admin, "2030-03-05", "2030-03-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 200.0, "description": "Laundry"}).json()
    r = admin.post(f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void",
                   json={"reason": "   "})
    assert r.status_code == 400, r.text
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k void`
Expected: FAIL — 404.

- [ ] **Step 3: Add the void endpoint to `backend/routers/folios.py`**

Add `void_direction` to the existing import from `services.folio`, add `VoidIn` to the import from `models.folio`, then append:

```python
@router.post("/folios/{folio_id}/entries/{entry_id}/void")
async def void_entry(folio_id: str, entry_id: str, payload: VoidIn,
                     user: dict = Depends(MANAGER)):
    """Reverse an entry by writing a compensating one. Nothing is ever deleted, so a
    disputed bill keeps its audit trail.

    An outlet entry also voids the underlying order: outlet revenue was recognised when
    the bill was served, so leaving the order settled would permanently overstate it.
    """
    await _require_open(folio_id)

    original = await db.folio_entries.find_one(
        {"id": entry_id, "folio_id": folio_id}, {"_id": 0})
    if not original:
        raise HTTPException(404, "Entry not found on this folio")
    if original["kind"] == "void":
        raise HTTPException(409, "A void cannot itself be voided")
    if not payload.reason.strip():
        raise HTTPException(400, "A reason is required to void an entry")

    already = await db.folio_entries.find_one({"kind": "void", "ref_entry_id": entry_id})
    if already:
        raise HTTPException(409, "That entry has already been voided")

    entry = FolioEntry(
        folio_id=folio_id, kind="void",
        direction=void_direction(original["direction"]),
        amount=original["amount"],
        description=f"Void: {original['description']} — {payload.reason.strip()}",
        ref_entry_id=entry_id, posted_by=user.get("id")).model_dump()
    await db.folio_entries.insert_one(entry)
    entry.pop("_id", None)

    voided_order = None
    if original["kind"] == "outlet" and original.get("ref_order_id"):
        await db.orders.update_one({"id": original["ref_order_id"]}, {"$set": {
            "status": "voided",
            "voided_at": datetime.now(timezone.utc).isoformat(),
            "void_reason": payload.reason.strip()}})
        voided_order = original["ref_order_id"]

    return {"entry": entry,
            "balance": await _sync_balance(folio_id),
            "voided_order_id": voided_order}
```

- [ ] **Step 4: Run the void tests**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k void
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/folios.py backend/tests/hotel_api_test.py
git commit -m "feat: void folio entries with reversing entries"
```

---

## Task 6: Charge to room at the POS

**Files:**
- Modify: `backend/routers/orders.py`
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `models.folio.FolioEntry`, `services.folio.direction_for`
- Produces: `POST /api/orders/{id}/settle` accepting `payment_method="room"` with `folio_id`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
def _open_order(admin):
    """Open a bar order on a free table and return it."""
    tables = admin.get(f"{API}/tables").json()
    table = next(t for t in tables if t["status"] == "free")
    menu = admin.get(f"{API}/menu").json()
    return admin.post(f"{API}/orders/table/{table['id']}/items", json={
        "items": [{"menu_item_id": menu[0]["id"], "quantity": 2}], "source": "pos"}).json()


def test_settle_to_room_posts_an_outlet_debit(admin):
    s = _checked_in(admin, "2030-04-05", "2030-04-08")
    before = admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"]
    order = _open_order(admin)

    r = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "settled"
    assert r.json()["payment_method"] == "room"

    after = admin.get(f"{API}/folios/{s['folio_id']}").json()
    outlet = [e for e in after["entries"] if e["kind"] == "outlet"]
    assert len(outlet) == 1
    assert outlet[0]["direction"] == "debit"
    assert outlet[0]["ref_order_id"] == order["id"]
    assert after["balance"] == round(before + order["total"], 2)


def test_settle_to_room_still_counts_as_outlet_revenue(admin):
    """A room-settled order is an ordinary settled order — reports must not special-case it."""
    s = _checked_in(admin, "2030-05-05", "2030-05-08")
    before = admin.get(f"{API}/reports/summary").json()["revenue_today"]
    order = _open_order(admin)
    admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    after = admin.get(f"{API}/reports/summary").json()["revenue_today"]
    assert round(after - before, 2) == round(order["total"], 2)


def test_settle_to_room_requires_a_folio_id(admin):
    order = _open_order(admin)
    r = admin.post(f"{API}/orders/{order['id']}/settle", json={"payment_method": "room"})
    assert r.status_code == 400, r.text


def test_settle_to_a_closed_folio_is_refused(admin):
    s = _checked_in(admin, "2030-06-05", "2030-06-08")
    admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
               json={"force": True, "reason": "test"})
    order = _open_order(admin)
    r = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    assert r.status_code == 409, r.text


def test_cash_settle_is_unchanged(admin):
    """Regression guard: the existing settle path must behave exactly as before."""
    order = _open_order(admin)
    r = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "cash", "customer_name": "Walk In", "customer_phone": "9000000001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "settled"
    assert body["payment_method"] == "cash"
    assert body["customer_name"] == "Walk In"
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "settle"`
Expected: the room tests fail with 422 (`"room"` is not an accepted `payment_method`); `test_cash_settle_is_unchanged` already passes.

- [ ] **Step 3: Widen `SettleIn` in `backend/routers/orders.py`**

Replace the existing `SettleIn` class with:

```python
class SettleIn(BaseModel):
    payment_method: Literal["cash", "card", "online", "room"] = "cash"
    discount: float = 0.0
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    # Required when payment_method is "room".
    folio_id: Optional[str] = None
```

- [ ] **Step 4: Post the folio debit inside `settle_order`**

In `backend/routers/orders.py`, add these imports at the top:

```python
from models.folio import FolioEntry
from services.folio import direction_for
```

Then, inside `settle_order`, immediately **after** `order = compute_totals(order)` and **before** `order["status"] = "settled"`, insert the validation; and immediately **after** the `db.tables.update_one(...)` call, insert the posting:

```python
    # --- validation, before anything is written ---
    folio = None
    if payload.payment_method == "room":
        if not payload.folio_id:
            raise HTTPException(400, "folio_id is required when charging to a room")
        folio = await db.folios.find_one({"id": payload.folio_id}, {"_id": 0})
        if not folio:
            raise HTTPException(404, "Folio not found")
        if folio["status"] != "open":
            raise HTTPException(409, f"That folio is {folio['status']} and cannot be charged")
```

```python
    # --- after the table is freed: post the receivable ---
    if folio is not None:
        entry = FolioEntry(
            folio_id=folio["id"], kind="outlet", direction=direction_for("outlet"),
            amount=round(order["total"], 2),
            description=f"{order['table_label']} · bill {order['id'][:8]}",
            ref_order_id=order["id"], posted_by=user.get("id")).model_dump()
        await db.folio_entries.insert_one(entry)
        entries = await db.folio_entries.find(
            {"folio_id": folio["id"]}, {"_id": 0}).to_list(5000)
        from services.folio import folio_balance
        await db.folios.update_one({"id": folio["id"]}, {"$set": {
            "balance": folio_balance(entries)}})
```

The order itself is untouched by this: it still settles, still frees the table, still counts as outlet revenue today. The folio entry is the receivable.

- [ ] **Step 5: Run the settle tests**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q 2>&1 | tail -1
```

Expected: hotel suite fully passing; regression suite still `1 failed, 9 passed, 1 skipped`.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/orders.py backend/tests/hotel_api_test.py
git commit -m "feat: charge a bill to a room at settle"
```

---

## Task 7: Front desk screen

**Files:**
- Create: `frontend/src/pages/hotel/FrontDesk.jsx`
- Modify: `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx`

**Interfaces:**
- Consumes: `GET /api/front-desk`, `POST /api/bookings/{id}/check-in`
- Produces: route `/app/hotel/front-desk`

- [ ] **Step 1: Read the existing patterns first**

Before writing anything, read `frontend/src/pages/hotel/Bookings.jsx` and
`frontend/src/pages/hotel/BookingDetail.jsx`. Follow what is actually there: the `HOTEL`
eyebrow plus big uppercase `<h1>`, `formatApiErrorDetail` with `toast.error`, `currency()`
for money, `tabular-nums` on numeric columns, `overflow-x-auto` on wide tables, and the
`isNavItemActive` / `exclude` mechanism in `AppLayout.jsx`. Do not restructure `AppLayout.jsx`.

- [ ] **Step 2: Create `frontend/src/pages/hotel/FrontDesk.jsx`**

```jsx
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function FrontDesk() {
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [rooms, setRooms] = useState([]);
  const [checkingIn, setCheckingIn] = useState(null); // booking being checked in
  const [form, setForm] = useState({ room_id: "", id_proof_type: "Aadhaar", id_proof_number: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    Promise.all([api.get("/front-desk"), api.get("/rooms")])
      .then(([d, r]) => {
        setData(d.data);
        setRooms(r.data);
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const startCheckIn = (booking) => {
    setCheckingIn(booking);
    setForm({ room_id: "", id_proof_type: "Aadhaar", id_proof_number: "" });
  };

  const submitCheckIn = async () => {
    if (!form.room_id) {
      toast.error("Pick a room");
      return;
    }
    if (!form.id_proof_number.trim()) {
      toast.error("ID proof number is required");
      return;
    }
    setBusy(true);
    try {
      const { data: res } = await api.post(`/bookings/${checkingIn.id}/check-in`, form);
      toast.success(`Checked in to room ${res.room.number}`);
      setCheckingIn(null);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <div className="p-6 md:p-10 text-stone-400">Loading front desk…</div>;

  const freeRooms = rooms.filter(
    (r) => r.active !== false && r.room_type_id === checkingIn?.room_type_id,
  );

  const Row = ({ b, action }) => (
    <li className="flex items-center justify-between gap-4 py-3 border-b border-stone-800">
      <div className="min-w-0">
        <div className="truncate">{b.guest?.name || "—"}</div>
        <div className="text-xs text-stone-500 font-mono">
          {b.reference} · {b.check_in} → {b.check_out}
          {b.room ? ` · room ${b.room.number}` : ""}
        </div>
      </div>
      {action}
    </li>
  );

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Front desk
      </h1>
      <p className="text-stone-500 font-mono text-xs mb-8">{data.date}</p>

      <div className="grid gap-8 lg:grid-cols-3">
        <section>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            Arrivals · {data.arrivals.length}
          </h2>
          {data.arrivals.length === 0 ? (
            <p className="text-stone-500 text-sm">No arrivals today.</p>
          ) : (
            <ul>
              {data.arrivals.map((b) => (
                <Row
                  key={b.id}
                  b={b}
                  action={
                    <button
                      onClick={() => startCheckIn(b)}
                      className="shrink-0 border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 rounded-full px-4 py-1 text-xs tracking-widest uppercase"
                    >
                      Check in
                    </button>
                  }
                />
              ))}
            </ul>
          )}
        </section>

        <section>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            Departures · {data.departures.length}
          </h2>
          {data.departures.length === 0 ? (
            <p className="text-stone-500 text-sm">No departures today.</p>
          ) : (
            <ul>
              {data.departures.map((b) => (
                <Row
                  key={b.id}
                  b={b}
                  action={
                    <button
                      onClick={() => nav(`/app/hotel/bookings/${b.id}`)}
                      className="shrink-0 border border-stone-700 text-stone-300 hover:border-orange-500 hover:text-orange-400 rounded-full px-4 py-1 text-xs tracking-widest uppercase"
                    >
                      Open
                    </button>
                  }
                />
              ))}
            </ul>
          )}
        </section>

        <section>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            In house · {data.in_house.length}
          </h2>
          {data.in_house.length === 0 ? (
            <p className="text-stone-500 text-sm">Nobody in house.</p>
          ) : (
            <ul>
              {data.in_house.map((b) => (
                <Row
                  key={b.id}
                  b={b}
                  action={
                    <Link
                      to={`/app/hotel/bookings/${b.id}`}
                      className="shrink-0 text-xs tracking-widest uppercase text-orange-400 hover:underline"
                    >
                      Folio
                    </Link>
                  }
                />
              ))}
            </ul>
          )}
        </section>
      </div>

      {checkingIn && (
        <div className="mt-10 border border-stone-800 bg-stone-900 rounded p-5 max-w-xl">
          <h3 className="text-lg font-semibold mb-1">
            Check in {checkingIn.guest?.name}
          </h3>
          <p className="text-xs text-stone-500 font-mono mb-4">
            {checkingIn.reference} · {checkingIn.check_in} → {checkingIn.check_out}
          </p>

          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Room
              <select
                value={form.room_id}
                onChange={(e) => setForm({ ...form, room_id: e.target.value })}
                className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                <option value="">Choose…</option>
                {freeRooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.number}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              ID type
              <select
                value={form.id_proof_type}
                onChange={(e) => setForm({ ...form, id_proof_type: e.target.value })}
                className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                {["Aadhaar", "Passport", "Driving Licence", "Voter ID"].map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              ID number
              <input
                value={form.id_proof_number}
                onChange={(e) => setForm({ ...form, id_proof_number: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          </div>

          <p className="text-xs text-stone-500 mt-4">
            ID capture is a legal requirement for Indian hotels and is recorded against the
            guest, not the booking.
          </p>

          <div className="flex gap-3 mt-5">
            <button
              onClick={submitCheckIn}
              disabled={busy}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Checking in…" : "Confirm check in"}
            </button>
            <button
              onClick={() => setCheckingIn(null)}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Wire the route and nav**

`frontend/src/App.js` — add the import beside the other hotel pages, and the route
**before** `/hotel/bookings/:id` is irrelevant here (different prefix), so position is free:

```jsx
import FrontDesk from "@/pages/hotel/FrontDesk";
```

```jsx
        <Route path="/hotel/front-desk" element={<Protected roles={["admin", "manager", "front_desk"]}><FrontDesk /></Protected>} />
```

`frontend/src/components/app/AppLayout.jsx` — add to the HOTEL nav group, following the
shape of the existing entries:

```jsx
  { to: "/app/hotel/front-desk", label: "Front desk", roles: ["admin", "manager", "front_desk"] },
```

- [ ] **Step 4: Verify**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build
```

Expected: compiles, with only the pre-existing eslint warnings in `CustomerMenu.jsx` and
`Reservations.jsx`. Then `rm -rf build`.

If a dev server is already running on port 3001 (check `lsof -nP -iTCP:3001 -sTCP:LISTEN`),
reuse it and load `/app/hotel/front-desk` as admin: today's arrivals, departures and
in-house should render, and the check-in panel should list only rooms of the booked type.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: front desk screen with check-in"
```

---

## Task 8: Folio screen

**Files:**
- Create: `frontend/src/pages/hotel/Folio.jsx`
- Modify: `frontend/src/App.js`

**Interfaces:**
- Consumes: `GET /api/folios/{id}`, `POST /api/folios/{id}/charges`, `POST /api/folios/{id}/payments`, `POST /api/folios/{id}/entries/{entry_id}/void`
- Produces: route `/app/hotel/folios/:id`

- [ ] **Step 1: Create `frontend/src/pages/hotel/Folio.jsx`**

```jsx
import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const KIND_LABEL = {
  room_night: "Room night",
  outlet: "Bar / restaurant",
  misc_charge: "Charge",
  payment: "Payment",
  refund: "Refund",
  discount: "Discount",
  void: "Void",
};

export default function Folio() {
  const { id } = useParams();
  const nav = useNavigate();
  const [folio, setFolio] = useState(null);
  const [charge, setCharge] = useState({ amount: "", description: "" });
  const [payment, setPayment] = useState({ amount: "", method: "cash", kind: "payment" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get(`/folios/${id}`)
      .then((r) => setFolio(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const post = async (fn) => {
    setBusy(true);
    try {
      await fn();
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const addCharge = () =>
    post(async () => {
      if (!charge.amount || Number(charge.amount) <= 0) throw noAmount();
      await api.post(`/folios/${id}/charges`, {
        amount: Number(charge.amount),
        description: charge.description,
      });
      setCharge({ amount: "", description: "" });
      toast.success("Charge posted");
    });

  const takePayment = () =>
    post(async () => {
      if (!payment.amount || Number(payment.amount) <= 0) throw noAmount();
      await api.post(`/folios/${id}/payments`, {
        amount: Number(payment.amount),
        method: payment.method,
        kind: payment.kind,
      });
      setPayment({ amount: "", method: "cash", kind: "payment" });
      toast.success("Recorded");
    });

  const voidEntry = (entry) =>
    post(async () => {
      const reason = window.prompt(`Reason for voiding "${entry.description}"?`);
      if (reason === null) return;
      if (!reason.trim()) throw { response: { data: { detail: "A reason is required" } } };
      await api.post(`/folios/${id}/entries/${entry.id}/void`, { reason });
      toast.success("Voided");
    });

  function noAmount() {
    return { response: { data: { detail: "Enter an amount greater than zero" } } };
  }

  if (!folio) return <div className="p-6 md:p-10 text-stone-400">Loading folio…</div>;

  const voided = new Set(
    folio.entries.filter((e) => e.kind === "void").map((e) => e.ref_entry_id),
  );
  const open = folio.status === "open";

  return (
    <div className="p-6 md:p-10">
      <button
        onClick={() => nav(`/app/hotel/bookings/${folio.booking_id}`)}
        className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 mb-4"
      >
        ← Booking
      </button>
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">
        Folio · {folio.status.replace("_", " ")}
      </div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        {folio.guest?.name}
      </h1>
      <p className="text-stone-400 mb-8">
        {folio.booking?.reference} · {folio.booking?.check_in} → {folio.booking?.check_out}
      </p>

      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-8 max-w-sm">
        <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">Balance</div>
        <div
          className={`text-4xl font-extrabold tabular-nums mt-1 ${
            folio.balance > 0 ? "text-orange-400" : "text-stone-100"
          }`}
        >
          {currency(folio.balance)}
        </div>
        <div className="text-xs text-stone-500 mt-1">
          {folio.balance > 0 ? "Outstanding" : folio.balance < 0 ? "In credit" : "Settled"}
        </div>
      </div>

      <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-3">Ledger</h2>
      <div className="overflow-x-auto mb-10">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
              <th className="text-left py-2 px-3 border-b border-stone-800">Posted</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Kind</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Description</th>
              <th className="text-right py-2 px-3 border-b border-stone-800">Debit</th>
              <th className="text-right py-2 px-3 border-b border-stone-800">Credit</th>
              <th className="border-b border-stone-800" />
            </tr>
          </thead>
          <tbody>
            {folio.entries.length === 0 && (
              <tr>
                <td colSpan={6} className="py-4 px-3 text-stone-500">
                  Nothing posted yet.
                </td>
              </tr>
            )}
            {folio.entries.map((e) => (
              <tr key={e.id} className={voided.has(e.id) ? "opacity-50 line-through" : ""}>
                <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs">
                  {(e.posted_at || "").slice(0, 10)}
                </td>
                <td className="py-2 px-3 border-b border-stone-800 text-stone-400">
                  {KIND_LABEL[e.kind] || e.kind}
                </td>
                <td className="py-2 px-3 border-b border-stone-800">{e.description}</td>
                <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">
                  {e.direction === "debit" ? currency(e.amount) : ""}
                </td>
                <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums text-stone-400">
                  {e.direction === "credit" ? currency(e.amount) : ""}
                </td>
                <td className="py-2 px-3 border-b border-stone-800 text-right">
                  {open && e.kind !== "void" && !voided.has(e.id) && (
                    <button
                      onClick={() => voidEntry(e)}
                      disabled={busy}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-red-400 disabled:opacity-50"
                    >
                      Void
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {open && (
        <div className="grid gap-6 md:grid-cols-2 max-w-3xl">
          <div className="border border-stone-800 bg-stone-900 rounded p-5">
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
              Add a charge
            </h3>
            <div className="flex flex-wrap gap-4 items-end">
              <label className="text-xs tracking-widest uppercase text-stone-500">
                Amount
                <input
                  type="number"
                  min="0"
                  value={charge.amount}
                  onChange={(e) => setCharge({ ...charge, amount: e.target.value })}
                  className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
                />
              </label>
              <label className="text-xs tracking-widest uppercase text-stone-500 flex-1 min-w-[10rem]">
                Description
                <input
                  value={charge.description}
                  onChange={(e) => setCharge({ ...charge, description: e.target.value })}
                  placeholder="Laundry"
                  className="block mt-2 w-full bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
                />
              </label>
              <button
                onClick={addCharge}
                disabled={busy}
                className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-5 py-2 text-xs tracking-widest uppercase"
              >
                Post
              </button>
            </div>
          </div>

          <div className="border border-stone-800 bg-stone-900 rounded p-5">
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
              Take a payment
            </h3>
            <div className="flex flex-wrap gap-4 items-end">
              <label className="text-xs tracking-widest uppercase text-stone-500">
                Amount
                <input
                  type="number"
                  min="0"
                  value={payment.amount}
                  onChange={(e) => setPayment({ ...payment, amount: e.target.value })}
                  className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
                />
              </label>
              <label className="text-xs tracking-widest uppercase text-stone-500">
                Method
                <select
                  value={payment.method}
                  onChange={(e) => setPayment({ ...payment, method: e.target.value })}
                  className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
                >
                  {["cash", "card", "online"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs tracking-widest uppercase text-stone-500">
                Type
                <select
                  value={payment.kind}
                  onChange={(e) => setPayment({ ...payment, kind: e.target.value })}
                  className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
                >
                  <option value="payment">Payment</option>
                  <option value="discount">Discount</option>
                  <option value="refund">Refund</option>
                </select>
              </label>
              <button
                onClick={takePayment}
                disabled={busy}
                className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-5 py-2 text-xs tracking-widest uppercase"
              >
                Record
              </button>
            </div>
            <p className="text-xs text-stone-500 mt-4">
              A refund hands money back, so it increases the balance. Managers only.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire the route**

`frontend/src/App.js`:

```jsx
import Folio from "@/pages/hotel/Folio";
```

```jsx
        <Route path="/hotel/folios/:id" element={<Protected roles={["admin", "manager", "front_desk"]}><Folio /></Protected>} />
```

No nav entry — a folio is always reached from a booking or the front desk.

- [ ] **Step 3: Verify**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build && rm -rf build
```

Expected: compiles with only the pre-existing warnings.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: folio screen with ledger, charges, payments and void"
```

---

## Task 9: Check-in/out on booking detail, and charge-to-room at the POS

**Files:**
- Modify: `frontend/src/pages/hotel/BookingDetail.jsx`, `frontend/src/pages/POS.jsx`

**Interfaces:**
- Consumes: `POST /api/bookings/{id}/check-out`, `GET /api/in-house`, `POST /api/orders/{id}/settle`
- Produces: nothing later depends on

- [ ] **Step 1: Add a folio link and check-out to `BookingDetail.jsx`**

Read the file first. Where the existing cancel button lives, add — for a booking whose
`status` is `checked_in` — a link to its folio and a check-out action:

```jsx
  const [folioId, setFolioId] = useState(null);

  useEffect(() => {
    if (b?.status !== "checked_in" && b?.status !== "checked_out") return;
    api
      .get("/folios")
      .then((r) => {
        const f = r.data.find((x) => x.booking_id === b.id);
        setFolioId(f ? f.id : null);
      })
      .catch(() => setFolioId(null));
  }, [b?.status, b?.id]);

  const checkOut = async (force) => {
    let reason = null;
    if (force) {
      reason = window.prompt("Reason for checking out with a balance?");
      if (reason === null) return;
    }
    try {
      await api.post(`/bookings/${id}/check-out`, force ? { force: true, reason } : {});
      toast.success("Checked out");
      load();
    } catch (e) {
      const detail = e.response?.data?.detail;
      // 409 carries the outstanding balance — offer the manager override rather than
      // making the desk guess why it refused.
      if (e.response?.status === 409 && detail?.balance !== undefined) {
        toast.error(`Outstanding balance ${currency(detail.balance)} — use Force check-out`);
      } else {
        toast.error(formatApiErrorDetail(detail));
      }
    }
  };
```

Render, alongside the existing actions:

```jsx
      {b.status === "checked_in" && (
        <div className="flex gap-3 flex-wrap">
          {folioId && (
            <Link
              to={`/app/hotel/folios/${folioId}`}
              className="border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Open folio
            </Link>
          )}
          <button
            onClick={() => checkOut(false)}
            className="bg-orange-600 hover:bg-orange-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Check out
          </button>
          <button
            onClick={() => checkOut(true)}
            className="border border-red-500/40 text-red-400 hover:bg-red-500/10 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Force check-out
          </button>
        </div>
      )}
```

Add `Link` to the existing `react-router-dom` import and `currency` to the `@/lib/api` import
if they are not already there.

- [ ] **Step 2: Add the Room option to `frontend/src/pages/POS.jsx`**

Read the file first and find the payment-method selector (it offers Cash, Card, Online) and
the settle handler. Add a fourth option plus an in-house search that appears only when Room
is chosen:

```jsx
  const [inHouse, setInHouse] = useState([]);
  const [roomQuery, setRoomQuery] = useState("");
  const [chosenFolio, setChosenFolio] = useState(null);

  useEffect(() => {
    if (payment !== "room") {
      setChosenFolio(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .get("/in-house", { params: { q: roomQuery } })
        .then((r) => setInHouse(r.data))
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
    }, 300);
    return () => clearTimeout(t);
  }, [payment, roomQuery]);
```

The picker, rendered when `payment === "room"`:

```jsx
      {payment === "room" && (
        <div className="mt-4">
          <input
            value={roomQuery}
            onChange={(e) => setRoomQuery(e.target.value)}
            placeholder="Room number, guest name or phone"
            className="w-full bg-transparent border-b border-stone-700 text-stone-100 py-2 focus:border-orange-500 outline-none"
          />
          <ul className="mt-2 max-h-40 overflow-y-auto divide-y divide-stone-800">
            {inHouse.length === 0 && (
              <li className="py-2 text-xs text-stone-500">No in-house guest matches.</li>
            )}
            {inHouse.map((x) => (
              <li key={x.folio.id}>
                <button
                  onClick={() => setChosenFolio(x)}
                  className={`w-full text-left py-2 text-sm ${
                    chosenFolio?.folio?.id === x.folio.id ? "text-orange-400" : "text-stone-300"
                  }`}
                >
                  Room {x.room?.number} · {x.guest?.name}
                  <span className="block text-xs text-stone-500 font-mono">
                    {x.guest?.phone}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
```

And in the settle handler, before calling the API:

```jsx
    if (payment === "room" && !chosenFolio) {
      toast.error("Pick the in-house guest to charge");
      return;
    }
```

with the request body gaining `folio_id: payment === "room" ? chosenFolio.folio.id : undefined`.

Only checked-in guests appear here, so a departed folio cannot be charged by mistake.

- [ ] **Step 3: Verify**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build && rm -rf build
```

Expected: compiles with only the pre-existing warnings.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: check-out on booking detail and charge-to-room at the POS"
```

---

## Task 10: End-to-end verification

**Files:** none modified — verification only.

- [ ] **Step 1: Start from a clean database**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/
```

- [ ] **Step 2: Run every suite and report exact output**

```bash
cd ~/dev/bar-management-system/backend
python3 -m pytest tests/test_pricing.py tests/test_availability.py tests/test_folio.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q   # second run, same db
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
```

Expected: pure suites `43 passed` (26 existing + 17 new); hotel suite fully green on **both**
runs — a second run failing means a test is not self-contained; regression suite exactly
`1 failed, 9 passed, 1 skipped`.

- [ ] **Step 3: Confirm the route table**

```bash
cd ~/dev/bar-management-system/backend
MONGO_URL=mock python3 -c "
from server import app
paths = {r.path for r in app.routes if hasattr(r,'methods')}
required = {'/api/front-desk','/api/in-house','/api/folios','/api/folios/{folio_id}',
            '/api/folios/{folio_id}/charges','/api/folios/{folio_id}/payments',
            '/api/bookings/{booking_id}/check-in','/api/bookings/{booking_id}/check-out',
            '/api/availability','/api/bookings','/api/orders/kot','/api/reports/summary'}
print('MISSING:', required - paths or 'none')"
```

Expected: `MISSING: none`

- [ ] **Step 4: Walk the money path against the API**

Using curl or a short Python script against the clean server, and reporting the actual
numbers at each step:

1. Create a room type with 2 rooms and a default rate of ₹5,000
2. Create a guest and a 3-night booking; note the quoted total
3. Check in, assigning a room and an ID number
4. Read the folio — confirm 3 room-night debits whose amounts match `quote.nights`
5. Open a bar order, settle it with `payment_method: "room"`
6. Confirm the folio balance rose by exactly the order total
7. Confirm `/reports/summary` `revenue_today` **also** rose by the order total — the room
   charge is outlet revenue today
8. Void the outlet entry; confirm the balance drops back **and** `revenue_today` drops back
9. Attempt check-out — expect 409 with the outstanding balance
10. Pay the balance in full; confirm balance is 0
11. Check out; confirm folio status is `settled` and the booking is `checked_out`

- [ ] **Step 5: Confirm no runtime artefacts are staged**

```bash
cd ~/dev/bar-management-system && git status --short
```

Expected: no `backend/db.json`, no `frontend/build/`, no `.env`.

- [ ] **Step 6: Commit if anything remains**

```bash
cd ~/dev/bar-management-system
git add -A && git commit -m "test: end-to-end verification of front desk and folio" || echo "nothing to commit"
```

---

## Deferred, by design

Split folios (company pays the room, guest pays the bar tab); housekeeping status;
events and banquets; night audit and unified occupancy/ADR reporting; printed or emailed
invoices; ID document image capture and its retention rules.
