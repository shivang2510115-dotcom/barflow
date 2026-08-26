/**
 * What a business agreed to pay, said in words — the client's half of
 * `backend/services/subscription.py`.
 *
 * Pure — no React, no axios, no DOM — for the same reason lib/tenancy.js and lib/domains.js
 * are. Everything here turns an API payload into the sentence a person reads, or turns a
 * form into the body the API accepts, and both are things that get quietly wrong in ways
 * only money notices: a business shown as owing ₹0 when nobody has priced it, an overdue
 * flag that reads as "switched off", an amount posted without the period that would tell
 * the server what it bought.
 *
 * Nothing here computes a term or a due date. The server derives `overdue`, `days_overdue`
 * and `paid_until` on every read and hands them over already worked out; recomputing them
 * here would be a second implementation of the arithmetic that could disagree with the
 * first, and the one that disagrees is always the one on screen.
 *
 * Keep in step with backend/services/subscription.py: BILLING_PERIODS, PAYMENT_METHODS and
 * METHOD_LABELS there. The ledger already carries `method_label` from the server, so the
 * labels below are for the *form* — the list of methods to offer — and a row is never
 * relabelled from its key when the server has already said how it is written.
 */

// The three billing periods, in the order they are offered. Mirrors BILLING_PERIODS.
export const MONTHLY = "monthly";
export const QUARTERLY = "quarterly";
export const YEARLY = "yearly";

export const BILLING_PERIODS = [MONTHLY, QUARTERLY, YEARLY];

// Two words for each period, because a price and a schedule are read differently. "₹12,000
// monthly" is what was agreed; "₹12,000 per month" is what it costs. The second is the one
// that goes beside a figure.
export const PERIOD_LABELS = {
  [MONTHLY]: "Monthly",
  [QUARTERLY]: "Quarterly",
  [YEARLY]: "Yearly",
};

export const PERIOD_UNITS = {
  [MONTHLY]: "month",
  [QUARTERLY]: "quarter",
  [YEARLY]: "year",
};

// How the money arrives. There is no card here by design — see the module docstring on the
// server — so this is the list a bank statement is reconciled against.
export const PAYMENT_METHODS = ["bank_transfer", "upi", "cash", "cheque"];

export const METHOD_LABELS = {
  bank_transfer: "Bank transfer",
  upi: "UPI",
  cash: "Cash",
  cheque: "Cheque",
};

/** Today as a plain YYYY-MM-DD, from local calendar parts. */
export function todayISO(now = new Date()) {
  const p = (v) => String(v).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/**
 * A plain YYYY-MM-DD as "21 Aug 2026".
 *
 * The parts are pulled apart and handed to the Date constructor rather than parsed from the
 * string, because `new Date("2026-08-21")` is UTC midnight and renders as the 20th for
 * every user west of Greenwich. Every date in this feature is a calendar day agreed between
 * two people, not an instant, and it must read the same everywhere.
 */
export function formatDay(iso) {
  if (!iso) return "—";
  const [y, m, d] = String(iso).split("-").map(Number);
  if (!y || !m || !d) return "—";
  const date = new Date(y, m - 1, d);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

/** "1 day", "37 days" — the plural said once so no caller writes `${n} day(s)`. */
export function days(n) {
  const count = Math.max(0, Number(n) || 0);
  return `${count} ${count === 1 ? "day" : "days"}`;
}

/**
 * Which of four states a subscription is in — the one derivation every label below reads.
 *
 * * `unpriced` — nobody has agreed a figure. **A normal state**, not a fault: businesses
 *   are approved before they are priced, so every new signup is here, and a console that
 *   painted it red would paint every new signup red.
 * * `unpaid` — priced, and no money has arrived yet. Not overdue: nothing has come due.
 * * `overdue` — an invoice went past due. Still trading; see `OVERDUE_BLURB`.
 * * `current` — paid through a date in the future.
 *
 * Read from the server's own `priced` / `never_paid` / `overdue` flags rather than
 * re-derived from the amount and the dates, so this cannot disagree with the record.
 */
export const UNPRICED = "unpriced";
export const UNPAID = "unpaid";
export const OVERDUE = "overdue";
export const CURRENT = "current";

export function subscriptionState(subscription) {
  const s = subscription || {};
  if (!s.priced) return UNPRICED;
  if (s.overdue) return OVERDUE;
  if (s.never_paid || !s.paid_until) return UNPAID;
  return CURRENT;
}

/**
 * The agreed price as one phrase, or a plain statement that there is not one.
 *
 * `currency` is passed in rather than imported so this module stays free of `@/lib/api`
 * and its axios instance — that is what makes it runnable in a bare script against real
 * API payloads. Callers pass `currency` from lib/api; there is no default, because a
 * fallback that quietly grouped ₹123,456 the American way is exactly the bug the shared
 * helper exists to prevent.
 */
export function priceLine(subscription, currency) {
  const s = subscription || {};
  if (!s.priced) return "Not priced yet";
  return `${currency(s.amount)} / ${PERIOD_UNITS[s.period] || s.period}`;
}

/** What the paid-until date means, in words, including when there is not one. */
export function paidUntilLine(subscription) {
  const s = subscription || {};
  if (!s.priced) return "No price agreed";
  if (!s.paid_until) return "No payment recorded";
  if (s.overdue) return `Due ${formatDay(s.paid_until)}`;
  return `Paid until ${formatDay(s.paid_until)}`;
}

/**
 * The overdue flag's own sentence, or null when there is nothing to flag.
 *
 * Deliberately about the invoice and never about access. An overdue business is still
 * trading — that is the product decision, and it is the server's too: nothing in
 * `routers/platform.py` switches a property off on a date. Copy that said "locked" or
 * "suspended" here would describe a thing the software does not do.
 */
export function overdueLine(subscription) {
  const s = subscription || {};
  if (!s.overdue) return null;
  return `${days(s.days_overdue)} overdue`;
}

/** Said once on the console, under the flag, so nobody reads red as "switched off". */
export const OVERDUE_BLURB =
  "Overdue is an unpaid invoice, not a switched-off business. They are still trading and " +
  "their staff can still log in. Stopping that is Suspend, which is a separate press.";

/** Said once above the ledger, because the first thing tried on a typo is to fix it. */
export const LEDGER_BLURB =
  "This ledger is append-only — there is no edit and no delete, here or in the API. A " +
  "correction is a new entry: record the difference, or record a reversing line and then " +
  "the right one, and put what happened in the reference.";

/**
 * The body for `PUT .../subscription`, or a refusal naming what is missing.
 *
 * Both halves or neither, checked here rather than discovered as a 422: the server answers
 * "An agreed price needs both an amount and a billing period" and the form is perfectly
 * able to know that before it asks. Returns `{ ok: false, error }` or `{ ok: true, body }`
 * — never throws, because the caller is a click handler and a rejected form is an ordinary
 * outcome, not an exception.
 *
 * Clearing both is a real thing to want and is allowed: a business moved to some other
 * arrangement should stop showing a figure nobody honours rather than keep the old one.
 */
export function pricePayload({ amount = "", period = "", note = "" } = {}) {
  const typed = String(amount).trim();
  const chosen = String(period).trim();

  if (!typed && !chosen) {
    // Both blank withdraws the price. The note survives: how they pay is still true of a
    // business whose figure is being renegotiated.
    return { ok: true, body: { amount: null, period: null, note: note.trim() }, withdraws: true };
  }
  if (typed && !chosen) {
    return { ok: false, error: "Pick a billing period — an amount on its own is not a price." };
  }
  if (!typed && chosen) {
    return { ok: false, error: "Enter an amount — a period on its own is not a price." };
  }
  const value = Number(typed);
  if (!Number.isFinite(value)) return { ok: false, error: "The amount has to be a number." };
  if (value < 0) return { ok: false, error: "An agreed price cannot be negative." };
  if (!BILLING_PERIODS.includes(chosen)) {
    return { ok: false, error: `Unknown billing period: ${chosen}` };
  }
  return { ok: true, body: { amount: value, period: chosen, note: note.trim() }, withdraws: false };
}

/**
 * The body for `POST .../payments`, or a refusal.
 *
 * `received_on` is sent as typed and defaults to today at the form, not here: a transfer
 * that lands on Friday is reconciled on Monday, and the operator must be able to say so.
 * It is not the same thing as the term the payment buys — the server works that out from
 * *its* today, which is why the two dates on the receipt below can differ.
 */
export function paymentPayload({ amount = "", method = "", received_on = "", reference = "" } = {}) {
  const typed = String(amount).trim();
  const value = Number(typed);
  if (!typed || !Number.isFinite(value)) return { ok: false, error: "Enter the amount received." };
  if (value <= 0) return { ok: false, error: "A payment has to be for something." };
  if (!PAYMENT_METHODS.includes(method)) return { ok: false, error: "Pick how the money arrived." };
  return {
    ok: true,
    body: {
      amount: value,
      method,
      received_on: String(received_on).trim(),
      reference: String(reference).trim(),
    },
  };
}

/**
 * What a payment just bought, from the `POST .../payments` answer.
 *
 * The operator has typed a figure and pressed a button; the next thing they want to know is
 * the term it covers and the new paid-until — especially when those are not what they
 * expected, which is exactly when the business was overdue and the term starts today
 * rather than at the stale date.
 */
export function paymentReceipt(response, currency) {
  const payment = response?.payment;
  if (!payment) return null;
  const until = response?.subscription?.paid_until;
  return {
    headline: `${currency(payment.amount)} recorded`,
    covers: `Covers ${formatDay(payment.covers_from)} → ${formatDay(payment.covers_to)}`,
    paidUntil: until ? `Paid until ${formatDay(until)}` : null,
    // The two dates the operator is most likely to read as one. Said only when they differ,
    // because saying it every time trains people to skip it.
    lateNote:
      payment.received_on && payment.covers_from && payment.received_on !== payment.covers_from
        ? `Received ${formatDay(payment.received_on)}; the term runs from ${formatDay(payment.covers_from)}.`
        : null,
  };
}

/**
 * What changing a property's type did to its people, from the `POST .../type` answer.
 *
 * Reported rather than left silent: somebody may have just been switched off by this, and
 * the operator who pressed it is the only person in a position to tell that business why.
 *
 * `narrowed` counts records the server rewrote, which on a *widening* includes the admin
 * being re-stamped with the domains it just got back — so the sentence follows
 * `unreachable` for whether anything was actually taken away, not the count.
 */
export function retypeReport(response, labelFor = (d) => d) {
  const staff = response?.staff || {};
  const lost = response?.unreachable || [];
  const narrowed = Number(staff.narrowed) || 0;
  const deactivated = Number(staff.deactivated) || 0;
  const names = lost.map(labelFor);
  return {
    lost: names,
    narrowed,
    deactivated,
    took: names.length
      ? `${names.join(" and ")} is no longer reachable for this business.`
      : "No work area was taken away.",
    people: narrowed
      ? `${narrowed} staff ${narrowed === 1 ? "record" : "records"} rewritten, ` +
        `${deactivated} deactivated.`
      : "No staff record changed.",
    // The one line that needs acting on. Nobody has to be told to look for it.
    stranded: deactivated
      ? `${deactivated} ${deactivated === 1 ? "person" : "people"} worked only in what this ` +
        "business just gave up and can no longer log in. Their own admin can reactivate them " +
        "from the staff screen once they have a job here again."
      : null,
  };
}

/**
 * What the operator is warned about *before* pressing, given the type they picked.
 *
 * Computed from the two types rather than from a response, because the point of a two-step
 * confirm is to say what will happen while it still might not. `domainsFor` is passed in
 * from lib/domains so this module keeps no second copy of the mapping.
 */
export function retypeWarning(from, to, domainsFor, labelFor = (d) => d) {
  const before = domainsFor(from || "both");
  const after = domainsFor(to);
  const lost = before.filter((d) => !after.includes(d));
  if (!lost.length) return null;
  return (
    `${lost.map(labelFor).join(" and ")} goes away. Every staff member is narrowed to what ` +
    "is left, and anyone who worked only there is deactivated. Nothing is deleted — rooms, " +
    "rates, bookings and folios sit untouched, so setting the type back gives them all up again."
  );
}

/** The banner the business itself sees. Null unless there is genuinely an invoice due. */
export function overdueNotice(property, currency) {
  const s = property?.subscription;
  if (!s?.overdue) return null;
  return {
    amount: currency(s.amount),
    // "monthly", not "month" + "ly": three of three happen to suffix cleanly today, and
    // that is the kind of coincidence a fourth period breaks silently.
    schedule: (PERIOD_LABELS[s.period] || s.period || "").toLowerCase(),
    since: formatDay(s.paid_until),
    days: days(s.days_overdue),
  };
}
