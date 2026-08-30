# Outlets, packages and the guest record

**Status:** design, awaiting review
**Date:** 2026-08-30

A hotel is more than rooms and a restaurant. It has a salon, a gym, a laundry, and one
day something nobody has thought of yet. This is how those arrive without the app
becoming a maze, and how a guest's package decides what they pay in each of them.

---

## The problem, stated honestly

The owner asked for five things at once: salon, gym and laundry as places that can serve
a guest; a guest's room class deciding what they get; every action a guest takes recorded;
HR; payroll. And in the same breath, that the flow stay simple.

Those fight. Five bolted-on modules is five more sidebar sections, five more places to
look, and a receptionist who now has to know which screen a laundry charge lives on.

They stop fighting on one observation: **a salon is not a new kind of thing. It is an
outlet, which the restaurant and the bar already are.** Each has a catalogue, takes an
order, charges a guest, and posts to a folio. All four of those exist and are tested.

`services/access.py:27` reads:

```python
OUTLET = ("restaurant", "bar")
```

A hardcoded tuple. The work is not adding three modules; it is making that list data.

---

## What this does NOT change

Stated first, because the value of this design is mostly in what it leaves alone.

**The authorization spine.** `can_access(user, domains, roles, property, *, setup_time,
permission)` keeps its signature. `require_access` stays the only dependency. The
role-before-admin-bypass trap — every role tuple must name `"admin"` — is untouched, and
so is every existing tuple.

**Tenancy.** Outlets are a property-scoped collection like any other. Routers receive a
bound handle; no route grows a `property_id` filter. `test_isolation.py` must keep
passing with no new allowlist entries.

**The folio ledger.** Append-only, corrections by reversal. Nothing here adds a PUT.

**The 1,127 pure tests.** This design is additive. A test that fails after this work is a
regression, not an expected update — with one deliberate exception, noted under Migration.

---

## Piece 1: outlets become data

### The record

```
outlets
  id                    uuid
  property_id           (stamped by the scoped handle, never passed)
  name                  "Serenity Salon"      — the guest-facing name
  kind                  "restaurant" | "bar" | "salon" | "gym" | "laundry" | "other"
  charges_to_folio      bool   — may an in-house guest charge here?
  takes_direct_payment  bool   — may a walk-in pay by cash/card/UPI?
  active                bool
  created_at
```

`kind` is a fixed vocabulary and `name` is free text, because two things are being asked
of one field everywhere this goes wrong: what the place *is* (which decides icons,
default catalogue and reporting bucket) and what it is *called* (which the hotel owns). A
property may have two restaurants with different names and the same kind.

At least one of `charges_to_folio` and `takes_direct_payment` must be true. An outlet
that can take money in neither way cannot complete a sale, and a hotel should discover
that when saving the outlet rather than when a guest is standing at the counter.

### Who creates one

The hotel's own admin, from Settings → Outlets. Not the platform operator.

The operator approves a property once and should never be in the loop again; a hotel
waiting on us to add a salon is a support ticket that scales linearly with customers.
This matches signup, which is already self-serve.

### How staff reach one

A staff member gains `outlet_ids: [...]` alongside their existing `domains`.

**The domain stays the category; the outlet is the instance.** `require_access(OUTLET,
...)` continues to answer "may this person work in outlets at all", exactly as today.
`outlet_ids` answers "which ones" — a new, narrower question that no existing call site
has to know about.

This is the whole reason the design is affordable. Making an outlet a *domain* would mean
every domain tuple in 244 call sites becomes dynamic, and the one part of this codebase
that must never wobble is the part that decides who may read a folio.

Enforcement lives in one place: a route that names an outlet resolves it through a single
dependency that refuses when the outlet is not in the caller's `outlet_ids`, is inactive,
or belongs to another property. Same shape as `require_access`, same reason — one call
site, one behaviour.

An admin bypasses `outlet_ids` the way they bypass domains today. Consistency matters more
here than the marginal safety of making owners tick their own boxes.

### Migration

Every live property already has a restaurant, a bar, or both, expressed as domains. A
backfill creates the matching outlet rows and fills every user's `outlet_ids`:

- property holds domain `restaurant` → outlet `{kind: restaurant, name: "Restaurant"}`
- property holds domain `bar` → outlet `{kind: bar, name: "Bar"}`
- a user holding domain `restaurant` gains that outlet's id

Run from `on_startup()`, idempotent, following `backfill_expenses` and `backfill_planner`
exactly — including that they are safe to run on every boot forever. A standalone script
would not run: the deployment has no shell step. That lesson is already recorded in
`docs/STATE.md`.

**The one expected test change:** assertions that enumerate the screen catalogue will need
the new outlet screens added. Those tests read the catalogue from `GET /api/permissions`
rather than counting a literal, so most will absorb it without edits.

### What it does to the sidebar

The sidebar builds from the property's actual outlets. A hotel with no salon has no salon
link — not a disabled one, not an empty screen. This is the part that makes the flow
*simpler* rather than more complex, and it is the reason Piece 1 comes first.

---

## Piece 2: packages and entitlements

### The records

```
packages     id · property_id · name · active
inclusions   id · package_id · outlet_id · scope · quantity · period
uses         id · booking_id · inclusion_id · folio_entry_id · used_at
```

`scope` names what is included: a specific catalogue item, a category within the outlet,
or the whole outlet. "Breakfast" is an item; "any treatment" is an outlet.

`period` is `per_stay`, `per_night` or `per_day`. A gym is usually `per_day` and
unlimited; two spa treatments are `per_stay`. Without this field every allowance would
have to be either once-ever or infinite, and neither describes a real rate plan.

A rate points at a package. **That is the entire difference between an elite room and a
normal one** — the elite rate points at a package with more in it. No code branches on
room class, which is what stops "elite" becoming a special case that leaks into twelve
files.

`meal_plan_id` already exists on bookings and rates. Meal plans become the first kind of
package rather than a parallel mechanism, so a property that has configured them keeps
what it configured.

### Consumption is append-only and idempotent

`uses` is a ledger, and each row's id is deterministic:

```python
uuid5(NAMESPACE, f"{booking_id}|{inclusion_id}|{folio_entry_id}")
```

Written with `upsert=True`, so a retried request, a double-tapped Save, or a network
retry consumes one allowance rather than two.

This is not defensive habit. `routers/folios.py` already carries the identical trick for
room nights, because a duplicate posting there double-charged a guest. Burning both of a
guest's two free massages on one tap is the same failure with a worse conversation at
checkout.

### At the point of service

Given a booking, an outlet and an item, the POS shows one of:

```
Massage 60min    Included  (1 of 2)
Massage 60min    Included  (2 of 2)
Massage 60min    Rs 1,800   beyond package
```

**Overage charges at full price and says so.** It does not block and it does not charge
silently. Blocking puts a manager decision in front of a guest who is standing there;
charging silently produces the checkout dispute this feature exists to prevent. The folio
entry carries which inclusion it exceeded, so the reason survives to the bill.

An outlet with `charges_to_folio: false` cannot consume an entitlement — there is no
folio to post against. The POS must not offer it.

---

## Piece 3: the guest record

Mostly a read. Once Pieces 1 and 2 land, a guest's activity is already recorded — it is
their folio.

The addition: **non-charged actions post as zero-value folio entries.** Gym entry at
07:40. Breakfast taken. Laundry collected. They carry an outlet, a time and a
description, and they sum to nothing.

This is what makes "every action of the customer recorded" nearly free rather than a new
subsystem. One timeline per booking, assembled from folio entries, entitlement uses and
the housekeeping events that already exist.

**A zero-value entry must never affect a balance or a total.** `folio_balance` sums
signed amounts, so zero is already inert — but analytics counts entries in places, and
this needs a test that proves a gym entry moves no revenue figure.

---

## Piece 4: the guest invoice

The owner's reference is a hotel PMS invoice screen: one row per stay, guest name,
booking reference, room, nights, amount, a paid/unpaid pill, and a download. Behind each
row, the itemised bill.

**Almost none of this is new plumbing.** `services/folio.py` is already an append-only
ledger with `room_night` and `outlet` entry kinds, every restaurant charge already posts
to it, and `frontdesk.py:189` already checks a guest out. What is missing is the
*document*: `routers/invoices.py` is the operator's GST invoices for platform
subscriptions and has nothing guest-facing in it.

So this piece is two screens reading a ledger that already exists, plus one artifact.

### The invoice is a snapshot, not a view

An invoice is not a live query against the folio. It is written at checkout, stores its
own lines, and never changes afterwards.

This matters more than it sounds. A folio keeps accruing — a guest checks out, and three
minutes later a bar tab from last night gets posted late. If the invoice re-derived
itself from the folio on every read, the copy the guest walked away with and the copy in
the system would silently disagree, and the first anyone would know is an argument. A
snapshot cannot drift.

```
invoices
  id · property_id · booking_id · folio_id
  number            per-property sequence, gapless
  issued_at · issued_by
  guest_name · guest_phone · room_label · nights
  lines[]           description · outlet_name · qty · amount · included?
  subtotal · tax_lines[] · total · paid · balance
  status            paid | unpaid | part_paid
```

`number` is a gapless per-property sequence because that is what a tax document requires;
a hotel cannot explain a missing invoice number to an auditor by pointing at a race
condition. It is allocated inside the same write that creates the invoice.

A late charge after checkout does not edit the invoice. It stays on the folio and, if
settled, produces a second invoice — the same rule as every other ledger here.

### What the guest sees

Lines are grouped by outlet, because "what did I spend at the bar" is the question a
guest actually asks at checkout:

```
ROOM
  Deluxe 103 · 3 nights                        Rs  7,500
RESTAURANT
  Dinner, 28 Aug                               Rs  1,240
  Breakfast, 29 Aug              Included          —
SERENITY SALON
  Massage 60min                  Included          —
  Massage 60min                  beyond package Rs 1,800
```

Included lines appear at zero rather than being hidden. A guest who was given something
should see that they were given it — that is most of the value of selling a package, and
hiding it wastes the goodwill the hotel already paid for.

### Where it lives

Front desk, on the checkout screen, and a list under the hotel section. The list is one
row per invoice with a status pill and a download, filtered by date and status.

Reuses `hotel.frontdesk` for issuing and reading — the person who checks a guest out is
the person who bills them, and a new screen key would grant nobody anything on existing
properties. The list of *all* invoices sits behind its own key, since reading every
guest's spend is a manager's job rather than a receptionist's.

---

## Piece 5: HR and payroll

Genuinely separate, and deliberately not specified here.

Different users (owner and manager, not floor staff), different data (attendance, leave,
documents, salary), and it touches money leaving the business rather than money arriving.
No wage or salary field exists anywhere in the codebase today — it is greenfield, which
is exactly why it should not be smuggled into a spec about outlets.

It gets its own design document. It can be built in parallel with Pieces 1–3, because it
shares nothing with them but the staff record.

---

## Order, and why

1. **Outlets become data** — nothing else can start first. An inclusion has to name an
   outlet.
2. **Packages and entitlements** — depends on 1.
3. **Guest record** — nearly free after 1 and 2.
4. **Guest invoice** — needs 1 and 2, so that a bill can show what a salon charged and
   what a package covered. Buildable straight after them, and the piece the owner can
   see working from the front desk on day one.
5. **HR and payroll** — independent, own spec, parallel if wanted.

---

## Testing

Beyond the usual per-piece coverage, three cases earn named tests because each one has
already bitten this codebase in another form:

**Cross-tenant.** A salon in property A is invisible and unfetchable from property B —
404, not 403. `test_isolation.py` gains no allowlist entry.

**Double consumption.** Posting the same entitlement use twice writes one row and leaves
one allowance remaining. This is the room-night bug in a new costume.

**Outlet scoping.** A waiter with `outlet_ids: [restaurant]` who requests the salon's
catalogue is refused — and the refusal is proven at the route, not only in the predicate.

Plus one that is easy to forget: **an admin of a property with no salon sees no salon
anywhere** — not in the sidebar, not in the screen catalogue, not as a 403 on a URL they
guessed.
