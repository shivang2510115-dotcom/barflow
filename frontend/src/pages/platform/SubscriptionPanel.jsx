import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { api, currency, formatApiErrorDetail } from "@/lib/api";
import InvoiceDocument from "@/pages/platform/InvoiceDocument";
import { DOMAIN_LABELS, PROPERTY_TYPE_CHOICES, domainsForPropertyType } from "@/lib/domains";
import {
  BILLING_PERIODS,
  LEDGER_BLURB,
  OVERDUE_BLURB,
  PAYMENT_METHODS,
  PERIOD_LABELS,
  METHOD_LABELS,
  formatDay,
  overdueLine,
  paidUntilLine,
  paymentPayload,
  paymentReceipt,
  priceLine,
  pricePayload,
  retypeReport,
  retypeWarning,
  todayISO,
} from "@/lib/subscription";

/**
 * The money half of the operator's detail panel: what this business agreed to pay, what it
 * has paid, and what kind of business it is.
 *
 * A file of its own rather than another four hundred lines inside Platform.jsx, and mounted
 * with `key={detail.id}` by the parent so switching property resets every form here — a
 * half-typed payment carried across to the next business is how ₹12,000 lands on the wrong
 * ledger.
 *
 * It owns its writes and nothing else: the reads all live in the parent, which already
 * caches the detail per property, and every successful write calls `onChanged` so the list
 * row, the detail and the ledger are re-read from the server rather than patched here from
 * a response this panel guessed the shape of.
 *
 * Three things about the surface are decisions rather than layout:
 *
 * * the price form sends both halves or neither, because the API answers 422 for an amount
 *   without a period and the form already knows that (see lib/subscription::pricePayload);
 * * the ledger has no edit and no delete, because the API has neither — a correction is a
 *   new entry, and that is said above the table rather than discovered;
 * * changing the type uses the inline two-step confirm this codebase uses for suspend and
 *   void, never window.confirm, and it reports back how many staff the server narrowed and
 *   deactivated — somebody may have just been switched off by this press.
 */

// Overdue is amber, suspension is red, and they are never the same colour. An overdue
// business is still trading; the copy and the palette both have to say so, because a red
// flag beside a name is read as "switched off" long before the words underneath are.
const OVERDUE_TONE = "text-amber-400 border-amber-500/40 bg-amber-500/5";

const FIELD =
  "block mt-2 w-full bg-transparent border-b border-stone-700 text-stone-100 py-1 " +
  "focus:border-orange-500 outline-none placeholder:text-stone-600";
const SELECT =
  "block mt-2 w-full bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded";
const LABEL = "text-xs tracking-widest uppercase text-stone-500";
const PRIMARY =
  "bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 " +
  "text-sm tracking-widest uppercase";
const GHOST =
  "border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 " +
  "rounded-full px-6 py-2 text-sm tracking-widest uppercase";

/** The subscription, stated: price, paid-until, and the overdue flag when there is one. */
export function SubscriptionCell({ subscription }) {
  const overdue = overdueLine(subscription);
  return (
    <div className="min-w-[11rem]">
      <div
        className={`tabular-nums ${subscription?.priced ? "text-stone-200" : "text-stone-500"}`}
      >
        {priceLine(subscription, currency)}
      </div>
      <div className="text-xs text-stone-500 tabular-nums mt-0.5">
        {paidUntilLine(subscription)}
      </div>
      {overdue && (
        <span
          className={`inline-block mt-1.5 text-[10px] tracking-widest uppercase border rounded-full px-2 py-0.5 whitespace-nowrap tabular-nums ${OVERDUE_TONE}`}
        >
          {overdue}
        </span>
      )}
    </div>
  );
}

function Line({ label, value, tone = "text-stone-100" }) {
  return (
    <div className="border border-stone-800 bg-stone-900 p-4">
      <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500">{label}</div>
      <div className={`text-lg font-bold tabular-nums mt-2 ${tone}`}>{value}</div>
    </div>
  );
}

/**
 * The ledger. Newest first, as the API sorts it — nothing here re-sorts or re-labels.
 *
 * The last column is the tax document for each line. It says "Issue invoice" once and
 * then never again: issuing is idempotent per payment on the server, and once a document
 * exists the button becomes its number, which opens it. A second invoice for money that
 * arrived once is a second tax invoice, and neither of them could be deleted afterwards.
 */
function Ledger({ rows, invoiceFor, onIssue, onOpen, busy }) {
  if (rows === null) return <p className="text-sm text-stone-500 mt-4">Reading the ledger…</p>;
  if (!rows.length) {
    return (
      <p className="text-sm text-stone-400 mt-4" data-testid="platform-ledger-empty">
        No payment recorded yet. The first one goes in above.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto mt-4">
      <table className="w-full text-sm border-collapse" data-testid="platform-ledger">
        <thead>
          <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
            <th className="text-left py-2 px-3 border-b border-stone-800">Received</th>
            <th className="text-right py-2 px-3 border-b border-stone-800">Amount</th>
            <th className="text-left py-2 px-3 border-b border-stone-800">How</th>
            <th className="text-left py-2 px-3 border-b border-stone-800">Covers</th>
            <th className="text-left py-2 px-3 border-b border-stone-800">Reference</th>
            <th className="text-left py-2 px-3 border-b border-stone-800">Invoice</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="py-2 px-3 border-b border-stone-800 text-stone-300 tabular-nums whitespace-nowrap">
                {formatDay(r.received_on)}
              </td>
              <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums text-stone-100">
                {currency(r.amount)}
              </td>
              {/* `method_label` comes back humanised from the server, so it is printed as
                  given rather than looked up again here — two spellings of "Bank transfer"
                  is one more than a bank statement can be reconciled against. */}
              <td className="py-2 px-3 border-b border-stone-800 text-stone-400 whitespace-nowrap">
                {r.method_label || r.method}
              </td>
              <td className="py-2 px-3 border-b border-stone-800 text-stone-400 tabular-nums whitespace-nowrap">
                {formatDay(r.covers_from)} → {formatDay(r.covers_to)}
              </td>
              <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs text-stone-400">
                {r.reference || <span className="text-stone-600">none given</span>}
              </td>
              <td className="py-2 px-3 border-b border-stone-800 whitespace-nowrap">
                {invoiceFor(r.id) ? (
                  <button
                    type="button"
                    data-testid={`platform-invoice-open-${r.id}`}
                    onClick={() => onOpen(invoiceFor(r.id))}
                    className="font-mono text-xs text-orange-400 hover:text-orange-300 underline underline-offset-4"
                  >
                    {invoiceFor(r.id).number}
                  </button>
                ) : (
                  <button
                    type="button"
                    data-testid={`platform-invoice-issue-${r.id}`}
                    disabled={busy}
                    onClick={() => onIssue(r.id)}
                    className="text-[10px] tracking-widest uppercase border border-stone-700 text-stone-400 hover:border-orange-500 hover:text-orange-400 rounded-full px-3 py-1 disabled:opacity-50"
                  >
                    Issue invoice
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SubscriptionPanel({ detail, payments, onChanged }) {
  const sub = detail.subscription || {};
  const id = detail.id;

  const [price, setPrice] = useState({
    amount: sub.amount == null ? "" : String(sub.amount),
    period: sub.period || "",
    note: detail.payment_note || "",
  });
  const [payment, setPayment] = useState({
    amount: sub.amount == null ? "" : String(sub.amount),
    method: "bank_transfer",
    received_on: todayISO(),
    reference: "",
  });
  // What the last payment bought. Kept on screen until the next action rather than shown as
  // a toast that is gone before the dates in it have been read.
  const [receipt, setReceipt] = useState(null);
  const [retyping, setRetyping] = useState(null); // the type picked, awaiting confirmation
  const [retyped, setRetyped] = useState(null); // what the server reported it did
  const [busy, setBusy] = useState(false);
  // Every document issued to this business, and the one being looked at. Not cached
  // across properties: an invoice issued from another window must not be missing from a
  // list the operator is about to reconcile against, which is the same reasoning the
  // parent applies to the ledger itself.
  const [invoices, setInvoices] = useState(null);
  const [showing, setShowing] = useState(null);
  const [crediting, setCrediting] = useState(null); // the invoice awaiting confirmation
  const [creditReason, setCreditReason] = useState("");

  const loadInvoices = useCallback(
    () =>
      api
        .get(`/platform/properties/${id}/invoices`)
        .then((r) => setInvoices(r.data || []))
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [id],
  );

  useEffect(() => {
    loadInvoices();
  }, [loadInvoices]);

  // The invoice for one payment, if it has one. Credit notes carry no payment id — they
  // reverse a document, not a transfer — so they never answer this and never turn a
  // ledger row back into "already invoiced".
  const invoiceFor = (paymentId) =>
    (invoices || []).find((i) => i.kind === "invoice" && i.payment_id === paymentId) || null;

  const issueInvoice = (paymentId) =>
    run(async () => {
      const { data } = await api.post(
        `/platform/properties/${id}/payments/${paymentId}/invoice`, {});
      await loadInvoices();
      setShowing(data);
      toast.success(`Invoice ${data.number} issued`);
    });

  const confirmCredit = () =>
    run(async () => {
      const { data } = await api.post(
        `/platform/invoices/${crediting.id}/credit-note`, { reason: creditReason.trim() });
      setCrediting(null);
      setCreditReason("");
      await loadInvoices();
      setShowing(data);
      toast.success(`Credit note ${data.number} issued`);
    });

  const creditNoteFor = (number) =>
    (invoices || []).find((i) => i.corrects === number) || null;

  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const savePrice = () => {
    const parsed = pricePayload(price);
    // Refused here, so the 422 the server would answer never happens and the operator is
    // told which half is missing rather than that both are.
    if (!parsed.ok) {
      toast.error(parsed.error);
      return;
    }
    run(async () => {
      await api.put(`/platform/properties/${id}/subscription`, parsed.body);
      setReceipt(null);
      toast.success(parsed.withdraws ? "Price withdrawn" : "Price agreed");
      await onChanged();
    });
  };

  const recordPayment = () => {
    const parsed = paymentPayload(payment);
    if (!parsed.ok) {
      toast.error(parsed.error);
      return;
    }
    run(async () => {
      const { data } = await api.post(`/platform/properties/${id}/payments`, parsed.body);
      setReceipt(paymentReceipt(data, currency));
      setPayment((p) => ({ ...p, reference: "", received_on: todayISO() }));
      await onChanged();
    });
  };

  const confirmRetype = () =>
    run(async () => {
      const { data } = await api.post(`/platform/properties/${id}/type`, {
        property_type: retyping,
      });
      setRetyped(retypeReport(data, (d) => DOMAIN_LABELS[d] || d));
      setRetyping(null);
      toast.success("Property type changed");
      await onChanged();
    });

  const warning = retyping
    ? retypeWarning(detail.property_type, retyping, domainsForPropertyType, (d) => DOMAIN_LABELS[d] || d)
    : null;

  return (
    <div className="mt-8" data-testid="platform-subscription">
      <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
        Subscription
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Line
          label="Agreed price"
          value={priceLine(sub, currency)}
          tone={sub.priced ? "text-stone-100" : "text-stone-500"}
        />
        <Line label="Billing" value={sub.period ? PERIOD_LABELS[sub.period] : "—"} />
        <Line
          label={sub.overdue ? "Overdue" : "Paid until"}
          value={sub.overdue ? overdueLine(sub) : paidUntilLine(sub)}
          tone={sub.overdue ? "text-amber-400" : "text-stone-100"}
        />
      </div>

      {/* Said under the flag, once. Without it the first reading of a red-ish badge beside a
          business's name is that somebody already switched it off. */}
      {sub.overdue && (
        <p className="text-sm text-amber-300/90 mt-4 max-w-2xl" data-testid="platform-overdue-blurb">
          {OVERDUE_BLURB}
        </p>
      )}

      {detail.payment_note && (
        <p className="text-xs text-stone-400 mt-4 max-w-2xl">
          <span className="tracking-widest uppercase text-stone-500">How they pay</span>{" "}
          {detail.payment_note}
        </p>
      )}

      {/* ------------------------------------------------------------------ the price */}
      <div className="mt-6 border border-stone-800 bg-stone-900 p-5 max-w-3xl">
        <h4 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
          Agree a price
        </h4>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className={LABEL}>
            Amount
            <input
              inputMode="decimal"
              data-testid="platform-price-amount"
              value={price.amount}
              onChange={(e) => setPrice({ ...price, amount: e.target.value })}
              placeholder="12000"
              className={`${FIELD} tabular-nums`}
            />
          </label>
          <label className={LABEL}>
            Billing period
            <select
              data-testid="platform-price-period"
              value={price.period}
              onChange={(e) => setPrice({ ...price, period: e.target.value })}
              className={SELECT}
            >
              {/* The blank is the only way to say "no agreement here", and it is offered
                  because withdrawing a price is a real thing to want. Choosing it with an
                  amount still typed is refused before the request, not by the server. */}
              <option value="">— none —</option>
              {BILLING_PERIODS.map((p) => (
                <option key={p} value={p}>
                  {PERIOD_LABELS[p]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className={`${LABEL} block mt-4`}>
          How they pay
          <input
            data-testid="platform-price-note"
            value={price.note}
            onChange={(e) => setPrice({ ...price, note: e.target.value })}
            placeholder="NEFT to HDFC 5021 by the 5th; ring Ravi on 98200 11111"
            className={FIELD}
          />
        </label>
        <div className="flex gap-3 mt-5">
          <button
            data-testid="platform-price-save"
            onClick={savePrice}
            disabled={busy}
            className={PRIMARY}
          >
            {busy ? "Saving…" : "Save price"}
          </button>
        </div>
        <p className="text-xs text-stone-500 mt-4 max-w-2xl">
          An amount and a period together, or neither. Clearing both withdraws the price and
          keeps the note — useful when a business moves to a different arrangement and the old
          figure should stop being shown. Agreeing a price does not move the paid-until date;
          only a payment does.
        </p>
      </div>

      {/* ---------------------------------------------------------------- the payment */}
      <div className="mt-6 border border-stone-800 bg-stone-900 p-5 max-w-3xl">
        <h4 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
          Record a payment
        </h4>
        {!sub.priced && (
          <p className="text-sm text-orange-300 mb-4" data-testid="platform-payment-unpriced">
            Agree a price first. A payment moves the paid-until date by the agreed period, and
            there is no period here yet to move it by — the API refuses this for the same
            reason.
          </p>
        )}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className={LABEL}>
            Amount
            <input
              inputMode="decimal"
              data-testid="platform-payment-amount"
              value={payment.amount}
              onChange={(e) => setPayment({ ...payment, amount: e.target.value })}
              placeholder="12000"
              className={`${FIELD} tabular-nums`}
            />
          </label>
          <label className={LABEL}>
            How it arrived
            <select
              data-testid="platform-payment-method"
              value={payment.method}
              onChange={(e) => setPayment({ ...payment, method: e.target.value })}
              className={SELECT}
            >
              {PAYMENT_METHODS.map((m) => (
                <option key={m} value={m}>
                  {METHOD_LABELS[m]}
                </option>
              ))}
            </select>
          </label>
          <label className={LABEL}>
            Received on
            {/* Defaulted to today and editable, because a transfer lands on Friday and gets
                reconciled on Monday. This is the day the money arrived; the term it buys is
                worked out by the server from *its* today, and the two are shown separately
                on the receipt below when they differ. */}
            <input
              type="date"
              data-testid="platform-payment-date"
              value={payment.received_on}
              onChange={(e) => setPayment({ ...payment, received_on: e.target.value })}
              className={SELECT}
            />
          </label>
          <label className={LABEL}>
            Reference
            <input
              data-testid="platform-payment-reference"
              value={payment.reference}
              onChange={(e) => setPayment({ ...payment, reference: e.target.value })}
              placeholder="NEFT/HDFC/0921331"
              className={`${FIELD} font-mono`}
            />
          </label>
        </div>
        <div className="flex gap-3 mt-5">
          <button
            data-testid="platform-payment-save"
            onClick={recordPayment}
            disabled={busy || !sub.priced}
            className={PRIMARY}
          >
            {busy ? "Recording…" : "Record payment"}
          </button>
        </div>

        {receipt && (
          <div
            data-testid="platform-receipt"
            className="mt-5 border border-emerald-500/40 bg-emerald-500/5 p-4"
          >
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-emerald-400">
              {receipt.headline}
            </div>
            <p className="text-sm text-stone-200 mt-2 tabular-nums">{receipt.covers}</p>
            {receipt.paidUntil && (
              <p className="text-sm text-stone-300 mt-1 tabular-nums">{receipt.paidUntil}</p>
            )}
            {receipt.lateNote && (
              <p className="text-xs text-stone-400 mt-2 tabular-nums">{receipt.lateNote}</p>
            )}
          </div>
        )}
      </div>

      {/* ----------------------------------------------------------------- the ledger */}
      <div className="mt-8">
        <h4 className="text-[11px] tracking-[0.2em] uppercase text-stone-500">Payments</h4>
        {/* There is no Edit and no Delete anywhere below, because there is no route for
            either. Said in words as well as by omission: the first thing an operator tries
            on a mistyped reference is to fix it in place, and finding no button is only an
            answer if you know it is deliberate. */}
        <p className="text-xs text-stone-500 mt-2 max-w-3xl" data-testid="platform-ledger-blurb">
          {LEDGER_BLURB}
        </p>
        <Ledger
          rows={payments}
          invoiceFor={invoiceFor}
          onIssue={issueInvoice}
          onOpen={setShowing}
          busy={busy}
        />
      </div>

      {/* ---------------------------------------------------------------- invoices */}
      <div className="mt-8">
        <h4 className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
          Tax documents
        </h4>
        {/* Said in words as well as by omission, exactly as the ledger above is: there is
            no Edit and no Delete here because the API has neither, and the first thing
            anybody tries on a mistyped invoice is to fix it in place. */}
        <p className="text-xs text-stone-500 mt-2 max-w-3xl" data-testid="platform-invoice-blurb">
          An issued invoice cannot be edited or deleted — it is a tax document, and its
          number is part of a series an auditor reads for gaps. A correction is a credit
          note that reverses it and names it; both documents stand.
        </p>

        {invoices === null ? (
          <p className="text-sm text-stone-500 mt-4">Reading the invoices…</p>
        ) : !invoices.length ? (
          <p className="text-sm text-stone-400 mt-4" data-testid="platform-invoices-empty">
            Nothing issued yet. Each recorded payment gets one from the ledger above.
          </p>
        ) : (
          <div className="overflow-x-auto mt-4">
            <table className="w-full text-sm border-collapse" data-testid="platform-invoices">
              <thead>
                <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                  <th className="text-left py-2 px-3 border-b border-stone-800">Number</th>
                  <th className="text-left py-2 px-3 border-b border-stone-800">Issued</th>
                  <th className="text-left py-2 px-3 border-b border-stone-800">Supply</th>
                  <th className="text-right py-2 px-3 border-b border-stone-800">Total</th>
                  <th className="text-left py-2 px-3 border-b border-stone-800"></th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id}>
                    <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => setShowing(inv)}
                        className="text-orange-400 hover:text-orange-300 underline underline-offset-4"
                      >
                        {inv.number}
                      </button>
                      {inv.corrects && (
                        <span className="block text-stone-500 mt-1">
                          reverses {inv.corrects}
                        </span>
                      )}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-stone-300 tabular-nums whitespace-nowrap">
                      {formatDay(inv.issued_on)}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-stone-400 whitespace-nowrap">
                      {inv.place_of_supply_label}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums text-stone-100">
                      {currency(inv.total)}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 whitespace-nowrap">
                      {inv.kind === "invoice" && !creditNoteFor(inv.number) && (
                        <button
                          type="button"
                          data-testid={`platform-credit-${inv.id}`}
                          disabled={busy}
                          onClick={() => {
                            setCreditReason("");
                            setCrediting(inv);
                          }}
                          className="text-[10px] tracking-widest uppercase border border-stone-700 text-stone-500 hover:border-red-500/60 hover:text-red-300 rounded-full px-3 py-1 disabled:opacity-50"
                        >
                          Credit note
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* The same inline two-step confirm this codebase uses for suspend, void and
            retype — never window.confirm. Red, because this one cannot be taken back. */}
        {crediting && (
          <div
            data-testid="platform-credit-confirm"
            className="mt-5 p-5 border border-red-500/40 bg-red-950/20 max-w-3xl"
          >
            <h5 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
              Credit {crediting.number}?
            </h5>
            <p className="text-sm text-red-300 mb-4">
              This issues a new document reversing {currency(crediting.total)}. The invoice
              itself is not changed and not removed — it cannot be — and an invoice can be
              credited once.
            </p>
            <label className={LABEL}>
              Why
              <input
                data-testid="platform-credit-reason"
                value={creditReason}
                onChange={(e) => setCreditReason(e.target.value)}
                placeholder="billed the wrong term"
                className={FIELD}
              />
            </label>
            <div className="flex gap-3 mt-5">
              <button
                data-testid="platform-credit-apply"
                onClick={confirmCredit}
                disabled={busy}
                className="bg-red-600 hover:bg-red-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase disabled:opacity-50"
              >
                {busy ? "Working…" : "Issue credit note"}
              </button>
              <button onClick={() => setCrediting(null)} disabled={busy} className={GHOST}>
                Never mind
              </button>
            </div>
          </div>
        )}
      </div>

      <InvoiceDocument invoice={showing} onClose={() => setShowing(null)} />

      {/* ------------------------------------------------------------------- the type */}
      <div className="mt-8 border-t border-stone-800 pt-6 max-w-3xl">
        <h4 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
          What this business is
        </h4>
        <p className="text-xs text-stone-500 mb-4">
          Picked at signup and not changeable from inside the app, so correcting a wrong
          choice is the operator's job. Widening gives a work area back; narrowing takes one
          away and takes its staff with it.
        </p>
        <div className="flex flex-wrap gap-2">
          {PROPERTY_TYPE_CHOICES.map((c) => {
            const current = c.key === detail.property_type;
            return (
              <button
                key={c.key}
                type="button"
                data-testid={`platform-type-${c.key}`}
                title={c.blurb}
                disabled={busy || current}
                onClick={() => {
                  setRetyped(null);
                  setRetyping(c.key);
                }}
                className={`text-[10px] tracking-widest uppercase border rounded-full px-4 py-1.5 transition-colors disabled:opacity-100 ${
                  current
                    ? "border-orange-500 text-orange-400 bg-orange-500/10"
                    : "border-stone-700 text-stone-500 hover:border-stone-500 hover:text-stone-300"
                }`}
              >
                {c.label}
                {current && <span className="ml-2 text-stone-500">now</span>}
              </button>
            );
          })}
        </div>

        {/* The same inline two-step panel as suspend and void — never window.confirm. Red
            only when the change actually takes something away; widening is not destructive
            and a red panel for it would spend the warning nobody then reads. */}
        {retyping && (
          <div
            data-testid="platform-retype-confirm"
            className={`mt-5 p-5 border ${
              warning ? "border-red-500/40 bg-red-950/20" : "border-stone-800 bg-stone-900"
            }`}
          >
            <h5 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
              Change {detail.name} to {PROPERTY_TYPE_CHOICES.find((c) => c.key === retyping)?.label}?
            </h5>
            <p className={`text-sm mb-4 ${warning ? "text-red-300" : "text-stone-400"}`}>
              {warning ||
                "Nothing is taken away — this business gets a work area back, and its staff are re-stamped with it. Nobody is deactivated by a widening."}
            </p>
            <div className="flex gap-3">
              <button
                data-testid="platform-retype-apply"
                onClick={confirmRetype}
                disabled={busy}
                className={
                  warning
                    ? "bg-red-600 hover:bg-red-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase disabled:opacity-50"
                    : PRIMARY
                }
              >
                {busy ? "Working…" : "Confirm change"}
              </button>
              <button onClick={() => setRetyping(null)} disabled={busy} className={GHOST}>
                Never mind
              </button>
            </div>
          </div>
        )}

        {/* What the server reported it did, not what this screen assumed it would. */}
        {retyped && (
          <div
            data-testid="platform-retype-report"
            className="mt-5 border border-stone-800 bg-stone-950 p-4"
          >
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-orange-500">
              Type changed
            </div>
            <p className="text-sm text-stone-300 mt-2">{retyped.took}</p>
            <p className="text-sm text-stone-300 mt-1 tabular-nums">{retyped.people}</p>
            {retyped.stranded && (
              <p className="text-sm text-amber-300 mt-2">{retyped.stranded}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
