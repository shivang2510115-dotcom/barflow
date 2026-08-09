# Staff Management & Work Domains — Design

**Date:** 2026-08-10
**Status:** Approved, ready for implementation planning
**Scope:** Foundation for the admin-console programme

---

## Why this exists

Today every `manager` is a manager of everything. There is no way to have a restaurant
manager who cannot see hotel data, and no way for an admin to add a staff member, change
what they can reach, or shut off a leaver's access — the only user-creation path is an API
endpoint with no screen behind it.

This adds a **work domain** as a second authorization axis alongside the existing role, and
an admin screen to manage staff.

### Where this sits

The original request covered five separate subsystems. Agreed decomposition:

| Piece | Status |
|---|---|
| **A. Staff & work-domain access** | **this spec** |
| B. Printable bills (folio invoice, restaurant bill) | later |
| C. Hotel reports (occupancy, ADR, RevPAR) | later |
| D. Printable data history — **print/PDF**, not CSV | later |
| E. Admin corrections to staff-entered data | later; see the note below |
| F. Segmented admin console | folded into this spec |

**E is deliberately deferred.** "Admin can edit data staff did" conflicts with a shipped
decision: the folio ledger is append-only, and corrections are reversing entries so a
disputed bill keeps its audit trail. Allowing silent edits removes that guarantee. There is
a good answer, but it needs its own conversation rather than being assumed here.

F is folded in because segmentation is what the domain axis enables — building it
separately would mean designing the same nav twice.

---

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Where domains are enforced | **At the API**, not only in navigation | Hiding nav while leaving the API open protects against accident, not intent. Anything less is decoration. |
| Domains per person | **A list** — several per user | A small property runs this way: the same person covers the desk at 3pm and the floor at 8pm. Barely harder than a single value. |
| Admin and domains | **`admin` bypasses the domain check** | Matches "one admin who sees everything". |
| Existing accounts on migration | **Backfilled with all domains** | Failing closed would lock staff out mid-service with no route back except the admin. Narrowing happens deliberately afterwards. |
| Leavers | **Deactivate, never delete** | `posted_by` and `created_by` must still resolve to a name. Deleting orphans the audit trail the ledger exists for. |
| Enforcement mechanism | **Explicit `require_access(domains, *roles)` per endpoint** | ~42 one-line edits, each greppable. Inferred security configuration cannot be audited. |
| Multi-area endpoints | **A `shared` domain** | A bar regular and a hotel guest are the same record. Splitting guests by domain would stop the desk seeing that an arrival has a ₹40,000 bar history — the product's whole claim. |

---

## Data model

### `users` — two new fields

| Field | Notes |
|---|---|
| `domains` | list of `hotel` \| `restaurant` \| `bar` |
| `active` | bool, default `true` |

No new collection. Roles are unchanged: `admin`, `manager`, `waiter`, `kitchen`,
`front_desk` (and `housekeeping` when that ships).

### Migration

One-shot, idempotent: every existing user without `domains` gets all three, and every user
without `active` gets `true`. Run once on deploy; re-running changes nothing.

---

## Enforcement

A new `require_access(domains: str | tuple[str, ...], *roles: str)` in
`backend/security.py` replaces `require_roles` at every call site. It accepts **one or
more** domains and grants access if the user holds **any** of them — without this, an
endpoint declared `restaurant` would deny a bar-only user the order screens this property
expects them to use. It checks, in order:

1. **Active** — a deactivated user is refused regardless of role. This check applies to
   `admin` too: a deactivated admin is locked out.
2. **Role** — as today.
3. **Domain** — the endpoint's domain is in the user's `domains`, **except**:
   - `admin` short-circuits this check entirely.
   - `shared` is satisfied by any user holding at least one domain.

`POST /auth/login` gains the same active check, so a deactivated user is refused at the
door rather than on their first request.

### Domain assignment

| Endpoints | Declared domains |
|---|---|
| bookings, availability, rooms, room types, rates, rate periods, meal plans, tax slabs, folios, front desk, in-house, check-in, check-out | `hotel` |
| orders, KOT, menu, tables, reservations, POS settle | `restaurant` **and** `bar` — this property's bar and restaurant share these screens, so either domain grants access |
| `/api/reports/*` | `restaurant` and `bar` — outlet sales analytics. The hotel report screen (piece C) gets `hotel` when built. |
| guests, inventory, `/auth/me` | `shared` |

---

## API

```
GET    /api/staff                    list: name, email, role, domains, active
POST   /api/staff                    create {name, email, password, role, domains}
PUT    /api/staff/{id}               edit {name, role, domains}
POST   /api/staff/{id}/active        {active: bool} — deactivate or reactivate
POST   /api/staff/{id}/password      {password} — admin resets
```

All admin-only. The existing `POST /auth/register` is superseded by `POST /api/staff` and
should be removed rather than left as a second, unscreened way to create users.

**Passwords:** the admin sets an initial password and can reset it. No email flow — there
is no mail infrastructure in this app, and adding one is a sub-project of its own.

---

## Screens

**`/app/admin/staff`** — admin only. A table of staff: name, email, role, domains, active
state. Create a staff member; edit role and domains; deactivate and reactivate; reset a
password. Domains are a multi-select, since a person can hold several.

**Navigation grouping** — the shell groups nav into **Hotel**, **Restaurant** and, for
admins, **Staff**. A user sees only the groups their domains reach; an admin sees all
three. This is the segmented console from the original request, and it falls out of the
domain axis rather than needing its own model.

---

## Lockout protection

Three rules, because without them one edit permanently locks the owner out of their own
system with no recovery short of editing the database by hand:

- A user cannot change **their own** role or domains.
- A user cannot deactivate **themselves**.
- The **last active admin** cannot be deactivated or demoted.

Each returns 409 with a message naming the reason.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Deactivated user logs in | 401, same message as a wrong password — do not reveal that the account exists but is disabled |
| Deactivated user with a still-valid token | 403 on the next request; the active check runs per request, not only at login |
| Unknown domain value | 422 |
| Empty `domains` on a non-admin | 400 — an account that can reach nothing is a mistake, not a state |
| Duplicate email on create | 409 |
| Self-edit of role or domains | 409 |
| Deactivating the last active admin | 409 |
| Role or domains changed mid-session | takes effect on the next request; tokens are not revoked |

**Tokens are not revoked on change.** A JWT already issued keeps its claims until it
expires, but every request re-reads the user, so a demotion or deactivation takes effect
immediately in practice. This is stated so nobody later assumes token revocation exists.

---

## Testing

**Pure function** — `can_access(user, domains, roles)` in `backend/services/access.py`,
following the pattern of `pricing.py`, `availability.py` and `folio.py`:

- role matches and domain held → allowed
- role matches, domain not held → denied
- `admin` with any domains → allowed for every domain
- `admin` but inactive → denied
- `shared` → allowed for any user holding at least one domain
- inactive user with a correct role and domain → denied

**Integration:**

- A restaurant-domain manager gets **403** on `/api/bookings` and **200** on `/api/orders`.
- A hotel-domain front_desk gets **200** on `/api/folios` and **403** on `/api/menu` writes.
- Any domain reaches `/api/guests` (`shared`).
- A deactivated user cannot log in, and their existing token is refused.
- The last active admin cannot be deactivated or demoted.
- A user cannot edit their own role or domains.
- After migration, an account that existed before still reaches everything it did.

**Regression:** the existing hotel and backend suites must hold at their recorded
baselines. Every currently-passing test runs as `admin` or a seeded role, so the migration
defaults must keep them green.

---

## Out of scope

Admin corrections to staff-entered data (piece E); printable bills, hotel reports and
printable history (B, C, D); email-based password reset; token revocation; per-endpoint
read-versus-write distinctions within a domain; multi-property tenancy.
