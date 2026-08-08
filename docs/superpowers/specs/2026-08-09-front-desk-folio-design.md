# Front Desk & Guest Folio — Design

**Date:** 2026-08-09
**Status:** Approved, ready for implementation planning
**Scope:** Sub-project 2 of 5 in the hotel/resort programme
**Builds on:** `2026-08-07-hotel-rooms-booking-design.md` (shipped)

---

## Why this exists

Sub-project 1 sells rooms. This one connects them to the outlets.

A guest checks in, drinks at the bar, charges it to room 204, and settles one bill at
departure. That single sentence is the difference between three systems that never speak
and one platform — and it is the claim the product pitch is built on.

### Programme position

| # | Sub-project | Status |
|---|---|---|
| 1 | Rooms + booking engine | **shipped** |
| **2** | **Front desk + guest folio** | **this spec** |
| 3 | Housekeeping | later |
| 4 | Events & banquets | later |
| 5 | Night audit + unified reporting | later |

---

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| How a bill reaches a room | A fourth option at settle: **Charge to room** | Reuses the settle screen staff already know. No new POS concept; the bill still closes and frees the table exactly as now. |
| Revenue timing | Recognised **at the outlet, when posted** | Standard hotel accounting. Monday's bar numbers stay honest and the nightly brief reflects what was actually sold. The folio payment settles a receivable, it is not revenue. |
| Folio shape | A **ledger** — any number of debits and credits | Advances at booking are near-universal for resorts; part-payments and refunds happen. A single-payment model cannot express them without a later rewrite. |
| Check-out with a balance | **Blocked, manager can override** with a recorded reason | Matches a real desk. The alternative is staff faking a payment to release a corporate guest, which corrupts the numbers worse than an audited override. |
| Check-in requirements | **ID capture and room assignment both required** | Indian hotels are legally obliged to record guest identity. Required at check-in, not at booking, because that is where the guest is physically present. |
| Voiding a room-charged bill | **Reverse both** — credit the folio and void the order | The guest is not charged and outlet revenue drops to match. Leaving the order settled would permanently overstate bar revenue. |
| Room-night posting | **One debit per night**, posted **lazily on folio read** | A mid-stay folio shows what is genuinely owed. Lazy posting needs no scheduler and cannot silently skip a night when the server sleeps. |
| Architecture | Folio ledger; orders post into it | Orders keep their current shape, so every existing report works untouched. |

Deferred deliberately: **split folios** (company pays the room, guest pays the bar tab).
Real corporate requirement, but every posting would have to choose a target folio, which
reaches into the POS flow. Revisit when corporate business exists.

---

## Data model

Two new collections. `orders` gains no structural change.

### `folios`

One per booking, created at check-in.

| Field | Notes |
|---|---|
| `id`, `booking_id`, `guest_id` | |
| `status` | `open` \| `settled` \| `closed_unpaid` |
| `opened_at`, `closed_at` | |
| `balance` | **cached for list views only** — recomputed from entries before every decision |
| `closed_reason` | set when force-closed with an outstanding balance |

### `folio_entries`

The ledger. **Append-only — nothing is ever updated or deleted.**

| Field | Notes |
|---|---|
| `id`, `folio_id` | |
| `kind` | `room_night` \| `outlet` \| `misc_charge` \| `payment` \| `refund` \| `discount` \| `void` |
| `direction` | `debit` \| `credit` |
| `amount` | positive; direction carries the sign |
| `description` | what the guest sees on the bill |
| `posted_at`, `posted_by` | |
| `ref_order_id` | set on `outlet` entries |
| `ref_entry_id` | set on `void` entries — the entry being reversed |
| `charge_date` | for `room_night`, the night it covers |

`balance = sum(debits) − sum(credits)`

**Direction per kind** — stated explicitly, because it is easy to get backwards:

| Kind | Direction | Why |
|---|---|---|
| `room_night`, `outlet`, `misc_charge` | debit | the guest owes more |
| `payment`, `discount` | credit | the guest owes less |
| `refund` | **debit** | money handed back to the guest increases what they owe again |
| `void` | opposite of the entry it reverses | voiding a charge credits; voiding a payment debits |

**Unique index on `(folio_id, kind, charge_date)` where `kind = "room_night"`.** This is
what makes lazy night-posting idempotent; it is a constraint, not a convention.

### Changes to existing collections

- `bookings` — `assigned_room_id` becomes live (it exists and is currently inert),
  plus `checked_in_at`, `checked_out_at`.
- `orders` — `payment_method` accepts `"room"` alongside `cash | card | online`, and gains
  an order status value `voided`. No other change.

---

## The three money rules

These exist because recognising revenue at posting creates exactly one way to get it
wrong, and this design must not.

**1. A room-settled order is a normal settled order.** It counts in outlet revenue on the
day it was served, identically to a cash bill. No report needs a special case. The folio
debit is a receivable, not revenue.

**2. A folio payment is never revenue.** It is a credit settling a receivable. Counting it
would book the same money twice — once at the bar on Monday, once at the desk on Thursday.

**3. Voids reverse both sides.** A disputed outlet charge writes a compensating credit
referencing the original entry, **and** sets the underlying order to `voided` so outlet
revenue drops to match. A historical day's revenue figure can therefore change after the
fact. That is correct, and it is the intended consequence of rule 1.

---

## Room-night posting

Nights post **lazily**: any read of a folio first posts every night due but not yet posted,
then returns. No scheduler exists to miss a night, and a laptop that slept is still
correct on next read.

Amounts come from the booking's `quote.nights` snapshot, **not** from a fresh rate lookup —
so the folio agrees with the price the guest was quoted, even if rates changed since.

Nights due as of a date = every night in `[check_in, min(as_of, check_out))` once the
booking is `checked_in`. Early departure simply stops posting; already-posted nights stand.

---

## API

```
POST /api/bookings/{id}/check-in     {room_id, id_proof_type, id_proof_number}
                                     → assigns room, creates folio, status → checked_in
POST /api/bookings/{id}/check-out    {force?, reason?}
                                     → 409 on non-zero balance unless force (manager only)

GET  /api/front-desk                 arrivals today, departures today, in-house now
GET  /api/in-house                   checked-in guests — powers the POS room search

GET  /api/folios?status=             open | settled | closed_unpaid
GET  /api/folios/{id}                folio + entries + balance (posts due nights first)
POST /api/folios/{id}/charges        {amount, description}          → debit
POST /api/folios/{id}/payments       {amount, method, kind}
                                     kind=payment → credit; kind=refund → debit (manager only)
POST /api/folios/{id}/entries/{entry_id}/void   {reason}
```

**Modified:** `POST /api/orders/{id}/settle` accepts `payment_method: "room"` with a
`folio_id`. The order closes and the table frees exactly as today; it additionally writes
the folio debit.

### Permissions

| Role | Check in/out | Post charge | Take payment | Void | Force check-out |
|---|---|---|---|---|---|
| `admin`, `manager` | yes | yes | yes | yes | yes |
| `front_desk` | yes | yes | payments only, **not refunds** | no | no |
| `waiter` | no | charge to room at settle only | no | no | no |

Void and force-check-out stay with managers: they are the two actions that move money
without the guest present.

---

## Screens

| Screen | Purpose |
|---|---|
| **Front desk** | Arrivals, departures and in-house for today. The desk's home screen. |
| **Folio** | The ledger with running balance; add charge, take payment, void. |
| **POS settle** | A "Room" option beside Cash/Card/Online, with in-house search. |
| **Booking detail** | Gains check-in and check-out actions. |

**POS room lookup** searches in-house guests by room number, name or phone. Only
checked-in guests are searchable, so a departed folio cannot be charged by mistake — the
commonest way charges land on the wrong bill.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Check-in, no room of that type free | 409 listing the rooms that **are** free — the upgrade conversation happens here |
| Chosen room occupied or out-of-order | 409 |
| Booking already checked in | 409; never creates a second folio |
| Check-out with non-zero balance | 409 with the balance; `force` + reason required, and the folio becomes `closed_unpaid` |
| Void an already-voided entry | 409 |
| Void an outlet entry | credit written **and** order set to `voided` |
| Charge to a departed folio | impossible — POS searches in-house only |
| Folio read twice in quick succession | night posting is idempotent via the unique index |
| Early departure | stop posting; posted nights stand |
| Stay extended | the extra night posts on next read, no special case |
| Folio with no entries at check-out | settles immediately |

**Concurrency is safe here.** The ledger is append-only, so two simultaneous posts both
land and the balance recomputes correctly — there is no lost update because nothing is
overwritten. This is a deliberate contrast with the booking race documented in
sub-project 1.

---

## Testing

**Pure functions** — no database, following the pattern established by
`services/pricing.py` and `services/availability.py`:

- `folio_balance(entries)` — mixed debits and credits; a void pair cancelling to zero;
  empty ledger is zero.
- `nights_due(booking, as_of)` — mid-stay, on departure day, after early departure,
  before check-in.

**Integration** — the full arc: check-in → charge a bar bill to the room → part-payment →
void a disputed line → check-out. Plus every refusal in the table above.

**Regression** — a cash-settled order behaves exactly as it does today, and outlet reports
are provably unchanged for non-room orders. The existing suites must hold at their
recorded baselines.

---

## Out of scope

Split folios; housekeeping status; events and banquets; night audit and unified
occupancy/ADR reporting; printed or emailed invoice documents; tax invoicing beyond the
GST already computed on room nights; ID document image capture and its retention rules.
