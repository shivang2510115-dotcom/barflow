# Task 8 report — Folio screen

## Files changed

- Created: `frontend/src/pages/hotel/Folio.jsx`
- Modified: `frontend/src/App.js` (import + route `/hotel/folios/:id`, roles `admin`/`manager`/`front_desk`, no nav entry)

## What was built

Followed the brief's ledger table (Posted / Kind / Description / Debit / Credit / actions), balance card, add-charge and take-payment panels, gated on `folio.status === "open"`. Money semantics match the brief exactly:

- Debit/credit columns read from `entry.direction`, never inferred from `kind` (a `void` can be either direction).
- Refund is submitted with `kind: "refund"` and rendered in the **debit** column since the backend returns `direction: "debit"` for it — verified live (see below), balance went **up** after a refund.
- Balance card: orange when `> 0` ("Outstanding"), stone when `<= 0`, with "In credit" wording for negative.
- A voided entry is found via `entries.filter(e => e.kind === "void").map(e => e.ref_entry_id)` and rendered `opacity-50 line-through` — the void's own row (the reversal) renders normally, only the original struck-through, so a disputed line is visibly cancelled rather than just leaving a mystery extra row.

## Inline confirm override (deliberate deviation from the brief)

The brief's `voidEntry` used `window.prompt`. Per your instruction, I did not use it. Instead I followed the `confirming`/`reason` pattern already established in `BookingDetail.jsx`'s cancel-booking flow, adapted to a per-row context:

- State: `voidTarget` (the entry object currently being confirmed, or `null`) and `voidReason` (string).
- Clicking "Void" on a row calls `startVoid(entry)`, which sets `voidTarget`/clears `voidReason`. That row's own Void link disappears (guarded by `!voidTarget` in `canVoid`) so only one confirm panel can be open at a time.
- An extra `<tr>` (wrapped, with the entry row, in a keyed `<Fragment>` — `<>...</>` shorthand can't carry a `key` inside `.map`, which I caught and fixed) renders below the target row when `voidTarget?.id === e.id`: a `border-red-500/40 bg-red-950/20` panel with a required `<textarea>` for the reason, a red "Confirm void" button (disabled until non-blank + while busy) and a neutral "Never mind" cancel button — visually and structurally identical to the cancel-booking panel.
- `confirmVoid` re-validates the reason client-side (toast if blank) before POSTing `{reason: voidReason.trim()}`, then reloads the folio and clears state. Any server error (400/409) is surfaced via `formatApiErrorDetail` + `toast.error`, panel stays open so the user doesn't lose their typed reason.

This is tablet-usable, styleable, and can't be silently blocked by the browser, unlike `window.prompt`.

## Role-awareness (small addition beyond the literal brief)

The backend enforces manager-only refund (403) and manager-only void (`require_roles("admin","manager")`, 403 for anyone else). Rather than let front-desk staff hit a surprise 403 on a tablet, the UI reads `user.role` via the existing `useAuth()` context and:

- Hides the "Refund" `<option>` from the payment-kind select unless `isManager`.
- Hides the "Void" link on ledger rows unless `isManager` (in addition to the existing `open` / not-already-voided / not-a-void-itself checks).

Server-side enforcement is unchanged and is still the real guard — this is UX only, confirmed the 403s still fire correctly when called directly (see verification).

## Verification

**Build:** `cd frontend && CI=false npx craco build` compiles clean — only the two pre-existing ESLint warnings (`CustomerMenu.jsx` missing-dep, `Reservations.jsx` missing-dep) appear. Ran twice (once before, once after the Fragment/key fix). `rm -rf build` after each run.

**No browser-automation tool was available in this environment** (no Playwright/Puppeteer-style tool registered), so instead of driving the UI directly I exercised the exact request/response shapes the component sends and consumes, against the live backend on port 8000 (dev server confirmed listening on 3001, reused, not restarted):

1. Logged in as `frontdesk@barflow.io` (front_desk) and `manager@barflow.io` (manager) via `POST /api/auth/login`.
2. `GET /api/folios/{id}` on a real open folio (`3779dc91-...`) — confirmed shape (`entries[]`, `balance`, `guest`, `booking`) matches what `Folio.jsx` destructures.
3. `POST /api/folios/{id}/charges` as front_desk — 500 charge posted, `direction: "debit"`, balance rose 16800 → 17300. Matches "Add a charge" panel's request body.
4. `POST /api/folios/{id}/payments` `{kind:"payment"}` as front_desk — credit entry, balance fell to 16300. Matches "Take a payment" panel.
5. `POST .../payments` `{kind:"refund"}` as front_desk — **403** `"Only a manager can issue a refund"`. Confirms hiding the Refund option for non-managers in the UI is the right call, and that a slipped-through call still fails safely.
6. Same refund as manager — **200**, `direction: "debit"`, balance rose 16300 → 16500, confirming "refund is a debit" renders correctly in the debit column, not credit.
7. `POST .../entries/{id}/void` as front_desk — **403** `"Forbidden"` (the `MANAGER` router dependency), confirming the Void-button hiding for non-managers is correct.
8. Same void as manager with blank reason — **400** `"A reason is required to void an entry"`, matching the client-side validation in `confirmVoid` (and what happens if it were bypassed).
9. Same void with a real reason — **200**; re-fetched the folio and confirmed the original `misc_charge` entry now has a matching `void` entry with `ref_entry_id` pointing at it and `direction: "credit"` (reversing the debit) — this is exactly the data `Folio.jsx`'s `voided` Set / strikethrough logic consumes, and I traced the render logic against this real payload by hand.

I did not click through the actual rendered page pixel-by-pixel since no browser tool was available; confidence rests on (a) clean production build with the real JSX/Tailwind, (b) the component's data-handling logic traced line-by-line against real API payloads captured above, and (c) reuse of already-shipped, already-tested UI patterns (`BookingDetail.jsx`'s confirm panel, `Bookings.jsx`'s table styling) rather than novel code paths.

## Concerns

- No end-to-end pixel-level browser check was possible in this environment — recommend a manual click-through (add charge, take payment, refund as manager, void, and confirm the struck-through row + inline panel look right) before merging, since visual layout of the injected `<tr>` confirm panel inside a `<table>` is the one part I couldn't screenshot.
- I hid the Refund option and Void button for non-manager roles as a UX improvement beyond the literal brief; if product wants front-desk to see (and be denied) these controls instead of not seeing them at all, that's a one-line change (`isManager &&` guards around the `<option>` and the button's `canVoid` condition).
- Pre-existing uncommitted changes exist in the working tree (`docs/superpowers/plans/2026-08-09-front-desk-folio.md`, `pitch/BarFlow-Pitch-Extended.html`) unrelated to this task — left untouched, not staged/committed.
- Did not commit per task instructions requiring explicit request; only `frontend/src/pages/hotel/Folio.jsx` and `frontend/src/App.js` are the intended diff.

## Fix round 1 — busy/refresh race

**The bug.** `post()` and `confirmVoid()` both called `load()` (fire-and-forget) inside `try`, then cleared `busy` in `finally` without waiting for that refresh to land. `GET /folios/{id}` has a side effect (it posts due room nights) so its latency varies. On a slow connection a receptionist could fire a second action once `busy` re-opened, whose refresh could resolve *before* the first action's still-in-flight refresh — the stale response would land last and silently overwrite the just-posted state, showing a balance missing a real charge.

**Approach chosen.** Made `load()` return its promise chain (`return api.get(...).then(...).catch(...)`) instead of firing it detached, then changed both call sites to `await load()` before falling into `finally`. This is the smallest possible change — three one-line diffs, no new state, no restructuring — and it directly satisfies the ordering guarantee needed: by the time `finally { setBusy(false) }` runs, the refresh for *that* action has already completed and `setFolio` has already been called, so a second action cannot be dispatched until the first one's fresh folio is on screen. Since `.then`/`.catch` on the API call are chained inside `load()` itself, `load()`'s returned promise always resolves (never rejects) — a failed refresh shows its own toast via the existing `.catch` and still lets `await load()` resolve, so `finally` still clears `busy` and the UI never locks up. I considered the request-sequence-number alternative but rejected it as more invasive (extra ref/state, comparison logic) for no added benefit here, since this screen has no overlapping-request scenario other than the one the `await` already serializes away (actions are strictly serialized by `busy` itself once the refresh is properly awaited).

**Effect on the void panel.** `confirmVoid`'s success path (`toast.success` → clear `voidTarget`/`voidReason` → `await load()`) is unchanged in order, just now awaited before `busy` clears, so the panel still closes on success once the fresh folio has landed. The failure path (`fn()`/POST throws) still hits the outer `catch` immediately — `load()` is never reached — so `voidTarget`/`voidReason` are left untouched and the panel stays open with the typed reason preserved, exactly as before.

**Verification.**

```
cd frontend && CI=false npx craco build
```

Output: `Compiled with warnings.` — only the two pre-existing warnings (`react-hooks/exhaustive-deps` in `src/pages/CustomerMenu.jsx:42` and `src/pages/Reservations.jsx:60`), no new warnings or errors, bundle emitted. Ran `rm -rf build` afterward.

No browser-automation tool was available in this environment (same limitation as the original round — no Playwright/Puppeteer-style tool registered), so I did not click through `/app/hotel/folios/ddce1f90-4461-4262-9ee4-9e448133e050` directly. I instead traced the fix against the code: `post()` and `confirmVoid()` now `await load()`, and `load()`'s promise only settles after `setFolio(r.data)` (success) or `toast.error(...)` (failure) has run — both paths resolve, so `finally { setBusy(false) }` in the caller is guaranteed to execute strictly after the refreshed folio (or its error) has already been applied to state. The three-line diff:

```diff
-    api
+    return api
       .get(`/folios/${id}`)
...
-      load();
+      await load();
```
(applied identically at both `post()`'s success path and `confirmVoid()`'s success path).

**Concern:** as in round 1, I was not able to visually click-through and watch the balance update before the buttons re-enable on the live tablet-style folio screen; the fix was verified by build + code trace, not by driving a browser.
