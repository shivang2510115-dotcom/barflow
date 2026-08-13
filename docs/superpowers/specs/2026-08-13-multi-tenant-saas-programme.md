# BarFlow as a Multi-Tenant SaaS — Programme Decomposition

**Date:** 2026-08-13
**Status:** Decomposition agreed; sub-project 1 ready for design
**Supersedes:** the singleton assumption in `2026-08-13-property-and-guest-registration-design.md`

---

## What changed

BarFlow was a system one property runs. It becomes a service many properties use, where a
hotel signs itself up and a **platform operator** approves it before it can trade.

That is not a feature on top of the existing app. It changes the meaning of every query in
it. Today `db.bookings.find({})` means "this property's bookings" because there is only one
property. Tomorrow it means "every property's bookings", and the same line becomes a data
breach.

### Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| A pending hotel | **Can set up, cannot operate** | They enter their property, rooms, rates, menu and staff, so they can evaluate the product with their own data. Bookings, check-in and settling are locked until approved, so no guest money moves through an unvetted property. |
| Billing | **Manual, per hotel** — the operator sets an amount, a period and a paid-until date | No payment gateway, no self-serve checkout, no subscription webhooks. Collection happens offline; the platform records what was agreed and when it lapses. |
| The operator account | **A `platform_admin` role seeded from an environment variable** | It belongs to no hotel and cannot be created through the app, so nobody can sign up their way into approving themselves. |
| Isolation mechanism | **A property-scoped database handle**, not 244 remembered filters | See below. This is the decision the whole programme rests on. |

---

## The isolation decision

There are **244 database call sites** across the routers today and not one is tenant-scoped.

Adding `{"property_id": pid}` to each of them would work exactly until somebody forgets
once — and the failure is silent. Nothing errors. A list simply contains another hotel's
rows, and it is discovered by a customer, not by a test.

**So the scope is not passed, it is bound.** A router receives its database handle from a
dependency that has already resolved the caller's property and applied it. Every read is
filtered and every write is stamped, by construction. The unscoped handle keeps existing —
seeding, migrations and the platform admin genuinely need it — but under a name that makes
any direct use visible on sight in review, the way `require_access` made authorization
greppable.

Three collections stand outside a property: `users` (a login must be findable by email
before we know which hotel it belongs to), `properties` itself, and `platform_*` records.
They are named explicitly rather than left as exceptions to notice later.

**Tests must prove isolation directly.** Not "hotel A sees its own three bookings" — that
passes when the filter is missing and hotel B has none. The test creates data in *both*
hotels and asserts that A's count is exactly A's, that B's ids never appear in A's response,
and that fetching B's record by id from A's session returns 404, not 403 — a 403 confirms
the record exists, which is itself a leak.

---

## Sub-projects, in dependency order

| # | Sub-project | Why this order |
|---|---|---|
| **1** | **Tenant isolation** | Everything else writes data that must already be scoped. Retrofitting isolation after hotels have real data means a migration under load, with a live breach until it lands. |
| **2** | **Signup, approval and the operator console** | The lifecycle: register → pending → live / rejected / suspended, the `platform_admin` role, and the gate that stops a pending hotel trading. |
| **3** | **Manual subscriptions** | Amount, period, paid-until, and what a lapsed hotel can still do. Depends on 2's lifecycle states. |
| **4** | **Public-launch hardening** | Detailed below. Must complete before the first external hotel is let in. |

Property setup and guest registration — the previous spec — fold into sub-project 1 and 2:
the property record stops being a singleton and becomes the tenant itself, and the signup
form is the first half of the setup screen.

---

## Sub-project 4 is a gate, not a nice-to-have

The app was built for one trusted property on a local network. These are safe there and not
safe on the public internet with other people's guest data. **None was introduced by recent
work; all predate it.**

| Issue | What it allows today |
|---|---|
| Stripe webhook falls back to parsing the raw body with **no signature verification** (`payments.py`) | An anonymous POST settles an order and frees a table without payment |
| Anonymous order creation on any table id (`orders.py`) | A caller with a table id appends items to a bill about to be settled; `source` is client-supplied |
| `CORS_ORIGINS` defaults to `*` while credentials are allowed | Any website can call the API with a logged-in user's token |
| `JWT_SECRET` falls back to a hardcoded default | If the env var is ever unset in production, every token is forgeable |
| `DEMO_LOGINS` seeds accounts whose passwords are published in this repo | Must be `false` everywhere public; today it defaults to `true` |
| No rate limiting on `POST /auth/login` | Unlimited password guessing |
| No password strength rule beyond 8 characters | |
| Guest ID-proof and passport numbers stored in plain text | A database compromise exposes identity documents |

Sub-project 4 fixes each, and adds what a paying customer will ask for: an audit trail of
who changed what, and a per-property data export.

---

## What this does not include

Self-serve card payment for subscriptions; per-tenant databases; regional data residency;
a public marketing site; hotel-to-hotel data sharing; white-labelled domains per hotel;
filing Form C with the FRRO portal; and SSO.
