# Where BarFlow stands

Written so a new session — or a new person — can pick this up without the conversation
that built it. The specs in `docs/superpowers/specs/` hold the *reasoning* behind each
decision; this is the map.

**Last updated:** 2026-08-30

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

**Test baselines** (keep these green): pure `1127 passed` · `hotel_api_test.py` `143
passed` · `backend_test.py` **exactly** `1 failed, 9 passed, 1 skipped` — that failure is
`TestStripeCheckout::test_create_checkout_session_returns_stripe_url`, environmental, and
must stay failing.

Run the API suites against a local server: `REACT_APP_BACKEND_URL=http://127.0.0.1:8000`.

---

## What is not done

**WhatsApp does not send.** The machinery is complete and refuses honestly, naming what is
missing. It needs a Meta WhatsApp Business account, an approved template per message type,
and credentials. The owner is registering one, going direct to Meta rather than a BSP.

**Credentials are still global env vars.** `WHATSAPP_TOKEN` / `WHATSAPP_PHONE_ID` /
`OWNER_PHONE` are per-deployment, so every hotel would message from the same number. The
owner's decision is that messages go from **the restaurant's own number only, with no
platform fallback**. The design is written up in
`specs/2026-08-28-per-property-whatsapp-design.md` and is the next thing to build.

**Razorpay** — specced in `specs/2026-08-27-gst-invoicing-and-razorpay-design.md`, not
built. Guest online payment, per-property credentials, enabled by the operator.

**Guest registration and Form C** — specced, folded into tenancy, not built.

---

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

## Running it locally

```
cd backend && MONGO_URL=mock DEMO_LOGINS=true SEED_DEMO_CONTENT=true \
  python3 -m uvicorn server:app --host 127.0.0.1 --port 8000
cd frontend && PORT=3001 REACT_APP_BACKEND_URL=http://127.0.0.1:8000 npx craco start
```

`backend/.env` holds the local keys and is gitignored. `.deploy-secrets` holds the
deployment answers — **including `GUEST_ID_ENCRYPTION_KEY`, which decrypts guests'
identity documents. Lose it and those are unrecoverable.** Back it up.
