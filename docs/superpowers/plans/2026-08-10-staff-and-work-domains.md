# Staff Management & Work Domains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the admin a screen to create staff, set what area of the business each can work in, and shut off a leaver — with that boundary enforced by the API, not just hidden in the navigation.

**Architecture:** A **work domain** becomes a second authorization axis beside the existing role. `users` gains `domains: list[str]` and `active: bool`. A pure `can_access(user, domains, roles)` in `backend/services/access.py` holds every rule; a `require_access(domains, *roles)` dependency in `backend/security.py` applies it, replacing `require_roles` at every call site. `admin` bypasses the domain check but not the active check.

**Tech Stack:** FastAPI, Motor/PyMongo with a JSON-file mock fallback, Pydantic v2, pytest + pytest-xdist, React 19 + CRA/craco, Tailwind, axios, react-router-dom v7, `sonner`.

**Spec:** `docs/superpowers/specs/2026-08-10-staff-and-work-domains-design.md`

---

## Global Constraints

- **Domains are enforced at the API.** Hiding navigation is a convenience on top of a real boundary, never a substitute for it.
- **`admin` bypasses the domain check but NOT the active check.** A deactivated admin is locked out — otherwise deactivating a compromised admin would do nothing.
- **`shared` is satisfied by any user holding at least one domain**, subject to the usual role check. It cannot be combined with a specific domain.
- **An endpoint may declare several domains**; a user reaches it if they hold **any** of them. The order/menu/KOT/table endpoints are declared `("restaurant", "bar")` — declaring them `restaurant` alone would lock out a bar-only waiter.
- **A user with an empty `domains` list reaches nothing** (except an admin, who bypasses).
- **An unknown domain raises, never silently denies.** A typo in an endpoint's declaration must fail loudly rather than quietly refusing every user.
- **Leavers are deactivated, never deleted.** `posted_by` and `created_by` must still resolve to a name; deleting orphans the audit trail the folio ledger exists for.
- **Migration backfills existing accounts with all three domains and `active: true`.** Failing closed would lock staff out mid-service with no route back except the admin.
- **Lockout protection:** a user cannot change their own role or domains, cannot deactivate themselves, and the last active admin cannot be deactivated or demoted. Each returns 409.
- **Do NOT modify `backend/pytest.ini`.** Its `addopts` is pinned to `-n 2 --dist loadscope` with an explicit warning comment.
- Python: 4-space indent, type hints on signatures, `HTTPException` for errors. Match surrounding style.

### Test baselines (measured 2026-08-10, must be preserved)

| Suite | Command | Expected |
|---|---|---|
| Pure units | `python3 -m pytest tests/test_pricing.py tests/test_availability.py tests/test_folio.py -q` | `43 passed` |
| Hotel API | `REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q` | `68 passed` |
| Regression | `REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q` | **`1 failed, 9 passed, 1 skipped`** |

The regression suite is **intentionally not green**. The failure is `TestStripeCheckout::test_create_checkout_session_returns_stripe_url` — environmental, because no real `STRIPE_API_KEY` is configured so the vendored stub returns a local URL. It predates all this work. **Do not fix it, skip it, or touch payments tests.**

**This is the highest-risk plan in the programme so far**: it changes authorization on every endpoint. The existing 68 hotel tests are the regression net — they run as `admin` and seeded roles, so the migration defaults must keep them green. If they break, the change is wrong, not the tests.

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
| `backend/services/access.py` | pure: domain normalisation and the access decision |
| `backend/routers/staff.py` | admin-only staff CRUD, deactivation, password reset |
| `backend/migrations/backfill_domains.py` | one-shot, idempotent backfill of `domains` and `active` |

**Backend — modified:**

| File | Change |
|---|---|
| `backend/security.py` | add `require_access`; keep `require_roles` until every call site has moved, then remove |
| `backend/models/hotel.py` | no change — staff models live in the new router, matching how other routers hold their own models |
| `backend/routers/*.py` | swap `require_roles(...)` / bare `get_current_user` for `require_access(...)` — 12 files |
| `backend/routers/auth.py` | login refuses inactive users; remove `POST /auth/register` |
| `backend/server.py` | register the staff router; seed `domains` and `active` on seeded users |

**Backend tests:**

| File | Responsibility |
|---|---|
| `backend/tests/test_access.py` | pure unit tests, no server |
| `backend/tests/hotel_api_test.py` | append integration tests for the new boundaries |

**Frontend:**

| File | Responsibility |
|---|---|
| `frontend/src/pages/admin/Staff.jsx` | staff list, create, edit role and domains, deactivate, reset password |
| `frontend/src/components/app/AppLayout.jsx` | modify: group nav into Hotel / Restaurant / Staff, filtered by domain |
| `frontend/src/App.js` | modify: `/app/admin/staff` route |
| `frontend/src/contexts/AuthContext.jsx` | modify: expose `domains` and `active` from `/auth/me` |

---

## Task 1: The access service — pure functions

Every authorization rule in one testable place, before anything depends on it.

**Files:**
- Create: `backend/services/access.py`
- Test: `backend/tests/test_access.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DOMAINS: tuple[str, ...]` — `("hotel", "restaurant", "bar")`
  - `SHARED: str` — `"shared"`
  - `AccessError` (exception)
  - `normalise_domains(domains: str | tuple[str, ...] | list[str]) -> tuple[str, ...]`
  - `can_access(user: dict, domains, roles) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_access.py`:

```python
"""Pure authorization tests — no server, no database."""
import pytest
from services.access import (
    DOMAINS, SHARED, AccessError, can_access, normalise_domains,
)


def u(role="manager", domains=("restaurant",), active=True):
    return {"role": role, "domains": list(domains), "active": active}


MGR = ("admin", "manager")


def test_role_and_domain_both_required():
    assert can_access(u(domains=("restaurant",)), "restaurant", MGR) is True
    assert can_access(u(domains=("restaurant",)), "hotel", MGR) is False


def test_wrong_role_is_refused_even_with_the_domain():
    assert can_access(u(role="waiter", domains=("hotel",)), "hotel", MGR) is False


def test_admin_reaches_every_domain_regardless_of_its_own_domains():
    admin = u(role="admin", domains=())
    for d in DOMAINS:
        assert can_access(admin, d, MGR) is True


def test_inactive_user_is_refused():
    assert can_access(u(domains=("restaurant",), active=False), "restaurant", MGR) is False


def test_inactive_admin_is_also_refused():
    # Deactivating a compromised admin must actually lock them out.
    assert can_access(u(role="admin", active=False), "hotel", MGR) is False


def test_shared_is_satisfied_by_any_domain():
    assert can_access(u(domains=("bar",)), SHARED, MGR) is True
    assert can_access(u(domains=("hotel",)), SHARED, MGR) is True


def test_shared_still_enforces_role():
    assert can_access(u(role="kitchen", domains=("bar",)), SHARED, MGR) is False


def test_user_with_no_domains_reaches_nothing():
    assert can_access(u(domains=()), "restaurant", MGR) is False
    assert can_access(u(domains=()), SHARED, MGR) is False


def test_several_domains_on_the_endpoint_grant_on_any_match():
    # The order screens are declared restaurant AND bar; a bar-only waiter must reach them.
    waiter = u(role="waiter", domains=("bar",))
    assert can_access(waiter, ("restaurant", "bar"), ("admin", "manager", "waiter")) is True


def test_several_domains_on_the_user_grant_on_any_match():
    duty = u(domains=("hotel", "restaurant"))
    assert can_access(duty, "hotel", MGR) is True
    assert can_access(duty, "restaurant", MGR) is True
    assert can_access(duty, "bar", MGR) is False


def test_empty_roles_means_any_authenticated_role():
    # Used by endpoints that were previously bare get_current_user.
    assert can_access(u(role="kitchen", domains=("bar",)), "bar", ()) is True


def test_unknown_domain_raises_rather_than_denying_silently():
    with pytest.raises(AccessError):
        normalise_domains("spa")
    with pytest.raises(AccessError):
        can_access(u(), "spa", MGR)


def test_no_domain_declared_raises():
    with pytest.raises(AccessError):
        normalise_domains(())


def test_shared_cannot_be_mixed_with_a_specific_domain():
    with pytest.raises(AccessError):
        normalise_domains((SHARED, "hotel"))


def test_single_domain_string_and_tuple_are_equivalent():
    assert normalise_domains("hotel") == ("hotel",)
    assert normalise_domains(("hotel",)) == ("hotel",)
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && python3 -m pytest tests/test_access.py -q`
Expected: collection error — `No module named 'services.access'`

- [ ] **Step 3: Implement `backend/services/access.py`**

```python
"""Authorization: role plus work domain.

Pure functions over a user dict — no database, no request — so every access rule is
testable in isolation and readable in one place.

A role says what someone does; a domain says which part of the business they do it in.
Both must pass, except for admin, who is never domain-checked.
"""

# The areas a staff member can be assigned to.
DOMAINS = ("hotel", "restaurant", "bar")

# Endpoints serving more than one area declare this instead. A bar regular and a hotel
# guest are the same person, so splitting guest records by domain would stop the desk
# seeing an arrival's bar history — which is the product's whole claim.
SHARED = "shared"


class AccessError(Exception):
    """Raised when an access rule is configured with something meaningless."""


def normalise_domains(domains: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Accept a single domain or several, and reject unknown values loudly.

    A typo in an endpoint's declared domain must fail at import, not silently deny
    every user at runtime.
    """
    if isinstance(domains, str):
        domains = (domains,)
    out = tuple(domains)
    if not out:
        raise AccessError("an endpoint must declare at least one domain")
    for d in out:
        if d != SHARED and d not in DOMAINS:
            raise AccessError(f"unknown domain: {d}")
    if SHARED in out and len(out) > 1:
        raise AccessError("shared cannot be combined with a specific domain")
    return out


def can_access(
    user: dict,
    domains: str | tuple[str, ...] | list[str],
    roles: tuple[str, ...] | list[str],
) -> bool:
    """True when this user may reach an endpoint requiring these roles and domains."""
    required = normalise_domains(domains)

    # Deactivated accounts are refused regardless of role. This applies to admin too —
    # otherwise deactivating a compromised admin would do nothing.
    if not user.get("active", True):
        return False

    role = user.get("role")
    if roles and role not in roles:
        return False

    # Admin is never domain-checked: one admin sees the whole property.
    if role == "admin":
        return True

    held = tuple(user.get("domains") or ())
    if not held:
        return False

    if required == (SHARED,):
        return True

    return any(d in held for d in required)
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python3 -m pytest tests/test_access.py -q`
Expected: `15 passed`

- [ ] **Step 5: Confirm the other pure suites are unaffected**

Run: `cd backend && python3 -m pytest tests/test_pricing.py tests/test_availability.py tests/test_folio.py -q`
Expected: `43 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/access.py backend/tests/test_access.py
git commit -m "feat: access service combining role and work domain"
```

---

## Task 2: User fields, migration, and the login active check

**Files:**
- Create: `backend/migrations/backfill_domains.py`
- Modify: `backend/server.py` (seed users with domains and active), `backend/routers/auth.py` (login refuses inactive)
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `services.access.DOMAINS`
- Produces: seeded users carry `domains` and `active`; `POST /auth/login` returns 401 for an inactive user

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_seeded_admin_has_all_domains_and_is_active(admin):
    me = admin.get(f"{API}/auth/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["active"] is True
    assert set(body["domains"]) == {"hotel", "restaurant", "bar"}
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k seeded_admin`
Expected: FAIL — `KeyError: 'active'` or the domains assertion, because `/auth/me` does not return those fields yet.

- [ ] **Step 3: Seed the new fields**

In `backend/server.py`, inside `seed_data`, where each default user is inserted, add both
fields to the inserted document:

```python
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": u["email"],
                "name": u["name"],
                "role": u["role"],
                "password_hash": hash_password(u["password"]),
                # Seeded staff work everywhere; the admin narrows them from the staff
                # screen. Seeding them narrow would lock a fresh install out of itself.
                "domains": list(DOMAINS),
                "active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
```

Add the import at the top of `backend/server.py`:

```python
from services.access import DOMAINS
```

- [ ] **Step 4: Return the fields from `/auth/me` and `/auth/staff`**

In `backend/routers/auth.py`, extend the `UserPublic` model:

```python
class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    role: str
    domains: list[str] = []
    active: bool = True
```

and make `me` and `list_staff` include them — both already build their response from the
user document, so ensure `domains` and `active` are carried through rather than dropped.

- [ ] **Step 5: Refuse inactive users at login**

In `backend/routers/auth.py`, inside `login`, after the password check succeeds:

```python
    # Refused at the door rather than on the first request. The message is identical to
    # a wrong password on purpose: revealing that an account exists but is disabled tells
    # a former employee their guess was right.
    if not user.get("active", True):
        raise HTTPException(status_code=401, detail="Invalid email or password")
```

- [ ] **Step 6: Write the migration**

Create `backend/migrations/backfill_domains.py`:

```python
"""One-shot: give every existing user all domains and mark them active.

Idempotent — a user that already has both fields is left alone, so re-running is safe.

Backfilling wide rather than narrow is deliberate. Domains are enforced at the API, so
seeding empty would lock every existing account out the instant this deploys, mid-service,
with no route back except the admin. The admin narrows people afterwards, deliberately.

    cd backend && MONGO_URL=... python3 -m migrations.backfill_domains
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from db import db  # noqa: E402
from services.access import DOMAINS  # noqa: E402


async def main() -> None:
    users = await db.users.find({}, {"_id": 0}).to_list(10000)
    updated = skipped = 0
    for user in users:
        patch = {}
        if not user.get("domains"):
            patch["domains"] = list(DOMAINS)
        if "active" not in user:
            patch["active"] = True
        if not patch:
            skipped += 1
            continue
        await db.users.update_one({"id": user["id"]}, {"$set": patch})
        updated += 1
    print(f"users updated: {updated}, already current: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Restart, run the test, and prove the migration is idempotent**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k seeded_admin
MONGO_URL=mock python3 -m migrations.backfill_domains
MONGO_URL=mock python3 -m migrations.backfill_domains
```

Expected: the test passes; the first migration run reports some number updated or zero
(the seed already sets the fields), and the **second run reports `users updated: 0`**.

- [ ] **Step 8: Confirm the baselines**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
```

Expected: hotel suite green; regression suite `1 failed, 9 passed, 1 skipped`.

- [ ] **Step 9: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/migrations/ backend/server.py backend/routers/auth.py backend/tests/hotel_api_test.py
git commit -m "feat: user domains and active flag, with idempotent backfill"
```

---

## Task 3: The `require_access` dependency

**Files:**
- Modify: `backend/security.py`
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `services.access.can_access`
- Produces: `require_access(domains: str | tuple[str, ...], *roles: str)` — a FastAPI dependency factory returning the current user dict, or raising 403

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/hotel_api_test.py`:

```python
def _staff_session(admin, email, password, role, domains):
    """Create a staff user directly via the seeded admin, and return a logged-in session."""
    import requests as _rq
    admin.post(f"{API}/staff", json={
        "name": email.split("@")[0], "email": email, "password": password,
        "role": role, "domains": domains})
    s = _rq.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


def test_restaurant_manager_is_refused_hotel_endpoints(admin):
    email = f"rest-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "rest12345", "manager", ["restaurant"])
    assert s.get(f"{API}/bookings").status_code == 403
    assert s.get(f"{API}/tables").status_code == 200
```

This test also needs `POST /api/staff`, which Task 4 builds. Run it after Task 4; for now
it documents the boundary the dependency must create.

- [ ] **Step 2: Add `require_access` to `backend/security.py`**

```python
from services.access import can_access


def require_access(domains: str | tuple[str, ...], *roles: str):
    """Dependency: the caller must be active, hold one of `roles`, and hold a domain.

    Replaces require_roles. Declaring the domain at each call site keeps authorization
    greppable — you can read any route and see exactly who reaches it. Inferring it from
    the router or the URL would make a misfiled endpoint silently inherit the wrong
    permission.
    """

    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if not can_access(user, domains, roles):
            raise HTTPException(status_code=403, detail="Not permitted")
        return user

    return checker
```

Keep `require_roles` for now — call sites move in Task 4 and it is removed there, so the
app stays runnable between the two.

- [ ] **Step 3: Verify the app still boots and the baselines hold**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
```

Expected: server boots; hotel suite green — nothing uses `require_access` yet, so this
only proves the addition broke nothing.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/security.py backend/tests/hotel_api_test.py
git commit -m "feat: require_access dependency combining role and domain"
```

---

## Task 4: Staff router — create, edit, deactivate, reset password

**Files:**
- Create: `backend/routers/staff.py`
- Modify: `backend/server.py` (register router), `backend/routers/auth.py` (remove `POST /auth/register`)
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `security.require_access`, `services.access.DOMAINS`, `security.hash_password`
- Produces: `GET|POST /api/staff`, `PUT /api/staff/{id}`, `POST /api/staff/{id}/active`, `POST /api/staff/{id}/password`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_admin_creates_staff_with_domains(admin):
    email = f"new-{uuid.uuid4().hex[:6]}@barflow.io"
    r = admin.post(f"{API}/staff", json={
        "name": "New Person", "email": email, "password": "newpass123",
        "role": "waiter", "domains": ["bar"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domains"] == ["bar"]
    assert body["active"] is True
    assert "password_hash" not in body and "password" not in body


def test_duplicate_email_is_refused(admin):
    email = f"dup-{uuid.uuid4().hex[:6]}@barflow.io"
    body = {"name": "A", "email": email, "password": "pass12345",
            "role": "waiter", "domains": ["bar"]}
    assert admin.post(f"{API}/staff", json=body).status_code == 200
    assert admin.post(f"{API}/staff", json=body).status_code == 409


def test_non_admin_cannot_reach_staff(admin):
    email = f"mgr-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "mgr12345", "manager", ["restaurant"])
    assert s.get(f"{API}/staff").status_code == 403


def test_empty_domains_on_a_non_admin_is_refused(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Nobody", "email": f"none-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "pass12345", "role": "waiter", "domains": []})
    assert r.status_code == 400, r.text


def test_unknown_domain_is_refused(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Spa", "email": f"spa-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "pass12345", "role": "waiter", "domains": ["spa"]})
    assert r.status_code == 422, r.text


def test_deactivated_user_cannot_log_in(admin):
    import requests as _rq
    email = f"leaver-{uuid.uuid4().hex[:6]}@barflow.io"
    created = admin.post(f"{API}/staff", json={
        "name": "Leaver", "email": email, "password": "leave12345",
        "role": "waiter", "domains": ["bar"]}).json()

    ok = _rq.post(f"{API}/auth/login", json={"email": email, "password": "leave12345"})
    assert ok.status_code == 200

    assert admin.post(f"{API}/staff/{created['id']}/active",
                      json={"active": False}).status_code == 200

    after = _rq.post(f"{API}/auth/login", json={"email": email, "password": "leave12345"})
    assert after.status_code == 401, after.text


def test_deactivated_users_existing_token_stops_working(admin):
    email = f"tok-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "tok12345", "waiter", ["bar"])
    assert s.get(f"{API}/auth/me").status_code == 200

    uid = next(u["id"] for u in admin.get(f"{API}/staff").json() if u["email"] == email)
    admin.post(f"{API}/staff/{uid}/active", json={"active": False})

    # Same token, now refused — the active check runs per request, not only at login.
    assert s.get(f"{API}/auth/me").status_code == 403


def test_admin_cannot_deactivate_themselves(admin):
    me = admin.get(f"{API}/auth/me").json()
    r = admin.post(f"{API}/staff/{me['id']}/active", json={"active": False})
    assert r.status_code == 409, r.text


def test_admin_cannot_change_their_own_role_or_domains(admin):
    me = admin.get(f"{API}/auth/me").json()
    r = admin.put(f"{API}/staff/{me['id']}", json={
        "name": me["name"], "role": "waiter", "domains": ["bar"]})
    assert r.status_code == 409, r.text


def test_last_active_admin_cannot_be_demoted(admin):
    # The seeded admin is the only admin in a fresh database.
    me = admin.get(f"{API}/auth/me").json()
    others = [u for u in admin.get(f"{API}/staff").json()
              if u["role"] == "admin" and u["active"] and u["id"] != me["id"]]
    if others:
        return  # another admin exists; the rule under test does not apply
    r = admin.put(f"{API}/staff/{me['id']}", json={
        "name": me["name"], "role": "manager", "domains": ["hotel"]})
    assert r.status_code == 409, r.text


def test_admin_resets_a_password(admin):
    import requests as _rq
    email = f"reset-{uuid.uuid4().hex[:6]}@barflow.io"
    created = admin.post(f"{API}/staff", json={
        "name": "Reset Me", "email": email, "password": "oldpass123",
        "role": "waiter", "domains": ["bar"]}).json()

    assert admin.post(f"{API}/staff/{created['id']}/password",
                      json={"password": "newpass456"}).status_code == 200

    assert _rq.post(f"{API}/auth/login",
                    json={"email": email, "password": "oldpass123"}).status_code == 401
    assert _rq.post(f"{API}/auth/login",
                    json={"email": email, "password": "newpass456"}).status_code == 200
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "staff or deactivat or password or domains or admin_cannot or last_active"`
Expected: FAIL — 404, `/api/staff` does not exist.

- [ ] **Step 3: Implement `backend/routers/staff.py`**

```python
"""Staff administration: who works here, what they do, and where they do it.

Admin-only. Leavers are deactivated rather than deleted, because posted_by and
created_by on orders and folio entries must still resolve to a name — deleting a user
would orphan the audit trail the ledger exists to keep.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from db import db
from security import hash_password, require_access
from services.access import DOMAINS, SHARED

router = APIRouter()

# Staff administration is admin-only, and is not tied to any one area of the business.
ADMIN = require_access(SHARED, "admin")

Domain = Literal["hotel", "restaurant", "bar"]


class StaffIn(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: Literal["admin", "manager", "waiter", "kitchen", "front_desk"]
    domains: List[Domain] = []


class StaffUpdateIn(BaseModel):
    name: str
    role: Literal["admin", "manager", "waiter", "kitchen", "front_desk"]
    domains: List[Domain] = []


class ActiveIn(BaseModel):
    active: bool


class PasswordIn(BaseModel):
    password: str


def _public(user: dict) -> dict:
    """Never return password_hash. Building the response explicitly rather than
    deleting keys means a new sensitive field cannot leak by being forgotten."""
    return {
        "id": user["id"],
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "domains": user.get("domains") or [],
        "active": user.get("active", True),
        "created_at": user.get("created_at"),
    }


async def _count_other_active_admins(exclude_id: str) -> int:
    admins = await db.users.find(
        {"role": "admin", "id": {"$ne": exclude_id}}, {"_id": 0}).to_list(1000)
    return sum(1 for a in admins if a.get("active", True))


@router.get("/staff")
async def list_staff(user: dict = Depends(ADMIN)):
    users = await db.users.find({}, {"_id": 0}).to_list(1000)
    return [_public(u) for u in sorted(users, key=lambda x: x.get("name") or "")]


@router.post("/staff")
async def create_staff(payload: StaffIn, user: dict = Depends(ADMIN)):
    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "A staff member with this email already exists")

    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "email": email,
        "role": payload.role,
        "domains": list(dict.fromkeys(payload.domains)),
        "active": True,
        "password_hash": hash_password(payload.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    return _public(doc)


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, payload: StaffUpdateIn, user: dict = Depends(ADMIN)):
    target = await db.users.find_one({"id": staff_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")

    # Without this, one edit locks the owner out of their own system with no recovery
    # short of editing the database by hand.
    if staff_id == user.get("id"):
        raise HTTPException(409, "You cannot change your own role or domains")

    if payload.role != "admin" and not payload.domains:
        raise HTTPException(400, "A non-admin needs at least one work domain")

    if (target.get("role") == "admin" and payload.role != "admin"
            and await _count_other_active_admins(staff_id) == 0):
        raise HTTPException(409, "This is the last active admin and cannot be demoted")

    await db.users.update_one({"id": staff_id}, {"$set": {
        "name": payload.name.strip(),
        "role": payload.role,
        "domains": list(dict.fromkeys(payload.domains)),
    }})
    return _public(await db.users.find_one({"id": staff_id}, {"_id": 0}))


@router.post("/staff/{staff_id}/active")
async def set_active(staff_id: str, payload: ActiveIn, user: dict = Depends(ADMIN)):
    target = await db.users.find_one({"id": staff_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Staff member not found")
    if staff_id == user.get("id"):
        raise HTTPException(409, "You cannot deactivate yourself")

    if (not payload.active and target.get("role") == "admin"
            and await _count_other_active_admins(staff_id) == 0):
        raise HTTPException(409, "This is the last active admin and cannot be deactivated")

    await db.users.update_one({"id": staff_id}, {"$set": {"active": payload.active}})
    return _public(await db.users.find_one({"id": staff_id}, {"_id": 0}))


@router.post("/staff/{staff_id}/password")
async def reset_password(staff_id: str, payload: PasswordIn, user: dict = Depends(ADMIN)):
    if not await db.users.find_one({"id": staff_id}):
        raise HTTPException(404, "Staff member not found")
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    await db.users.update_one({"id": staff_id}, {"$set": {
        "password_hash": hash_password(payload.password)}})
    return {"ok": True}
```

- [ ] **Step 4: Register the router and remove the superseded endpoint**

In `backend/server.py`, add `staff` to the `from routers import ...` line and the
`for module in (...)` loop.

In `backend/routers/auth.py`, **delete** `POST /auth/register` and its `RegisterIn` model.
It is superseded by `POST /api/staff` and would otherwise remain a second, unscreened way
to create users that bypasses the domain and password rules above.

**One existing test calls it and must be updated in the same step.**
`backend/tests/hotel_api_test.py` has `test_front_desk_role_exists`, written in an earlier
sub-project, which registers a front_desk user to prove that role is valid. Rewrite it to
use the new endpoint, keeping what it actually tests — that `front_desk` is an accepted
role:

```python
def test_front_desk_role_exists(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Desk Tester",
        "email": f"desk-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "desk12345",
        "role": "front_desk",
        "domains": ["hotel"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "front_desk"
```

The unique email also makes it self-contained, which the original was not — it relied on
accepting either 200 or a 400 "already exists".

- [ ] **Step 5: Restart and run**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
```

Expected: hotel suite green including the eleven new tests; regression suite
`1 failed, 9 passed, 1 skipped`.

**Verified:** `backend/tests/backend_test.py` does **not** call `/auth/register`, so its
baseline is unaffected by the removal. The only caller is the hotel test rewritten above.
If `backend_test.py` does fail, **stop and report** — do not edit it.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/staff.py backend/routers/auth.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: admin staff management with deactivation and lockout guards"
```

---

## Task 5: Move every call site to `require_access`

The task that actually creates the boundary. Mechanical, but it touches authorization on
every endpoint — this is where a mistake silently opens or closes a door.

**Files:**
- Modify: all 12 files in `backend/routers/` that use `require_roles` or a bare `get_current_user`
- Modify: `backend/security.py` (remove `require_roles` once nothing uses it)
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `security.require_access`, `services.access.SHARED`
- Produces: no new API surface; every existing endpoint now enforces a domain

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`:

```python
def test_hotel_front_desk_is_refused_restaurant_writes(admin):
    email = f"fd-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "fd12345678", "front_desk", ["hotel"])
    assert s.get(f"{API}/folios").status_code == 200
    assert s.post(f"{API}/menu", json={
        "name": "Nope", "category": "Cocktails", "price": 100,
        "station": "bar", "description": ""}).status_code == 403


def test_bar_only_waiter_reaches_the_order_screens(admin):
    # The order endpoints are declared ("restaurant", "bar"); declaring them restaurant
    # alone would lock a bar-only waiter out of the POS.
    email = f"bar-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "bar12345678", "waiter", ["bar"])
    assert s.get(f"{API}/tables").status_code == 200
    assert s.get(f"{API}/orders/kot").status_code == 200


def test_shared_endpoints_reachable_from_any_domain(admin):
    email = f"sh-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "sh12345678", "manager", ["bar"])
    assert s.get(f"{API}/guests").status_code == 200
    assert s.get(f"{API}/inventory").status_code == 200


def test_admin_still_reaches_everything(admin):
    for path in ("/bookings", "/tables", "/guests", "/inventory", "/folios", "/staff"):
        assert admin.get(f"{API}{path}").status_code == 200, path
```

- [ ] **Step 2: Run to confirm they fail**

Run: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k "refused_restaurant or bar_only or shared_endpoints"`
Expected: FAIL — the domain-restricted users currently reach everything, because no call
site enforces a domain yet.

- [ ] **Step 3: Move the hotel routers**

In each of `backend/routers/bookings.py`, `rooms.py`, `rates.py`, `folios.py`,
`frontdesk.py`: change the import from `require_roles` to `require_access`, and give every
constant and inline dependency the `"hotel"` domain. The module-level constants become:

```python
# bookings.py
BOOK = require_access("hotel", "admin", "manager", "front_desk")

# frontdesk.py
DESK = require_access("hotel", "admin", "manager", "front_desk")
IN_HOUSE_LOOKUP = require_access("hotel", "admin", "manager", "front_desk", "waiter")

# folios.py
DESK = require_access("hotel", "admin", "manager", "front_desk")
MANAGER = require_access("hotel", "admin", "manager")

# rooms.py
MANAGE = require_access("hotel", "admin", "manager")

# rates.py
MANAGE = require_access("hotel", "admin", "manager")
```

Inline `Depends(require_roles(...))` in these files becomes
`Depends(require_access("hotel", ...))` with the same roles. Any bare
`Depends(get_current_user)` in these files becomes `Depends(require_access("hotel"))` —
no roles means any role, which preserves today's behaviour while adding the domain.

**`IN_HOUSE_LOOKUP` keeps `waiter`** — a waiter must find the in-house guest to charge a
bar bill to their room. It stays `"hotel"` because that is the data it reads.

- [ ] **Step 4: Move the outlet routers**

In `backend/routers/orders.py`, `menu.py`, `tables.py`: the same swap, with the domain
tuple `("restaurant", "bar")` so either domain grants access.

```python
OUTLET = ("restaurant", "bar")
```

Define that constant at the top of each of the three files and use it:
`require_access(OUTLET, "admin", "manager")`, and
`Depends(require_access(OUTLET))` where a bare `get_current_user` was used.

`backend/routers/reports.py` uses the same `OUTLET` tuple — these are outlet sales
analytics. The hotel report screen is a later sub-project and will declare `"hotel"`.

- [ ] **Step 5: Move the shared routers**

`backend/routers/guests.py` and `inventory.py` use `SHARED`:

```python
from services.access import SHARED

MANAGE = require_access(SHARED, "admin", "manager", "front_desk")   # guests.py
```

and for `inventory.py`, each existing `require_roles(...)` becomes
`require_access(SHARED, ...)` with the same roles.

In `backend/routers/auth.py`, `/auth/me` becomes `Depends(require_access(SHARED))` — every
logged-in staff member reads their own profile.

- [ ] **Step 6: Remove `require_roles`**

Confirm nothing references it, then delete it from `backend/security.py`:

```bash
cd ~/dev/bar-management-system/backend
grep -rn "require_roles" routers/ security.py tests/ || echo "no references remain"
```

Expected: `no references remain`. If anything is listed, move it before deleting — leaving
both mechanisms in place means the next person picks the one without a domain check.

- [ ] **Step 7: Restart and run everything**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/ > /dev/null
python3 -m pytest tests/test_access.py tests/test_pricing.py tests/test_availability.py tests/test_folio.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
```

Expected: pure suites `58 passed` (43 existing + 15 access); hotel suite green; regression
suite `1 failed, 9 passed, 1 skipped`.

**The existing hotel tests are the regression net.** They run as `admin` and seeded roles,
which the migration gives all domains — so they must stay green. If one fails, the domain
assigned to that endpoint is wrong. Fix the assignment, not the test.

- [ ] **Step 8: Prove no endpoint was left unguarded**

```bash
cd ~/dev/bar-management-system/backend
grep -rn "Depends(get_current_user)" routers/ | grep -v "security.py" || echo "no bare get_current_user remains"
```

Expected: `no bare get_current_user remains` — every endpoint now declares a domain.
`get_current_user` itself stays in `security.py`, since `require_access` depends on it.

- [ ] **Step 9: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/ backend/security.py backend/tests/hotel_api_test.py
git commit -m "feat: enforce work domains on every endpoint"
```

---

## Task 6: Staff screen

**Files:**
- Create: `frontend/src/pages/admin/Staff.jsx`
- Modify: `frontend/src/App.js`

**Interfaces:**
- Consumes: `GET|POST /api/staff`, `PUT /api/staff/{id}`, `POST /api/staff/{id}/active`, `POST /api/staff/{id}/password`
- Produces: route `/app/admin/staff`

- [ ] **Step 1: Read the existing patterns first**

Read `frontend/src/pages/hotel/Rates.jsx` and `frontend/src/pages/hotel/Folio.jsx` before
writing. Follow what is there: the eyebrow plus big uppercase `<h1>`, `formatApiErrorDetail`
with `toast.error`, `tabular-nums`, `overflow-x-auto` on wide tables, and — for anything
destructive — the **inline two-step confirm panel** used for cancel and void. This codebase
does not use `window.prompt`, `window.confirm` or `alert`, and deactivating a staff member
is exactly the kind of action that needs the inline pattern rather than a browser dialog.

- [ ] **Step 2: Create `frontend/src/pages/admin/Staff.jsx`**

```jsx
import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const ROLES = ["admin", "manager", "front_desk", "waiter", "kitchen"];
const DOMAINS = ["hotel", "restaurant", "bar"];

const BLANK = {
  name: "",
  email: "",
  password: "",
  role: "waiter",
  domains: ["restaurant"],
};

function DomainPicker({ value, onChange, disabled }) {
  const toggle = (d) =>
    onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d]);
  return (
    <div className="flex gap-2 flex-wrap">
      {DOMAINS.map((d) => (
        <button
          key={d}
          type="button"
          disabled={disabled}
          onClick={() => toggle(d)}
          className={`text-[10px] tracking-widest uppercase border rounded-full px-3 py-1 disabled:opacity-40 ${
            value.includes(d)
              ? "border-orange-500 text-orange-400"
              : "border-stone-700 text-stone-500 hover:border-stone-500"
          }`}
        >
          {d}
        </button>
      ))}
    </div>
  );
}

export default function Staff() {
  const [rows, setRows] = useState([]);
  const [me, setMe] = useState(null);
  const [creating, setCreating] = useState(BLANK);
  const [editing, setEditing] = useState(null);      // { id, name, role, domains }
  const [deactivating, setDeactivating] = useState(null); // staff row
  const [resetting, setResetting] = useState(null);  // { id, password }
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () =>
      Promise.all([api.get("/staff"), api.get("/auth/me")])
        .then(([s, m]) => {
          setRows(s.data);
          setMe(m.data);
        })
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const create = () =>
    run(async () => {
      if (!creating.name.trim() || !creating.email.trim()) {
        throw { response: { data: { detail: "Name and email are required" } } };
      }
      if (creating.role !== "admin" && creating.domains.length === 0) {
        throw {
          response: { data: { detail: "Pick at least one work domain" } },
        };
      }
      await api.post("/staff", creating);
      setCreating(BLANK);
      toast.success("Staff member added");
    });

  const saveEdit = () =>
    run(async () => {
      await api.put(`/staff/${editing.id}`, {
        name: editing.name,
        role: editing.role,
        domains: editing.domains,
      });
      setEditing(null);
      toast.success("Saved");
    });

  const confirmDeactivate = () =>
    run(async () => {
      await api.post(`/staff/${deactivating.id}/active`, {
        active: !deactivating.active,
      });
      setDeactivating(null);
      toast.success(deactivating.active ? "Deactivated" : "Reactivated");
    });

  const confirmReset = () =>
    run(async () => {
      if (resetting.password.length < 8) {
        throw {
          response: { data: { detail: "Password must be at least 8 characters" } },
        };
      }
      await api.post(`/staff/${resetting.id}/password`, {
        password: resetting.password,
      });
      setResetting(null);
      toast.success("Password reset");
    });

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Staff
      </h1>

      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-8 max-w-4xl">
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
          Add a staff member
        </h2>
        <div className="flex flex-wrap gap-4 items-end">
          {[
            ["name", "Name", "text"],
            ["email", "Email", "email"],
            ["password", "Password", "password"],
          ].map(([k, label, type]) => (
            <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
              {label}
              <input
                type={type}
                value={creating[k]}
                onChange={(e) => setCreating({ ...creating, [k]: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          ))}
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Role
            <select
              value={creating.role}
              onChange={(e) => setCreating({ ...creating, role: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <div className="text-xs tracking-widest uppercase text-stone-500">
            Works in
            <div className="mt-2">
              <DomainPicker
                value={creating.domains}
                onChange={(d) => setCreating({ ...creating, domains: d })}
                disabled={creating.role === "admin"}
              />
            </div>
          </div>
          <button
            onClick={create}
            disabled={busy}
            className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Add
          </button>
        </div>
        <p className="text-xs text-stone-500 mt-4">
          An admin reaches everything regardless of domains. Everyone else reaches only the
          areas selected here — enforced by the API, not just hidden in the menu.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
              <th className="text-left py-2 px-3 border-b border-stone-800">Name</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Email</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Role</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Works in</th>
              <th className="text-left py-2 px-3 border-b border-stone-800">Status</th>
              <th className="border-b border-stone-800" />
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => {
              const isSelf = me && u.id === me.id;
              return (
                <tr key={u.id} className={u.active ? "" : "opacity-50"}>
                  <td className="py-2 px-3 border-b border-stone-800">
                    {u.name}
                    {isSelf && (
                      <span className="text-[10px] tracking-widest uppercase text-stone-500 ml-2">
                        you
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs text-stone-400">
                    {u.email}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800">
                    {u.role.replace("_", " ")}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800 text-xs text-stone-400">
                    {u.role === "admin" ? "everything" : (u.domains || []).join(", ") || "—"}
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800">
                    <span
                      className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 ${
                        u.active
                          ? "text-orange-400 border-orange-500/40"
                          : "text-stone-500 border-stone-700"
                      }`}
                    >
                      {u.active ? "active" : "inactive"}
                    </span>
                  </td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right whitespace-nowrap">
                    <button
                      onClick={() =>
                        setEditing({
                          id: u.id,
                          name: u.name,
                          role: u.role,
                          domains: u.domains || [],
                        })
                      }
                      disabled={busy || isSelf}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400 disabled:opacity-30 mr-3"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => setResetting({ id: u.id, password: "" })}
                      disabled={busy}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400 disabled:opacity-30 mr-3"
                    >
                      Password
                    </button>
                    <button
                      onClick={() => setDeactivating(u)}
                      disabled={busy || isSelf}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-red-400 disabled:opacity-30"
                    >
                      {u.active ? "Deactivate" : "Reactivate"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {editing && (
        <div className="mt-8 border border-stone-800 bg-stone-900 rounded p-5 max-w-2xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Edit {editing.name}
          </h3>
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Name
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Role
              <select
                value={editing.role}
                onChange={(e) => setEditing({ ...editing, role: e.target.value })}
                className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <div className="text-xs tracking-widest uppercase text-stone-500">
              Works in
              <div className="mt-2">
                <DomainPicker
                  value={editing.domains}
                  onChange={(d) => setEditing({ ...editing, domains: d })}
                  disabled={editing.role === "admin"}
                />
              </div>
            </div>
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={saveEdit}
              disabled={busy}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Save
            </button>
            <button
              onClick={() => setEditing(null)}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {deactivating && (
        <div className="mt-8 border border-stone-800 bg-stone-900 rounded p-5 max-w-2xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            {deactivating.active ? "Deactivate" : "Reactivate"} {deactivating.name}?
          </h3>
          <p className="text-sm text-stone-400 mb-4">
            {deactivating.active
              ? "They will be signed out and unable to log in. Their record stays, so past bills and folio entries still show who posted them."
              : "They will be able to log in again with their existing password."}
          </p>
          <div className="flex gap-3">
            <button
              onClick={confirmDeactivate}
              disabled={busy}
              className="border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {deactivating.active ? "Deactivate" : "Reactivate"}
            </button>
            <button
              onClick={() => setDeactivating(null)}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {resetting && (
        <div className="mt-8 border border-stone-800 bg-stone-900 rounded p-5 max-w-2xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Set a new password
          </h3>
          <input
            type="password"
            value={resetting.password}
            onChange={(e) => setResetting({ ...resetting, password: e.target.value })}
            placeholder="At least 8 characters"
            className="bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
          <div className="flex gap-3 mt-5">
            <button
              onClick={confirmReset}
              disabled={busy || resetting.password.length < 8}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Set password
            </button>
            <button
              onClick={() => setResetting(null)}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-stone-500 mt-4">
            Tell them the new password directly — there is no email delivery in this app.
          </p>
        </div>
      )}
    </div>
  );
}
```

Edit and Deactivate are disabled on your own row, matching the server's 409 — the control
should not offer an action the API will refuse.

- [ ] **Step 3: Wire the route**

`frontend/src/App.js`:

```jsx
import Staff from "@/pages/admin/Staff";
```

```jsx
        <Route path="/admin/staff" element={<Protected roles={["admin"]}><Staff /></Protected>} />
```

- [ ] **Step 4: Verify**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build && rm -rf build
```

Expected: compiles with only the pre-existing eslint warnings in `CustomerMenu.jsx` and
`Reservations.jsx`. A dev server may already run on port 3001 — check
`lsof -nP -iTCP:3001 -sTCP:LISTEN` and reuse it rather than starting another.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: admin staff screen with roles, domains and deactivation"
```

---

## Task 7: Segmented navigation

**Files:**
- Modify: `frontend/src/components/app/AppLayout.jsx`, `frontend/src/contexts/AuthContext.jsx`

**Interfaces:**
- Consumes: `domains` and `role` from `/auth/me` via `useAuth()`
- Produces: nav grouped into Hotel / Restaurant / Staff, filtered by the signed-in user's domains

- [ ] **Step 1: Expose domains from the auth context**

Read `frontend/src/contexts/AuthContext.jsx`. It already stores the user object from
`/auth/me`, which now carries `domains` and `active`. Confirm those fields reach consumers
unchanged — if the context copies specific fields rather than the whole object, add them.

- [ ] **Step 2: Group and filter the nav**

Read `frontend/src/components/app/AppLayout.jsx` first. It already has a `section` concept
and an `isNavItemActive` helper with an `exclude` mechanism. **Add to it; do not
restructure it.**

Give each nav item a `domains` field naming the areas it belongs to, then filter:

```jsx
// A nav item is visible when the user is an admin, or holds any domain the item serves.
// This mirrors the server's rule rather than inventing a second one — the API is the
// real boundary, and a mismatch here would show a menu entry that 403s when clicked.
const visibleFor = (item, user) => {
  if (!item.to) return true;                  // section headings
  if (user?.role === "admin") return true;
  if (!item.domains) return true;             // unscoped items, e.g. Overview
  const held = user?.domains || [];
  return item.domains.some((d) => held.includes(d));
};
```

Tag the existing items: the hotel group entries get `domains: ["hotel"]`, the
bar/restaurant entries get `domains: ["restaurant", "bar"]`, and the new Staff section gets
no `domains` but is rendered only when `user.role === "admin"`.

There is **no "Restaurant" heading today** — those items sit at the top of `NAV` with no
section above them. Add one, so the three groups the spec asks for actually read as three
groups. `Overview` stays above it, ungrouped and unscoped: it is the landing page every
role gets.

Every nav item carries an `icon` from `lucide-react`. The new entries need one too — import
`ShieldCheck` alongside the existing icon imports:

```jsx
  { section: "Restaurant", roles: ["admin", "manager", "waiter", "kitchen"] },
  // ... the existing Tables / Reservations / POS / KOT / Inventory / Menu / Reports items,
  //     each gaining domains: ["restaurant", "bar"]
  { section: "Hotel", roles: ["admin", "manager", "front_desk"] },
  // ... the existing hotel items, each gaining domains: ["hotel"]
  { section: "Staff", roles: ["admin"] },
  { to: "/app/admin/staff", label: "Staff", icon: ShieldCheck, roles: ["admin"] },
```

A section heading whose items are all filtered out must not render — an empty "Hotel"
heading with nothing under it looks like a bug.

- [ ] **Step 3: Verify**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build && rm -rf build
```

Expected: compiles cleanly.

If you can drive a browser, sign in as the admin and confirm all three groups appear; then
create a restaurant-only manager on the staff screen, sign in as them, and confirm the
Hotel group and Staff section are absent while the restaurant items remain.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: nav grouped by work domain, staff section for admins"
```

---

## Task 8: End-to-end verification

> **Note:** Tasks 9-11 were added after this plan was first written, in response to a
> widened request: separate hotel revenue, a combined analytics view filtered by domain,
> and an admin landing screen offering the three sections. Run Task 8 as written when
> Tasks 1-7 are done — it verifies the access boundary, which is the risky part — then
> continue to Task 9. Re-run Task 8's suites once more after Task 11.


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
python3 -m pytest tests/test_access.py tests/test_pricing.py tests/test_availability.py tests/test_folio.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q   # second run, same db
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/backend_test.py -q
```

Expected: pure suites `58 passed` (43 existing + 15 access); hotel suite green on **both**
runs — a second run failing means a test is not self-contained; regression suite exactly
`1 failed, 9 passed, 1 skipped`.

- [ ] **Step 3: Prove no endpoint is unguarded**

```bash
cd ~/dev/bar-management-system/backend
grep -rn "require_roles" routers/ security.py || echo "require_roles fully removed"
grep -rn "Depends(get_current_user)" routers/ || echo "no bare get_current_user in routers"
```

Expected both lines: the confirmation message. Every endpoint declares a domain, and there
is no second mechanism for the next person to reach for.

- [ ] **Step 4: Walk the access boundary against the API**

Using curl or a short Python script against the clean server, reporting the actual status
code at each step:

1. Log in as the seeded admin; confirm `/auth/me` returns all three domains and `active: true`
2. Create a `manager` with `domains: ["restaurant"]`
3. Log in as them; `GET /bookings` → **403**, `GET /tables` → **200**
4. Create a `waiter` with `domains: ["bar"]`; `GET /orders/kot` → **200** (declared restaurant+bar)
5. Same waiter: `GET /guests` → **200** (shared)
6. Same waiter: `GET /staff` → **403** (admin only)
7. Deactivate the waiter as admin; their **existing token** → **403**, and a fresh login → **401**
8. Admin attempts to deactivate themselves → **409**
9. Admin attempts to change their own role → **409**
10. Admin still reaches `/bookings`, `/tables`, `/guests`, `/folios`, `/staff` → all **200**

- [ ] **Step 5: Frontend build**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build && rm -rf build
```

Expected: compiles with only the two pre-existing warnings.

- [ ] **Step 6: Confirm no runtime artefacts are staged**

```bash
cd ~/dev/bar-management-system && git status --short
```

Expected: no `backend/db.json`, no `frontend/build/`, no `.env`.

---

---

## Task 9: Revenue attribution — pure functions

**Files:**
- Create: `backend/services/revenue.py`
- Test: `backend/tests/test_revenue.py`

**Interfaces:**
- Consumes: nothing — plain dicts only, following `services/pricing.py`, `availability.py`, `folio.py`, `access.py`
- Produces: `hotel_revenue(entries, start, end) -> dict`, `entry_revenue_date(entry) -> str | None`

### Why this is a pure module and not a query

Money that is counted twice is the failure that matters here, and it is invisible in an
integration test that only ever posts one kind of charge. The rules below are worth stating
in one readable place and pinning with tests that need no database.

**The rule, stated once:**

| Entry kind | Counts as hotel revenue? | Why |
|---|---|---|
| `room_night` | **yes** | the room sold; this is the hotel's own revenue |
| `misc_charge` | **yes** | laundry, late check-out, a hotel-side extra |
| `discount` | **negative** | reduces what the property actually earned |
| `outlet` | **no** | a bar or restaurant bill charged to the room. Revenue was already recognised at the outlet when the order settled. Counting it here too would double it. |
| `payment` | **no** | settling a receivable is not earning |
| `refund` | **no** | handing cash back; the reversal of the sale is the void, not this |
| `void` | **no** | it cancels its target instead — see below |

**Voids.** A void is its own entry carrying `ref_entry_id`. Rather than adding the void's
signed amount, the function **excludes the entry the void points at**. Both approaches give
the same total, but excluding makes a voided night vanish from the day it was charged to
instead of leaving a positive and a negative on two different days.

**Which day an entry belongs to.** A `room_night` carries `charge_date` — the night it
covers, which is often not the day it was posted. Everything else falls back to the date
part of `posted_at`. Attributing a night to its posting date would pile a three-night stay
onto one day at check-out and show two empty days beside it.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_revenue.py`:

```python
"""Hotel revenue attribution — pure, no database."""
import pytest

from services.revenue import entry_revenue_date, hotel_revenue


def e(kind, amount, *, id="e1", charge_date=None, posted_at="2026-03-05T10:00:00+00:00",
      ref_entry_id=None):
    return {"id": id, "kind": kind, "amount": amount, "charge_date": charge_date,
            "posted_at": posted_at, "ref_entry_id": ref_entry_id}


def test_room_nights_and_misc_charges_are_revenue():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("misc_charge", 300, id="e2")], "2026-03-01", "2026-03-31")
    assert r["total"] == 5300.0


def test_a_discount_reduces_revenue():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("discount", 500, id="e2")], "2026-03-01", "2026-03-31")
    assert r["total"] == 4500.0


def test_an_outlet_charge_is_not_hotel_revenue():
    # The bar already recognised this when the order settled. Counting it again here
    # would report the same rupee twice across the combined view.
    r = hotel_revenue([e("outlet", 800)], "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_a_payment_is_not_revenue():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("payment", 5000, id="e2")], "2026-03-01", "2026-03-31")
    assert r["total"] == 5000.0


def test_a_refund_is_not_revenue():
    r = hotel_revenue([e("refund", 900)], "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_a_voided_night_is_excluded_not_negated():
    entries = [
        e("room_night", 5000, id="n1", charge_date="2026-03-05"),
        e("void", 5000, id="v1", ref_entry_id="n1", posted_at="2026-03-09T10:00:00+00:00"),
    ]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0
    # and it leaves nothing behind on either day
    assert all(d["revenue"] == 0.0 for d in r["by_day"])


def test_a_night_is_counted_on_the_night_it_covers_not_when_posted():
    # Three nights posted in one go at check-out must still land on three days.
    entries = [e("room_night", 1000, id=f"n{i}", charge_date=f"2026-03-0{i}",
                 posted_at="2026-03-04T09:00:00+00:00") for i in (1, 2, 3)]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    by = {d["date"]: d["revenue"] for d in r["by_day"]}
    assert by["2026-03-01"] == 1000.0
    assert by["2026-03-02"] == 1000.0
    assert by["2026-03-03"] == 1000.0
    assert by["2026-03-04"] == 0.0


def test_entries_outside_the_range_are_ignored():
    entries = [e("room_night", 1000, id="a", charge_date="2026-02-28"),
               e("room_night", 1000, id="b", charge_date="2026-03-01"),
               e("room_night", 1000, id="c", charge_date="2026-03-31"),
               e("room_night", 1000, id="d", charge_date="2026-04-01")]
    r = hotel_revenue(entries, "2026-03-01", "2026-03-31")
    assert r["total"] == 2000.0


def test_the_range_is_inclusive_of_both_ends():
    # Unlike a stay, a report range names the days the user picked. Both are included.
    r = hotel_revenue([e("room_night", 100, charge_date="2026-03-01")],
                      "2026-03-01", "2026-03-01")
    assert r["total"] == 100.0


def test_by_day_covers_every_day_in_range_including_empty_ones():
    r = hotel_revenue([], "2026-03-01", "2026-03-03")
    assert [d["date"] for d in r["by_day"]] == ["2026-03-01", "2026-03-02", "2026-03-03"]
    assert all(d["revenue"] == 0.0 for d in r["by_day"])


def test_room_nights_are_reported_separately_from_extras():
    r = hotel_revenue([e("room_night", 5000, charge_date="2026-03-05"),
                       e("misc_charge", 300, id="e2")], "2026-03-01", "2026-03-31")
    assert r["room_nights"] == 5000.0
    assert r["extras"] == 300.0


def test_an_entry_with_no_usable_date_is_ignored_not_crashed_on():
    # Legacy rows predate charge_date. Losing one from a report beats a 500 on the
    # whole analytics screen.
    assert entry_revenue_date({"kind": "misc_charge", "posted_at": None}) is None
    r = hotel_revenue([{"id": "x", "kind": "misc_charge", "amount": 100,
                        "posted_at": None}], "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_a_missing_amount_is_treated_as_zero():
    r = hotel_revenue([{"id": "x", "kind": "room_night", "charge_date": "2026-03-05"}],
                      "2026-03-01", "2026-03-31")
    assert r["total"] == 0.0


def test_start_after_end_raises():
    with pytest.raises(ValueError):
        hotel_revenue([], "2026-03-31", "2026-03-01")
```

- [ ] **Step 2: Run and confirm it fails**

```bash
cd ~/dev/bar-management-system/backend && python3 -m pytest tests/test_revenue.py -q
```

Expected: collection error, `No module named 'services.revenue'`.

- [ ] **Step 3: Write `backend/services/revenue.py`**

```python
"""Which folio money is hotel revenue, and which day it belongs to.

Pure functions over supplied entries — no database — so the double-counting rule is
testable in isolation and stated in exactly one place.

The rule that matters: an `outlet` folio entry is NOT hotel revenue. A bar bill charged
to a room was already recognised as revenue at the outlet when the order settled; the
folio entry is the receivable that mirrors it. Counting both is how a combined
hotel-plus-restaurant total silently reports the same rupee twice.
"""
from datetime import date, timedelta

# Signed contribution of each entry kind to hotel revenue. Anything absent contributes
# nothing: payments and refunds move cash without earning it, and `outlet` belongs to
# the outlet that sold it.
REVENUE_SIGN = {
    "room_night": 1,
    "misc_charge": 1,
    "discount": -1,
}


def entry_revenue_date(entry: dict) -> str | None:
    """The day this entry's money belongs to, as YYYY-MM-DD, or None if unknowable.

    A room night is earned on the night it covers, not the day it was posted — three
    nights posted together at check-out must still land on three separate days.
    """
    charge_date = entry.get("charge_date")
    if charge_date:
        return str(charge_date)[:10]
    posted_at = entry.get("posted_at")
    if posted_at:
        return str(posted_at)[:10]
    return None


def _days(start: str, end: str) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError(f"start {start} is after end {end}")
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def hotel_revenue(entries: list[dict], start: str, end: str) -> dict:
    """Hotel revenue over an inclusive [start, end] range of calendar days.

    Unlike a stay, which is half-open because the departure night is not slept in, a
    report range names the days the user picked — so both ends are included.
    """
    days = _days(start, end)
    in_range = set(days)

    # A void removes the entry it points at rather than adding a negative of its own.
    # Both give the same total, but this way a cancelled night disappears from the night
    # it covered instead of leaving a stray credit on the day someone noticed.
    voided = {e.get("ref_entry_id") for e in entries if e.get("kind") == "void"}
    voided.discard(None)

    by_day = {d: 0.0 for d in days}
    room_nights = 0.0
    extras = 0.0

    for e in entries:
        sign = REVENUE_SIGN.get(e.get("kind"))
        if sign is None or e.get("id") in voided:
            continue
        day = entry_revenue_date(e)
        if day is None or day not in in_range:
            continue
        amount = float(e.get("amount") or 0) * sign
        by_day[day] += amount
        if e.get("kind") == "room_night":
            room_nights += amount
        else:
            extras += amount

    return {
        "total": round(room_nights + extras, 2),
        "room_nights": round(room_nights, 2),
        "extras": round(extras, 2),
        "by_day": [{"date": d, "revenue": round(by_day[d], 2)} for d in days],
    }
```

- [ ] **Step 4: Run and confirm green**

```bash
cd ~/dev/bar-management-system/backend && python3 -m pytest tests/test_revenue.py -q
```

Expected: `14 passed`.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/services/revenue.py backend/tests/test_revenue.py
git commit -m "feat: hotel revenue attribution, excluding outlet receivables"
```

---

## Task 10: The combined analytics endpoint

**Files:**
- Create: `backend/routers/analytics.py`
- Modify: `backend/server.py` (register the router)
- Test: `backend/tests/hotel_api_test.py` (append)

**Interfaces:**
- Consumes: `require_access` (Task 3), `hotel_revenue` (Task 9)
- Produces: `GET /api/analytics/revenue?domains=hotel,restaurant&start=&end=`

### What the caller picks and what comes back

`domains` is a comma-separated subset of `hotel`, `restaurant`, `bar` — the tick-boxes on
the screen. Omitted means every domain the **signed-in user** can reach, which for an admin
is all three. A user who ticks a domain they do not hold gets **403**, not a silently
narrowed answer: a manager who asks for hotel figures and receives restaurant ones would
report the wrong number to an owner.

`restaurant` and `bar` share one set of orders in this property — the same tables, the same
POS. Ticking either includes outlet revenue, and ticking both does **not** count it twice.
The response says so explicitly in `outlets_combined`, so the screen can label it rather
than leaving the user to wonder.

Response:

```json
{
  "start": "2026-03-01", "end": "2026-03-31",
  "domains": ["hotel", "restaurant"],
  "outlets_combined": true,
  "total": 184300.0,
  "hotel": {"total": 120000.0, "room_nights": 108000.0, "extras": 12000.0},
  "outlets": {"total": 64300.0, "orders": 212},
  "by_day": [{"date": "2026-03-01", "hotel": 4000.0, "outlets": 2100.0, "total": 6100.0}]
}
```

`hotel` is `null` when hotel is not selected; `outlets` is `null` when neither outlet
domain is. `by_day` always spans every day in the range, so a chart never has to guess at
gaps.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/hotel_api_test.py`. These follow the file's existing fixture style
and must be self-contained — each creates the data it asserts on, in its own date window.

```python
def test_analytics_rejects_a_domain_the_user_does_not_hold(admin, api_post):
    # A manager who asks for hotel figures must not silently receive restaurant ones.
    email = f"rest-{uuid.uuid4().hex[:6]}@barflow.io"
    admin.post(f"{API}/staff", json={"name": "Rest Mgr", "email": email,
                                     "password": "rest12345", "role": "manager",
                                     "domains": ["restaurant"]})
    s = session_for(email, "rest12345")
    r = s.get(f"{API}/analytics/revenue", params={"domains": "hotel",
                                                  "start": "2026-03-01", "end": "2026-03-31"})
    assert r.status_code == 403, r.text


def test_analytics_defaults_to_the_users_own_domains(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"start": "2026-03-01", "end": "2026-03-31"})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["domains"]) == ["bar", "hotel", "restaurant"]


def test_analytics_hotel_only_omits_outlets(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "hotel", "start": "2026-03-01", "end": "2026-03-31"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outlets"] is None
    assert body["hotel"] is not None


def test_analytics_restaurant_only_omits_hotel(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "restaurant", "start": "2026-03-01", "end": "2026-03-31"})
    assert r.status_code == 200, r.text
    assert r.json()["hotel"] is None


def test_selecting_both_outlet_domains_does_not_double_count(admin):
    one = admin.get(f"{API}/analytics/revenue",
                    params={"domains": "restaurant", "start": "2026-03-01",
                            "end": "2026-03-31"}).json()
    both = admin.get(f"{API}/analytics/revenue",
                     params={"domains": "restaurant,bar", "start": "2026-03-01",
                             "end": "2026-03-31"}).json()
    assert both["outlets"]["total"] == one["outlets"]["total"]
    assert both["outlets_combined"] is True


def test_analytics_by_day_spans_the_whole_range(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "hotel", "start": "2026-03-01", "end": "2026-03-05"})
    days = [d["date"] for d in r.json()["by_day"]]
    assert days == ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]


def test_analytics_rejects_start_after_end(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "hotel", "start": "2026-03-31", "end": "2026-03-01"})
    assert r.status_code == 400, r.text


def test_analytics_rejects_an_unknown_domain(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "spa", "start": "2026-03-01", "end": "2026-03-31"})
    assert r.status_code == 422, r.text


def test_a_waiter_cannot_reach_analytics(waiter):
    r = waiter.get(f"{API}/analytics/revenue",
                   params={"start": "2026-03-01", "end": "2026-03-31"})
    assert r.status_code == 403, r.text


def test_a_room_night_appears_in_hotel_revenue_on_the_night_it_covers(admin):
    """End to end: a real stay's nights land on the right days, and the outlet bill
    charged to that room is NOT added to hotel revenue a second time."""
    # Build a stay well away from other tests' windows.
    start, end = "2026-09-14", "2026-09-16"
    before = admin.get(f"{API}/analytics/revenue",
                       params={"domains": "hotel", "start": start, "end": "2026-09-15"}).json()
    booking = make_booking(admin, start, end)          # existing helper in this file
    check_in(admin, booking["id"])                     # existing helper
    folio = admin.get(f"{API}/folios/{booking['folio_id']}").json()
    assert folio["balance"] > 0                        # nights posted
    after = admin.get(f"{API}/analytics/revenue",
                      params={"domains": "hotel", "start": start, "end": "2026-09-15"}).json()
    # Two nights: 09-14 and 09-15. 09-16 is the departure and is not slept in.
    assert after["hotel"]["room_nights"] > before["hotel"]["room_nights"]
```

**Before writing these, read the fixtures at the top of `hotel_api_test.py`.** Helper names
above (`api_post`, `session_for`, `make_booking`, `check_in`, `waiter`) are the intent, not
a promise — use whatever that file actually provides, and add a fixture only if none fits.
If `waiter` does not exist, create the user through `POST /api/staff` inside the test.

- [ ] **Step 2: Run and confirm they fail**

```bash
cd ~/dev/bar-management-system/backend
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q -k analytics
```

Expected: failures with 404 — the route does not exist yet.

- [ ] **Step 3: Write `backend/routers/analytics.py`**

```python
"""Revenue across the whole property, filtered to the domains the caller selects."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from db import db
from security import require_access
from services.access import DOMAINS
from services.revenue import hotel_revenue

router = APIRouter()

# Analytics is a management view in every domain, so it declares all of them and lets
# the query narrow. The role list still gates it: a waiter holds `restaurant` but has no
# business reading the property's revenue.
ANALYTICS = require_access(DOMAINS, "admin", "manager")

# This property's bar and restaurant share one POS and one set of orders. Selecting
# either shows outlet revenue; selecting both must not show it twice.
OUTLET_DOMAINS = ("restaurant", "bar")


def _parse_domains(raw: str | None, user: dict) -> list[str]:
    held = list(DOMAINS) if user.get("role") == "admin" else list(user.get("domains") or ())
    if not raw:
        return sorted(held)
    picked = [d.strip() for d in raw.split(",") if d.strip()]
    for d in picked:
        if d not in DOMAINS:
            raise HTTPException(422, f"Unknown domain: {d}")
    # Answering a narrower question than the one asked is worse than refusing it — an
    # owner reading a figure labelled "hotel" must be able to trust the label.
    missing = [d for d in picked if d not in held]
    if missing:
        raise HTTPException(403, f"You do not have access to: {', '.join(sorted(missing))}")
    return sorted(set(picked))


@router.get("/analytics/revenue")
async def revenue(
    start: str = Query(...),
    end: str = Query(...),
    domains: str | None = Query(None),
    user: dict = Depends(ANALYTICS),
):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD dates")
    if a > b:
        raise HTTPException(400, "start must not be after end")

    picked = _parse_domains(domains, user)
    days = [d["date"] for d in hotel_revenue([], start, end)["by_day"]]

    hotel = None
    if "hotel" in picked:
        entries = await db.folio_entries.find().to_list(100000)
        hotel = hotel_revenue(entries, start, end)

    outlets = None
    if any(d in picked for d in OUTLET_DOMAINS):
        orders = await db.orders.find({"status": "settled"}).to_list(100000)
        rows = []
        for o in orders:
            day = str(o.get("settled_at") or o.get("created_at") or "")[:10]
            if day and start <= day <= end:
                rows.append((day, float(o.get("total") or 0)))
        per_day = {d: 0.0 for d in days}
        for day, amount in rows:
            per_day[day] += amount
        outlets = {"total": round(sum(a for _, a in rows), 2), "orders": len(rows),
                   "by_day": per_day}

    by_day = []
    for i, d in enumerate(days):
        h = hotel["by_day"][i]["revenue"] if hotel else 0.0
        o = outlets["by_day"][d] if outlets else 0.0
        by_day.append({"date": d, "hotel": round(h, 2), "outlets": round(o, 2),
                       "total": round(h + o, 2)})

    if outlets:
        outlets.pop("by_day")

    return {
        "start": start, "end": end, "domains": picked,
        # Stated rather than implied, so the screen can label it instead of leaving the
        # user to wonder whether ticking both outlets doubled the figure.
        "outlets_combined": sum(1 for d in OUTLET_DOMAINS if d in picked) > 1,
        "total": round((hotel["total"] if hotel else 0.0)
                       + (outlets["total"] if outlets else 0.0), 2),
        "hotel": {k: hotel[k] for k in ("total", "room_nights", "extras")} if hotel else None,
        "outlets": outlets,
        "by_day": by_day,
    }
```

**Check the order field name before trusting it.** The code reads `settled_at` and falls
back to `created_at`. Open `backend/routers/orders.py` and confirm what the settle handler
actually writes; if it is neither of those, use the real field and say so in your report.
A wrong field name here silently reports zero outlet revenue.

- [ ] **Step 4: Register the router**

In `backend/server.py`, follow exactly how the neighbouring routers are included:

```python
from routers.analytics import router as analytics_router
```

```python
api_router.include_router(analytics_router)
```

- [ ] **Step 5: Run and confirm green**

```bash
cd ~/dev/bar-management-system/backend
pkill -f "uvicorn server:app"; rm -f db.json
nohup env MONGO_URL=mock python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 > /tmp/bf.log 2>&1 & disown
curl -s --retry 40 --retry-delay 1 --retry-all-errors --max-time 90 http://127.0.0.1:8000/api/
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 python3 -m pytest tests/hotel_api_test.py -q
```

Expected: green on **both** runs against the same database.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add backend/routers/analytics.py backend/server.py backend/tests/hotel_api_test.py
git commit -m "feat: combined revenue analytics filtered by work domain"
```

---

## Task 11: Admin console — the three doors, and the analytics screen

**Files:**
- Create: `frontend/src/pages/admin/Console.jsx`, `frontend/src/pages/admin/Analytics.jsx`
- Modify: `frontend/src/App.js`, `frontend/src/components/app/AppLayout.jsx`

**Interfaces:**
- Consumes: `GET /api/analytics/revenue` (Task 10), `useAuth()` (Task 7)
- Produces: routes `/app/admin` and `/app/admin/analytics`

### The three doors

An admin signing in lands on `/app/admin`: three cards — **Hotel**, **Bar & Restaurant**,
**Staff** — plus a fourth for **Analytics**, which is the only screen that spans domains.
Each card is a link into that section's own landing screen. Non-admins never see this
route; they land where they land today.

The cards are a shortcut, not a security boundary. The API is the boundary — see Task 3.

- [ ] **Step 1: Read the house style first**

Read `frontend/src/pages/hotel/FrontDesk.jsx` and `frontend/src/pages/Reports.jsx` before
writing. Match the eyebrow plus uppercase `<h1>`, the stone/orange palette, `currency()`
from `@/lib/api` for money, `tabular-nums` on figures, and `formatApiErrorDetail` with
`toast.error` on failure. Do not introduce a chart library — `Reports.jsx` already draws
its bars with plain divs, and adding a dependency for one screen is not worth it.

- [ ] **Step 2: Create `frontend/src/pages/admin/Console.jsx`**

```jsx
import { Link } from "react-router-dom";
import { BedDouble, LineChart, ShieldCheck, UtensilsCrossed } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

const DOORS = [
  { to: "/app/hotel/front-desk", icon: BedDouble, label: "Hotel",
    detail: "Front desk, bookings, rooms, rates and guest folios." },
  { to: "/app/tables", icon: UtensilsCrossed, label: "Bar & Restaurant",
    detail: "Tables, POS, kitchen board, menu and stock." },
  { to: "/app/admin/staff", icon: ShieldCheck, label: "Staff",
    detail: "Add people, set what they can reach, reset a password." },
  { to: "/app/admin/analytics", icon: LineChart, label: "Analytics",
    detail: "Revenue across the property — hotel, outlets, or both." },
];

export default function Console() {
  const { user } = useAuth();
  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        {user?.name ? `Evening, ${user.name.split(" ")[0]}` : "Console"}
      </h1>
      <p className="text-stone-400 mb-10">Pick where you want to work.</p>

      <div className="grid gap-5 sm:grid-cols-2 max-w-4xl">
        {DOORS.map(({ to, icon: Icon, label, detail }) => (
          <Link
            key={to}
            to={to}
            className="group border border-stone-800 bg-stone-900 rounded p-6 hover:border-orange-500/60 transition-colors"
          >
            <Icon className="w-7 h-7 text-orange-500 mb-4" strokeWidth={1.5} />
            <div className="text-lg font-bold uppercase tracking-wide text-stone-100 group-hover:text-orange-400">
              {label}
            </div>
            <p className="text-sm text-stone-400 mt-2">{detail}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

The greeting is fixed text rather than time-based: the server clock and the property's
clock are not reliably the same, and a "Good morning" at 9pm reads as broken.

- [ ] **Step 3: Create `frontend/src/pages/admin/Analytics.jsx`**

```jsx
import { useCallback, useEffect, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const DOMAINS = [
  { key: "hotel", label: "Hotel" },
  { key: "restaurant", label: "Restaurant" },
  { key: "bar", label: "Bar" },
];

// Default to the current calendar month, computed locally. Building these from an ISO
// slice of a UTC timestamp puts the property a day out for most of its evening.
function monthRange() {
  const n = new Date();
  const p = (v) => String(v).padStart(2, "0");
  const first = `${n.getFullYear()}-${p(n.getMonth() + 1)}-01`;
  const today = `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}`;
  return { start: first, end: today };
}

export default function Analytics() {
  const [range, setRange] = useState(monthRange);
  const [picked, setPicked] = useState(["hotel", "restaurant", "bar"]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    if (picked.length === 0) {
      setData(null);
      return;
    }
    setLoading(true);
    api
      .get("/analytics/revenue", {
        params: { start: range.start, end: range.end, domains: picked.join(",") },
      })
      .then((r) => setData(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setLoading(false));
  }, [range.start, range.end, picked]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (k) =>
    setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));

  const peak = Math.max(1, ...(data?.by_day || []).map((d) => d.total));

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Analytics
      </h1>

      <div className="flex flex-wrap gap-6 items-end mb-8">
        {[["start", "From"], ["end", "To"]].map(([k, label]) => (
          <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
            {label}
            <input
              type="date"
              value={range[k]}
              onChange={(e) => setRange({ ...range, [k]: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            />
          </label>
        ))}
        <div className="text-xs tracking-widest uppercase text-stone-500">
          Include
          <div className="flex gap-2 mt-2">
            {DOMAINS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => toggle(key)}
                aria-pressed={picked.includes(key)}
                className={`text-[10px] tracking-widest uppercase border rounded-full px-4 py-1.5 ${
                  picked.includes(key)
                    ? "border-orange-500 text-orange-400 bg-orange-500/10"
                    : "border-stone-700 text-stone-500 hover:border-stone-500"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {picked.length === 0 && (
        <p className="text-stone-400">Tick at least one area to see figures.</p>
      )}

      {data && (
        <>
          {data.outlets_combined && (
            <p className="text-xs text-stone-500 mb-6 max-w-2xl">
              Bar and restaurant share one till here, so their revenue is reported together
              — ticking both does not count it twice.
            </p>
          )}

          <div className="grid gap-4 sm:grid-cols-3 max-w-4xl mb-10">
            <Figure label="Total" value={data.total} accent />
            <Figure label="Hotel" value={data.hotel?.total} sub={
              data.hotel ? `${currency(data.hotel.room_nights)} rooms · ${currency(data.hotel.extras)} extras` : null
            } />
            <Figure label="Bar & restaurant" value={data.outlets?.total} sub={
              data.outlets ? `${data.outlets.orders} orders` : null
            } />
          </div>

          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            By day
          </h2>
          <div className="overflow-x-auto">
            <div className="flex items-end gap-1 h-40 min-w-[480px]">
              {data.by_day.map((d) => (
                <div key={d.date} className="flex-1 flex flex-col justify-end" title={`${d.date} — ${currency(d.total)}`}>
                  <div className="bg-orange-500/80" style={{ height: `${(d.hotel / peak) * 100}%` }} />
                  <div className="bg-stone-500" style={{ height: `${(d.outlets / peak) * 100}%` }} />
                </div>
              ))}
            </div>
          </div>
          <div className="flex gap-5 mt-3 text-[10px] tracking-widest uppercase text-stone-500">
            <span><span className="inline-block w-3 h-2 bg-orange-500/80 mr-2" />Hotel</span>
            <span><span className="inline-block w-3 h-2 bg-stone-500 mr-2" />Bar &amp; restaurant</span>
          </div>
        </>
      )}

      {loading && <p className="text-stone-500 text-sm mt-6">Loading…</p>}
    </div>
  );
}

function Figure({ label, value, sub, accent }) {
  return (
    <div className={`border rounded p-5 ${accent ? "border-orange-500/40 bg-orange-500/5" : "border-stone-800 bg-stone-900"}`}>
      <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500 mb-2">{label}</div>
      <div className="text-2xl font-bold tabular-nums text-stone-100">
        {value == null ? "—" : currency(value)}
      </div>
      {sub && <div className="text-xs text-stone-500 mt-2 tabular-nums">{sub}</div>}
    </div>
  );
}
```

A domain the admin unticks is not sent, so the server returns `null` for that block and the
card shows `—` rather than a misleading `₹0.00`.

- [ ] **Step 4: Wire the routes and the nav**

`frontend/src/App.js`:

```jsx
import Console from "@/pages/admin/Console";
import Analytics from "@/pages/admin/Analytics";
```

```jsx
        <Route path="/admin" element={<Protected roles={["admin"]}><Console /></Protected>} />
        <Route path="/admin/analytics" element={<Protected roles={["admin", "manager"]}><Analytics /></Protected>} />
```

In `AppLayout.jsx`, the Staff section from Task 7 gains two siblings. `Console` needs
`exclude` so it does not stay lit while a child route is open — the same mechanism
`/app/hotel/bookings` already uses:

```jsx
  { section: "Staff", roles: ["admin"] },
  { to: "/app/admin", label: "Console", icon: LayoutGrid, roles: ["admin"],
    exclude: ["/app/admin/staff", "/app/admin/analytics"] },
  { to: "/app/admin/staff", label: "Staff", icon: ShieldCheck, roles: ["admin"] },
  { to: "/app/admin/analytics", label: "Analytics", icon: LineChart, roles: ["admin", "manager"] },
```

- [ ] **Step 5: Verify**

```bash
cd ~/dev/bar-management-system/frontend && CI=false npx craco build && rm -rf build
```

Expected: compiles with only the pre-existing warnings in `CustomerMenu.jsx` and
`Reservations.jsx`.

Then, against a running backend, sign in as the admin and confirm: `/app/admin` shows four
cards; Analytics defaults to this month with all three ticked; unticking Hotel makes the
Hotel figure read `—` and removes the orange bars; unticking everything shows the prompt
rather than an empty chart.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/bar-management-system
git add frontend/src
git commit -m "feat: admin console and cross-domain revenue analytics"
```

---

## Deferred, by design

Printable bills (folio invoice, restaurant bill); printable data history; occupancy, ADR
and RevPAR — this plan covers hotel **revenue**, not the occupancy-derived hotel metrics;
admin corrections to staff-entered data, which conflicts with the folio ledger being
append-only and needs its own conversation. Also out of scope: email-based password reset,
token revocation, read-versus-write distinctions within a domain, and multi-property
tenancy.

