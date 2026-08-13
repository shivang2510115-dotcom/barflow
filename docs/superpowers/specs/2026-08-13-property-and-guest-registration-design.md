# Property Setup & Guest Registration — Design

**Date:** 2026-08-13
**Status:** Approved, ready for implementation planning
**Builds on:** the shipped hotel programme (rooms/booking, front desk/folio) and the
staff & work-domains branch, now merged.

---

## Why this exists

The system knows how to run a property but does not know **which** property it is. Every
bill, every report and every legally-required form needs the hotel's own identity — name,
address, GSTIN — and today that is nowhere. "BarFlow" is printed where the hotel's name
belongs.

Separately, check-in captures an ID proof type and number and nothing else. Indian law
requires more than that, and for a foreign national requires a great deal more.

Two pieces, in this order, because the second needs the first.

---

## Piece A — Property setup

### What it is

One screen, `/app/admin/property`, admin only. The property's own record: there is exactly
one, and it always exists.

| Field | Notes |
|---|---|
| `name`, `legal_name` | the board outside vs. the name on the invoice; often differ |
| `address_line1`, `address_line2`, `city`, `state`, `pincode` | |
| `phone`, `email`, `website` | |
| `gstin` | 15 characters, validated by format |
| `fssai_licence` | 14 digits, required to serve food in India |
| `check_in_time`, `check_out_time` | default `14:00` / `11:00` |
| `logo` | a data URI, stored on the record |
| `timezone` | defaults to `Asia/Kolkata`; the clock helper already reads `PROPERTY_TZ` |

### Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| How many properties | **Exactly one**, a singleton row | Multi-property is a different product with a tenant on every query. Designing for it now costs every table a `property_id` that buys nothing today. |
| Missing property record | **Seeded blank with a sensible name**, never absent | A `None` here would need a guard at every call site that prints a bill. A blank record needs one banner. |
| GSTIN validation | **Format only**, not a government lookup | The checksum-and-registry call needs a paid API and network access at save time. A format check catches the realistic error — a typo — without either. |
| Who may edit | **admin only** | It is the legal identity of the business. |
| Where it is read | Bills, the registration card, Form C, report headers | |

### Clearing the demo data

The same screen carries a **"Clear demo data"** action, separate from saving the profile.
It deletes seeded rooms, room types, rates, tables, menu items, guests, bookings, folios and
orders — and **nothing else**. Staff accounts survive, because deleting the admin's own
login mid-setup is unrecoverable.

It is destructive and irreversible, so it uses the inline two-step confirm this codebase
already uses for cancel and void, and it states the exact counts it is about to delete
(*"12 rooms, 31 orders, 6 bookings"*) rather than a vague warning. A property that has
already taken a real booking must not be able to run it by accident: if any booking exists
whose `created_at` is later than the property record's `demo_cleared_at`, the action asks
for the property name to be typed to confirm.

---

## Piece B — Guest registration

### What it is

The registration card the guest completes on arrival. It extends check-in rather than
replacing it: check-in already captures `id_proof_type` and `id_proof_number`, and already
refuses to proceed without them.

**New fields on the guest record**

| Field | Notes |
|---|---|
| `date_of_birth` | |
| `arriving_from`, `proceeding_to` | asked on every Indian registration card |
| `purpose_of_visit` | leisure / business / event / other |
| `vehicle_number` | blank for most guests |
| `company` | for a business stay billed to an employer |
| `emergency_contact_name`, `emergency_contact_phone` | |
| `signature` | a data URI, captured on screen |

**Form C — foreign nationals only.** Legally required in India, and the fields exist for no
other purpose:

| Field |
|---|
| `passport_number`, `passport_issue_place`, `passport_issue_date`, `passport_expiry_date` |
| `visa_number`, `visa_type`, `visa_issue_place`, `visa_issue_date`, `visa_expiry_date` |
| `arrival_date_in_india`, `port_of_arrival` |

These appear **only** when `nationality` is set to something other than Indian. Showing
eleven passport fields to a domestic guest is how a form gets abandoned.

### Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Where registration lives | **On the guest**, not the booking | A returning guest re-registers rarely; their passport does not change between stays. The stay records that a card was signed and when. |
| Form C submission | **Generate the form; do not file it** | Filing goes to the government's FRRO portal, which needs credentials, an account and its own error handling. Producing a correct, printable form is the part that helps today. |
| When it is required | **Never blocks check-in** | An arriving guest at 2am with a queue behind them gets a room. The card is flagged incomplete on the stay until finished. |
| Signature | **Drawn on screen**, stored as a data URI | A tablet at the desk is how this is actually done. No third-party signature service. |
| Validation | **Format only, and only what is filled in** | A half-complete card must save. Rejecting a partial form loses the work already typed. |

### Screens

**`/app/hotel/guests/{id}/registration`** — the card. Domestic fields always; the Form C
block appears when nationality is non-Indian. Saves partially. A **Print** action renders
the card on the property's letterhead, using Piece A's record.

**Front desk** — a stay whose guest has an incomplete card shows a quiet marker, and the
check-in panel offers "Complete registration" after assigning the room. It never blocks.

---

## API

```
GET    /api/property                     the singleton; readable by any signed-in user
PUT    /api/property                     admin only
POST   /api/property/clear-demo-data     admin only; returns the counts it deleted
GET    /api/guests/{id}/registration     the card, including Form C when applicable
PUT    /api/guests/{id}/registration     partial save
```

`GET /api/property` is `shared`: a bill printed at the POS needs the property's header just
as much as a folio invoice does.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Property never configured | Reads return the blank seeded record; screens show a "finish setting up" banner |
| Malformed GSTIN or FSSAI | 400, naming which field and the expected shape |
| Clear demo data with real bookings present | 409 unless the typed property name matches |
| Form C fields sent for an Indian national | Stored but not printed — nationality can be corrected later, and silently dropping typed data is worse |
| Passport expiry before arrival date | 400 — the one cross-field check worth making |
| Registration saved with only some fields | 200; the card is marked incomplete |
| Guest deleted with a signed card | unchanged — guests are already never hard-deleted |

---

## Testing

**Pure functions** — `backend/services/registration.py`, following the existing pure modules:

- `validate_gstin(value)` — 15 characters, `NN AAAAA NNNN A N A`; rejects a 14-character value
- `validate_fssai(value)` — 14 digits
- `is_foreign(nationality)` — the predicate that reveals Form C; treats blank as domestic
- `registration_complete(guest)` — which fields are still missing, so the marker and the
  banner both read from one rule rather than two

**Integration:**

- `PUT /api/property` as a manager → 403; as an admin → 200
- A malformed GSTIN → 400 naming the field
- `GET /api/property` reachable by a waiter (it is `shared`)
- Clear demo data returns the counts, leaves staff accounts intact, and 409s when real
  bookings exist without the typed confirmation
- A partial registration saves and reports itself incomplete
- Form C fields round-trip for a foreign national
- Passport expiring before arrival → 400

---

## Out of scope

Multi-property tenancy; filing Form C with the FRRO portal; government GSTIN verification;
scanning an ID document to fill the card (that is the bill-scan sub-project's machinery,
pointed at a different document, and worth doing only once that ships); e-signature legal
attestation; guest self-service pre-registration by link.
