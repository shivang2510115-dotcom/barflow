# Housekeeping — Design

**Date:** 2026-08-09
**Status:** Approved, ready for implementation planning
**Scope:** Sub-project 3 of 5 in the hotel/resort programme
**Builds on:** `2026-08-07-hotel-rooms-booking-design.md` and `2026-08-09-front-desk-folio-design.md` (both shipped)

---

## Why this exists

Rooms sell and guests check in and out. Nothing yet tracks whether a room is actually
ready. Today the front desk can assign a departed, unmade room to an arriving guest and
nobody finds out until the guest opens the door.

This adds a room status an attendant updates from their phone in the room they have just
finished, and surfaces it where the desk assigns rooms.

### Programme position

| # | Sub-project | Status |
|---|---|---|
| 1 | Rooms + booking engine | shipped |
| 2 | Front desk + guest folio | shipped |
| **3** | **Housekeeping** | **this spec** |
| 4 | Events & banquets | later |
| 5 | Night audit + unified reporting | later |

This is deliberately the smallest sub-project in the programme.

---

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Check-in and a dirty room | **Warn, but allow** | An early arrival waits while a room is turned; the desk needs to assign it now and hand the key later. A hard block turns that into a workaround. |
| Who updates status | New **`housekeeping`** role, individual logins | The attendant updates from the room they just finished rather than radioing it in. Individual logins make every change attributable. |
| Lifecycle | Check-out **auto-dirties**; inspection **optional** | Nobody has to remember the one step most likely to be forgotten. Both `clean` and `inspected` count as ready, so a small property can ignore inspection and a larger one can use it. |
| Broken room | Attendant flags **out-of-order**; a manager takes it off sale | Stops the desk assigning it immediately, but removing inventory from sale has revenue consequences and stays a manager decision. |
| Architecture | Status on `rooms` + append-only `housekeeping_events` | Status lives where it is read; the log is what makes individual logins worth having. |
| Assignment to attendants | **Out of scope** | Nothing described needs it. A flat room list is what an attendant uses. Adding it later is cheap; guessing now at how this property divides floors is not. |

---

## Two different axes, deliberately not merged

`rooms.out_of_order` already exists: a list of **date ranges** that remove a room from
availability. It is about what can be **sold**, it is manager-controlled on the Rooms
screen, and `services/availability.py` reads it.

`housekeeping_status = "out_of_order"` means **not usable right now**. It blocks room
assignment at check-in and nothing else. It does not touch availability and does not
affect what the booking engine will sell.

Merging them would mean an attendant's tap silently costs bookings. They stay separate
fields with separate owners.

---

## Data model

### `rooms` — four new fields

| Field | Notes |
|---|---|
| `housekeeping_status` | `clean` \| `dirty` \| `inspected` \| `out_of_order`; seeded `clean` |
| `housekeeping_note` | free text; **required** when status is `out_of_order` |
| `housekeeping_updated_at` | |
| `housekeeping_updated_by` | user id |

### `housekeeping_events` — new collection, append-only

| Field | Notes |
|---|---|
| `id`, `room_id` | |
| `from_status`, `to_status` | |
| `note` | |
| `changed_by`, `changed_at` | |

Nothing here is ever updated or deleted. When a guest complains their room was filthy,
this is what answers who marked it clean and when.

**Ready** means `housekeeping_status` is `clean` or `inspected`. That predicate lives in
one place and is used by both the check-in warning and the housekeeping screen.

---

## Transitions

**The only automatic transition:** check-out sets the room to `dirty`. One line in the
existing check-out handler in `backend/routers/frontdesk.py`.

| From | To | Who |
|---|---|---|
| any | `dirty` | housekeeping, front_desk, manager, admin — or automatically at check-out |
| `dirty` | `clean` | housekeeping, manager, admin |
| `clean` | `inspected` | **manager, admin only** |
| any | `out_of_order` | housekeeping, manager, admin — **note required** |
| `out_of_order` | `dirty` or `clean` | manager, admin |

**Housekeeping can put a room out-of-order but cannot take it back.** Only a manager or
admin clears `out_of_order`. That is deliberate: the attendant reports the fault, someone
accountable confirms it is actually fixed before the room is sold again. On the
housekeeping screen an `out_of_order` room therefore shows its note and offers an
attendant no status options — the room is visibly waiting on someone else.

A room occupied by a checked-in guest can still be marked dirty — guests generate mess
mid-stay. Status never blocks anything for the guest already in the room; it only affects
assigning that room to somebody new.

---

## API

```
GET /api/housekeeping        rooms with status, whether occupied, and whether the
                             current guest departs today — so an attendant can see
                             which rooms are turning
PUT /api/rooms/{id}/housekeeping   {status, note?}
```

`GET /api/housekeeping` is readable by `housekeeping`, `front_desk`, `manager`, `admin`.
`PUT` enforces the transition table above.

**Modified:** the check-out handler additionally sets the room `dirty` and writes an event.

`GET /api/rooms` already returns rooms and now carries the new fields, so the front desk
check-in dropdown needs no new call.

---

## Screens

**`/app/hotel/housekeeping`** — one mobile-first screen. A list of room cards, each
showing number, current status, and whether the room is occupied or departing today.
Tapping a card offers the statuses that role may set. Big touch targets, no table — this
is used one-handed, standing in a corridor.

**Front desk check-in** — the existing room dropdown gains a status label per room, and
picking a room that is not ready asks for confirmation before proceeding. It never
refuses.

### Permissions

| Role | View housekeeping | Set dirty / clean | Set inspected | Set out-of-order |
|---|---|---|---|---|
| `admin`, `manager` | yes | yes | yes | yes |
| `housekeeping` | yes | yes | no | yes |
| `front_desk` | yes | dirty only | no | no |
| `waiter`, `kitchen` | no | no | no | no |

`housekeeping` reaches only this screen. It cannot see bookings, rates, reports or
the POS.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Unknown status value | 422 |
| `out_of_order` with no note | 400 |
| Setting the status a room already has | **no-op; writes no event** — otherwise a double-tap on a phone fills the log with noise |
| `housekeeping` user sets `inspected` | 403 |
| Room not found | 404 |
| Marking an occupied room dirty | allowed — mid-stay mess is normal |
| Check-in to a not-ready room | allowed, after a confirmation |

---

## Testing

**Pure functions** — no database, following `services/pricing.py`, `availability.py` and
`folio.py`:

- `is_ready(status)` — true for `clean` and `inspected`, false otherwise.
- `can_set(role, from_status, to_status)` — the transition table, including
  `housekeeping` being refused `inspected`.

**Integration:**

- Check-out sets the room `dirty` and writes an event.
- `housekeeping` can set `clean`, is refused `inspected` with 403.
- `out_of_order` without a note is 400.
- Setting an unchanged status writes no event.
- The event records `from_status`, `to_status` and `changed_by`.
- A `waiter` cannot reach `GET /api/housekeeping`.

---

## Out of scope

Assigning rooms to named attendants; per-attendant workload views; cleaning duration or
productivity tracking; maintenance work orders beyond the free-text note; linen or amenity
inventory; anything touching the `out_of_order` **date ranges** that control availability.
