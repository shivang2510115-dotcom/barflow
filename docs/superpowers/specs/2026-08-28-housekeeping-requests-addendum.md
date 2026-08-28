# Housekeeping: Requests, Priority and the Live Alert — Addendum

**Date:** 2026-08-28
**Status:** Approved, to be built together with the base spec
**Extends:** `2026-08-09-housekeeping-design.md`, which is approved and unbuilt

---

## What the base spec already settles

Read it first. It covers the room's `housekeeping_status` (`clean` / `dirty` / `inspected`
/ `out_of_order`), the append-only `housekeeping_events` log, the `housekeeping` role, the
transition table, check-out auto-dirtying a room, and the attendant's phone screen. None
of that changes.

It also settles one thing worth restating, because it looks like a bug otherwise:
`rooms.out_of_order` (date ranges controlling what can be **sold**) and
`housekeeping_status = "out_of_order"` (not usable **right now**) are deliberately
separate fields with separate owners. An attendant's tap must never silently cost the
hotel a booking.

## What this adds

### 1. A job, raised by a person

Today the base spec has a *status* an attendant changes. This adds a **request**: someone
asks for a room to be cleaned, and it is a thing with a life of its own — raised, picked
up, done — separate from the room's current status.

| Field | Notes |
|---|---|
| `room_id` | |
| `priority` | `low` \| `normal` \| `high` |
| `reason` | free text — "spill on the carpet", "guest asked for fresh linens" |
| `raised_by` | a staff id, or **null when a guest raised it from the QR** |
| `status` | `open` \| `in_progress` \| `done` \| `cancelled` |
| `created_at`, `acknowledged_at`, `completed_at`, `completed_by` | |

Append-only in spirit: a job is never deleted, and cancelling is a status, so "who asked
for this and when" survives.

**Who may raise one:** admin, manager, front desk, and housekeeping itself. Anyone
working the floor can see a room needs attention.

### 2. A QR in each room, for the guest

Each room gets its own QR, printed and placed in the room. A guest scans it, states what
they need, and a job appears. No account, exactly like the table QR ordering already
shipped — and scoped the same way, from the room in the URL.

**A guest cannot see anything else.** Not the room's status, not other rooms, not who is
staying anywhere. They see the hotel's name, their room number, a reason box, and
confirmation that it was received. Follow the reasoning in `routers/frontdesk.py`'s
`_pos_guest`: everything left in the response is something an anonymous caller can read.

**It must be rate-limited**, per room and per address, reusing `services/ratelimit.py`.
A QR in a public place is an open door, and a room with two hundred pending requests is
a denial of service against the housekeeping screen.

A printable PDF of room QR codes, following `make-table-qr-pdf.py` — same card shape,
same 100%-scale warning.

### 3. A live alert, by polling

An open job appears on every screen of every signed-in user who holds the hotel domain,
without them refreshing.

**Polling, not a live connection.** The alternative — the browser subscribing to Firestore
directly — would put tenant isolation into Firestore security rules, a second
authorization system running beside the one in `services/access.py`. Two systems that must
agree about who sees what is how one hotel's data reaches another's screen. One place
decides access, and it stays the backend.

Roughly every 15 seconds. Quiet: a request appears within 15 seconds, which is
operationally the same as instant for a room that needs cleaning. Do not poll when the tab
is hidden — a front desk leaves this open all day and a background tab costs invocations
for nothing.

The alert names the room, the priority and the reason, and offers acknowledgement. It must
not reappear once acknowledged, and must not stack into a wall a receptionist has to
dismiss one at a time.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Guest raises a second request for a room with one already open | The existing job is kept and its reason appended; no duplicate. A guest pressing twice is a guest who is unsure it worked. |
| Guest scans a room of a pending or suspended property | Refused, as every other operating action is |
| Job raised for a room that does not exist | 404 |
| Two staff acknowledge at once | Last write wins; the log records both, and neither sees an error |
| Unknown priority | 422 |
| Empty reason from a guest | Allowed — "something is wrong in 204" is still worth knowing |
| Job completed for an occupied room | Allowed. Mid-stay cleaning is the ordinary case. |

---

## Testing

**Pure:** the transition table for a job, including that a done job cannot be reopened;
and the rule that a second guest request merges rather than duplicates.

**Integration:** a guest can raise a job and read nothing else about the room; a waiter
cannot see the housekeeping screen; a job raised by QR appears in the alert for a hotel
user and not for an outlet-only one; acknowledging removes it from the alert but leaves it
in the log; and the rate limiter refuses a flood from one address.

---

## Out of scope

Assigning jobs to named attendants; time tracking; linen and amenity stock; scheduled
recurring cleans; and pushing to a phone outside the browser.
