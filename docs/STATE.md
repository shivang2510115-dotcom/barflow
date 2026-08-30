# Where BarFlow stands

Written so a new session — or a new person — can pick this up without the conversation
that built it. The specs in `docs/superpowers/specs/` hold the *reasoning* behind each
decision; this is the map.

**Last updated:** 2026-08-30 (second pass — 23 commits later)

---

## What it is

A multi-tenant SaaS for hotels and restaurants in India. Businesses register themselves, a
platform operator approves them, and each one runs its property: rooms, bookings, folios, a
POS, housekeeping, staff, money.

**Live:** https://barflow-33e80.web.app — Firebase Hosting + a Python Cloud Function +
Firestore, all in one Firebase project. `./deploy.sh` does the whole thing.

**Repo:** github.com/shivang2510115-dotcom/barflow

---

## The four decisions everything else rests on

**1. Tenancy is bound, not passed.** There are ~244 database call sites. None of them
carries a `property_id` filter, because a router receives a handle that has already
resolved the caller's property — reads filtered, writes stamped, by construction. The
unscoped handle is called `unscoped_db` precisely so any direct use is greppable; only
`auth`, `staff`, `property`, `payments`, `platform` and the background scheduler touch it,
each for a collection outside tenancy.

A test in `backend/tests/test_isolation.py` fails if any route reaches the database
without a bound handle and is not on an allowlist with a stated reason. It has caught new
routes several times.

**2. Authorization is one function.** `can_access(user, domains, roles, property, *,
setup_time, permission)` in `backend/services/access.py`. Four axes, checked in order:
property usable → user active → role → domain → screen permission. `require_access` in
`security.py` is the only dependency that applies it.

**The trap:** the role check runs *before* the admin bypass, so **any role tuple that
omits `"admin"` refuses admins**. Every tuple in the codebase names it.

**3. Money ledgers are append-only.** Folio entries, platform invoices and expenses are
never edited or deleted — a correction is a reversing entry that references the original.
Both survive. This is deliberate and tested; do not add a PUT.

**4. Days are the property's, never the server's.** `backend/services/clock.py`. A UTC
timestamp sliced for its date put revenue on the wrong day once already — everything
recorded between midnight and 05:30 IST landed on the previous day. Use `local_date()` and
`today()`, never `[:10]` on a timestamp.

---

## What is built

Rooms, room types, rates and GST slabs · bookings with pre-assignment and stay extension ·
front desk, check-in/out, folios with an append-only ledger · a POS with tables, KOT, menu
with portion variants, and QR self-ordering · housekeeping with jobs, priorities, an
in-room guest QR and a polled alert · guests, occasions and follow-up messaging ·
inventory with a reviewed bulk import · staff with roles, work domains and per-screen
permissions · a planning calendar · expenses with graphical reports · analytics splitting
hotel and outlet revenue · the operator console: approve, suspend, price, record payments,
issue GST invoices.

**Added in the second pass, and each worth knowing about:**

**Outlets are data.** `OUTLET = ("restaurant", "bar")` used to be a hardcoded tuple. A
hotel's own admin now adds a salon, gym or laundry from Settings. The domain stayed the
*category* and `outlet_ids` on the user answers *which one* — so `require_access` never
changed and neither did its 244 call sites.

**Packages and entitlements.** A rate points at a package; a package holds inclusions
scoped to an item, a category or a whole outlet, refilling per stay, per night or per
day. **That is the entire difference between an elite room and a normal one** — no code
branches on room class. A booking snapshots its `package_id` at creation, deliberately
not a `rate_id`: a rate is editable and a price change must not retroactively alter what
a guest already staying was sold. The POS shows what is left and can comp a line.

**Bills.** Drawn at checkout, listed and printable. **A bill is a snapshot, not a view** —
written once, never changed. A folio keeps accruing, so a bill that re-derived itself
would quietly disagree with the paper the guest is holding. A late charge produces a
second bill. Numbers are gapless per Indian financial year.

**The stay timeline.** Everything that happened during one stay, merged from the folio,
entitlement uses, the room's housekeeping log and the booking itself. Deliberately *not*
the bill: a bill drops a voided charge, a timeline keeps it, because a mis-keyed charge
that was voided is what happened.

**The Today board** replaced the section chooser as the landing screen, and carries ADR,
RevPAR and occupancy. **⌘K** reaches any screen, room or guest. The sidebar shows
everything reachable when no section is picked — before, a deep link gave you one link
and no way to navigate.

**One palette, both themes.** Colour is chosen by role (`ground`, `surface`, `ink`,
`hairline`, `brass`) not by shade, defined once in `index.css`. Light is the default with
a toggle; the old dark palette is preserved exactly. Room state left the brand hue
entirely — orange used to mean both "brand" and "occupied". Charts read the tokens at
runtime via `lib/theme.js`; hex literals in JS were how the first repaint half-failed.

**Every control is 44px.** No button in this app used to be tappable — the commonest was
29px against a floor WCAG, Apple and Material all agree on.

**Test baselines** (keep these green): pure `1199 passed` · `hotel_api_test.py` `183
passed` · `backend_test.py` **exactly** `1 failed, 9 passed, 1 skipped` — that failure is
`TestStripeCheckout::test_create_checkout_session_returns_stripe_url`, environmental, and
must stay failing.

Run the API suites against a local server: `REACT_APP_BACKEND_URL=http://127.0.0.1:8000`.
The operator tests need `PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD` set on the
server too, or they skip.

## What is not done

**WhatsApp has no credentials yet.** The machinery is complete and per-property: a hotel
messages from its own number, entered by the operator, token encrypted and never
returned. There is no platform fallback and it is enforced structurally —
`services/whatsapp.py` does not import `os`, and a test asserts that. What is missing is
a Meta WhatsApp Business Account, a dedicated number, an approved display name, business
verification, and approved templates. None of that is code.

**Razorpay** — specced in `specs/2026-08-27-gst-invoicing-and-razorpay-design.md`, not
built. Guest online payment, per-property credentials, enabled by the operator.

**HR and payroll** — Piece 5 of the outlets spec, not started. The largest remaining
piece. It touches money leaving the business, so it needs the append-only discipline the
folio and expense ledgers already have.

**Guest registration and Form C** — specced, folded into tenancy, not built. Form C is a
legal requirement for foreign guests in India.

**Menu cost** — one field on a menu item would give profit-per-item across the menu.
**Checkout feedback** — one control would give satisfaction scores and catch a complaint
before it becomes a public review. Both small, both unbuilt.

**The POS comps only outlet-scoped inclusions.** Category and item scopes work in the
engine but the cart line does not carry the menu item's category, so the button cannot
be offered honestly for them yet.

## Things that will bite

- **`backend_test.py` targets a remote host** that now 404s. Run it against a local server
  with `STRIPE_WEBHOOK_SECRET` set, or it reports failures that are not yours.
- **The mock database is not the deployed one.** `MONGO_URL=mock` gives a JSON file;
  production is Firestore. `create_index` is a no-op in both the mock and Firestore, so
  **no uniqueness is enforced by the database** — the routers' own pre-checks are the only
  guard, and a room night's document id is deterministic for exactly that reason.
- **A restarted local server serves old code.** More than one "regression" here was a
  stale uvicorn. Restart before believing a failure.
- **The alert polls every 15 seconds** per visible tab per hotel user. That is 98% of all
  function invocations and most Firestore reads. Comfortable at ~4 staff; crosses the free
  tier at ~8. Widening to 30s halves it.
- **A new screen key reaches nobody already hired.** `backfill_permissions` fills a
  *missing* permissions field and deliberately never touches a present one, which is what
  stops an account an owner narrowed being widened back. So every new key needs its own
  migration — `backfill_housekeeping`, `backfill_expenses`, `backfill_planner` and
  `backfill_bills` are four instances of the same lesson, and `hotel.bills` would have
  been invisible at every live property without the fourth.
- **A string replace that does not match fails silently.** Two sidebar entries were
  written and never landed because the target line differed by one character. Assert on
  every scripted edit, and look at the screen afterwards.
- **Colour lives in JS too.** The first repaint rewrote Tailwind classes and missed every
  hex literal, so charts kept drawing the dark palette on porcelain. `lib/theme.js` reads
  the tokens at runtime; grep for `#[0-9a-f]{6}` before believing a repaint is done.

## Running it locally

```
cd backend && MONGO_URL=mock DEMO_LOGINS=true SEED_DEMO_CONTENT=true \
  python3 -m uvicorn server:app --host 127.0.0.1 --port 8000
cd frontend && PORT=3001 REACT_APP_BACKEND_URL=http://127.0.0.1:8000 npx craco start
```

`backend/.env` holds the local keys and is gitignored. `.deploy-secrets` holds the
deployment answers — **including `GUEST_ID_ENCRYPTION_KEY`, which decrypts guests'
identity documents. Lose it and those are unrecoverable.** Back it up.
