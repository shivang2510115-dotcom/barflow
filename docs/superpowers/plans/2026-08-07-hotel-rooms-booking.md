# Hotel Rooms & Booking Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add room inventory, seasonal pricing with meal plans, a phone-keyed guest record, and staff-operated room bookings to the existing BarFlow app.

**Architecture:** Modular monolith. `backend/server.py` (1179 lines, all seven existing domains) is first split into `backend/routers/*` with zero behaviour change, then hotel routers are added alongside. Availability is an indexed overlap query over `bookings` — no inventory ledger. Pricing and GST live in pure functions in `backend/services/` so they are unit-testable without a database.

**Tech Stack:** FastAPI, Motor/PyMongo (with a JSON-file mock fallback), Pydantic v2, pytest + pytest-xdist, React 19 + CRA/craco, Tailwind, shadcn/ui, axios, react-router-dom v7.

**Spec:** `docs/superpowers/specs/2026-08-07-hotel-rooms-booking-design.md`

---

## Global Constraints

- **Dates are `YYYY-MM-DD` strings, never datetimes.** Check-in is a calendar date. Compare as strings. Applies to `check_in`, `check_out`, `rate_periods.start_date/end_date`, `out_of_order.from/to`.
- **All date ranges are half-open `[from, to)`.** A checkout on the 5th and an arrival on the 5th do not collide. Same rule for `out_of_order`.
- **A missing rate is an error, never a zero.** Raise with the specific uncovered dates.
- **Bookings snapshot their price** into `booking.quote`. Rate edits must never change a confirmed total. Re-quote only when dates, occupancy or meal plan change.
- **One booking = one room.** Multi-room parties share a `group_ref`.
- **Overbooking is hard-blocked** at zero availability.
- **New collection is `bookings`.** Existing `reservations` keeps meaning restaurant table bookings. Do not rename it.
- **Currency is ₹.** Backend already reads `CURRENCY_SYMBOL` (default `₹`). Note: `frontend/src/lib/api.js:currency()` hardcodes `$` — Task 15 fixes this.
- **Do NOT modify `backend/pytest.ini` `addopts`.** It is pinned to `-n 2 --dist loadscope` with an explicit warning comment. Run serial with `-n 0` if ever needed.
- **New roles:** add `front_desk` to the existing `admin`, `manager`, `waiter`, `kitchen`.
- **Python:** match existing style — 4-space indent, type hints on signatures, `HTTPException` for errors.

### Test baseline (measured 2026-08-07, must be preserved)

`backend/tests/backend_test.py` is an **HTTP integration suite that requires a running server**. It reads `REACT_APP_BACKEND_URL` and defaults to a remote preview host. Against a local server it produces:

```
1 failed, 9 passed, 1 skipped
```

- **FAILED** `TestStripeCheckout::test_create_checkout_session_returns_stripe_url` — pre-existing and environmental. Without a real `STRIPE_API_KEY`, the vendored `emergentintegrations` stub returns a local URL instead of a `stripe.com` one.
- **SKIPPED** `test_checkout_status` — depends on the session id from the failed test.

**The extraction guard in Task 1 is "results identical to this baseline", not "all green".** Do not try to fix the Stripe test; it is out of scope.

To run it:

```bash
cd ~/dev/bar-management-system/backend
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 &
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
```

---

## File Structure

**Backend — created by Task 1 (extraction, no behaviour change):**

| File | Responsibility |
|---|---|
| `backend/db.py` | Mongo client + `db` handle, including the mock fallback |
| `backend/security.py` | `hash_password`, `verify_password`, `create_access_token`, `get_current_user`, `require_roles`, `Role` |
| `backend/routers/auth.py` | login, register, me, staff |
| `backend/routers/tables.py` | tables + restaurant reservations |
| `backend/routers/menu.py` | menu CRUD |
| `backend/routers/orders.py` | orders, KOT, settle |
| `backend/routers/inventory.py` | inventory CRUD + adjust |
| `backend/routers/reports.py` | summary, analytics, daily brief, scheduler |
| `backend/routers/payments.py` | Stripe checkout + webhook |
| `backend/server.py` | app assembly, middleware, startup/shutdown, seed only |

**Backend — new hotel code:**

| File | Responsibility |
|---|---|
| `backend/models/hotel.py` | Pydantic models for guests, room types, rooms, rates, bookings |
| `backend/services/pricing.py` | pure: rate resolution, nightly quote, GST slabs |
| `backend/services/availability.py` | pure: availability arithmetic over supplied rooms/bookings |
| `backend/routers/guests.py` | guest CRUD + search + history |
| `backend/routers/rooms.py` | room types + rooms + out-of-order |
| `backend/routers/rates.py` | meal plans, rate periods, rates, tax slabs |
| `backend/routers/bookings.py` | availability endpoint, booking CRUD, cancel, calendar |
| `backend/migrations/backfill_guests.py` | one-shot guest backfill from `orders` |

**Backend tests:**

| File | Responsibility |
|---|---|
| `backend/tests/test_pricing.py` | pure unit tests, no server |
| `backend/tests/test_availability.py` | pure unit tests, no server |
| `backend/tests/hotel_api_test.py` | HTTP integration, mirrors `backend_test.py` style |

**Frontend:**

| File | Responsibility |
|---|---|
| `frontend/src/pages/hotel/NewBooking.jsx` | availability search + book |
| `frontend/src/pages/hotel/Bookings.jsx` | list + filters |
| `frontend/src/pages/hotel/BookingDetail.jsx` | itinerary, edit, cancel |
| `frontend/src/pages/hotel/Calendar.jsx` | occupancy grid |
| `frontend/src/pages/hotel/Rooms.jsx` | room types + rooms |
| `frontend/src/pages/hotel/Rates.jsx` | periods, rates, meal plans |
| `frontend/src/pages/hotel/Guests.jsx` | search + profile |
| `frontend/src/components/app/AppLayout.jsx` | modify: HOTEL nav group |
| `frontend/src/App.js` | modify: hotel routes |
| `frontend/src/lib/api.js` | modify: `currency()` → ₹ |

---

## Task 1: Extract `server.py` into routers

No behaviour change. This is the precondition for all hotel work.

**Files:**
- Create: `backend/db.py`, `backend/security.py`, `backend/routers/__init__.py`, `backend/routers/{auth,tables,menu,orders,inventory,reports,payments}.py`
- Modify: `backend/server.py` (reduce to app assembly)
- Test: `backend/tests/backend_test.py` (unchanged — used as the guard)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `backend/db.py` → `db` (database handle), `client`
  - `backend/security.py` → `hash_password(password: str) -> str`, `verify_password(plain: str, hashed: str) -> bool`, `create_access_token(user_id: str, email: str, role: str) -> str`, `get_current_user(request: Request) -> dict`, `require_roles(*roles: str)`, `Role` (Literal)
  - each `backend/routers/X.py` → `router` (an `APIRouter()` with **no prefix**)

- [ ] **Step 1: Capture the baseline**

```bash
cd ~/dev/bar-management-system/backend
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q 2>&1 | tail -3
```

Expected, exactly: `1 failed, 9 passed, 1 skipped`

- [ ] **Step 2: Create `backend/db.py`**

Move lines 31–41 of `server.py` verbatim.

```python
"""Database handle. Falls back to a JSON-file mock when MONGO_URL is unset."""
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

mongo_url = os.environ.get('MONGO_URL', 'mock')
if not mongo_url or mongo_url.startswith('mock') or mongo_url.startswith('local'):
    logger.info("Using local JSON file-based database mock...")
    from mock_db import MockMongoClient
    client = MockMongoClient(None)
    db = client[None]
else:
    logger.info("Connecting to remote MongoDB client...")
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get('DB_NAME', 'barflow')]
```

- [ ] **Step 3: Create `backend/security.py`**

Move lines 48–102 of `server.py` (`JWT_ALGORITHM` through `require_roles`) verbatim, adding the imports they need.

```python
"""Password hashing, JWT issuing, and role-based access dependencies."""
import os
from datetime import datetime, timezone, timedelta
from typing import Literal

import bcrypt
import jwt
from fastapi import HTTPException, Request, status

from db import db

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ.get("JWT_SECRET", "supersecret-key-123456789")

Role = Literal["admin", "manager", "waiter", "kitchen"]
```

Then copy `hash_password`, `verify_password`, `create_access_token`, `get_current_user`, `require_roles` from `server.py` **without editing their bodies**.

- [ ] **Step 4: Verify the app still boots**

```bash
cd ~/dev/bar-management-system/backend
python3 -c "import db, security; print('imports ok')"
```

Expected: `imports ok`

- [ ] **Step 5: Create `backend/routers/__init__.py`**

```python
```

(Empty file.)

- [ ] **Step 6: Create `backend/routers/auth.py`**

Move `UserPublic`, `LoginIn`, `RegisterIn` (lines 104–121) and the four auth endpoints (lines 261–301). Change the decorator from `@api_router.` to `@router.`.

```python
"""Authentication and staff listing."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from db import db
from security import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_roles, Role,
)

router = APIRouter()


class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    role: str
```

Then copy `LoginIn`, `RegisterIn` and the bodies of `login`, `register`, `me`, `list_staff` unchanged, with `@router.post("/auth/login")` etc.

- [ ] **Step 7: Create the remaining six routers the same way**

Same mechanical move for each. Every one starts with `router = APIRouter()` and swaps `@api_router.` for `@router.`. **Move the models each router owns with it.**

| Router | Endpoints (from `server.py`) | Models to move |
|---|---|---|
| `tables.py` | lines 302–397 (tables + reservations) | `TableIn`, `Table`, `ReservationIn`, `StatusIn`, `Reservation` |
| `menu.py` | lines 399–425 | `MenuItemIn`, `MenuItem` |
| `orders.py` | lines 427–537 (incl. `_get_or_create_open_order`, `compute_totals`) | `OrderItemIn`, `OrderItem`, `Order`, `AddItemsIn`, `SettleIn`, `UpdateItemStatusIn` |
| `inventory.py` | lines 538–573 | `InventoryItemIn`, `InventoryItem`, `InventoryAdjustIn` |
| `reports.py` | lines 574–870 (incl. `_money`, `build_daily_brief`, `_send_whatsapp`, `daily_brief_scheduler`) | `BriefSendIn` |
| `payments.py` | lines 872–1027 | `CheckoutStartIn` |

`orders.py` must export `compute_totals` — `payments.py` uses it.
`reports.py` must export `daily_brief_scheduler` — `server.py` startup uses it.

- [ ] **Step 8: Rewrite `backend/server.py` as assembly only**

```python
"""BarFlow API — application assembly."""
from dotenv import load_dotenv
from pathlib import Path
import os
import logging

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import asyncio
from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware

from db import db, client
from routers import auth, tables, menu, orders, inventory, reports, payments

app = FastAPI(title="BarFlow API")
api_router = APIRouter(prefix="/api")

for module in (auth, tables, menu, orders, inventory, reports, payments):
    api_router.include_router(module.router)


@api_router.get("/")
async def root():
    return {"service": "BarFlow API", "status": "ok"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Then append `seed_data`, `on_startup` and `shutdown_db_client` from the original lines 1029–1179 unchanged.

**Order matters:** register `api_router.get("/")` before `app.include_router(api_router)`.

- [ ] **Step 9: Run the guard**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q 2>&1 | tail -3
```

Expected, identical to Step 1: `1 failed, 9 passed, 1 skipped`

If any *other* test fails, the extraction broke something. Fix before continuing.

- [ ] **Step 10: Verify the route table is unchanged**

```bash
cd ~/dev/bar-management-system/backend
MONGO_URL=mock python3 -c "
from server import app
routes = sorted(f'{sorted(r.methods)} {r.path}' for r in app.routes if hasattr(r, 'methods'))
print(len(routes))
print('\n'.join(routes))
" | head -5
```

Expected: first line `40`. Every path still begins `/api/`. The stronger check is that the route table is identical to the pre-refactor one — compare it against the same command run at the previous commit.

- [ ] **Step 11: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/
git commit -m "refactor: split server.py into routers, no behaviour change"
```

---

## Task 2: Add the `front_desk` role

**Files:**
- Modify: `backend/security.py`
- Test: `backend/tests/hotel_api_test.py` (create)

**Interfaces:**
- Consumes: `security.Role`, `security.require_roles` from Task 1
- Produces: `Role` now includes `"front_desk"`; a `front_desk` user exists when `DEMO_LOGINS=true`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/hotel_api_test.py`:

```python
"""Hotel API integration tests. Requires a running server (see backend_test.py)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@barflow.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


def test_front_desk_role_exists(admin):
    r = admin.post("{}/auth/register".format(API), json={
        "email": "desk-test@barflow.io",
        "name": "Desk Tester",
        "password": "desk12345",
        "role": "front_desk",
    })
    assert r.status_code in (200, 400), r.text
    if r.status_code == 400:
        assert "exists" in r.text.lower()
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py::test_front_desk_role_exists -q
```

Expected: FAIL — 422, because `front_desk` is not a valid `Role`.

- [ ] **Step 3: Add the role**

In `backend/security.py`:

```python
Role = Literal["admin", "manager", "waiter", "kitchen", "front_desk"]
```

- [ ] **Step 4: Seed a demo front desk user**

In `backend/server.py`, inside `seed_data`, add to the `DEMO_LOGINS` block:

```python
            {"email": "frontdesk@barflow.io", "name": "Nina Patel", "role": "front_desk", "password": "desk123"},
```

- [ ] **Step 5: Restart and run the test**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/security.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: add front_desk role"
```

---

## Task 3: Pricing service — rate resolution, meal plans, GST slabs

Pure functions. No database, no server. This is the highest-value test surface in the plan.

**Files:**
- Create: `backend/services/__init__.py`, `backend/services/pricing.py`
- Test: `backend/tests/test_pricing.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `daterange(check_in: str, check_out: str) -> list[str]` — half-open list of night dates
  - `resolve_rate(date: str, room_type_id: str, rates: list[dict], periods: list[dict]) -> dict` — raises `MissingRateError`
  - `gst_for(tariff: float, slabs: list[dict]) -> float` — returns the percentage
  - `quote_stay(check_in, check_out, room_type_id, adults, children, base_occupancy, meal_plan, rates, periods, slabs) -> dict` — returns `{"nights": [...], "room_subtotal": float, "tax_total": float, "total": float}`
  - `MissingRateError` (exception, carries `.dates: list[str]`)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pricing.py`:

```python
"""Pure pricing tests — no server, no database."""
import pytest

from services.pricing import (
    daterange, resolve_rate, gst_for, quote_stay, MissingRateError,
)

RT = "type-deluxe"

DEFAULT_RATE = {"room_type_id": RT, "period_id": None,
                "base_rate": 5000.0, "extra_adult_rate": 1000.0, "extra_child_rate": 500.0}
PEAK_RATE = {"room_type_id": RT, "period_id": "peak",
             "base_rate": 9000.0, "extra_adult_rate": 1500.0, "extra_child_rate": 700.0}

PEAK = {"id": "peak", "name": "Peak", "start_date": "2026-12-20", "end_date": "2027-01-05",
        "priority": 10, "active": True}

SLABS = [
    {"min_tariff": 0.0, "max_tariff": 7500.0, "rate_percent": 12.0, "active": True},
    {"min_tariff": 7500.0, "max_tariff": None, "rate_percent": 18.0, "active": True},
]

EP = {"id": "ep", "code": "EP", "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0}
MAP_PLAN = {"id": "map", "code": "MAP", "price_per_adult_per_night": 800.0,
            "price_per_child_per_night": 400.0}


def test_daterange_is_half_open():
    # A 2-night stay arriving the 3rd, leaving the 5th, bills the 3rd and 4th only.
    assert daterange("2026-08-03", "2026-08-05") == ["2026-08-03", "2026-08-04"]


def test_daterange_rejects_zero_nights():
    with pytest.raises(ValueError):
        daterange("2026-08-03", "2026-08-03")


def test_resolve_rate_uses_default_outside_periods():
    r = resolve_rate("2026-08-03", RT, [DEFAULT_RATE, PEAK_RATE], [PEAK])
    assert r["base_rate"] == 5000.0


def test_resolve_rate_uses_period_inside_range():
    r = resolve_rate("2026-12-25", RT, [DEFAULT_RATE, PEAK_RATE], [PEAK])
    assert r["base_rate"] == 9000.0


def test_resolve_rate_period_end_is_exclusive():
    # Peak ends 2027-01-05, so the night of the 5th is back to default.
    r = resolve_rate("2027-01-05", RT, [DEFAULT_RATE, PEAK_RATE], [PEAK])
    assert r["base_rate"] == 5000.0


def test_resolve_rate_raises_when_uncovered():
    with pytest.raises(MissingRateError) as e:
        resolve_rate("2026-08-03", RT, [PEAK_RATE], [PEAK])
    assert e.value.dates == ["2026-08-03"]


def test_gst_slab_boundaries():
    assert gst_for(5000.0, SLABS) == 12.0
    assert gst_for(7500.0, SLABS) == 12.0     # upper bound is inclusive
    assert gst_for(7500.01, SLABS) == 18.0
    assert gst_for(20000.0, SLABS) == 18.0


def test_quote_simple_two_nights_ep():
    q = quote_stay("2026-08-03", "2026-08-05", RT, adults=2, children=0,
                   base_occupancy=2, meal_plan=EP,
                   rates=[DEFAULT_RATE], periods=[], slabs=SLABS)
    assert len(q["nights"]) == 2
    assert q["room_subtotal"] == 10000.0
    assert q["tax_total"] == 1200.0           # 12% of 5000, twice
    assert q["total"] == 11200.0


def test_quote_extra_adult_and_child():
    q = quote_stay("2026-08-03", "2026-08-04", RT, adults=3, children=1,
                   base_occupancy=2, meal_plan=EP,
                   rates=[DEFAULT_RATE], periods=[], slabs=SLABS)
    # 5000 base + 1000 extra adult + 500 child = 6500
    assert q["room_subtotal"] == 6500.0
    assert q["tax_total"] == 780.0            # 12%


def test_quote_meal_plan_is_per_person_per_night():
    q = quote_stay("2026-08-03", "2026-08-04", RT, adults=2, children=1,
                   base_occupancy=2, meal_plan=MAP_PLAN,
                   rates=[DEFAULT_RATE], periods=[], slabs=SLABS)
    # 5000 room + 500 child occupancy + (800*2 + 400*1) meals = 7500
    assert q["room_subtotal"] == 7500.0
    assert q["tax_total"] == 900.0            # 7500 is still the 12% slab


def test_quote_crosses_gst_slab_mid_stay():
    # One default night at 5000 (12%), one peak night at 9000 (18%).
    q = quote_stay("2026-12-19", "2026-12-21", RT, adults=2, children=0,
                   base_occupancy=2, meal_plan=EP,
                   rates=[DEFAULT_RATE, PEAK_RATE], periods=[PEAK], slabs=SLABS)
    assert [n["tariff"] for n in q["nights"]] == [5000.0, 9000.0]
    assert [n["gst_percent"] for n in q["nights"]] == [12.0, 18.0]
    assert q["room_subtotal"] == 14000.0
    assert q["tax_total"] == 600.0 + 1620.0
    assert q["total"] == 16220.0


def test_quote_raises_on_any_uncovered_night():
    with pytest.raises(MissingRateError) as e:
        quote_stay("2026-12-19", "2026-12-21", RT, adults=2, children=0,
                   base_occupancy=2, meal_plan=EP,
                   rates=[PEAK_RATE], periods=[PEAK], slabs=SLABS)
    assert e.value.dates == ["2026-12-19"]
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd ~/dev/bar-management-system/backend
python3 -m pytest tests/test_pricing.py -q
```

Expected: collection error — `No module named 'services'`

- [ ] **Step 3: Create `backend/services/__init__.py`**

```python
```

(Empty file.)

- [ ] **Step 4: Implement `backend/services/pricing.py`**

```python
"""Room pricing: rate resolution, meal plans, and per-night GST.

Pure functions — every input is passed in, nothing is read from a database. All dates are
YYYY-MM-DD strings and every range is half-open [from, to).
"""
from datetime import date, timedelta
from typing import Optional


class MissingRateError(Exception):
    """No rate covers one or more nights. Never price these as zero."""

    def __init__(self, dates: list[str]):
        self.dates = dates
        super().__init__(f"No rate defined for: {', '.join(dates)}")


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def daterange(check_in: str, check_out: str) -> list[str]:
    """Night dates billed for a stay. Half-open: check_out is not billed."""
    start, end = _parse(check_in), _parse(check_out)
    if end <= start:
        raise ValueError("check_out must be after check_in")
    out, cur = [], start
    while cur < end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _period_for(day: str, periods: list[dict]) -> Optional[dict]:
    """Highest-priority active period covering `day`. Ties break on most recent."""
    matches = [
        p for p in periods
        if p.get("active", True) and p["start_date"] <= day < p["end_date"]
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda p: (p.get("priority", 0), p.get("created_at", "")))[-1]


def resolve_rate(day: str, room_type_id: str, rates: list[dict], periods: list[dict]) -> dict:
    """Rate row for one night. Falls back to the default row (period_id None)."""
    period = _period_for(day, periods)
    if period:
        for r in rates:
            if r["room_type_id"] == room_type_id and r.get("period_id") == period["id"]:
                return r
    for r in rates:
        if r["room_type_id"] == room_type_id and r.get("period_id") is None:
            return r
    raise MissingRateError([day])


def gst_for(tariff: float, slabs: list[dict]) -> float:
    """GST percentage for a nightly tariff. Slab upper bounds are inclusive: a ₹7,500
    tariff sits in the 12% band, ₹7,500.01 in the 18% band."""
    for s in sorted(
        (s for s in slabs if s.get("active", True)),
        key=lambda s: s["min_tariff"],
    ):
        upper = s.get("max_tariff")
        if tariff >= s["min_tariff"] and (upper is None or tariff <= upper):
            return float(s["rate_percent"])
    raise ValueError(f"No GST slab covers tariff {tariff}")


def quote_stay(
    check_in: str,
    check_out: str,
    room_type_id: str,
    adults: int,
    children: int,
    base_occupancy: int,
    meal_plan: dict,
    rates: list[dict],
    periods: list[dict],
    slabs: list[dict],
) -> dict:
    """Priced breakdown for a stay. Raises MissingRateError listing every uncovered night."""
    nights = daterange(check_in, check_out)

    uncovered = []
    for day in nights:
        try:
            resolve_rate(day, room_type_id, rates, periods)
        except MissingRateError:
            uncovered.append(day)
    if uncovered:
        raise MissingRateError(uncovered)

    extra_adults = max(0, adults - base_occupancy)
    lines, room_subtotal, tax_total = [], 0.0, 0.0

    for day in nights:
        rate = resolve_rate(day, room_type_id, rates, periods)
        tariff = (
            float(rate["base_rate"])
            + float(rate.get("extra_adult_rate", 0)) * extra_adults
            + float(rate.get("extra_child_rate", 0)) * children
            + float(meal_plan.get("price_per_adult_per_night", 0)) * adults
            + float(meal_plan.get("price_per_child_per_night", 0)) * children
        )
        tariff = round(tariff, 2)
        percent = gst_for(tariff, slabs)
        tax = round(tariff * percent / 100, 2)

        lines.append({
            "date": day,
            "tariff": tariff,
            "gst_percent": percent,
            "gst_amount": tax,
        })
        room_subtotal += tariff
        tax_total += tax

    room_subtotal = round(room_subtotal, 2)
    tax_total = round(tax_total, 2)
    return {
        "nights": lines,
        "room_subtotal": room_subtotal,
        "tax_total": tax_total,
        "total": round(room_subtotal + tax_total, 2),
    }
```

- [ ] **Step 5: Run the tests**

```bash
cd ~/dev/bar-management-system/backend
python3 -m pytest tests/test_pricing.py -q
```

Expected: `12 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/ backend/tests/test_pricing.py
git commit -m "feat: pricing service with seasonal rates, meal plans and GST slabs"
```

---

## Task 4: Availability service

**Files:**
- Create: `backend/services/availability.py`
- Test: `backend/tests/test_availability.py`

**Interfaces:**
- Consumes: `services.pricing.daterange`
- Produces:
  - `ranges_overlap(a_from: str, a_to: str, b_from: str, b_to: str) -> bool`
  - `room_is_available(room: dict, check_in: str, check_out: str) -> bool`
  - `count_available(room_type_id: str, check_in: str, check_out: str, rooms: list[dict], bookings: list[dict]) -> int`
  - `CONSUMING_STATUSES: frozenset[str]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_availability.py`:

```python
"""Pure availability tests — no server, no database."""
from services.availability import (
    ranges_overlap, room_is_available, count_available, CONSUMING_STATUSES,
)

RT = "type-deluxe"


def room(rid, active=True, ooo=None):
    return {"id": rid, "room_type_id": RT, "active": active, "out_of_order": ooo or []}


def booking(check_in, check_out, status="confirmed", rt=RT):
    return {"room_type_id": rt, "check_in": check_in, "check_out": check_out, "status": status}


ROOMS = [room("r1"), room("r2"), room("r3")]


def test_touching_ranges_do_not_overlap():
    # Checkout on the 5th, arrival on the 5th — both fit.
    assert ranges_overlap("2026-08-03", "2026-08-05", "2026-08-05", "2026-08-07") is False


def test_partial_overlap():
    assert ranges_overlap("2026-08-03", "2026-08-06", "2026-08-05", "2026-08-08") is True


def test_fully_contained_overlap():
    assert ranges_overlap("2026-08-01", "2026-08-10", "2026-08-04", "2026-08-06") is True


def test_checkout_day_frees_the_room():
    bookings = [booking("2026-08-03", "2026-08-05")]
    assert count_available(RT, "2026-08-05", "2026-08-07", ROOMS, bookings) == 3


def test_overlapping_booking_consumes_one_room():
    bookings = [booking("2026-08-03", "2026-08-06")]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 2


def test_cancelled_and_no_show_do_not_consume():
    bookings = [
        booking("2026-08-03", "2026-08-06", status="cancelled"),
        booking("2026-08-03", "2026-08-06", status="no_show"),
    ]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 3


def test_tentative_and_checked_in_do_consume():
    assert "tentative" in CONSUMING_STATUSES
    assert "checked_in" in CONSUMING_STATUSES
    bookings = [
        booking("2026-08-03", "2026-08-06", status="tentative"),
        booking("2026-08-03", "2026-08-06", status="checked_in"),
    ]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 1


def test_other_room_types_are_ignored():
    bookings = [booking("2026-08-03", "2026-08-06", rt="type-suite")]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 3


def test_inactive_room_never_counts():
    rooms = [room("r1"), room("r2", active=False)]
    assert count_available(RT, "2026-08-04", "2026-08-05", rooms, []) == 1


def test_out_of_order_removes_room_for_those_dates_only():
    rooms = [room("r1", ooo=[{"from": "2026-08-04", "to": "2026-08-06", "reason": "Repaint"}]), room("r2")]
    assert count_available(RT, "2026-08-04", "2026-08-05", rooms, []) == 1
    # Free again from the 6th — the out-of-order range is half-open.
    assert count_available(RT, "2026-08-06", "2026-08-07", rooms, []) == 2


def test_room_is_available_helper():
    r = room("r1", ooo=[{"from": "2026-08-04", "to": "2026-08-06"}])
    assert room_is_available(r, "2026-08-06", "2026-08-08") is True
    assert room_is_available(r, "2026-08-05", "2026-08-08") is False


def test_never_returns_negative():
    bookings = [booking("2026-08-03", "2026-08-06") for _ in range(5)]
    assert count_available(RT, "2026-08-04", "2026-08-05", ROOMS, bookings) == 0
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd ~/dev/bar-management-system/backend
python3 -m pytest tests/test_availability.py -q
```

Expected: collection error — `No module named 'services.availability'`

- [ ] **Step 3: Implement `backend/services/availability.py`**

```python
"""Room availability arithmetic.

Pure functions over supplied rooms and bookings — no database access, so the rules are
testable in isolation. Every range is half-open [from, to): a stay ending on the 5th and
one starting on the 5th do not conflict.
"""

# Statuses that hold inventory. Cancelled and no-show release it.
CONSUMING_STATUSES = frozenset({"tentative", "confirmed", "checked_in"})


def ranges_overlap(a_from: str, a_to: str, b_from: str, b_to: str) -> bool:
    """True when two half-open date ranges share at least one night."""
    return a_from < b_to and a_to > b_from


def room_is_available(room: dict, check_in: str, check_out: str) -> bool:
    """False when the room is inactive or out of order for any night in the window."""
    if not room.get("active", True):
        return False
    for block in room.get("out_of_order") or []:
        if ranges_overlap(check_in, check_out, block["from"], block["to"]):
            return False
    return True


def count_available(
    room_type_id: str,
    check_in: str,
    check_out: str,
    rooms: list[dict],
    bookings: list[dict],
) -> int:
    """Rooms of a type free for the whole window. Never negative."""
    usable = sum(
        1 for r in rooms
        if r.get("room_type_id") == room_type_id
        and room_is_available(r, check_in, check_out)
    )
    taken = sum(
        1 for b in bookings
        if b.get("room_type_id") == room_type_id
        and b.get("status") in CONSUMING_STATUSES
        and ranges_overlap(check_in, check_out, b["check_in"], b["check_out"])
    )
    return max(0, usable - taken)
```

- [ ] **Step 4: Run the tests**

```bash
cd ~/dev/bar-management-system/backend
python3 -m pytest tests/test_availability.py -q
```

Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/availability.py backend/tests/test_availability.py
git commit -m "feat: availability service with half-open range arithmetic"
```

---

## Task 5: Hotel models and seed data

**Files:**
- Create: `backend/models/__init__.py`, `backend/models/hotel.py`
- Modify: `backend/server.py` (`seed_data`)

**Interfaces:**
- Consumes: nothing
- Produces: `Guest`, `GuestIn`, `RoomType`, `RoomTypeIn`, `Room`, `RoomIn`, `OutOfOrderIn`, `MealPlan`, `MealPlanIn`, `RatePeriod`, `RatePeriodIn`, `Rate`, `RateIn`, `TaxSlab`, `Booking`, `BookingIn`, `BookingUpdateIn`, `CancelIn`, `BookingStatus`

- [ ] **Step 1: Create `backend/models/__init__.py`**

Empty file.

- [ ] **Step 2: Create `backend/models/hotel.py`**

```python
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


class Booking(BookingIn):
    id: str = Field(default_factory=_uuid)
    reference: str
    assigned_room_id: Optional[str] = None
    quote: dict = {}
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=_now)
    cancelled_at: Optional[str] = None
    cancellation_reason: Optional[str] = None
```

- [ ] **Step 3: Seed tax slabs and meal plans**

In `backend/server.py`, inside `seed_data`, after the inventory seeding block:

```python
    # Room GST bands. Editable, because these change by statute.
    if await db.tax_slabs.count_documents({}) == 0:
        await db.tax_slabs.insert_many([
            {"id": str(uuid.uuid4()), "min_tariff": 0.0, "max_tariff": 7500.0,
             "rate_percent": 12.0, "active": True},
            {"id": str(uuid.uuid4()), "min_tariff": 7500.0, "max_tariff": None,
             "rate_percent": 18.0, "active": True},
        ])

    if await db.meal_plans.count_documents({}) == 0:
        await db.meal_plans.insert_many([
            {"id": str(uuid.uuid4()), "code": "EP", "name": "Room only",
             "price_per_adult_per_night": 0.0, "price_per_child_per_night": 0.0, "active": True},
            {"id": str(uuid.uuid4()), "code": "CP", "name": "With breakfast",
             "price_per_adult_per_night": 500.0, "price_per_child_per_night": 250.0, "active": True},
            {"id": str(uuid.uuid4()), "code": "MAP", "name": "Half board",
             "price_per_adult_per_night": 1200.0, "price_per_child_per_night": 600.0, "active": True},
        ])
```

- [ ] **Step 4: Add indexes**

In `seed_data`, alongside the existing `create_index` calls:

```python
    await db.guests.create_index("phone", unique=True)
    await db.bookings.create_index([("room_type_id", 1), ("check_in", 1), ("check_out", 1), ("status", 1)])
    await db.bookings.create_index("reference", unique=True)
    await db.rooms.create_index("room_type_id")
```

- [ ] **Step 5: Restart and verify the seed**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
python3 -c "
import json; d = json.load(open('db.json'))
print('tax_slabs', len(d.get('tax_slabs', [])), '| meal_plans', len(d.get('meal_plans', [])))"
```

Expected: `tax_slabs 2 | meal_plans 3`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/models/ backend/server.py
git commit -m "feat: hotel domain models, tax slab and meal plan seeds"
```

---

## Task 6: Guests router and backfill

**Files:**
- Create: `backend/routers/guests.py`, `backend/migrations/backfill_guests.py`
- Modify: `backend/server.py` (register router)
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `models.hotel.Guest/GuestIn`, `security.require_roles`
- Produces: `GET/POST /api/guests`, `GET/PUT /api/guests/{id}`, `guests.router`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
import uuid


def test_create_and_find_guest(admin):
    phone = f"99{uuid.uuid4().int % 100000000:08d}"
    r = admin.post(f"{API}/guests", json={"name": "Test Guest", "phone": phone})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]

    r2 = admin.get(f"{API}/guests", params={"q": phone})
    assert r2.status_code == 200
    assert any(g["id"] == gid for g in r2.json())


def test_duplicate_phone_returns_409_with_existing_guest(admin):
    phone = f"98{uuid.uuid4().int % 100000000:08d}"
    first = admin.post(f"{API}/guests", json={"name": "First", "phone": phone})
    assert first.status_code == 200

    dup = admin.post(f"{API}/guests", json={"name": "Second", "phone": phone})
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["guest"]["id"] == first.json()["id"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k guest
```

Expected: FAIL — 404, route does not exist.

- [ ] **Step 3: Implement `backend/routers/guests.py`**

```python
"""Guest records. Phone is the identity key across bar, restaurant and rooms."""
from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import Guest, GuestIn
from security import get_current_user, require_roles

router = APIRouter()

MANAGE = require_roles("admin", "manager", "front_desk")


@router.get("/guests")
async def list_guests(q: str = "", limit: int = 50, user: dict = Depends(MANAGE)):
    query = {}
    if q:
        query = {"$or": [
            {"phone": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]}
    return await db.guests.find(query, {"_id": 0}).to_list(limit)


@router.post("/guests")
async def create_guest(payload: GuestIn, user: dict = Depends(MANAGE)):
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(400, "Phone is required")

    existing = await db.guests.find_one({"phone": phone}, {"_id": 0})
    if existing:
        # Return the existing record so the desk can open it instead of retyping.
        raise HTTPException(409, {"message": "A guest with this phone already exists",
                                  "guest": existing})

    guest = Guest(**{**payload.model_dump(), "phone": phone}).model_dump()
    await db.guests.insert_one(guest)
    guest.pop("_id", None)
    return guest


@router.get("/guests/{guest_id}")
async def get_guest(guest_id: str, user: dict = Depends(MANAGE)):
    guest = await db.guests.find_one({"id": guest_id}, {"_id": 0})
    if not guest:
        raise HTTPException(404, "Guest not found")

    stays = await db.bookings.find({"guest_id": guest_id}, {"_id": 0}).to_list(200)
    orders = await db.orders.find(
        {"customer_phone": guest["phone"], "status": "settled"}, {"_id": 0}
    ).to_list(200)

    return {
        **guest,
        "stays": stays,
        "outlet_orders": len(orders),
        "outlet_spend": round(sum(o.get("total", 0) for o in orders), 2),
    }


@router.put("/guests/{guest_id}")
async def update_guest(guest_id: str, payload: GuestIn, user: dict = Depends(MANAGE)):
    clash = await db.guests.find_one({"phone": payload.phone.strip(), "id": {"$ne": guest_id}})
    if clash:
        raise HTTPException(409, "Another guest already uses this phone")

    result = await db.guests.update_one({"id": guest_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Guest not found")
    return await db.guests.find_one({"id": guest_id}, {"_id": 0})
```

- [ ] **Step 4: Register the router**

In `backend/server.py`:

```python
from routers import auth, tables, menu, orders, inventory, reports, payments, guests

for module in (auth, tables, menu, orders, inventory, reports, payments, guests):
    api_router.include_router(module.router)
```

- [ ] **Step 5: Restart and run the tests**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k guest
```

Expected: `2 passed`

- [ ] **Step 6: Write the backfill migration**

Create `backend/migrations/backfill_guests.py`:

```python
"""One-shot: create a guest for every distinct customer_phone in orders.

Idempotent — existing guests are skipped, so re-running is safe. Run once after
deploying the hotel module:

    cd backend && MONGO_URL=... python3 -m migrations.backfill_guests
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import db  # noqa: E402


async def main() -> None:
    orders = await db.orders.find(
        {"customer_phone": {"$nin": [None, ""]}}, {"_id": 0}
    ).to_list(100000)

    # Most recent name wins, so a corrected spelling beats an older typo.
    latest: dict[str, dict] = {}
    for o in sorted(orders, key=lambda o: o.get("created_at") or ""):
        phone = (o.get("customer_phone") or "").strip()
        if phone:
            latest[phone] = o

    created = skipped = 0
    for phone, order in latest.items():
        if await db.guests.find_one({"phone": phone}):
            skipped += 1
            continue
        await db.guests.insert_one({
            "id": str(uuid.uuid4()),
            "name": (order.get("customer_name") or "Guest").strip(),
            "phone": phone,
            "email": None, "address": None, "nationality": None,
            "id_proof_type": None, "id_proof_number": None,
            "notes": "Imported from outlet order history",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        created += 1

    print(f"guests created: {created}, already present: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Run the backfill and verify**

```bash
cd ~/dev/bar-management-system/backend
touch migrations/__init__.py
MONGO_URL=mock python3 -m migrations.backfill_guests
MONGO_URL=mock python3 -m migrations.backfill_guests
```

Expected: first run reports some created; **second run reports `guests created: 0`** — proving idempotency.

- [ ] **Step 8: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/guests.py backend/migrations/ backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: guests router with phone identity and order-history backfill"
```

---

## Task 7: Rooms and room types router

**Files:**
- Create: `backend/routers/rooms.py`
- Modify: `backend/server.py`
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `models.hotel.RoomType/RoomTypeIn/Room/RoomIn/OutOfOrderIn`
- Produces: `GET/POST /api/room-types`, `PUT/DELETE /api/room-types/{id}`, `GET/POST /api/rooms`, `PUT/DELETE /api/rooms/{id}`, `POST /api/rooms/{id}/out-of-order`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
@pytest.fixture(scope="module")
def room_type(admin):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    r = admin.post(f"{API}/room-types", json={
        "name": "Deluxe Test", "code": code,
        "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_create_room_and_list(admin, room_type):
    number = f"R{uuid.uuid4().hex[:5]}"
    r = admin.post(f"{API}/rooms", json={"number": number, "room_type_id": room_type["id"]})
    assert r.status_code == 200, r.text

    listing = admin.get(f"{API}/rooms")
    assert any(x["number"] == number for x in listing.json())


def test_delete_room_type_with_rooms_is_blocked(admin, room_type):
    r = admin.delete(f"{API}/room-types/{room_type['id']}")
    assert r.status_code == 409, r.text
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k room
```

Expected: FAIL — 404.

- [ ] **Step 3: Implement `backend/routers/rooms.py`**

```python
"""Room types and the physical rooms belonging to them."""
from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import OutOfOrderIn, Room, RoomIn, RoomType, RoomTypeIn
from security import get_current_user, require_roles

router = APIRouter()

MANAGE = require_roles("admin", "manager")

# Statuses that mean a booking still matters when deleting inventory.
LIVE_STATUSES = ["tentative", "confirmed", "checked_in"]


@router.get("/room-types")
async def list_room_types(user: dict = Depends(get_current_user)):
    return await db.room_types.find({}, {"_id": 0}).to_list(200)


@router.post("/room-types")
async def create_room_type(payload: RoomTypeIn, user: dict = Depends(MANAGE)):
    if payload.max_occupancy < payload.base_occupancy:
        raise HTTPException(400, "max_occupancy cannot be below base_occupancy")
    rt = RoomType(**payload.model_dump()).model_dump()
    await db.room_types.insert_one(rt)
    rt.pop("_id", None)
    return rt


@router.put("/room-types/{type_id}")
async def update_room_type(type_id: str, payload: RoomTypeIn, user: dict = Depends(MANAGE)):
    if payload.max_occupancy < payload.base_occupancy:
        raise HTTPException(400, "max_occupancy cannot be below base_occupancy")
    result = await db.room_types.update_one({"id": type_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Room type not found")
    return await db.room_types.find_one({"id": type_id}, {"_id": 0})


@router.delete("/room-types/{type_id}")
async def delete_room_type(type_id: str, user: dict = Depends(MANAGE)):
    rooms = await db.rooms.find({"room_type_id": type_id}, {"_id": 0}).to_list(500)
    if rooms:
        raise HTTPException(409, {
            "message": "Room type still has rooms",
            "rooms": [r["number"] for r in rooms],
        })

    live = await db.bookings.find(
        {"room_type_id": type_id, "status": {"$in": LIVE_STATUSES}}, {"_id": 0}
    ).to_list(50)
    if live:
        raise HTTPException(409, {
            "message": "Room type has live bookings",
            "bookings": [b["reference"] for b in live],
        })

    await db.room_types.delete_one({"id": type_id})
    return {"ok": True}


@router.get("/rooms")
async def list_rooms(user: dict = Depends(get_current_user)):
    return await db.rooms.find({}, {"_id": 0}).to_list(500)


@router.post("/rooms")
async def create_room(payload: RoomIn, user: dict = Depends(MANAGE)):
    if not await db.room_types.find_one({"id": payload.room_type_id}):
        raise HTTPException(400, "Unknown room_type_id")
    if await db.rooms.find_one({"number": payload.number}):
        raise HTTPException(409, "A room with this number already exists")

    room = Room(**payload.model_dump()).model_dump()
    await db.rooms.insert_one(room)
    room.pop("_id", None)
    return room


@router.put("/rooms/{room_id}")
async def update_room(room_id: str, payload: RoomIn, user: dict = Depends(MANAGE)):
    result = await db.rooms.update_one({"id": room_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Room not found")
    return await db.rooms.find_one({"id": room_id}, {"_id": 0})


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: str, user: dict = Depends(MANAGE)):
    room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if room.get("assigned_booking_id"):
        raise HTTPException(409, "Room is currently assigned to a booking")
    await db.rooms.delete_one({"id": room_id})
    return {"ok": True}


@router.post("/rooms/{room_id}/out-of-order")
async def mark_out_of_order(room_id: str, payload: OutOfOrderIn, user: dict = Depends(MANAGE)):
    """Block a room for a half-open date range. Warns if it drops availability below
    existing live bookings, but never cancels anything."""
    room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room not found")
    if payload.to_date <= payload.from_date:
        raise HTTPException(400, "'to' must be after 'from'")

    block = {"from": payload.from_date, "to": payload.to_date, "reason": payload.reason}
    await db.rooms.update_one({"id": room_id}, {"$push": {"out_of_order": block}})

    from services.availability import count_available

    rooms = await db.rooms.find({"room_type_id": room["room_type_id"]}, {"_id": 0}).to_list(500)
    bookings = await db.bookings.find(
        {"room_type_id": room["room_type_id"], "status": {"$in": LIVE_STATUSES}}, {"_id": 0}
    ).to_list(1000)

    remaining = count_available(
        room["room_type_id"], payload.from_date, payload.to_date, rooms, bookings
    )
    return {
        "ok": True,
        "room": await db.rooms.find_one({"id": room_id}, {"_id": 0}),
        "warning": (
            f"Availability for these dates is now {remaining}. Existing bookings were "
            "not cancelled — move or reassign them."
        ) if remaining < 0 or remaining == 0 else None,
    }
```

- [ ] **Step 4: Register the router**

In `backend/server.py`, add `rooms` to both the import and the loop.

- [ ] **Step 5: Restart and run**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k room
```

Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/rooms.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: room types and rooms with out-of-order blocking"
```

---

## Task 8: Rates router

**Files:**
- Create: `backend/routers/rates.py`
- Modify: `backend/server.py`
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `models.hotel.MealPlan/MealPlanIn/RatePeriod/RatePeriodIn/Rate/RateIn/TaxSlab`
- Produces: `GET/POST/PUT/DELETE` for `/api/meal-plans`, `/api/rate-periods`, `/api/rates`; `GET/PUT /api/tax-slabs`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_seeded_meal_plans_and_tax_slabs(admin):
    plans = admin.get(f"{API}/meal-plans")
    assert plans.status_code == 200, plans.text
    assert {p["code"] for p in plans.json()} >= {"EP", "CP", "MAP"}

    slabs = admin.get(f"{API}/tax-slabs")
    assert slabs.status_code == 200
    assert len(slabs.json()) >= 2


def test_create_rate_for_room_type(admin, room_type):
    r = admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": None,
        "base_rate": 5000.0, "extra_adult_rate": 1000.0, "extra_child_rate": 500.0,
    })
    assert r.status_code == 200, r.text
    assert r.json()["base_rate"] == 5000.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "meal or rate"
```

Expected: FAIL — 404.

- [ ] **Step 3: Implement `backend/routers/rates.py`**

```python
"""Meal plans, seasonal rate periods, per-type rates, and GST slabs."""
from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import (
    MealPlan, MealPlanIn, Rate, RateIn, RatePeriod, RatePeriodIn, TaxSlab,
)
from security import get_current_user, require_roles

router = APIRouter()

MANAGE = require_roles("admin", "manager")


# --------------------------- meal plans ---------------------------
@router.get("/meal-plans")
async def list_meal_plans(user: dict = Depends(get_current_user)):
    return await db.meal_plans.find({}, {"_id": 0}).to_list(50)


@router.post("/meal-plans")
async def create_meal_plan(payload: MealPlanIn, user: dict = Depends(MANAGE)):
    plan = MealPlan(**payload.model_dump()).model_dump()
    await db.meal_plans.insert_one(plan)
    plan.pop("_id", None)
    return plan


@router.put("/meal-plans/{plan_id}")
async def update_meal_plan(plan_id: str, payload: MealPlanIn, user: dict = Depends(MANAGE)):
    result = await db.meal_plans.update_one({"id": plan_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Meal plan not found")
    return await db.meal_plans.find_one({"id": plan_id}, {"_id": 0})


# -------------------------- rate periods --------------------------
@router.get("/rate-periods")
async def list_rate_periods(user: dict = Depends(get_current_user)):
    return await db.rate_periods.find({}, {"_id": 0}).to_list(200)


@router.post("/rate-periods")
async def create_rate_period(payload: RatePeriodIn, user: dict = Depends(MANAGE)):
    if payload.end_date <= payload.start_date:
        raise HTTPException(400, "end_date must be after start_date")

    period = RatePeriod(**payload.model_dump()).model_dump()
    await db.rate_periods.insert_one(period)
    period.pop("_id", None)

    # Overlaps are legal — priority decides — but the desk should know.
    others = await db.rate_periods.find(
        {"id": {"$ne": period["id"]}, "active": True}, {"_id": 0}
    ).to_list(200)
    clashes = [
        p["name"] for p in others
        if p["start_date"] < period["end_date"] and p["end_date"] > period["start_date"]
        and p.get("priority", 0) == period.get("priority", 0)
    ]
    return {**period, "overlap_warning": clashes or None}


@router.put("/rate-periods/{period_id}")
async def update_rate_period(period_id: str, payload: RatePeriodIn, user: dict = Depends(MANAGE)):
    if payload.end_date <= payload.start_date:
        raise HTTPException(400, "end_date must be after start_date")
    result = await db.rate_periods.update_one({"id": period_id}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Rate period not found")
    return await db.rate_periods.find_one({"id": period_id}, {"_id": 0})


@router.delete("/rate-periods/{period_id}")
async def delete_rate_period(period_id: str, user: dict = Depends(MANAGE)):
    await db.rates.delete_many({"period_id": period_id})
    await db.rate_periods.delete_one({"id": period_id})
    return {"ok": True}


# ------------------------------ rates -----------------------------
@router.get("/rates")
async def list_rates(user: dict = Depends(get_current_user)):
    return await db.rates.find({}, {"_id": 0}).to_list(500)


@router.post("/rates")
async def create_rate(payload: RateIn, user: dict = Depends(MANAGE)):
    if not await db.room_types.find_one({"id": payload.room_type_id}):
        raise HTTPException(400, "Unknown room_type_id")
    if payload.period_id and not await db.rate_periods.find_one({"id": payload.period_id}):
        raise HTTPException(400, "Unknown period_id")

    existing = await db.rates.find_one({
        "room_type_id": payload.room_type_id, "period_id": payload.period_id
    })
    if existing:
        await db.rates.update_one({"id": existing["id"]}, {"$set": payload.model_dump()})
        return await db.rates.find_one({"id": existing["id"]}, {"_id": 0})

    rate = Rate(**payload.model_dump()).model_dump()
    await db.rates.insert_one(rate)
    rate.pop("_id", None)
    return rate


@router.delete("/rates/{rate_id}")
async def delete_rate(rate_id: str, user: dict = Depends(MANAGE)):
    await db.rates.delete_one({"id": rate_id})
    return {"ok": True}


# ---------------------------- tax slabs ---------------------------
@router.get("/tax-slabs")
async def list_tax_slabs(user: dict = Depends(get_current_user)):
    return await db.tax_slabs.find({}, {"_id": 0}).to_list(20)


@router.put("/tax-slabs")
async def replace_tax_slabs(slabs: list[TaxSlab], user: dict = Depends(MANAGE)):
    """Replace the whole band table. Statutory rates change; they are not hardcoded."""
    if not slabs:
        raise HTTPException(400, "At least one slab is required")
    await db.tax_slabs.delete_many({})
    await db.tax_slabs.insert_many([s.model_dump() for s in slabs])
    return await db.tax_slabs.find({}, {"_id": 0}).to_list(20)
```

- [ ] **Step 4: Register the router**

In `backend/server.py`, add `rates` to the import and the loop.

- [ ] **Step 5: Restart and run**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "meal or rate"
```

Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/rates.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: meal plans, rate periods, rates and editable GST slabs"
```

---

## Task 9: Availability endpoint and booking creation

The heart of the feature.

**Files:**
- Create: `backend/routers/bookings.py`
- Modify: `backend/server.py`
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `services.availability.count_available`, `services.pricing.quote_stay`, `services.pricing.MissingRateError`, `models.hotel.Booking/BookingIn/BookingUpdateIn/CancelIn`
- Produces: `GET /api/availability`, `GET/POST /api/bookings`, `GET/PUT /api/bookings/{id}`, `POST /api/bookings/{id}/cancel`, `GET /api/bookings/calendar`, and the helper `_load_pricing_context() -> tuple[list, list, list]`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
@pytest.fixture(scope="module")
def priced_type(admin, room_type):
    """A room type with two rooms and a default rate."""
    for _ in range(2):
        admin.post(f"{API}/rooms", json={
            "number": f"R{uuid.uuid4().hex[:5]}", "room_type_id": room_type["id"]})
    admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": None,
        "base_rate": 5000.0, "extra_adult_rate": 1000.0, "extra_child_rate": 500.0})
    return room_type


@pytest.fixture(scope="module")
def ep_plan(admin):
    plans = admin.get(f"{API}/meal-plans").json()
    return next(p for p in plans if p["code"] == "EP")


@pytest.fixture(scope="module")
def a_guest(admin):
    phone = f"97{uuid.uuid4().int % 100000000:08d}"
    return admin.post(f"{API}/guests", json={"name": "Booking Guest", "phone": phone}).json()


def test_availability_returns_priced_quote(admin, priced_type, ep_plan):
    r = admin.get(f"{API}/availability", params={
        "check_in": "2027-03-01", "check_out": "2027-03-03", "adults": 2, "children": 0})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["room_type"]["id"] == priced_type["id"])
    assert row["available"] == 2
    quote = next(q for q in row["quotes"] if q["meal_plan"]["code"] == "EP")
    assert quote["room_subtotal"] == 10000.0
    assert quote["tax_total"] == 1200.0
    assert quote["total"] == 11200.0


def test_create_booking_consumes_inventory(admin, priced_type, ep_plan, a_guest):
    body = {
        "guest_id": a_guest["id"], "room_type_id": priced_type["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-04-01",
        "check_out": "2027-04-03", "adults": 2, "children": 0,
    }
    r = admin.post(f"{API}/bookings", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["quote"]["total"] == 11200.0
    assert r.json()["reference"].startswith("BF-")

    avail = admin.get(f"{API}/availability", params={
        "check_in": "2027-04-01", "check_out": "2027-04-03", "adults": 2, "children": 0})
    row = next(x for x in avail.json() if x["room_type"]["id"] == priced_type["id"])
    assert row["available"] == 1


def test_checkout_day_does_not_block_next_arrival(admin, priced_type, ep_plan, a_guest):
    # Existing stay is 2027-04-01 → 2027-04-03, so the 3rd must be fully free.
    avail = admin.get(f"{API}/availability", params={
        "check_in": "2027-04-03", "check_out": "2027-04-04", "adults": 2, "children": 0})
    row = next(x for x in avail.json() if x["room_type"]["id"] == priced_type["id"])
    assert row["available"] == 2


def test_overbooking_is_refused(admin, priced_type, ep_plan, a_guest):
    body = {
        "guest_id": a_guest["id"], "room_type_id": priced_type["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-05-01",
        "check_out": "2027-05-02", "adults": 2, "children": 0,
    }
    assert admin.post(f"{API}/bookings", json=body).status_code == 200
    assert admin.post(f"{API}/bookings", json=body).status_code == 200
    third = admin.post(f"{API}/bookings", json=body)
    assert third.status_code == 409, third.text


def test_missing_rate_refuses_rather_than_pricing_zero(admin, room_type, ep_plan, a_guest):
    bare = admin.post(f"{API}/room-types", json={
        "name": "Unpriced", "code": f"U{uuid.uuid4().hex[:6].upper()}"}).json()
    admin.post(f"{API}/rooms", json={"number": f"R{uuid.uuid4().hex[:5]}",
                                     "room_type_id": bare["id"]})
    r = admin.post(f"{API}/bookings", json={
        "guest_id": a_guest["id"], "room_type_id": bare["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-06-01",
        "check_out": "2027-06-02", "adults": 2, "children": 0})
    assert r.status_code == 422, r.text
    assert "2027-06-01" in str(r.json()["detail"])


def test_checkout_before_checkin_is_rejected(admin, priced_type, ep_plan, a_guest):
    r = admin.post(f"{API}/bookings", json={
        "guest_id": a_guest["id"], "room_type_id": priced_type["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-07-05",
        "check_out": "2027-07-05", "adults": 2, "children": 0})
    assert r.status_code == 400, r.text


def test_occupancy_above_ceiling_is_rejected(admin, priced_type, ep_plan, a_guest):
    # base 2, max_occupancy 3, max_extra_beds 1 → ceiling of 4
    r = admin.post(f"{API}/bookings", json={
        "guest_id": a_guest["id"], "room_type_id": priced_type["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-08-01",
        "check_out": "2027-08-02", "adults": 9, "children": 0})
    assert r.status_code == 400, r.text


def test_cancel_releases_inventory(admin, priced_type, ep_plan, a_guest):
    body = {
        "guest_id": a_guest["id"], "room_type_id": priced_type["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-09-01",
        "check_out": "2027-09-02", "adults": 2, "children": 0,
    }
    booking = admin.post(f"{API}/bookings", json=body).json()

    before = admin.get(f"{API}/availability", params={
        "check_in": "2027-09-01", "check_out": "2027-09-02", "adults": 2, "children": 0})
    assert next(x for x in before.json()
                if x["room_type"]["id"] == priced_type["id"])["available"] == 1

    assert admin.post(f"{API}/bookings/{booking['id']}/cancel",
                      json={"reason": "Guest called"}).status_code == 200

    after = admin.get(f"{API}/availability", params={
        "check_in": "2027-09-01", "check_out": "2027-09-02", "adults": 2, "children": 0})
    assert next(x for x in after.json()
                if x["room_type"]["id"] == priced_type["id"])["available"] == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "availability or booking or cancel or occupancy or checkout"
```

Expected: FAIL — 404.

- [ ] **Step 3: Implement `backend/routers/bookings.py`**

```python
"""Availability search and room bookings.

Availability is an indexed overlap query, not a maintained ledger — see the spec for why,
and for the documented double-booking window this design accepts.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import db
from models.hotel import Booking, BookingIn, BookingUpdateIn, CancelIn
from security import get_current_user, require_roles
from services.availability import CONSUMING_STATUSES, count_available
from services.pricing import MissingRateError, quote_stay

router = APIRouter()

BOOK = require_roles("admin", "manager", "front_desk")
LIVE = list(CONSUMING_STATUSES)


async def _load_pricing_context():
    """Rates, periods and tax slabs — everything quote_stay needs."""
    rates = await db.rates.find({}, {"_id": 0}).to_list(500)
    periods = await db.rate_periods.find({"active": True}, {"_id": 0}).to_list(200)
    slabs = await db.tax_slabs.find({"active": True}, {"_id": 0}).to_list(20)
    return rates, periods, slabs


async def _reference() -> str:
    """Human-quotable code, e.g. BF-2608-0042."""
    stamp = datetime.now(timezone.utc).strftime("%y%m")
    count = await db.bookings.count_documents({})
    return f"BF-{stamp}-{count + 1:04d}"


def _validate_window(check_in: str, check_out: str) -> None:
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")


def _validate_occupancy(room_type: dict, adults: int, children: int, extra_beds: int) -> None:
    ceiling = room_type.get("max_occupancy", 2) + room_type.get("max_extra_beds", 0)
    if adults + children > ceiling:
        raise HTTPException(
            400,
            f"{room_type['name']} sleeps at most {ceiling} "
            f"({room_type.get('max_occupancy')} plus {room_type.get('max_extra_beds')} extra beds)",
        )
    if adults < 1:
        raise HTTPException(400, "At least one adult is required")


async def _quote_or_422(room_type: dict, check_in: str, check_out: str,
                        adults: int, children: int, meal_plan: dict) -> dict:
    rates, periods, slabs = await _load_pricing_context()
    try:
        return quote_stay(
            check_in, check_out, room_type["id"], adults, children,
            room_type.get("base_occupancy", 2), meal_plan, rates, periods, slabs,
        )
    except MissingRateError as e:
        raise HTTPException(422, {
            "message": f"No rate is defined for {room_type['name']} on these dates",
            "dates": e.dates,
        })


@router.get("/availability")
async def availability(check_in: str, check_out: str, adults: int = 2, children: int = 0,
                       user: dict = Depends(BOOK)):
    """Free rooms and a priced quote per room type per meal plan."""
    _validate_window(check_in, check_out)

    room_types = await db.room_types.find({"active": True}, {"_id": 0}).to_list(200)
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(500)
    bookings = await db.bookings.find({"status": {"$in": LIVE}}, {"_id": 0}).to_list(5000)
    meal_plans = await db.meal_plans.find({"active": True}, {"_id": 0}).to_list(50)
    rates, periods, slabs = await _load_pricing_context()

    results = []
    for rt in room_types:
        free = count_available(rt["id"], check_in, check_out, rooms, bookings)

        quotes, unpriced = [], None
        for plan in meal_plans:
            try:
                q = quote_stay(check_in, check_out, rt["id"], adults, children,
                               rt.get("base_occupancy", 2), plan, rates, periods, slabs)
                quotes.append({**q, "meal_plan": plan})
            except MissingRateError as e:
                unpriced = e.dates
                break

        ceiling = rt.get("max_occupancy", 2) + rt.get("max_extra_beds", 0)
        results.append({
            "room_type": rt,
            "available": free,
            "quotes": quotes,
            "unpriced_dates": unpriced,
            "fits_party": adults + children <= ceiling,
        })
    return results


@router.get("/bookings/calendar")
async def calendar(start: str, end: str, user: dict = Depends(BOOK)):
    """Per-room-type occupancy for each night in the window."""
    _validate_window(start, end)

    from services.pricing import daterange

    room_types = await db.room_types.find({"active": True}, {"_id": 0}).to_list(200)
    rooms = await db.rooms.find({}, {"_id": 0}).to_list(500)
    bookings = await db.bookings.find({"status": {"$in": LIVE}}, {"_id": 0}).to_list(5000)

    grid = []
    for rt in room_types:
        nights = []
        for day in daterange(start, end):
            free = count_available(rt["id"], day, _next_day(day), rooms, bookings)
            total = sum(1 for r in rooms if r["room_type_id"] == rt["id"] and r.get("active", True))
            nights.append({"date": day, "available": free, "total": total,
                           "occupied": max(0, total - free)})
        grid.append({"room_type": rt, "nights": nights})
    return grid


def _next_day(day: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


@router.get("/bookings")
async def list_bookings(start: str = "", end: str = "", status: str = "", q: str = "",
                        user: dict = Depends(BOOK)):
    query: dict = {}
    if status:
        query["status"] = status
    if start:
        query["check_out"] = {"$gt": start}
    if end:
        query["check_in"] = {"$lt": end}

    rows = await db.bookings.find(query, {"_id": 0}).to_list(2000)

    guests = {g["id"]: g for g in await db.guests.find({}, {"_id": 0}).to_list(5000)}
    for b in rows:
        b["guest"] = guests.get(b["guest_id"])

    if q:
        needle = q.lower()
        rows = [
            b for b in rows
            if needle in (b.get("reference") or "").lower()
            or needle in ((b.get("guest") or {}).get("name") or "").lower()
            or needle in ((b.get("guest") or {}).get("phone") or "")
        ]
    return sorted(rows, key=lambda b: b["check_in"])


@router.post("/bookings")
async def create_booking(payload: BookingIn, user: dict = Depends(BOOK)):
    _validate_window(payload.check_in, payload.check_out)

    room_type = await db.room_types.find_one({"id": payload.room_type_id}, {"_id": 0})
    if not room_type:
        raise HTTPException(400, "Unknown room_type_id")
    if not await db.guests.find_one({"id": payload.guest_id}):
        raise HTTPException(400, "Unknown guest_id")
    meal_plan = await db.meal_plans.find_one({"id": payload.meal_plan_id}, {"_id": 0})
    if not meal_plan:
        raise HTTPException(400, "Unknown meal_plan_id")

    _validate_occupancy(room_type, payload.adults, payload.children, payload.extra_beds)
    quote = await _quote_or_422(room_type, payload.check_in, payload.check_out,
                                payload.adults, payload.children, meal_plan)

    # Re-checked here, immediately before the write. The spec documents the residual
    # race: without transactions this narrows the window but does not close it.
    rooms = await db.rooms.find({"room_type_id": room_type["id"]}, {"_id": 0}).to_list(500)
    live = await db.bookings.find(
        {"room_type_id": room_type["id"], "status": {"$in": LIVE}}, {"_id": 0}
    ).to_list(5000)
    if count_available(room_type["id"], payload.check_in, payload.check_out, rooms, live) < 1:
        raise HTTPException(409, {
            "message": f"No {room_type['name']} free for these dates",
            "check_in": payload.check_in, "check_out": payload.check_out,
        })

    booking = Booking(
        **payload.model_dump(),
        reference=await _reference(),
        quote=quote,
        created_by=user.get("id"),
    ).model_dump()
    await db.bookings.insert_one(booking)
    booking.pop("_id", None)
    return booking


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: str, user: dict = Depends(BOOK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    booking["guest"] = await db.guests.find_one({"id": booking["guest_id"]}, {"_id": 0})
    booking["room_type"] = await db.room_types.find_one(
        {"id": booking["room_type_id"]}, {"_id": 0})
    booking["meal_plan"] = await db.meal_plans.find_one(
        {"id": booking["meal_plan_id"]}, {"_id": 0})
    return booking


@router.put("/bookings/{booking_id}")
async def update_booking(booking_id: str, payload: BookingUpdateIn, user: dict = Depends(BOOK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] in ("cancelled", "checked_out", "no_show"):
        raise HTTPException(409, f"A {booking['status']} booking cannot be edited")

    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not changes:
        return booking

    merged = {**booking, **changes}
    _validate_window(merged["check_in"], merged["check_out"])

    room_type = await db.room_types.find_one({"id": merged["room_type_id"]}, {"_id": 0})
    meal_plan = await db.meal_plans.find_one({"id": merged["meal_plan_id"]}, {"_id": 0})
    _validate_occupancy(room_type, merged["adults"], merged["children"], merged["extra_beds"])

    repricing = any(k in changes for k in
                    ("check_in", "check_out", "adults", "children", "meal_plan_id"))
    if repricing:
        rooms = await db.rooms.find({"room_type_id": room_type["id"]}, {"_id": 0}).to_list(500)
        live = await db.bookings.find({
            "room_type_id": room_type["id"], "status": {"$in": LIVE},
            "id": {"$ne": booking_id},
        }, {"_id": 0}).to_list(5000)

        if count_available(room_type["id"], merged["check_in"],
                           merged["check_out"], rooms, live) < 1:
            raise HTTPException(409, {
                "message": "Those dates are full — the booking was not changed",
                "check_in": merged["check_in"], "check_out": merged["check_out"],
            })

        changes["quote"] = await _quote_or_422(
            room_type, merged["check_in"], merged["check_out"],
            merged["adults"], merged["children"], meal_plan)

    await db.bookings.update_one({"id": booking_id}, {"$set": changes})
    return await db.bookings.find_one({"id": booking_id}, {"_id": 0})


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, payload: CancelIn, user: dict = Depends(BOOK)):
    booking = await db.bookings.find_one({"id": booking_id}, {"_id": 0})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] == "checked_in":
        raise HTTPException(409, "Check the guest out instead of cancelling")
    if booking["status"] == "cancelled":
        return booking

    await db.bookings.update_one({"id": booking_id}, {"$set": {
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "cancellation_reason": payload.reason,
    }})
    return await db.bookings.find_one({"id": booking_id}, {"_id": 0})
```

- [ ] **Step 4: Register the router**

In `backend/server.py`, add `bookings` to the import and the loop.

**Route order matters:** `/bookings/calendar` is declared before `/bookings/{booking_id}` in the file above, so `calendar` is not swallowed as an id. Keep that order.

- [ ] **Step 5: Restart and run**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
```

Expected: all tests pass, including the eight booking tests.

- [ ] **Step 6: Confirm the whole backend suite still matches baseline**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/ -q 2>&1 | tail -3
```

Expected: the pre-existing Stripe failure and its skip, everything else passing.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/bookings.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: availability search and room bookings"
```

---

## Task 10: Frontend routing, nav and currency fix

**Files:**
- Modify: `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx`, `frontend/src/lib/api.js`
- Create: `frontend/src/pages/hotel/Rooms.jsx` (placeholder-free stub that already lists room types)

**Interfaces:**
- Consumes: `GET /api/room-types` from Task 7
- Produces: routes `/app/hotel/rooms`, `/app/hotel/rates`, `/app/hotel/bookings`, `/app/hotel/bookings/new`, `/app/hotel/bookings/:id`, `/app/hotel/calendar`, `/app/hotel/guests`

- [ ] **Step 1: Fix the currency symbol**

`frontend/src/lib/api.js` renders `$` while the backend brief renders `₹`, so Reports currently shows `$788.70` beside `₹789`.

```javascript
export function currency(v) {
  return `₹${Number(v || 0).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
```

- [ ] **Step 2: Verify the fix in the running app**

```bash
cd ~/dev/bar-management-system/frontend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 BROWSER=none PORT=3001 npx craco start
```

Open `http://127.0.0.1:3001/app/reports`. The KPI cards must read `₹`, matching the WhatsApp brief beside them. Stop the server with Ctrl-C when confirmed.

- [ ] **Step 3: Create `frontend/src/pages/hotel/Rooms.jsx`**

```jsx
import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function Rooms() {
  const [types, setTypes] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = () =>
    Promise.all([api.get("/room-types"), api.get("/rooms")])
      .then(([t, r]) => {
        setTypes(t.data);
        setRooms(r.data);
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  if (loading) return <div className="p-6 md:p-10 text-stone-400">Loading rooms…</div>;

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Rooms
      </h1>

      {types.length === 0 ? (
        <p className="text-stone-400">
          No room types yet. Add one to start taking bookings.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {types.map((t) => {
            const count = rooms.filter((r) => r.room_type_id === t.id).length;
            return (
              <div key={t.id} className="border border-stone-800 bg-stone-900 rounded p-5">
                <div className="flex items-baseline justify-between">
                  <h3 className="text-lg font-semibold">{t.name}</h3>
                  <span className="text-xs font-mono text-stone-500">{t.code}</span>
                </div>
                <p className="text-sm text-stone-400 mt-2">
                  Sleeps {t.base_occupancy}, up to {t.max_occupancy}
                  {t.max_extra_beds ? ` plus ${t.max_extra_beds} extra bed` : ""}
                </p>
                <p className="text-sm text-orange-400 mt-3 font-mono">
                  {count} room{count === 1 ? "" : "s"}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add the hotel routes**

In `frontend/src/App.js`, inside the `AppShell` `<Routes>` block, after the existing `/reports` route:

```jsx
        <Route path="/hotel/rooms" element={<Protected roles={["admin", "manager"]}><Rooms /></Protected>} />
```

And add the import beside the other page imports:

```jsx
import Rooms from "@/pages/hotel/Rooms";
```

- [ ] **Step 5: Add the HOTEL nav group**

In `frontend/src/components/app/AppLayout.jsx`, find the array of nav items and append, following the existing item shape exactly (read the file first — it defines the icon and label conventions):

```jsx
  { section: "Hotel" },
  { to: "/app/hotel/rooms", label: "Rooms", roles: ["admin", "manager"] },
```

If the existing nav has no `section` concept, render a plain label row above the hotel entries instead — do not restructure the component.

- [ ] **Step 6: Verify**

Restart the frontend, sign in as admin, and confirm the HOTEL group appears and `/app/hotel/rooms` renders the seeded room types (or the empty-state message).

- [ ] **Step 7: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: hotel nav group, rooms screen, and rupee currency formatting"
```

---

## Task 11: New Booking screen

**Files:**
- Create: `frontend/src/pages/hotel/NewBooking.jsx`
- Modify: `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx`

**Interfaces:**
- Consumes: `GET /api/availability`, `GET/POST /api/guests`, `POST /api/bookings`
- Produces: route `/app/hotel/bookings/new`

- [ ] **Step 1: Create the screen**

```jsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const today = () => new Date().toISOString().slice(0, 10);
const tomorrow = () =>
  new Date(Date.now() + 86400000).toISOString().slice(0, 10);

export default function NewBooking() {
  const nav = useNavigate();
  const [form, setForm] = useState({
    check_in: today(),
    check_out: tomorrow(),
    adults: 2,
    children: 0,
  });
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [choice, setChoice] = useState(null); // { room_type, quote }
  const [guest, setGuest] = useState({ name: "", phone: "" });
  const [saving, setSaving] = useState(false);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const search = async () => {
    if (form.check_out <= form.check_in) {
      toast.error("Check-out must be after check-in");
      return;
    }
    setSearching(true);
    setChoice(null);
    try {
      const { data } = await api.get("/availability", { params: form });
      setResults(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSearching(false);
    }
  };

  const book = async () => {
    if (!guest.name.trim() || !guest.phone.trim()) {
      toast.error("Guest name and phone are required");
      return;
    }
    setSaving(true);
    try {
      // Reuse the guest if the phone is already known, rather than failing on 409.
      let guestId;
      try {
        const created = await api.post("/guests", guest);
        guestId = created.data.id;
      } catch (e) {
        const existing = e.response?.data?.detail?.guest;
        if (e.response?.status === 409 && existing) {
          guestId = existing.id;
          toast.info(`Existing guest matched: ${existing.name}`);
        } else {
          throw e;
        }
      }

      const { data } = await api.post("/bookings", {
        guest_id: guestId,
        room_type_id: choice.room_type.id,
        meal_plan_id: choice.quote.meal_plan.id,
        check_in: form.check_in,
        check_out: form.check_out,
        adults: Number(form.adults),
        children: Number(form.children),
      });
      toast.success(`Booked — ${data.reference}`);
      nav(`/app/hotel/bookings/${data.id}`);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        New booking
      </h1>

      <div className="flex flex-wrap gap-4 items-end mb-8">
        {[
          ["check_in", "Check in", "date"],
          ["check_out", "Check out", "date"],
          ["adults", "Adults", "number"],
          ["children", "Children", "number"],
        ].map(([key, label, type]) => (
          <label key={key} className="text-xs tracking-widest uppercase text-stone-500">
            {label}
            <input
              type={type}
              min={type === "number" ? 0 : undefined}
              value={form[key]}
              onChange={(e) => set(key, e.target.value)}
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
        ))}
        <button
          onClick={search}
          disabled={searching}
          className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {results && results.length === 0 && (
        <p className="text-stone-400">No room types are set up yet.</p>
      )}

      <div className="grid gap-4">
        {(results || []).map((row) => (
          <div key={row.room_type.id} className="border border-stone-800 bg-stone-900 rounded p-5">
            <div className="flex items-baseline justify-between flex-wrap gap-2">
              <h3 className="text-lg font-semibold">{row.room_type.name}</h3>
              <span
                className={
                  row.available > 0
                    ? "text-orange-400 font-mono text-sm"
                    : "text-stone-500 font-mono text-sm"
                }
              >
                {row.available} free
              </span>
            </div>

            {row.unpriced_dates && (
              <p className="text-sm text-red-400 mt-3">
                No rate set for {row.unpriced_dates.join(", ")} — add one under Rates
                before booking this type.
              </p>
            )}
            {!row.fits_party && (
              <p className="text-sm text-stone-500 mt-3">Too small for this party.</p>
            )}

            {row.available > 0 && row.fits_party && (
              <div className="grid gap-2 mt-4 md:grid-cols-3">
                {row.quotes.map((q) => (
                  <button
                    key={q.meal_plan.id}
                    onClick={() => setChoice({ room_type: row.room_type, quote: q })}
                    className={`text-left border rounded p-3 transition-colors ${
                      choice?.quote?.meal_plan?.id === q.meal_plan.id &&
                      choice?.room_type?.id === row.room_type.id
                        ? "border-orange-500 bg-stone-800"
                        : "border-stone-800 hover:border-stone-600"
                    }`}
                  >
                    <div className="text-xs tracking-widest uppercase text-stone-500">
                      {q.meal_plan.code} · {q.meal_plan.name}
                    </div>
                    <div className="text-xl font-semibold mt-1">{currency(q.total)}</div>
                    <div className="text-xs text-stone-500 mt-1">
                      {q.nights.length} night{q.nights.length === 1 ? "" : "s"} incl.{" "}
                      {currency(q.tax_total)} tax
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {choice && (
        <div className="mt-10 border border-stone-800 bg-stone-900 rounded p-5 max-w-xl">
          <h3 className="text-lg font-semibold mb-1">
            {choice.room_type.name} · {choice.quote.meal_plan.code}
          </h3>
          <p className="text-sm text-stone-400 mb-4">
            {form.check_in} → {form.check_out} · {currency(choice.quote.total)}
          </p>

          <div className="flex gap-4 flex-wrap">
            {[["name", "Guest name"], ["phone", "Phone"]].map(([k, label]) => (
              <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
                {label}
                <input
                  value={guest[k]}
                  onChange={(e) => setGuest((g) => ({ ...g, [k]: e.target.value }))}
                  className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
                />
              </label>
            ))}
          </div>

          <button
            onClick={book}
            disabled={saving}
            className="mt-6 bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-8 py-2 text-sm tracking-widest uppercase"
          >
            {saving ? "Booking…" : "Confirm booking"}
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire the route and nav**

`frontend/src/App.js`:

```jsx
import NewBooking from "@/pages/hotel/NewBooking";
```

```jsx
        <Route path="/hotel/bookings/new" element={<Protected roles={["admin", "manager", "front_desk"]}><NewBooking /></Protected>} />
```

Nav entry, in the Hotel group:

```jsx
  { to: "/app/hotel/bookings/new", label: "New booking", roles: ["admin", "manager", "front_desk"] },
```

- [ ] **Step 3: Manual verification**

With backend and frontend running and seeded room types, rooms and rates in place:

1. Open `/app/hotel/bookings/new`
2. Search a two-night window → each room type shows a free count and three meal-plan prices
3. Pick a quote, enter a name and phone, confirm
4. A toast shows the `BF-…` reference and you land on the booking detail route

Then search the same dates again — the free count must have dropped by one.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: availability search and new booking screen"
```

---

## Task 12: Bookings list, detail and calendar

**Files:**
- Create: `frontend/src/pages/hotel/Bookings.jsx`, `frontend/src/pages/hotel/BookingDetail.jsx`, `frontend/src/pages/hotel/Calendar.jsx`
- Modify: `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx`

**Interfaces:**
- Consumes: `GET /api/bookings`, `GET /api/bookings/{id}`, `PUT /api/bookings/{id}`, `POST /api/bookings/{id}/cancel`, `GET /api/bookings/calendar`
- Produces: routes `/app/hotel/bookings`, `/app/hotel/bookings/:id`, `/app/hotel/calendar`

- [ ] **Step 1: Create `Bookings.jsx`**

```jsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const STATUS_STYLE = {
  confirmed: "text-orange-400 border-orange-500/40",
  tentative: "text-amber-300 border-amber-400/40",
  checked_in: "text-emerald-400 border-emerald-500/40",
  checked_out: "text-stone-400 border-stone-600",
  cancelled: "text-stone-500 border-stone-700 line-through",
  no_show: "text-red-400 border-red-500/40",
};

export default function Bookings() {
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .get("/bookings", { params: { q, status } })
      .then((r) => setRows(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setLoading(false));
  }, [q, status]);

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Bookings
      </h1>

      <div className="flex flex-wrap gap-4 items-end mb-6">
        <label className="text-xs tracking-widest uppercase text-stone-500">
          Search
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Reference, name or phone"
            className="block mt-2 w-64 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
        </label>
        <label className="text-xs tracking-widest uppercase text-stone-500">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="block mt-2 bg-stone-900 border border-stone-700 text-stone-100 py-1 px-2 rounded"
          >
            <option value="">All</option>
            {Object.keys(STATUS_STYLE).map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading ? (
        <p className="text-stone-400">Loading bookings…</p>
      ) : rows.length === 0 ? (
        <p className="text-stone-400">No bookings match.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                <th className="text-left py-3 px-3 border-b border-stone-800">Reference</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Guest</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Dates</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Status</th>
                <th className="text-right py-3 px-3 border-b border-stone-800">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.id} className="hover:bg-stone-900">
                  <td className="py-3 px-3 border-b border-stone-800 font-mono">
                    <Link className="text-orange-400 hover:underline" to={`/app/hotel/bookings/${b.id}`}>
                      {b.reference}
                    </Link>
                  </td>
                  <td className="py-3 px-3 border-b border-stone-800">
                    {b.guest?.name || "—"}
                    <span className="block text-xs text-stone-500">{b.guest?.phone}</span>
                  </td>
                  <td className="py-3 px-3 border-b border-stone-800 font-mono text-xs">
                    {b.check_in} → {b.check_out}
                  </td>
                  <td className="py-3 px-3 border-b border-stone-800">
                    <span className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 ${STATUS_STYLE[b.status] || ""}`}>
                      {b.status.replace("_", " ")}
                    </span>
                    {b.status === "tentative" && b.hold_expires_at && (
                      <span className="block text-[10px] text-amber-400 mt-1">
                        hold until {b.hold_expires_at.slice(0, 10)}
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-3 border-b border-stone-800 text-right tabular-nums">
                    {currency(b.quote?.total)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `BookingDetail.jsx`**

```jsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function BookingDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [b, setB] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api
      .get(`/bookings/${id}`)
      .then((r) => setB(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  useEffect(() => {
    load();
  }, [id]);

  const cancel = async () => {
    const reason = window.prompt("Reason for cancelling?");
    if (reason === null) return;
    setBusy(true);
    try {
      await api.post(`/bookings/${id}/cancel`, { reason });
      toast.success("Booking cancelled");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!b) return <div className="p-6 md:p-10 text-stone-400">Loading booking…</div>;

  return (
    <div className="p-6 md:p-10">
      <button onClick={() => nav("/app/hotel/bookings")} className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 mb-4">
        ← All bookings
      </button>
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">
        {b.status.replace("_", " ")}
      </div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        {b.reference}
      </h1>
      <p className="text-stone-400 mb-8">
        {b.guest?.name} · {b.guest?.phone}
      </p>

      <div className="grid gap-4 md:grid-cols-3 mb-8">
        {[
          ["Room type", b.room_type?.name],
          ["Meal plan", `${b.meal_plan?.code} · ${b.meal_plan?.name}`],
          ["Occupancy", `${b.adults} adult${b.adults === 1 ? "" : "s"}, ${b.children} child${b.children === 1 ? "" : "ren"}`],
          ["Check in", b.check_in],
          ["Check out", b.check_out],
          ["Source", b.source],
        ].map(([label, value]) => (
          <div key={label} className="border border-stone-800 bg-stone-900 rounded p-4">
            <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">{label}</div>
            <div className="mt-1">{value || "—"}</div>
          </div>
        ))}
      </div>

      <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">Price breakdown</h2>
      <div className="overflow-x-auto mb-8">
        <table className="w-full text-sm border-collapse max-w-2xl">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
              <th className="text-left py-2 px-3 border-b border-stone-800">Night</th>
              <th className="text-right py-2 px-3 border-b border-stone-800">Tariff</th>
              <th className="text-right py-2 px-3 border-b border-stone-800">GST</th>
            </tr>
          </thead>
          <tbody>
            {(b.quote?.nights || []).map((n) => (
              <tr key={n.date}>
                <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs">{n.date}</td>
                <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(n.tariff)}</td>
                <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums text-stone-400">
                  {currency(n.gst_amount)} <span className="text-xs">({n.gst_percent}%)</span>
                </td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td className="py-3 px-3">Total</td>
              <td />
              <td className="py-3 px-3 text-right tabular-nums text-orange-400">
                {currency(b.quote?.total)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {!["cancelled", "checked_out"].includes(b.status) && (
        <button
          onClick={cancel}
          disabled={busy}
          className="border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
        >
          {busy ? "Cancelling…" : "Cancel booking"}
        </button>
      )}
      {b.status === "cancelled" && b.cancellation_reason && (
        <p className="text-sm text-stone-500">Cancelled — {b.cancellation_reason}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create `Calendar.jsx`**

```jsx
import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const addDays = (iso, n) =>
  new Date(new Date(iso).getTime() + n * 86400000).toISOString().slice(0, 10);

export default function Calendar() {
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
  const [grid, setGrid] = useState([]);

  useEffect(() => {
    api
      .get("/bookings/calendar", { params: { start, end: addDays(start, 14) } })
      .then((r) => setGrid(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, [start]);

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Occupancy
      </h1>

      <div className="flex gap-3 items-end mb-6">
        <label className="text-xs tracking-widest uppercase text-stone-500">
          From
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
        </label>
        <button onClick={() => setStart(addDays(start, -14))} className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 pb-1">
          ← Earlier
        </button>
        <button onClick={() => setStart(addDays(start, 14))} className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 pb-1">
          Later →
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="text-sm border-collapse">
          <thead>
            <tr>
              <th className="text-left py-2 px-3 border-b border-stone-800 text-[11px] tracking-[0.2em] uppercase text-stone-500 sticky left-0 bg-stone-950">
                Room type
              </th>
              {(grid[0]?.nights || []).map((n) => (
                <th key={n.date} className="py-2 px-2 border-b border-stone-800 text-[10px] font-mono text-stone-500">
                  {n.date.slice(5)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.map((row) => (
              <tr key={row.room_type.id}>
                <td className="py-2 px-3 border-b border-stone-800 whitespace-nowrap sticky left-0 bg-stone-950">
                  {row.room_type.name}
                </td>
                {row.nights.map((n) => (
                  <td
                    key={n.date}
                    title={`${n.occupied} of ${n.total} occupied`}
                    className={`py-2 px-2 border-b border-stone-800 text-center tabular-nums text-xs ${
                      n.available === 0
                        ? "bg-orange-600/30 text-orange-200"
                        : n.occupied > 0
                        ? "bg-stone-800 text-stone-300"
                        : "text-stone-600"
                    }`}
                  >
                    {n.available}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-stone-500 mt-4">Numbers are rooms still free that night.</p>
    </div>
  );
}
```

- [ ] **Step 4: Wire routes and nav**

`frontend/src/App.js` imports:

```jsx
import Bookings from "@/pages/hotel/Bookings";
import BookingDetail from "@/pages/hotel/BookingDetail";
import Calendar from "@/pages/hotel/Calendar";
```

Routes — **list before the `:id` route**:

```jsx
        <Route path="/hotel/bookings" element={<Protected roles={["admin", "manager", "front_desk"]}><Bookings /></Protected>} />
        <Route path="/hotel/bookings/:id" element={<Protected roles={["admin", "manager", "front_desk"]}><BookingDetail /></Protected>} />
        <Route path="/hotel/calendar" element={<Protected roles={["admin", "manager", "front_desk"]}><Calendar /></Protected>} />
```

`/hotel/bookings/new` from Task 11 must be declared **before** `/hotel/bookings/:id`, or "new" is read as an id.

Nav entries:

```jsx
  { to: "/app/hotel/bookings", label: "Bookings", roles: ["admin", "manager", "front_desk"] },
  { to: "/app/hotel/calendar", label: "Occupancy", roles: ["admin", "manager", "front_desk"] },
```

- [ ] **Step 5: Manual verification**

1. `/app/hotel/bookings` lists the booking made in Task 11 with its reference and total
2. Clicking the reference opens the detail with a per-night breakdown whose GST percentages match the tariffs
3. Cancel it, then check `/app/hotel/calendar` — that night's free count goes back up
4. Confirm `/app/hotel/bookings/new` still resolves to the New Booking screen, not the detail screen

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: bookings list, detail and occupancy calendar"
```

---

## Task 13: Rates and Guests screens

**Files:**
- Create: `frontend/src/pages/hotel/Rates.jsx`, `frontend/src/pages/hotel/Guests.jsx`
- Modify: `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx`

**Interfaces:**
- Consumes: `GET/POST /api/rates`, `GET /api/rate-periods`, `GET /api/meal-plans`, `GET /api/room-types`, `GET /api/guests`, `GET /api/guests/{id}`
- Produces: routes `/app/hotel/rates`, `/app/hotel/guests`

- [ ] **Step 1: Create `Rates.jsx`**

```jsx
import { useEffect, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function Rates() {
  const [types, setTypes] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [rates, setRates] = useState([]);
  const [plans, setPlans] = useState([]);
  const [draft, setDraft] = useState({
    room_type_id: "",
    period_id: "",
    base_rate: "",
    extra_adult_rate: "",
    extra_child_rate: "",
  });

  const load = () =>
    Promise.all([
      api.get("/room-types"),
      api.get("/rate-periods"),
      api.get("/rates"),
      api.get("/meal-plans"),
    ])
      .then(([t, p, r, m]) => {
        setTypes(t.data);
        setPeriods(p.data);
        setRates(r.data);
        setPlans(m.data);
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    if (!draft.room_type_id || draft.base_rate === "") {
      toast.error("Pick a room type and enter a base rate");
      return;
    }
    try {
      await api.post("/rates", {
        room_type_id: draft.room_type_id,
        period_id: draft.period_id || null,
        base_rate: Number(draft.base_rate),
        extra_adult_rate: Number(draft.extra_adult_rate || 0),
        extra_child_rate: Number(draft.extra_child_rate || 0),
      });
      toast.success("Rate saved");
      setDraft({ ...draft, base_rate: "", extra_adult_rate: "", extra_child_rate: "" });
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  const typeName = (id) => types.find((t) => t.id === id)?.name || "—";
  const periodName = (id) =>
    id ? periods.find((p) => p.id === id)?.name || "—" : "Default";

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Rates
      </h1>

      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-8 max-w-3xl">
        <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-4">Set a rate</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Room type
            <select
              value={draft.room_type_id}
              onChange={(e) => setDraft({ ...draft, room_type_id: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            >
              <option value="">Choose…</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Period
            <select
              value={draft.period_id}
              onChange={(e) => setDraft({ ...draft, period_id: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            >
              <option value="">Default (all year)</option>
              {periods.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          {[
            ["base_rate", "Base rate"],
            ["extra_adult_rate", "Extra adult"],
            ["extra_child_rate", "Extra child"],
          ].map(([k, label]) => (
            <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
              {label}
              <input
                type="number"
                min="0"
                value={draft[k]}
                onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          ))}
          <button
            onClick={save}
            className="bg-orange-600 hover:bg-orange-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Save
          </button>
        </div>
        <p className="text-xs text-stone-500 mt-4">
          Saving a rate for a room type and period that already has one replaces it.
        </p>
      </div>

      <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">Current rates</h2>
      {rates.length === 0 ? (
        <p className="text-stone-400 mb-8">
          No rates yet. A room type with no rate cannot be booked — the system refuses
          rather than pricing it at zero.
        </p>
      ) : (
        <div className="overflow-x-auto mb-8">
          <table className="w-full text-sm border-collapse max-w-3xl">
            <thead>
              <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                <th className="text-left py-2 px-3 border-b border-stone-800">Room type</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">Period</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Base</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Extra adult</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Extra child</th>
              </tr>
            </thead>
            <tbody>
              {rates.map((r) => (
                <tr key={r.id}>
                  <td className="py-2 px-3 border-b border-stone-800">{typeName(r.room_type_id)}</td>
                  <td className="py-2 px-3 border-b border-stone-800">{periodName(r.period_id)}</td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(r.base_rate)}</td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(r.extra_adult_rate)}</td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(r.extra_child_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">Meal plans</h2>
      <div className="grid gap-3 md:grid-cols-3 max-w-3xl">
        {plans.map((p) => (
          <div key={p.id} className="border border-stone-800 bg-stone-900 rounded p-4">
            <div className="font-mono text-xs text-orange-400">{p.code}</div>
            <div className="mt-1">{p.name}</div>
            <div className="text-xs text-stone-500 mt-2">
              {currency(p.price_per_adult_per_night)} per adult / night
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add season creation to `Rates.jsx`**

Without this, the period dropdown is permanently empty and seasonal pricing is unreachable
from the UI.

Add to the component's state:

```jsx
  const [season, setSeason] = useState({ name: "", start_date: "", end_date: "", priority: 10 });
```

Add the handler:

```jsx
  const saveSeason = async () => {
    if (!season.name.trim() || !season.start_date || !season.end_date) {
      toast.error("Name, start and end are all required");
      return;
    }
    if (season.end_date <= season.start_date) {
      toast.error("End must be after start");
      return;
    }
    try {
      const { data } = await api.post("/rate-periods", {
        ...season,
        priority: Number(season.priority),
      });
      if (data.overlap_warning) {
        toast.warning(`Overlaps ${data.overlap_warning.join(", ")} at the same priority`);
      } else {
        toast.success("Season saved");
      }
      setSeason({ name: "", start_date: "", end_date: "", priority: 10 });
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };
```

Render it above the "Set a rate" card:

```jsx
      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-6 max-w-3xl">
        <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-4">Seasons</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Name
            <input
              value={season.name}
              onChange={(e) => setSeason({ ...season, name: e.target.value })}
              placeholder="Peak"
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
          {[["start_date", "Starts"], ["end_date", "Ends"]].map(([k, label]) => (
            <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
              {label}
              <input
                type="date"
                value={season[k]}
                onChange={(e) => setSeason({ ...season, [k]: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          ))}
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Priority
            <input
              type="number"
              value={season.priority}
              onChange={(e) => setSeason({ ...season, priority: e.target.value })}
              className="block mt-2 w-20 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
          <button
            onClick={saveSeason}
            className="bg-orange-600 hover:bg-orange-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Add season
          </button>
        </div>
        {periods.length > 0 && (
          <ul className="mt-4 text-sm text-stone-400 space-y-1">
            {periods.map((p) => (
              <li key={p.id} className="font-mono text-xs">
                {p.name}: {p.start_date} → {p.end_date} (priority {p.priority})
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-stone-500 mt-4">
          End dates are exclusive — a season ending 5 Jan covers the night of 4 Jan, not the
          5th. Higher priority wins where seasons overlap.
        </p>
      </div>
```

- [ ] **Step 3: Create `Guests.jsx`**

```jsx
import { useEffect, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function Guests() {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    api
      .get("/guests", { params: { q } })
      .then((r) => setRows(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, [q]);

  const open = (id) =>
    api
      .get(`/guests/${id}`)
      .then((r) => setSelected(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Guests
      </h1>

      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by name or phone"
        className="mb-6 w-full max-w-md bg-transparent border-b border-stone-700 text-stone-100 py-2 focus:border-orange-500 outline-none"
      />

      <div className="grid gap-8 lg:grid-cols-2">
        <div>
          {rows.length === 0 ? (
            <p className="text-stone-400">No guests match.</p>
          ) : (
            <ul className="divide-y divide-stone-800">
              {rows.map((g) => (
                <li key={g.id}>
                  <button
                    onClick={() => open(g.id)}
                    className="w-full text-left py-3 hover:text-orange-400"
                  >
                    {g.name}
                    <span className="block text-xs text-stone-500 font-mono">{g.phone}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {selected && (
          <div className="border border-stone-800 bg-stone-900 rounded p-5 h-fit">
            <h2 className="text-lg font-semibold">{selected.name}</h2>
            <p className="text-xs text-stone-500 font-mono mb-4">{selected.phone}</p>

            <div className="grid grid-cols-2 gap-3 mb-5">
              <div>
                <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">Stays</div>
                <div className="text-2xl font-semibold">{selected.stays?.length || 0}</div>
              </div>
              <div>
                <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                  Bar & restaurant
                </div>
                <div className="text-2xl font-semibold text-orange-400">
                  {currency(selected.outlet_spend)}
                </div>
                <div className="text-xs text-stone-500">
                  {selected.outlet_orders} bill{selected.outlet_orders === 1 ? "" : "s"}
                </div>
              </div>
            </div>

            {(selected.stays || []).length > 0 && (
              <>
                <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
                  Stay history
                </div>
                <ul className="text-sm space-y-1">
                  {selected.stays.map((s) => (
                    <li key={s.id} className="font-mono text-xs text-stone-400">
                      {s.check_in} → {s.check_out} · {s.reference} · {s.status}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire routes and nav**

```jsx
import Rates from "@/pages/hotel/Rates";
import Guests from "@/pages/hotel/Guests";
```

```jsx
        <Route path="/hotel/rates" element={<Protected roles={["admin", "manager"]}><Rates /></Protected>} />
        <Route path="/hotel/guests" element={<Protected roles={["admin", "manager", "front_desk"]}><Guests /></Protected>} />
```

```jsx
  { to: "/app/hotel/rates", label: "Rates", roles: ["admin", "manager"] },
  { to: "/app/hotel/guests", label: "Guests", roles: ["admin", "manager", "front_desk"] },
```

- [ ] **Step 5: Manual verification**

1. `/app/hotel/rates` — save a default rate for a room type, then confirm it appears in the table and that `/app/hotel/bookings/new` can now price that type
2. `/app/hotel/guests` — search a phone that exists in old bar orders (after running the Task 6 backfill) and confirm the profile shows outlet spend
3. Sign in as `frontdesk@barflow.io` / `desk123` and confirm Rates is not reachable while Bookings and Guests are

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: rates and guests screens"
```

---

## Task 14: End-to-end verification

**Files:**
- Modify: none (verification only)

**Interfaces:**
- Consumes: everything above
- Produces: a signed-off working feature

- [ ] **Step 1: Run every backend test**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"
MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 &
sleep 3
python3 -m pytest tests/test_pricing.py tests/test_availability.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q 2>&1 | tail -3
```

Expected: pure suites fully green; hotel suite fully green; `backend_test.py` matching the recorded baseline of `1 failed, 9 passed, 1 skipped` with the failure still being the Stripe URL assertion.

- [ ] **Step 2: Confirm no regression in the existing route table**

```bash
cd ~/dev/bar-management-system/backend
MONGO_URL=mock python3 -c "
from server import app
paths = {r.path for r in app.routes if hasattr(r, 'methods')}
required = {'/api/tables', '/api/menu', '/api/orders/kot', '/api/reports/summary',
            '/api/reports/daily-brief', '/api/payments/checkout/session',
            '/api/availability', '/api/bookings', '/api/guests', '/api/rooms', '/api/rates'}
missing = required - paths
print('MISSING:', missing or 'none')
"
```

Expected: `MISSING: none`

- [ ] **Step 3: Walk the happy path in the browser**

With both servers running and signed in as admin:

1. Rooms — create a room type with two rooms
2. Rates — set a default rate for it
3. New booking — search two nights, pick a meal plan, book with a new guest
4. Bookings — the booking appears with its reference and total
5. Booking detail — the per-night breakdown's GST percentages match the tariffs
6. Occupancy — that room type shows one fewer free room on those nights
7. Cancel the booking — occupancy returns to full

- [ ] **Step 4: Verify the front desk boundary**

Sign out, sign in as `frontdesk@barflow.io` / `desk123`:

- Bookings, New booking, Occupancy and Guests all work
- Rates and Reports are not reachable

- [ ] **Step 5: Confirm the working tree is clean of runtime artefacts**

```bash
cd ~/dev/bar-management-system
git status --short
```

Expected: no `backend/db.json`, no `frontend/build`, no `.env`. All three are covered by `.gitignore`.

- [ ] **Step 6: Commit any final touches**

```bash
cd ~/dev/bar-management-system
git add -A
git commit -m "test: end-to-end verification of hotel rooms and booking engine" --allow-empty
```

---

## Deferred to later sub-projects

Not in this plan, by design — see the spec's decomposition table:

- Check-in, check-out, room assignment (sub-project 2)
- Guest folio and posting bar/restaurant charges to a room (sub-project 2)
- Housekeeping status (sub-project 3)
- Events and banquets (sub-project 4)
- Night audit and unified occupancy/ADR reporting (sub-project 5)
- Guest self-booking, OTA sync, deposits, invoice PDFs (unscheduled)
- Closing the double-booking race with an atomic per-date counter (ships with OTA sync)
