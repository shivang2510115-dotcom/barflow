# Outlets as Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the hardcoded tuple `OUTLET = ("restaurant", "bar")` into a property-scoped `outlets` collection, so a hotel can add a salon, gym or laundry itself and its staff can be assigned to specific ones.

**Architecture:** An outlet becomes a row a hotel admin creates. Work domains stay exactly as they are — the domain answers *may this person work in outlets at all*, and a new `outlet_ids` list on the user answers *which ones*. One new domain, `services`, covers the non-food kinds. `require_access` is not touched; a second, narrower dependency resolves the outlet named in a URL.

**Tech Stack:** FastAPI, Motor/PyMongo interface with a JSON mock and a Firestore adapter, pytest with pytest-xdist, React 19 + craco + Tailwind.

## Global Constraints

- `pyproject.toml` / `pytest.ini` `addopts = -n 2 --dist loadscope` — **must never be modified**.
- Test baselines to keep green: pure suites `1127 passed`; `hotel_api_test.py` `143 passed`; `backend_test.py` **exactly** `1 failed, 9 passed, 1 skipped` (the Stripe failure is environmental and must stay failing).
- API suites need a running server: `REACT_APP_BACKEND_URL=http://127.0.0.1:8000`. Restart the server before believing any failure — a stale uvicorn has masqueraded as a regression more than once in this repo.
- Every role tuple passed to `require_access` **must include `"admin"`**. The role check runs before the admin domain-bypass, so omitting it locks admins out.
- Routers receive a bound handle via `Depends(tenant_db)`. Never add `{"property_id": ...}` to a query in `routers/`. `backend/tests/test_isolation.py` must pass **with no new allowlist entries**.
- Money and consumption ledgers are append-only. No PUT, no DELETE on them.
- Local calendar days come from `services/clock.py` (`today()`, `local_date()`). Never slice a UTC timestamp with `[:10]`.
- Migrations run from `on_startup()` in `server.py` and must be idempotent. A standalone script will not run — the deployment has no shell step.
- `create_index` is a no-op in both the mock and Firestore. **No uniqueness is enforced by the database.** Router pre-checks are the only guard.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/outlets.py` (new) | Pure rules: the kind vocabulary, which domain a kind sits behind, default names, validation. No database, no HTTP. |
| `backend/models/outlet.py` (new) | `Outlet` and `OutletIn` pydantic models. |
| `backend/routers/outlets.py` (new) | CRUD for outlets. Admin-only writes, domain-gated reads. |
| `backend/security.py` (modify) | Add `require_outlet` — resolves an outlet id from the path and refuses one the caller may not reach. |
| `backend/services/access.py` (modify) | Add the `services` domain, widen `OUTLET`, add the `admin.outlets` screen key. |
| `backend/routers/staff.py` (modify) | Accept and return `outlet_ids`. |
| `backend/migrations/backfill_outlets.py` (new) | Create outlet rows for existing restaurant/bar domains; fill `outlet_ids`. |
| `backend/server.py` (modify) | Register the router; call the backfill from `on_startup()`. |
| `frontend/src/pages/admin/Outlets.jsx` (new) | The hotel admin's outlet list and editor. |
| `frontend/src/components/app/AppLayout.jsx` (modify) | Sidebar builds outlet links from the property's actual outlets. |

---

## Task 1: The outlet rules, as pure functions

**Files:**
- Create: `backend/services/outlets.py`
- Test: `backend/tests/test_outlets.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `KINDS: tuple[str, ...]`, `SERVICES: str`, `KIND_DOMAIN: dict[str, str]`, `default_name(kind: str) -> str`, `outlet_problem(name: str, kind: str, charges_to_folio: bool, takes_direct_payment: bool) -> str | None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_outlets.py`:

```python
"""The rules an outlet obeys, with no database and no HTTP under them.

Kept separate from the router for the reason services/password.py is: a rule that can
only be exercised by starting a server is a rule nobody exercises.
"""
import pytest

from services.outlets import (
    KINDS, KIND_DOMAIN, SERVICES, default_name, outlet_problem)


def test_every_kind_names_the_domain_it_sits_behind():
    # A kind with no domain would produce an outlet no staff member could ever be
    # granted, which is a row that exists and does nothing.
    for kind in KINDS:
        assert kind in KIND_DOMAIN, kind
        assert KIND_DOMAIN[kind]


def test_food_kinds_keep_the_domains_they_already_had():
    # Existing properties have staff holding these two. Remapping them would silently
    # move every waiter in production out of the screens they work in.
    assert KIND_DOMAIN["restaurant"] == "restaurant"
    assert KIND_DOMAIN["bar"] == "bar"


def test_the_new_kinds_share_one_domain_rather_than_inventing_three():
    # One `services` domain instead of salon/gym/laundry as three. A hotel that adds a
    # salon and a gym almost always staffs them from the same small group, and three
    # domains would make the staff screen ask three questions to express that.
    assert KIND_DOMAIN["salon"] == SERVICES
    assert KIND_DOMAIN["gym"] == SERVICES
    assert KIND_DOMAIN["laundry"] == SERVICES
    assert KIND_DOMAIN["other"] == SERVICES


def test_a_kind_has_a_default_name_so_the_form_is_never_blank():
    assert default_name("salon") == "Salon"
    assert default_name("restaurant") == "Restaurant"
    # An unknown kind still answers, because a caller that has already been validated
    # should not be able to crash the form by asking for a label.
    assert default_name("nonsense") == "Outlet"


def test_an_outlet_that_can_take_money_no_way_at_all_is_refused():
    problem = outlet_problem("Serenity Salon", "salon",
                             charges_to_folio=False, takes_direct_payment=False)
    assert problem
    assert "folio" in problem or "payment" in problem


def test_either_way_of_taking_money_on_its_own_is_enough():
    assert outlet_problem("Spa", "salon", True, False) is None
    assert outlet_problem("Spa", "salon", False, True) is None


def test_a_nameless_outlet_is_refused():
    assert outlet_problem("   ", "salon", True, True)


def test_an_unknown_kind_is_refused_and_the_message_names_it():
    problem = outlet_problem("Helipad", "helipad", True, True)
    assert problem
    assert "helipad" in problem
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_outlets.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.outlets'`

- [ ] **Step 3: Write the implementation**

Create `backend/services/outlets.py`:

```python
"""What an outlet is, and what makes one valid.

A salon is not a new kind of thing. It is an outlet, which the restaurant and the bar
already are: a place with a catalogue that serves a guest, takes money, and posts to a
folio. This module holds the vocabulary and the rules; the database and the HTTP live
in routers/outlets.py, and nothing here imports either.

`outlet_problem` returns a message rather than raising, following services/password.py —
services/ is kept free of HTTP so that a rule can be tested without a server.
"""

# The kinds a hotel can choose from. Fixed rather than free text because `kind` decides
# which domain staff need and which reporting bucket the takings land in; a hotel that
# could invent a kind could invent one nobody can be assigned to.
#
# `name` is the free-text field, and the two are deliberately separate: a property may
# run two restaurants with different names and the same kind.
KINDS = ("restaurant", "bar", "salon", "gym", "laundry", "other")

# One domain for every non-food kind, rather than one per kind.
#
# A hotel that adds a salon and a gym staffs them from the same handful of people
# almost every time, and three domains would make the staff screen ask three questions
# to express one fact. If a property ever genuinely needs to keep salon staff out of the
# gym, `outlet_ids` already says exactly that — which is the narrower question, and the
# one this design added it for.
SERVICES = "services"

# Which work domain each kind sits behind. `restaurant` and `bar` keep the domains they
# have always had: production has waiters holding those, and remapping them would move
# every one of them out of the screens they work in.
KIND_DOMAIN = {
    "restaurant": "restaurant",
    "bar": "bar",
    "salon": SERVICES,
    "gym": SERVICES,
    "laundry": SERVICES,
    "other": SERVICES,
}

_DEFAULT_NAMES = {
    "restaurant": "Restaurant",
    "bar": "Bar",
    "salon": "Salon",
    "gym": "Gym",
    "laundry": "Laundry",
    "other": "Outlet",
}


def default_name(kind: str) -> str:
    """A name to prefill the form with, so the field is never blank.

    Answers for an unknown kind too: a caller that has already passed validation should
    not be able to crash a form by asking for a label.
    """
    return _DEFAULT_NAMES.get(kind, "Outlet")


def outlet_problem(name: str, kind: str, charges_to_folio: bool,
                   takes_direct_payment: bool) -> str | None:
    """What is wrong with this outlet, in words, or None if nothing is.

    Returns the message rather than raising for the reason given in the module
    docstring. The caller turns it into a 400.
    """
    if not (name or "").strip():
        return "The outlet needs a name"
    if kind not in KINDS:
        return f"{kind} is not an outlet kind — expected one of: {', '.join(KINDS)}"
    if not charges_to_folio and not takes_direct_payment:
        # Caught here rather than discovered at the counter with a guest waiting.
        return ("An outlet must take money somehow: charge to a room folio, "
                "accept direct payment, or both")
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_outlets.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/outlets.py backend/tests/test_outlets.py
git commit -m "feat: what an outlet is, and what makes one valid"
```

---

## Task 2: The `services` domain and the `admin.outlets` screen

**Files:**
- Modify: `backend/services/access.py`
- Test: `backend/tests/test_access.py`

**Interfaces:**
- Consumes: `services.outlets.SERVICES` from Task 1.
- Produces: `DOMAINS` gains `"services"`; `OUTLET` gains `"services"`; `SCREENS` gains `"admin.outlets"`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_access.py`:

```python
def test_the_services_domain_exists_and_counts_as_an_outlet_domain():
    from services.access import DOMAINS, OUTLET
    from services.outlets import SERVICES
    assert SERVICES in DOMAINS
    # Widening OUTLET is the point: a salon's staff reach the POS, the menu and the
    # tables screens through the same keys a waiter does, because those screens are
    # about selling from a catalogue and a salon sells from a catalogue.
    assert SERVICES in OUTLET


def test_the_food_domains_are_unchanged_and_still_first():
    # Production data holds these strings on user records. Reordering is harmless;
    # renaming or dropping either would strand every waiter and bartender.
    from services.access import DOMAINS
    assert "restaurant" in DOMAINS
    assert "bar" in DOMAINS
    assert "hotel" in DOMAINS


def test_managing_outlets_is_an_admin_screen():
    from services.access import SCREENS
    assert "admin.outlets" in SCREENS
    assert SCREENS["admin.outlets"]["section"] == "Admin"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_access.py -q -k "services_domain or food_domains or managing_outlets"`
Expected: FAIL — `AssertionError` on `SERVICES in DOMAINS`

- [ ] **Step 3: Write the implementation**

In `backend/services/access.py`, replace the `DOMAINS` and `OUTLET` definitions:

```python
# The areas a staff member can be assigned to.
#
# `services` covers the non-food outlets — salon, gym, laundry — as one domain rather
# than one each. See services/outlets.py::SERVICES for why one is enough: `outlet_ids`
# already answers the narrower "which salon", which is the question a property that
# needs the distinction is actually asking.
DOMAINS = ("hotel", "restaurant", "bar", "services")

# Endpoints serving more than one area declare this instead. A bar regular and a hotel
# guest are the same person, so splitting guest records by domain would stop the desk
# seeing an arrival's bar history — which is the product's whole claim.
SHARED = "shared"

# Every outlet domain. The POS, menu, tables and reservation screens declare this, so
# holding any one of them grants access — declaring "restaurant" alone would lock a
# bar-only waiter out of the POS, and now a salon's staff out of it too.
OUTLET = ("restaurant", "bar", SERVICES)
```

Add the import near the other service imports at the top of the file:

```python
from services.outlets import SERVICES
```

Add the screen key to `SCREENS`, beside the other `admin.` entries:

```python
    # Who may add a salon, a gym or a second restaurant, and who may switch one off.
    # Admin-only: an outlet decides which screens exist for a whole group of staff, and
    # that is a decision about how the business is arranged rather than a daily task.
    "admin.outlets":      {"label": "Outlets",      "section": "Admin",      "domains": (SHARED,)},
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_access.py -q`
Expected: all pass. If a test asserting the full screen catalogue fails, add `"admin.outlets"` to its expected set — that is the one expected test change named in the spec.

- [ ] **Step 5: Run the whole pure suite for regressions**

Run: `cd backend && python3 -m pytest tests/ -q --ignore=tests/hotel_api_test.py --ignore=tests/backend_test.py`
Expected: `1139 passed` (1127 baseline + 9 from Task 1 + 3 from this task). Any *failure* is a regression — investigate rather than updating the assertion.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/access.py backend/tests/test_access.py
git commit -m "feat: the services domain, and the screen that manages outlets"
```

---

## Task 3: The outlets router

**Files:**
- Create: `backend/routers/outlets.py`
- Create: `backend/models/outlet.py`
- Modify: `backend/server.py` (import and register)
- Test: `backend/tests/hotel_api_test.py`

**Interfaces:**
- Consumes: `services.outlets.{KINDS, KIND_DOMAIN, outlet_problem}` from Task 1; `services.access.SHARED` and the `admin.outlets` key from Task 2.
- Produces: `GET /api/outlets`, `POST /api/outlets`, `PATCH /api/outlets/{outlet_id}`. Outlet dicts shaped `{id, name, kind, domain, charges_to_folio, takes_direct_payment, active, created_at}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_an_admin_creates_an_outlet_and_it_comes_back_with_its_domain(admin):
    r = admin.post(f"{API}/outlets", json={
        "name": "Serenity Salon", "kind": "salon",
        "charges_to_folio": True, "takes_direct_payment": True})
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["name"] == "Serenity Salon"
    assert made["kind"] == "salon"
    # The domain is derived, never sent: a client that could choose it could create a
    # salon nobody on staff is able to reach.
    assert made["domain"] == "services"
    assert made["active"] is True

    listed = admin.get(f"{API}/outlets").json()
    assert any(o["id"] == made["id"] for o in listed)


def test_an_outlet_that_takes_money_no_way_at_all_is_refused(admin):
    r = admin.post(f"{API}/outlets", json={
        "name": "Reading Room", "kind": "other",
        "charges_to_folio": False, "takes_direct_payment": False})
    assert r.status_code == 400
    assert "money" in r.json()["detail"].lower()


def test_an_unknown_kind_is_refused(admin):
    r = admin.post(f"{API}/outlets", json={
        "name": "Helipad", "kind": "helipad",
        "charges_to_folio": True, "takes_direct_payment": True})
    assert r.status_code == 400


def test_a_client_cannot_choose_the_domain_or_the_id(admin):
    r = admin.post(f"{API}/outlets", json={
        "name": "Gym", "kind": "gym", "charges_to_folio": True,
        "takes_direct_payment": False,
        "domain": "hotel", "id": "chosen-by-the-client", "property_id": "somebody-else"})
    assert r.status_code == 200
    made = r.json()
    assert made["domain"] == "services"
    assert made["id"] != "chosen-by-the-client"


def test_switching_an_outlet_off_leaves_it_readable(admin):
    made = admin.post(f"{API}/outlets", json={
        "name": "Old Bar", "kind": "bar", "charges_to_folio": True,
        "takes_direct_payment": True}).json()
    r = admin.patch(f"{API}/outlets/{made['id']}", json={"active": False})
    assert r.status_code == 200
    assert r.json()["active"] is False
    # Deactivated, not deleted: its past orders still name it, and a row that vanishes
    # takes the label off every one of them.
    assert any(o["id"] == made["id"] for o in admin.get(f"{API}/outlets").json())


def test_a_waiter_cannot_create_an_outlet(waiter):
    # `waiter` and `front_desk` are the non-admin fixtures this file has; there is no
    # `manager` one. A waiter is the sharper test regardless — they hold an outlet
    # domain, so this proves the `admin.outlets` key is what refuses them.
    r = waiter.post(f"{API}/outlets", json={
        "name": "Sneaky Spa", "kind": "salon",
        "charges_to_folio": True, "takes_direct_payment": True})
    assert r.status_code == 403
```

- [ ] **Step 2: Run the test to verify it fails**

Restart the server first — a stale one serves old code:

```bash
cd backend && pkill -f "uvicorn server:app"; sleep 2; rm -f db.json
nohup env MONGO_URL=mock DEMO_LOGINS=true SEED_DEMO_CONTENT=true \
  STRIPE_WEBHOOK_SECRET=whsec_local python3 -m uvicorn server:app \
  --host 127.0.0.1 --port 8000 > /tmp/bf-api.log 2>&1 & disown
sleep 9
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k outlet
```

Expected: FAIL — 404 on `POST /api/outlets`

- [ ] **Step 3: Write the model**

Create `backend/models/outlet.py`:

```python
"""What a place that serves a guest looks like on disk."""
from typing import Optional

from pydantic import BaseModel


class OutletIn(BaseModel):
    """What a hotel admin sends when adding or editing an outlet.

    `domain`, `id` and `property_id` are deliberately absent. The domain is derived from
    the kind — a client that could choose it could create a salon nobody on staff is
    able to reach — and the other two belong to the server and the scoped handle.
    """
    name: str
    kind: str
    charges_to_folio: bool = True
    takes_direct_payment: bool = True


class OutletPatch(BaseModel):
    """A partial edit. Every field optional; absent means unchanged.

    `kind` is not editable. Changing it would move the outlet's domain out from under
    the staff already assigned to it, silently revoking their access to a place they
    work in. Deactivate and create the right one instead.
    """
    name: Optional[str] = None
    charges_to_folio: Optional[bool] = None
    takes_direct_payment: Optional[bool] = None
    active: Optional[bool] = None
```

- [ ] **Step 4: Write the router**

Create `backend/routers/outlets.py`:

```python
"""The places a property serves guests: its restaurants, bars, salon, gym, laundry.

This collection is what replaced `OUTLET = ("restaurant", "bar")`. A hotel adds its own
rather than waiting for us, which is the same reason signup is self-serve: a hotel
waiting on the platform operator to add a salon is a support ticket that scales with the
customer count.

**Writes are admin-only, reads are not.** Deciding that the property has a salon is a
decision about how the business is arranged; knowing that it does is something every
staff member needs in order to be shown the right sidebar. So `POST` and `PATCH` carry
the `admin.outlets` key and `GET` carries none — the same split `routers/planner.py`
uses, and for the same reason: gating the read would hide the sidebar from everyone who
does not administer the place.

**Nothing here is deleted.** An outlet is deactivated, because its past orders name it
and a row that vanishes takes the label off every one of them.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from models.outlet import OutletIn, OutletPatch
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
from services.access import DOMAINS, SHARED
from services.outlets import KIND_DOMAIN, outlet_problem

router = APIRouter()

# Reading the list. No screen key and no role: a waiter needs to know the property has a
# salon in order for the sidebar to be right, and gating this would hide the navigation
# from everyone who does not administer the place. `routers/planner.py::READ` is the
# precedent.
READ = require_access(DOMAINS, "admin", "manager", "front_desk", "waiter", "housekeeping")

# Writing. Admin only — note "admin" appears in the role tuple, as it must everywhere:
# the role check runs before the admin domain-bypass, so a tuple omitting it locks
# admins out of their own screen.
WRITE = require_access(SHARED, "admin", permission="admin.outlets")


def _public(row: dict) -> dict:
    """One outlet, as the client sees it."""
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "kind": row.get("kind"),
        "domain": row.get("domain"),
        "charges_to_folio": bool(row.get("charges_to_folio")),
        "takes_direct_payment": bool(row.get("takes_direct_payment")),
        "active": bool(row.get("active", True)),
        "created_at": row.get("created_at"),
    }


@router.get("/outlets")
async def list_outlets(user: dict = Depends(READ),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    rows = await db.outlets.find({}, {"_id": 0}).to_list(200)
    rows.sort(key=lambda r: (not r.get("active", True), r.get("name") or ""))
    return [_public(r) for r in rows]


@router.post("/outlets")
async def create_outlet(payload: OutletIn, user: dict = Depends(WRITE),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    name = payload.name.strip()
    problem = outlet_problem(name, payload.kind,
                             payload.charges_to_folio, payload.takes_direct_payment)
    if problem:
        raise HTTPException(400, problem)

    # No database enforces uniqueness here — `create_index` is a no-op in both the mock
    # and Firestore — so the pre-check is the only guard. Two salons called "Spa" is a
    # hotel's own business, but two identical rows created by a double-tapped Save is
    # not, and this is the cheapest place to notice.
    existing = await db.outlets.find_one({"name": name, "kind": payload.kind})
    if existing:
        raise HTTPException(409, f"This property already has a {payload.kind} called {name}")

    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "kind": payload.kind,
        # Derived, never accepted from the client.
        "domain": KIND_DOMAIN[payload.kind],
        "charges_to_folio": payload.charges_to_folio,
        "takes_direct_payment": payload.takes_direct_payment,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.outlets.insert_one(row)
    return _public(row)


@router.patch("/outlets/{outlet_id}")
async def update_outlet(outlet_id: str, payload: OutletPatch,
                        user: dict = Depends(WRITE),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    row = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    if not row:
        # 404 rather than 403 for an outlet in another property: the scoped handle
        # filtered it out, so from here it does not exist. Answering 403 would confirm
        # that some other hotel has an outlet with this id.
        raise HTTPException(404, "No such outlet")

    changes = payload.model_dump(exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()

    merged = {**row, **changes}
    problem = outlet_problem(merged["name"], merged["kind"],
                             merged["charges_to_folio"], merged["takes_direct_payment"])
    if problem:
        raise HTTPException(400, problem)

    if changes:
        await db.outlets.update_one({"id": outlet_id}, {"$set": changes})
    return _public(merged)
```

- [ ] **Step 5: Register the router**

In `backend/server.py`, add `outlets` to the import line and to the module tuple:

```python
from routers import auth, staff, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings, frontdesk, folios, analytics, permissions, property as property_router, signup, platform, invoices, messaging, housekeeping, expenses, planner, outlets
```

```python
for module in (auth, staff, tables, menu, orders, inventory, reports, payments, guests, rooms, rates, bookings, frontdesk, folios, analytics, permissions, property_router, signup, platform, invoices, messaging, housekeeping, expenses, planner, outlets):
```

- [ ] **Step 6: Run the tests to verify they pass**

Restart the server, then:

```bash
cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
```

Expected: `149 passed` (143 baseline + 6 new).

- [ ] **Step 7: Confirm tenancy is intact**

Run: `cd backend && python3 -m pytest tests/test_isolation.py -q`
Expected: PASS **with no new allowlist entries**. If it demands one, the router is reaching `unscoped_db` — fix the router, not the allowlist.

- [ ] **Step 8: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/outlets.py backend/models/outlet.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: a property's outlets, added by its own admin"
```

---

## Task 4: Staff are assigned to specific outlets

**Files:**
- Modify: `backend/security.py` (add `require_outlet`)
- Modify: `backend/routers/staff.py` (accept and return `outlet_ids`)
- Test: `backend/tests/hotel_api_test.py`

**Interfaces:**
- Consumes: `GET /api/outlets` from Task 3.
- Produces: `security.require_outlet(outlet_id: str, user, db) -> dict` as a FastAPI dependency; user records gain `outlet_ids: list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_a_staff_member_is_assigned_to_specific_outlets(admin):
    salon = admin.post(f"{API}/outlets", json={
        "name": "Calm Salon", "kind": "salon",
        "charges_to_folio": True, "takes_direct_payment": True}).json()

    r = admin.post(f"{API}/staff", json={
        "name": "Priya", "phone": "9812345670", "password": "Wildflower-8821",
        "role": "waiter", "domains": ["services"], "outlet_ids": [salon["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["outlet_ids"] == [salon["id"]]


def test_a_staff_member_cannot_be_assigned_to_another_propertys_outlet(admin):
    # A fabricated id stands in for one belonging to a different hotel: the scoped
    # handle cannot see it either way, which is exactly the point.
    r = admin.post(f"{API}/staff", json={
        "name": "Ravi", "phone": "9812345671", "password": "Wildflower-8822",
        "role": "waiter", "domains": ["services"],
        "outlet_ids": ["00000000-0000-0000-0000-000000000000"]})
    assert r.status_code == 400
    assert "outlet" in r.json()["detail"].lower()


def test_outlet_ids_defaults_to_empty_rather_than_to_everything(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Anil", "phone": "9812345672", "password": "Wildflower-8823",
        "role": "waiter", "domains": ["restaurant"]})
    assert r.status_code == 200
    # Empty means "not narrowed", handled by require_outlet. It must not mean "every
    # outlet" as a stored value, or adding a salon later would silently staff it with
    # everybody who already worked anywhere.
    assert r.json()["outlet_ids"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Restart the server, then:
`cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k outlet_ids or assigned_to_specific`
Expected: FAIL — `outlet_ids` missing from the response

- [ ] **Step 3: Add the resolver to `backend/security.py`**

Append to `backend/security.py`:

```python
async def require_outlet(
    outlet_id: str,
    user: dict = Depends(get_current_user),
    db: PropertyScopedDatabase = Depends(tenant_db),
) -> dict:
    """Resolve the outlet named in the path, refusing one the caller may not reach.

    A plain dependency rather than a factory: every route that needs it names its path
    parameter `outlet_id`, so there is nothing to configure.

    Deliberately separate from `require_access` rather than folded into it. That
    dependency answers "may this person work in outlets at all", which every existing
    call site already asks and none of them should have to change. This answers the
    narrower "which one", and only the handful of routes that name an outlet in their
    URL ask it.

    An empty `outlet_ids` means *not narrowed* — the staff member works wherever their
    domain reaches. This is what every account created before outlets existed has, and
    treating empty as "none" would lock every waiter in production out of the POS on the
    morning this deploys.

    An admin passes regardless, the way they bypass domains everywhere else.
    """
    outlet = await db.outlets.find_one({"id": outlet_id}, {"_id": 0})
    if not outlet:
        # 404, not 403 — the scoped handle already filtered another property's outlets
        # out, so from here it genuinely does not exist. A 403 would confirm that the id
        # belongs to somebody.
        raise HTTPException(404, "No such outlet")
    if not outlet.get("active", True):
        raise HTTPException(409, f"{outlet.get('name')} is switched off")

    if user.get("role") == "admin":
        return outlet

    assigned = user.get("outlet_ids") or []
    if assigned and outlet_id not in assigned:
        raise HTTPException(403, "Not permitted in this outlet")
    return outlet
```

- [ ] **Step 4: Accept `outlet_ids` in `backend/routers/staff.py`**

In the staff input model, add the field:

```python
    # Which outlets this person works in. Empty means "not narrowed" — they work
    # wherever their domains reach, which is what every account predating outlets has.
    # See security.py::require_outlet, which is the only place this is enforced.
    outlet_ids: list[str] = []
```

In the create and update handlers, validate before storing:

```python
async def _checked_outlet_ids(db, outlet_ids: list[str]) -> list[str]:
    """Every id must name an outlet of *this* property.

    The scoped handle means an id from another hotel simply is not found, so this one
    check covers both a typo and a caller reaching across tenants.
    """
    wanted = [o for o in dict.fromkeys(outlet_ids) if o]
    if not wanted:
        return []
    rows = await db.outlets.find({"id": {"$in": wanted}}, {"_id": 0, "id": 1}).to_list(200)
    found = {r["id"] for r in rows}
    missing = [o for o in wanted if o not in found]
    if missing:
        raise HTTPException(400, f"No such outlet in this property: {missing[0]}")
    return wanted
```

Call it where the user record is built, storing `"outlet_ids": await _checked_outlet_ids(db, payload.outlet_ids)`, and include `outlet_ids` in whatever projection the staff routes return.

- [ ] **Step 5: Run the tests to verify they pass**

Restart the server, then:
`cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q`
Expected: `152 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/security.py backend/routers/staff.py backend/tests/hotel_api_test.py
git commit -m "feat: staff are assigned to outlets, and empty means not narrowed"
```

---

## Task 5: The migration

**Files:**
- Create: `backend/migrations/backfill_outlets.py`
- Modify: `backend/server.py` (import and call from `on_startup`)
- Test: `backend/tests/test_backfill_outlets.py`

**Interfaces:**
- Consumes: the `outlets` collection shape from Task 3.
- Produces: `backfill() -> tuple[int, int, int]` returning `(outlets_created, users_filled, properties_current)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_backfill_outlets.py`:

```python
"""The migration that gives every existing property the outlets it has been running.

Idempotence is the property under test, not a nicety: this runs from `on_startup()` on
every boot forever, because the deployment has no shell step to run a script from.
"""
import pytest

from migrations.backfill_outlets import outlets_for_domains


def test_a_property_with_both_food_domains_gets_both_outlets():
    made = outlets_for_domains(["hotel", "restaurant", "bar"])
    kinds = {o["kind"] for o in made}
    assert kinds == {"restaurant", "bar"}


def test_a_hotel_with_no_outlet_domain_gets_no_outlets():
    assert outlets_for_domains(["hotel"]) == []


def test_the_created_outlets_carry_their_default_names_and_can_take_money():
    made = outlets_for_domains(["restaurant"])
    assert len(made) == 1
    assert made[0]["name"] == "Restaurant"
    assert made[0]["kind"] == "restaurant"
    assert made[0]["domain"] == "restaurant"
    # Both true, because that is what a restaurant in this product has always been able
    # to do: charge a resident to their room, or take payment at the table.
    assert made[0]["charges_to_folio"] is True
    assert made[0]["takes_direct_payment"] is True
    assert made[0]["active"] is True


def test_running_it_twice_over_the_same_domains_is_stable():
    # The function is pure; the router half's idempotence is asserted by the API test
    # below. This guards the half that decides *what* to create.
    assert outlets_for_domains(["restaurant", "bar"]) == outlets_for_domains(["restaurant", "bar"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_backfill_outlets.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'migrations.backfill_outlets'`

- [ ] **Step 3: Write the implementation**

Create `backend/migrations/backfill_outlets.py`:

```python
"""One-shot: the outlets every existing property has been running all along.

Before outlets were rows, a property's restaurant and bar existed only as work domains
on its record. This creates the matching rows so that a property which has been taking
restaurant orders for months has a restaurant to point them at.

Idempotent, and it has to be: it runs from `on_startup()` on every boot forever, because
the deployment has no shell step from which to run a script once. A property that already
has an outlet of a kind is left alone — including one whose admin renamed it, which is
why the check is on `kind` and not on `name`.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_outlets
"""
import logging
import uuid
from datetime import datetime, timezone

import db as _db_module
from scoped_db import PropertyScopedDatabase
from services.access import DEFAULT_PROPERTY_TYPE, domains_for_property_type
from services.outlets import KIND_DOMAIN, default_name

logger = logging.getLogger(__name__)

# The domains that used to mean "this property has one of these".
_FOOD_KINDS = ("restaurant", "bar")


def outlets_for_domains(domains: list[str]) -> list[dict]:
    """What outlets a property holding these domains has been operating.

    Pure, so the decision can be tested without a database. Ids and timestamps are added
    by the caller — a pure function that invented a uuid would not be testable for the
    stability this migration depends on.
    """
    held = set(domains or [])
    made = []
    for kind in _FOOD_KINDS:
        if kind not in held:
            continue
        made.append({
            "name": default_name(kind),
            "kind": kind,
            "domain": KIND_DOMAIN[kind],
            # What a restaurant in this product has always been able to do: charge a
            # resident to their room, or take payment at the table.
            "charges_to_folio": True,
            "takes_direct_payment": True,
            "active": True,
        })
    return made


async def backfill() -> tuple[int, int, int]:
    """Create missing outlet rows and point existing staff at them.

    Returns (outlets_created, users_filled, properties_already_current).
    """
    created = filled = current = 0
    properties = await _db_module.unscoped_db.properties.find({}, {"_id": 0}).to_list(5000)

    for prop in properties:
        pid = prop["id"]
        scoped = PropertyScopedDatabase(pid)

        existing = await scoped.outlets.find({}, {"_id": 0}).to_list(200)
        have_kinds = {o.get("kind") for o in existing}

        # A property's domains come from its type, the same way signup decides them.
        domains = list(domains_for_property_type(
            prop.get("property_type") or DEFAULT_PROPERTY_TYPE))

        by_kind = {o.get("kind"): o["id"] for o in existing}
        for row in outlets_for_domains(domains):
            if row["kind"] in have_kinds:
                continue
            made = {**row, "id": str(uuid.uuid4()),
                    "created_at": datetime.now(timezone.utc).isoformat()}
            await scoped.outlets.insert_one(made)
            by_kind[row["kind"]] = made["id"]
            created += 1

        if not created:
            current += 1

        # Point each staff member at the outlets matching the domains they already hold.
        #
        # Only users whose `outlet_ids` is *missing* are touched, never one that is
        # present and empty. `backfill_permissions` learned this rule the hard way: an
        # account an owner deliberately narrowed must not be widened again on the next
        # restart, and empty already means "not narrowed" to require_outlet.
        users = await _db_module.unscoped_db.users.find(
            {"property_id": pid}, {"_id": 0}).to_list(5000)
        for u in users:
            if "outlet_ids" in u:
                continue
            held = set(u.get("domains") or [])
            ids = [oid for kind, oid in by_kind.items() if kind in held]
            await _db_module.unscoped_db.users.update_one(
                {"id": u["id"]}, {"$set": {"outlet_ids": ids}})
            filled += 1

    return created, filled, current
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_backfill_outlets.py -q`
Expected: `4 passed`

- [ ] **Step 5: Wire it into startup**

In `backend/server.py`, add the import beside the other migrations:

```python
from migrations.backfill_outlets import backfill as backfill_outlets
```

And in `on_startup()`, after `backfill_planner()`:

```python
    # Outlets, after the permission backfills above for the same reason housekeeping and
    # expenses are: a property that has been taking restaurant orders for months needs a
    # restaurant row to point them at before any screen asks which outlet an order
    # belongs to. Idempotent, and safe on every boot forever.
    out_created, out_filled, out_current = await backfill_outlets()
    logger.info(
        "Outlets: %s outlet(s) created, %s user(s) pointed at them, "
        "%s propert(ies) already current.", out_created, out_filled, out_current)
```

- [ ] **Step 6: Verify it runs on a real boot and is idempotent**

```bash
cd backend && pkill -f "uvicorn server:app"; sleep 2; rm -f db.json
nohup env MONGO_URL=mock DEMO_LOGINS=true SEED_DEMO_CONTENT=true \
  STRIPE_WEBHOOK_SECRET=whsec_local python3 -m uvicorn server:app \
  --host 127.0.0.1 --port 8000 > /tmp/bf-api.log 2>&1 & disown
sleep 9 && grep -i "Outlets:" /tmp/bf-api.log
```

Expected: a line reporting outlets created. Then restart **without** deleting `db.json` and confirm the second run reports `0 outlet(s) created` — that is the idempotence claim, checked rather than asserted.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/migrations/backfill_outlets.py backend/server.py backend/tests/test_backfill_outlets.py
git commit -m "feat: give every existing property the outlets it has been running"
```

---

## Task 6: The admin screen, and a sidebar that knows what exists

**Files:**
- Create: `frontend/src/pages/admin/Outlets.jsx`
- Modify: `frontend/src/components/app/AppLayout.jsx`
- Modify: `frontend/src/App.js` (route)

**Interfaces:**
- Consumes: `GET/POST/PATCH /api/outlets` from Task 3.
- Produces: the `/admin/outlets` route; the sidebar's outlet links.

- [ ] **Step 1: Build the screen**

Create `frontend/src/pages/admin/Outlets.jsx`. Follow `frontend/src/pages/admin/Expenses.jsx` for structure and house style. Requirements, all of which have a reason:

- List every outlet with its kind, its name, and whether it is active.
- An add form with: name, kind (a select over the six kinds), and two checkboxes for `charges_to_folio` and `takes_direct_payment`.
- Prefill the name from the kind using the same defaults the backend uses, so the field is never blank.
- Deactivating asks for confirmation **inline, in the row** — never `window.prompt` or `window.confirm`. This codebase confirms destructive things in place everywhere else, and a browser dialog is unstyleable and awkward on the tablet this runs on.
- Show the 400 from `outlet_problem` verbatim. It is written to be read by the person who typed the form.
- Use the palette tokens — `bg-surface`, `text-ink`, `border-hairline`, `bg-brass`, `text-on-brass`. No `stone-` or `orange-` classes; there are currently zero in the codebase and this must not reintroduce the first.

- [ ] **Step 2: Make the sidebar build from the outlets**

In `frontend/src/components/app/AppLayout.jsx`, fetch `/api/outlets` alongside the existing permissions fetch and render one entry per **active** outlet in the Restaurant section, labelled with the outlet's name rather than the word "Restaurant".

A property with no salon must have **no salon link** — not a disabled one, not one leading to an empty screen. This is the part of the whole design that makes the flow simpler rather than more complex.

- [ ] **Step 3: Add the route**

In `frontend/src/App.js`, add the route beside the other admin routes:

```jsx
<Route path="/admin/outlets" element={<Outlets />} />
```

- [ ] **Step 4: Build and verify**

```bash
cd frontend && CI=false npx craco build 2>&1 | grep -E "Compiled|Failed"
```

Expected: `Compiled with warnings.` — the two pre-existing warnings only, no new ones.

Then confirm no colour regression:

```bash
cd frontend/src && grep -rhoE "\b(bg|text|border|ring)-(stone|orange)-[0-9]{2,3}" --include="*.jsx" . | wc -l
```

Expected: `0`

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src/pages/admin/Outlets.jsx frontend/src/components/app/AppLayout.jsx frontend/src/App.js
git commit -m "feat: the outlets screen, and a sidebar that shows only what the property has"
```

---

## Final verification

Before calling this done, run all three suites and compare against the baselines:

```bash
cd backend
python3 -m pytest tests/ -q --ignore=tests/hotel_api_test.py --ignore=tests/backend_test.py
# expected: 1143 passed  (1127 baseline + 9 Task 1 + 3 Task 2 + 4 Task 5)

pkill -f "uvicorn server:app"; sleep 2; rm -f db.json
nohup env MONGO_URL=mock DEMO_LOGINS=true SEED_DEMO_CONTENT=true \
  STRIPE_WEBHOOK_SECRET=whsec_local python3 -m uvicorn server:app \
  --host 127.0.0.1 --port 8000 > /tmp/bf-api.log 2>&1 & disown
sleep 9

REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
# expected: 152 passed

REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
# expected: exactly 1 failed, 9 passed, 1 skipped — the Stripe failure is environmental
```

Any *failure* in the pure suites is a regression. The only expected assertion change in
the whole plan is the screen-catalogue set gaining `"admin.outlets"`, noted in Task 2.
