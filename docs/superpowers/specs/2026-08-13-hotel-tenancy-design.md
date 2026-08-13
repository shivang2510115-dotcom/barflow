# Hotel Tenancy: Signup, Approval, Suspension — Design

**Date:** 2026-08-13
**Status:** Approved, ready for implementation planning
**Part of:** `2026-08-13-multi-tenant-saas-programme.md` (sub-projects 1 and 2, built together)

---

## The lifecycle, in one line

A hotel registers → the owner approves it in the platform portal → it goes live and its
admin logs in and creates staff → the owner can deactivate it and it stops.

Three states, nothing else:

| State | What the hotel can do |
|---|---|
| `pending` | Log in, set up: property details, room types, rooms, rates, menu, tables, staff. **Cannot** take a booking, check a guest in, or settle an order. |
| `live` | Everything. |
| `suspended` | Nothing. Every user of that hotel is refused at login and on every request, exactly as a deactivated staff member is. |

Suspension is reversible and destroys nothing. A suspended hotel's data sits untouched, so
paying an overdue invoice restores the property rather than rebuilding it.

---

## Why this reuses the staff mechanism

Staff already carry `active`, and `can_access` refuses an inactive user before it looks at
role or domain. A suspended hotel is the same rule one level up: **a user is refused if
their property is not usable, for the same reason and in the same place.**

That matters because it means there is exactly one function to read to know who can reach
what. A second, parallel gate for property status would be the thing someone later forgets
to apply to a new endpoint.

`can_access` gains one argument — the property — and one rule ahead of the others.

---

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Isolation mechanism | **A property-scoped database handle**, bound by a dependency | 244 call sites are unscoped today. Passing a filter to each works until someone forgets once, and that failure is silent: no error, just another hotel's rows in a list. Binding it makes the safe thing the only easy thing. |
| Platform operator | **A `platform_admin` role, seeded from `PLATFORM_ADMIN_EMAIL` / `PLATFORM_ADMIN_PASSWORD`** | It belongs to no hotel and cannot be created through the app, so nobody can sign up their way into approving themselves. |
| Operator's data access | **Sees hotels, not their guests** | The portal lists properties, their status and their size. It does not read guest records, folios or ID documents. An operator who does not need the data should not hold the key to it. |
| Signup | **Public, no invite** | One form creates the property and its first admin together. A property with no way in is a support ticket. |
| What `pending` blocks | **Operating, not configuring** | They evaluate the product with their own rooms and rates; no guest money moves through an unvetted property. |
| Suspension | **Reversible, destroys nothing** | |
| Existing data | **Becomes the first property, stamped by a startup migration** | Idempotent, like the domains backfill that already runs there. |

---

## Data model

### `properties` — the tenant

| Field | Notes |
|---|---|
| `id`, `name`, `legal_name` | |
| `address_line1`, `address_line2`, `city`, `state`, `pincode` | |
| `phone`, `email`, `gstin`, `fssai_licence` | GSTIN and FSSAI validated by format only |
| `check_in_time`, `check_out_time` | default `14:00` / `11:00` |
| `logo` | data URI |
| `status` | `pending` \| `live` \| `suspended` |
| `created_at`, `approved_at`, `approved_by`, `suspended_at`, `suspension_reason` | |

### `users` — one new field

`property_id` — the hotel this login belongs to. **Null for a `platform_admin`**, who
belongs to none.

### Every other collection

Gains `property_id`. Bookings, rooms, room types, rates, rate periods, meal plans, tax
slabs, folios, folio entries, orders, tables, reservations, menu, inventory, guests.

**Outside tenancy, named explicitly:** `users` (a login must be findable by email before we
know its hotel), `properties`, and `payment_transactions`.

---

## Enforcement

### The scoped handle

A dependency resolves the caller's property once per request and yields a handle already
bound to it. Reads are filtered; writes are stamped. The unscoped handle keeps existing —
seeding, migrations, signup and the platform portal need it — under a name that makes any
direct use visible on sight in review.

A router that asks for the scoped handle **cannot** reach another hotel's rows, because the
filter is not its to forget.

### The access rule

`can_access` checks, in order:

1. **Property usable** — `live`, or `pending` for an endpoint marked as setup-time. A
   `suspended` property refuses everyone, including its own admin.
2. **User active** — as today.
3. **Role** — as today.
4. **Domain** — as today, with `admin` bypassing.

A `platform_admin` has no property and is refused every hotel endpoint by rule 1. It reaches
only `/api/platform/*`. That is deliberate: the operator's login is not a master key into
customer data.

### Marking setup-time endpoints

Endpoints reachable while `pending` are declared, one at a time, at the call site — the same
explicitness `require_access` already uses for domains. Property, room types, rooms, rates,
meal plans, tax slabs, menu, tables and staff are setup-time. Bookings, availability,
check-in, check-out, folios, orders and the POS are not.

---

## API

```
POST   /api/signup                              public: creates a pending property + its first admin
GET    /api/property                            the caller's own property
PUT    /api/property                            hotel admin only

GET    /api/platform/properties                 platform_admin only; filterable by status
GET    /api/platform/properties/{id}            counts and setup progress, never guest data
POST   /api/platform/properties/{id}/status     {status, reason?} — approve, suspend, restore
```

`POST /api/signup` is the only unauthenticated write in the application that creates a
record, so it is rate-limited by IP and rejects an email that already has a login.

---

## Screens

**`/signup`** — public. Hotel name, city, GSTIN, and the first admin's name, email and
password. On success: the pending screen.

**Pending banner** — shown across the app while `pending`, saying what is unlocked and what
is not, so a locked button is explained rather than merely disabled.

**`/platform`** — `platform_admin` only. Properties with status, city, room count and signup
date; filter by status; approve, suspend and restore, each recording a reason. It shows a
hotel's *size*, never its guests.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Signup with an email that already has a login | 409 |
| Signup with a malformed GSTIN | 400 naming the field |
| Any user of a suspended property logs in | 401, message identical to a wrong password |
| Suspended mid-session, existing token | 403 on the next request; the check runs per request |
| Pending property attempts a booking | 403 saying approval is pending, not a bare "Forbidden" |
| `platform_admin` calls a hotel endpoint | 403 |
| Hotel admin calls `/api/platform/*` | 403 |
| Approving an already-live property | 200, no change; approving is idempotent |
| Suspending the last live property | allowed — there is no lockout rule between hotels |
| A request whose user has no `property_id` and is not `platform_admin` | 403; the startup migration makes this unreachable |

---

## Testing

**Pure** — extends `services/access.py`'s existing suite:

- `suspended` property refuses every role including its own admin
- `pending` allows a setup-time endpoint, refuses an operating one
- `live` behaves exactly as today, so nothing regresses
- `platform_admin` is refused every hotel endpoint

**Isolation — the tests that matter.** Each creates data in **two** properties:

- A's list contains exactly A's rows; none of B's ids appear anywhere in the response
- Fetching B's booking by id from A's session returns **404, not 403** — a 403 confirms the
  record exists, which is itself a leak
- Updating and deleting B's record by id from A's session both 404
- A's revenue analytics exclude B's orders and folio entries entirely
- The same for guests, rooms, folios, orders, menu and inventory

**Integration:** signup creates property and admin together; the new admin logs in and
reaches setup but not booking; approval unlocks booking; suspension refuses login and refuses
an existing token; restore returns access with data intact.

---

## Out of scope

Self-serve card payment; the manual subscription record (sub-project 3); per-tenant
databases; hardening (sub-project 4); operator teams — one `platform_admin` for now;
custom domains per hotel; guest registration and Form C, which return once tenancy exists.
