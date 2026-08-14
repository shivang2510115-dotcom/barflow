# Screen Permissions & the Section Chooser — Design

**Date:** 2026-08-14
**Status:** Approved, ready for implementation planning
**Builds on:** the merged work-domains branch

---

## Why this exists

Two problems with how access works today.

**Roles are too coarse.** `waiter` means a fixed bundle of screens decided in code. An owner
who wants one trusted waiter to also see Inventory, or a receptionist who must not see
Rates, has no way to say so. The role list is a developer's guess at a property's staffing.

**Everything is one flat sidebar.** A property running both a hotel and a restaurant shows
every screen for both at once, so the person on the desk scrolls past the KOT board to reach
Bookings.

---

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Permissions vs roles | **Ticks replace roles for what you can reach** | What the owner ticks is what the person sees. No second rule to reconcile, and no guessing what a role name means at this property. |
| What roles still decide | **Who may edit** | Roles stop gating navigation and keep one job: `admin` edits, everyone else views and operates. |
| Enforcement | **At the API**, keyed per endpoint | Hiding a nav item while leaving the endpoint open protects against accident, not intent. Same reasoning that put domains on the API. |
| Section chooser | **Skipped when a person has one section** | A one-option menu is a click that teaches nothing. Only someone with both sections chooses. |
| Sidebar before a choice | **Empty** | The chooser is the page. A sidebar offering everything defeats the point of asking. |
| Existing accounts | **Backfilled with the permissions their role implied** | Nobody loses access on deploy. Narrowing happens deliberately afterwards. |
| Relationship to domains | **Domains stay, unchanged** | Domains are what a person's *job* covers and drive the chooser; permissions are which screens within it. A permission is refused if its domain is not held, so ticking cannot widen someone past their domain. |

---

## Data model

### `users` — one new field

`permissions: list[str]` — screen keys the user may reach. An `admin` holds every screen
regardless; the field is ignored for them, exactly as `domains` already is.

### Screen keys

Stable identifiers, never renamed once shipped — a rename silently revokes access.

| Section | Keys |
|---|---|
| Hotel | `hotel.front_desk`, `hotel.bookings`, `hotel.calendar`, `hotel.rooms`, `hotel.rates`, `hotel.guests` |
| Restaurant | `outlet.tables`, `outlet.pos`, `outlet.kot`, `outlet.reservations`, `outlet.menu`, `outlet.inventory`, `outlet.reports` |
| Admin | `admin.staff`, `admin.analytics` — grantable, so a manager can be given analytics without being made an admin |

`hotel.guests` and `outlet.inventory` both reach `shared` endpoints; they appear under a
section for presentation only.

---

## Enforcement

### Reaching a screen

`require_access` gains an optional `permission` argument. The order becomes:

1. **Active** — as today
2. **Role** — as today
3. **Domain** — as today, `admin` bypassing
4. **Permission** — the user holds this screen key, `admin` bypassing

A permission is only meaningful inside a domain the user holds, so step 3 still runs first
and ticking a screen cannot widen someone beyond their domains.

### Editing

**Only `admin` may change configuration**: room types, rooms, rates, rate periods, meal
plans, tax slabs, menu items and inventory items. Every write to those returns **403** for
anyone else, with a message saying editing is restricted to an administrator.

**Operational writes are not configuration** and follow permissions as normal: taking a
booking, checking in, checking out, posting a charge, opening and settling an order,
seating a table. A receptionist who cannot take a booking is not a receptionist.

The distinction is declared per endpoint, not inferred from the HTTP verb — `POST
/bookings` and `POST /rates` are both writes and only one is configuration.

---

## The section chooser

`/app` becomes the chooser rather than a dashboard.

- Someone holding **both** hotel and an outlet domain sees two cards, each with a live
  figure that makes the choice useful rather than decorative: in-house and arrivals today
  for Hotel, open tables for Restaurant.
- Someone holding **one** section is redirected straight to their first permitted screen.
  They never see the chooser.
- Someone holding **none** — a state the migration prevents — sees a plain message naming
  who to ask, not an empty frame.

**While no section is chosen the sidebar is empty.** Once chosen, it shows that section's
screens, filtered to the user's permissions, and a control in the top bar switches sections
without logging out. The choice persists across a reload, so a refresh mid-shift does not
ask again.

An admin also sees the Admin group, and the chooser shows a third card for it.

---

## API

```
GET  /api/permissions          the catalogue: every screen key, its label and section
PUT  /api/staff/{id}           gains `permissions`, alongside `role` and `domains`
```

The catalogue is served rather than duplicated in the frontend, so the staff screen and the
navigation cannot disagree about what exists. It is `shared` and readable by any signed-in
user — knowing the list of screens is not sensitive, and the staff screen needs it.

---

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Unknown permission key on save | 422 naming the key |
| Permission granted for a domain the user lacks | 400 — it could never take effect, and silently storing it is a lie the owner would rely on |
| Non-admin with zero permissions | 400 on save; an account that reaches nothing is a mistake |
| Existing accounts on migration | Backfilled from their role, so nothing changes on deploy |
| Non-admin attempts a configuration write | 403 naming administrator restriction, not a bare "Forbidden" |
| Permission removed mid-session | Takes effect on the next request; tokens are not revoked |
| A screen key present on a user but retired in code | Ignored on read, dropped on next save |

---

## Testing

**Pure** — extends `services/access.py`:

- holds the permission and the domain → allowed
- holds the domain, not the permission → denied
- `admin` → allowed regardless of permissions
- permission held but domain not held → denied, proving ticks cannot widen
- inactive user with both → denied, the earlier rule still runs

**Integration:**

- a manager without `hotel.rates` gets 403 on `GET /api/rates` and 200 on `GET /api/bookings`
- granting `hotel.rates` makes the same call 200, without a re-login
- a non-admin with `outlet.menu` can read the menu and gets 403 creating a menu item
- the same user can still open and settle an order — operational, not configuration
- saving a permission outside the user's domains is 400
- an unknown key is 422
- after migration, every existing account reaches what it did before

---

## Out of scope

Per-record permissions; read-versus-write ticks per screen — editing is admin-only for now,
by decision; time-boxed access; permission templates or named groups; delegating staff
administration to a non-admin.
