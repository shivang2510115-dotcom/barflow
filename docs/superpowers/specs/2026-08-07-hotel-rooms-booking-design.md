# Hotel: Rooms & Booking Engine — Design

**Date:** 2026-08-07
**Status:** Approved, ready for implementation planning
**Scope:** Sub-project 1 of 5 in the hotel/resort programme

---

## Why this exists

BarFlow runs the bar and the restaurant. The property also sells rooms, and today that
happens outside the system entirely. This spec adds room inventory, seasonal pricing and
staff-operated bookings — the foundation every later hotel capability depends on.

It deliberately stops short of check-in, folio and events. Those are separate specs.

### Programme decomposition

The full request ("complete hotel resort management with bookings and events") is seven
independent subsystems. Building them as one spec would produce a document nobody can
implement. Agreed order:

| # | Sub-project | Depends on | Status |
|---|---|---|---|
| **1** | **Rooms + booking engine** | — | **this spec** |
| 2 | Front desk + guest folio | 1 | later |
| 3 | Housekeeping | 1 | later |
| 4 | Events & banquets | — (independent) | later |
| 5 | Night audit + unified reporting | 1, 2, 4 | later |

Sub-project 2 is where hotel meets the existing POS: a bar bill posts to a room folio
instead of settling. That is the product differentiator, and it is only possible once
this spec ships.

---

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Who books | Staff only, at the desk | Smallest correct core. Guest self-booking and OTA sync build on this exact model, so nothing is wasted. |
| Room allocation | Book a **room type**, assign a room at check-in | How real hotels work. Avoids blocking a physical room for a whole stay; allows upgrades and maintenance moves without editing bookings. |
| Rates | Seasonal periods + meal plans + extra-person pricing | Standard for an Indian resort. Flat rates would be wrong for a property with a peak season. |
| Guest identity | New `guests` collection, phone as key | Delivers the "one guest record" claim. Sub-project 2 needs it regardless; building it now avoids a migration across bookings and orders. |
| Property scope | One property per deployment | Matches BarFlow today. Multi-tenancy would touch every existing endpoint and risks cross-client data leaks for no current benefit. |
| Architecture | Modular monolith — extract to routers, add hotel alongside | See below. |
| Naming | New collection is `bookings`; existing `reservations` keeps meaning restaurant tables | No migration, no ambiguity. |
| Room tax | GST slabs by nightly tariff | Indian room GST is slab-based (12% up to ₹7,500, 18% above). A seasonal rate can cross the slab mid-stay, so it is computed per night. |
| Overbooking | Hard block at zero availability | Predictable. Deliberate overbooking allowance can come with OTA sync. |
| Booking granularity | One booking = one room | Multi-room parties create several bookings sharing a `group_ref`. |
| New role | `front_desk` | Can book and manage guests; cannot edit rates, rooms or view financial reports. |

---

## Architecture

### Current state

`backend/server.py` is 1179 lines holding all seven existing domains — auth, tables, menu,
orders, inventory, reports, payments. Adding rooms, rates, guests and bookings in place
would take it past 2,200 lines before front desk, folio, housekeeping and events exist.

### Target structure

```
backend/
  server.py            app assembly, middleware, startup — thin
  db.py                client + database handle
  security.py          hashing, JWT, get_current_user, require_roles
  routers/
    auth.py            \
    tables.py           |
    menu.py             |  extracted, no behaviour change
    orders.py           |
    inventory.py        |
    reports.py          |
    payments.py        /
    guests.py          \
    rooms.py            |  new — room types + physical rooms
    rates.py            |  new — periods, rates, meal plans
    bookings.py        /   new — availability + bookings
  services/
    availability.py    pure functions: availability count, quoting, GST
```

Rejected alternatives:

- **Extend `server.py` in place.** Fastest start, but forces a refactor under pressure at
  sub-project 2 and produces a file too large to edit reliably.
- **Separate hotel service.** Clean isolation, but makes the folio in sub-project 2 a
  distributed transaction across two databases and two auth systems. Wrong trade at one
  property.

### Extraction is a precondition

The extraction happens **first**, as its own step, with no hotel code in it.
`backend/tests/backend_test.py` must pass unchanged before and after. Same routes, same
responses, same status codes.

---

## Data model

Five new collections. No existing collection changes shape.

### `guests`

The identity record. `phone` is the key.

| Field | Notes |
|---|---|
| `id` | uuid |
| `name` | |
| `phone` | **unique index** — the identity key |
| `email`, `address`, `nationality` | optional |
| `id_proof_type`, `id_proof_number` | optional in v1; required at check-in in sub-project 2 |
| `notes` | free text |
| `created_at` | |

**Backfill:** on first migration, create a guest for each distinct non-empty
`customer_phone` in `orders`, taking the most recent `customer_name`. A bar regular is
then already recognised at check-in.

### `room_types`

| Field | Notes |
|---|---|
| `id`, `name`, `code` | e.g. "Deluxe Sea View" / `DLX-SV` |
| `description`, `amenities[]`, `images[]` | |
| `block` | e.g. "Main Block", "Cottages" — mirrors the zone idea on `tables` |
| `base_occupancy` | rate covers this many adults |
| `max_occupancy` | hard ceiling |
| `max_extra_beds` | |
| `active` | |

### `rooms`

Physical units.

| Field | Notes |
|---|---|
| `id`, `number`, `room_type_id`, `floor`, `block` | |
| `active` | inactive rooms never count toward availability |
| `out_of_order[]` | list of `{from, to, reason}` date ranges |

### `rates`

One row per (`room_type_id`, `period_id`). `period_id` null means the default rate.

| Field | Notes |
|---|---|
| `id`, `room_type_id`, `period_id` | |
| `base_rate` | per night, covers `base_occupancy` adults |
| `extra_adult_rate`, `extra_child_rate` | per person per night |

Supporting collections:

- **`rate_periods`** — `{id, name, start_date, end_date, priority, active}`.
  Named ranges like "Peak — 20 Dec to 5 Jan". Overlaps resolve by `priority` (higher
  wins); equal priority resolves by most recently created, and the UI warns on overlap.
- **`meal_plans`** — `{id, code, name, price_per_adult_per_night, price_per_child_per_night, active}`.
  EP (room only), CP (breakfast), MAP (half board).
- **`tax_slabs`** — `{id, min_tariff, max_tariff, rate_percent, active}`. Seeded with the
  current room-GST bands (0–7,500 → 12%; above 7,500 → 18%) but editable, because these
  rates change by statute and must not be hardcoded. `max_tariff` null means "no upper
  bound". Applied per night against that night's tariff.

Nightly price for a date:

```
room_rate(date, type)
  + extra_adult_rate × max(0, adults − base_occupancy)
  + extra_child_rate × children
  + meal_plan.per_adult × adults
  + meal_plan.per_child × children
```

GST is applied per night against that night's tariff, using the slab table.

### `bookings`

| Field | Notes |
|---|---|
| `id`, `reference` | human code, e.g. `BF-2608-0042` |
| `guest_id` | |
| `group_ref` | shared across a multi-room party; null otherwise |
| `room_type_id`, `meal_plan_id` | |
| `check_in`, `check_out` | **`YYYY-MM-DD` calendar dates, never timestamps** |
| `adults`, `children`, `extra_beds` | |
| `status` | `tentative` \| `confirmed` \| `checked_in` \| `checked_out` \| `cancelled` \| `no_show` |
| `hold_expires_at` | set only while `tentative`; null otherwise |
| `assigned_room_id` | null until check-in (sub-project 2) |
| `quote` | **snapshot**: per-night breakdown, room subtotal, tax, total |
| `source` | `front_desk` \| `phone` \| `walk_in` |
| `notes`, `created_by`, `created_at`, `cancelled_at`, `cancellation_reason` | |

**The `quote` snapshot is load-bearing.** Editing next season's rates must never change
the total on an already-confirmed booking. Prices are recomputed only when the booking's
dates, occupancy or meal plan change.

**Status flow in this spec.** `POST /bookings` creates a `confirmed` booking by default.
`tentative` is an explicit option for a phone enquiry the desk wants to hold without
commitment; it consumes inventory exactly like `confirmed`, and carries
`hold_expires_at`. Expiry is not swept automatically in v1 — the bookings list flags
expired holds and staff release them. An automatic sweep is deferred until there is
evidence holds are actually used. `checked_in`, `checked_out` and `no_show` are set by
sub-project 2; this spec only stores and reads them.

### Dates are calendar dates

Check-in is not a moment in time. Stored as `YYYY-MM-DD` strings, compared as strings.
The existing codebase stores ISO datetimes in UTC; a guest arriving on the 5th in IST
must not become the 4th. This is a structural choice, not a convention to remember.

---

## Availability

```
available(type, from, to) =
      count(rooms of type where active
            and no out_of_order range overlaps [from, to))
    − count(bookings of type
            where check_in < to and check_out > from
            and status in (tentative, confirmed, checked_in))
```

Half-open intervals `[check_in, check_out)`: a checkout on the 5th and an arrival on the
5th do not collide. Index on `(room_type_id, check_in, check_out, status)`.

`out_of_order` ranges use the same half-open convention: `{from, to}` blocks the nights
`from` up to but excluding `to`, so a room free again on the 5th is bookable for a guest
arriving the 5th. One convention everywhere; no per-field exceptions to remember.

No inventory ledger. At 20–60 rooms with staff-only booking, an indexed overlap query is
simpler and cannot drift out of sync. A per-(type, date) counter with atomic conditional
updates is the upgrade path if OTA sync ever lands.

---

## API

All under `/api`, all requiring auth unless noted.

```
GET    /guests?q=                  search by phone or name
POST   /guests                     409 with the existing guest if phone is taken
GET    /guests/{id}                profile + stay history + bar/restaurant spend
PUT    /guests/{id}

GET    /room-types                 CRUD  (admin, manager)
POST   /room-types
PUT    /room-types/{id}
DELETE /room-types/{id}            blocked if future bookings exist

GET    /rooms                      CRUD  (admin, manager)
POST   /rooms
PUT    /rooms/{id}
DELETE /rooms/{id}                 blocked if future bookings exist
POST   /rooms/{id}/out-of-order    {from, to, reason}

GET    /meal-plans                 CRUD  (admin, manager)
GET    /rate-periods               CRUD  (admin, manager)
GET    /rates                      CRUD  (admin, manager)

GET    /availability               ?check_in=&check_out=&adults=&children=
                                   → per room type: rooms free, nightly breakdown,
                                     total per meal plan

GET    /bookings                   ?from=&to=&status=&q=
POST   /bookings                   re-checks availability inside the write
GET    /bookings/{id}
PUT    /bookings/{id}              re-quotes and re-validates on date/occupancy change
POST   /bookings/{id}/cancel       {reason}
GET    /bookings/calendar          ?from=&to=  occupancy grid by room type
```

`/availability` returns a priced quote per room type per meal plan, so the booking screen
shows real totals without a second round trip.

### Permissions

| Role | Rooms, rates, meal plans | Bookings, guests | Financial reports |
|---|---|---|---|
| `admin` | full | full | full |
| `manager` | full | full | full |
| `front_desk` | read | full | none |
| `waiter`, `kitchen` | none | none | none |

`front_desk` is new. Rate control stays with admin/manager — the thing an owner least
wants a receptionist changing mid-season.

---

## Screens

Follow existing page conventions and the dark/orange system. Added under a **HOTEL** nav
group so existing bar items stay reachable.

| Screen | Purpose |
|---|---|
| **Availability & New Booking** | Date range + occupancy → room types with counts and prices → book in place |
| **Bookings** | List with date/status filters and guest search |
| **Booking detail** | Itinerary, price breakdown, edit, cancel |
| **Calendar** | Occupancy grid by room type across dates — the manager's daily view |
| **Rooms** | Room types and physical rooms; out-of-order marking |
| **Rates** | Periods, per-type rates, meal plans |
| **Guests** | Search and profile, showing bar/restaurant spend alongside stays |

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| No rate covers a date | **Refuse** with the specific uncovered dates. Never price a night at zero. |
| `check_out <= check_in` | 400 |
| Occupancy > `max_occupancy + max_extra_beds` | 400 with the ceiling |
| No availability | 409 with the dates that are free |
| Date edit makes booking unavailable | 409, booking unchanged, response says what *is* free |
| Out-of-order drops availability below confirmed bookings | Warn with the affected bookings; never auto-cancel |
| Delete room type/room with future bookings | 409 listing them |
| Duplicate guest phone | 409 returning the existing guest so the desk can open it |
| Cancel a `checked_in` booking | 409 — belongs to the check-out flow in sub-project 2 |
| Overlapping rate periods | Resolved by `priority`; UI warns |

### Known limitation: the double-booking window

Availability is re-checked inside the write, but the mock DB has no transactions and
Mongo's single-document atomicity does not span a count-then-insert. Two receptionists
confirming the last room simultaneously could both succeed.

At staff-only volume this is very unlikely, and it is stated rather than papered over.
The fix, when it matters, is a per-(type, date) counter document updated with an atomic
conditional write — worth building alongside OTA sync, not before.

---

## Testing

Pytest, extending `backend/tests/`.

**Extraction guard.** `backend_test.py` passes unchanged before and after the router
split. This is the gate on the extraction step.

**Availability arithmetic** — the core, tested as pure functions in
`services/availability.py` with no database:

- exact-boundary overlap: checkout on the 5th plus arrival on the 5th must both fit
- a booking wholly inside another's range reduces availability
- out-of-order windows remove a room for those dates only
- cancelled and `no_show` bookings do not consume inventory

**Pricing:**

- a stay spanning two rate periods prices each night from the correct period
- GST slab crossing mid-stay (a night below ₹7,500 and a night above)
- extra adults and children beyond `base_occupancy`
- each meal plan priced per person per night
- missing rate raises rather than returning zero

**Booking lifecycle:** create, edit dates with re-quote, cancel, and the refusal paths in
the table above.

**Permissions:** `front_desk` can book but is refused on rates and reports.

---

## Out of scope

Explicitly not in this spec: check-in/check-out, room assignment, guest folio and posting
POS charges to a room, housekeeping status, events and banquets, night audit, guest
self-booking, OTA/channel sync, deposits and payment at booking, invoice PDFs.
