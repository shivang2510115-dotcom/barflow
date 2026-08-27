import { useEffect } from "react";
import { createPortal } from "react-dom";
import { Printer, X } from "lucide-react";

import { currency } from "@/lib/api";
import { formatDay } from "@/lib/subscription";

/**
 * One issued document, on screen and on paper.
 *
 * The same markup does both. Tailwind's `print:` variants — already in this project, no
 * new dependency — turn the overlay into the page: the console behind it is hidden, the
 * dark palette becomes black on white because nobody prints a dark background, and the
 * two controls disappear because a printed invoice with a "Print" button on it is
 * somebody's photocopy of a screenshot.
 *
 * Every figure comes from the server and none is recomputed here. That matters more than
 * usual: this is a tax document, it cannot be edited after it is issued, and a client
 * that re-derived the split from the total would eventually print a different invoice
 * from the one that is stored.
 *
 * Rupees go through `currency()`, like everywhere else in this app. There is no symbol
 * written into this file.
 */

const CELL = "py-2 px-3 border-b border-stone-800 print:border-neutral-300";
const HEAD =
  "text-left text-[10px] tracking-[0.2em] uppercase text-stone-500 print:text-neutral-600 " +
  CELL;
const LABEL =
  "text-[10px] tracking-[0.2em] uppercase text-stone-500 print:text-neutral-600";

function Party({ title, party }) {
  return (
    <div>
      <div className={LABEL}>{title}</div>
      <div className="text-sm font-bold mt-2 text-stone-100 print:text-black">
        {party?.legal_name || party?.name || "—"}
      </div>
      {party?.address && (
        <div className="text-xs text-stone-400 print:text-neutral-700 mt-1 max-w-xs">
          {party.address}
        </div>
      )}
      <div className="text-xs font-mono text-stone-400 print:text-neutral-700 mt-2">
        GSTIN {party?.gstin || "—"}
      </div>
      {party?.state && (
        <div className="text-xs text-stone-500 print:text-neutral-600 mt-0.5">
          State {party.state}
        </div>
      )}
    </div>
  );
}

/** One money row. A credit note's figures arrive negative and are printed that way —
    `currency()` puts the sign outside the symbol, which is the point of it. */
function Row({ label, value, bold = false }) {
  return (
    <div
      className={`flex justify-between gap-8 py-1 ${
        bold
          ? "font-bold text-stone-100 print:text-black border-t border-stone-800 print:border-neutral-400 pt-2 mt-1"
          : "text-stone-300 print:text-neutral-800"
      }`}
    >
      <span>{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

export default function InvoiceDocument({ invoice, onClose }) {
  // Stamped on <body> while the document is open. App.css uses it to take the console
  // out of the printed page entirely — `print:hidden` on this overlay's own buttons
  // would still leave the screen behind it paginating.
  useEffect(() => {
    if (!invoice) return undefined;
    document.body.classList.add("printing-document");
    return () => document.body.classList.remove("printing-document");
  }, [invoice]);

  if (!invoice) return null;
  const isNote = invoice.kind === "credit_note";
  const intra = invoice.place_of_supply === "intra";

  // Into a portal on <body>, so the rule above can name it as the one child that
  // survives printing. Rendered in place it would be a descendant of #root, which is
  // exactly what has to disappear.
  return createPortal(
    <div
      data-testid="invoice-document"
      className="print-root fixed inset-0 z-50 overflow-y-auto bg-stone-950/90 print:bg-white print:static print:overflow-visible"
    >
      <div className="min-h-full flex items-start justify-center p-4 md:p-10 print:p-0 print:block">
        <div className="w-full max-w-3xl border border-stone-800 bg-stone-950 p-8 print:border-0 print:bg-white print:text-black print:max-w-none print:p-0">
          {/* Chrome. Gone on paper — see the module note. */}
          <div className="flex justify-between items-center mb-8 print:hidden">
            <button
              data-testid="invoice-print"
              onClick={() => window.print()}
              className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-4 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
            >
              <Printer size={14} /> Print
            </button>
            <button
              data-testid="invoice-close"
              onClick={onClose}
              className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-stone-400 hover:text-stone-200"
            >
              <X size={14} /> Close
            </button>
          </div>

          <div className="flex flex-wrap justify-between items-start gap-6 border-b border-stone-800 print:border-neutral-400 pb-6">
            <div>
              <div className="text-lg font-extrabold uppercase tracking-tight text-stone-100 print:text-black">
                {isNote ? "Credit note" : "Tax invoice"}
              </div>
              <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500 print:text-neutral-600 mt-1">
                {invoice.place_of_supply_label ||
                  (intra ? "Within state — CGST + SGST" : "Inter-state — IGST")}
              </div>
            </div>
            <div className="text-right">
              <div className={LABEL}>Number</div>
              <div
                data-testid="invoice-number"
                className="text-sm font-mono text-stone-100 print:text-black mt-1"
              >
                {invoice.number}
              </div>
              <div className="text-xs text-stone-400 print:text-neutral-700 mt-2 tabular-nums">
                Issued {formatDay(invoice.issued_on)}
              </div>
            </div>
          </div>

          <div className="grid gap-8 sm:grid-cols-2 py-6 border-b border-stone-800 print:border-neutral-300">
            <Party title="From" party={invoice.supplier} />
            <Party title="To" party={invoice.customer} />
          </div>

          {isNote && invoice.corrects && (
            <p
              data-testid="invoice-corrects"
              className="text-sm text-stone-300 print:text-neutral-800 py-4 border-b border-stone-800 print:border-neutral-300"
            >
              This credit note reverses invoice{" "}
              <span className="font-mono">{invoice.corrects}</span>
              {invoice.reason ? ` — ${invoice.reason}` : "."}
            </p>
          )}

          <table className="w-full text-sm border-collapse mt-6">
            <thead>
              <tr>
                <th className={HEAD}>Description</th>
                <th className={`${HEAD} text-right`}>Amount</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={`${CELL} text-stone-200 print:text-black`}>
                  BarFlow subscription
                  {invoice.period_from && invoice.period_to && (
                    <span className="block text-xs text-stone-500 print:text-neutral-600 mt-1 tabular-nums">
                      {formatDay(invoice.period_from)} → {formatDay(invoice.period_to)}
                    </span>
                  )}
                </td>
                <td
                  className={`${CELL} text-right tabular-nums text-stone-100 print:text-black`}
                >
                  {currency(invoice.taxable_value)}
                </td>
              </tr>
            </tbody>
          </table>

          <div className="mt-6 flex justify-end">
            <div className="w-full sm:w-80 text-sm font-mono">
              <Row label="Taxable value" value={currency(invoice.taxable_value)} />
              {/* The split, and only the split that applies. Printing a zeroed IGST line
                  beside a CGST/SGST pair invites somebody to claim against it. */}
              {intra ? (
                <>
                  <Row label="CGST 9%" value={currency(invoice.cgst)} />
                  <Row label="SGST 9%" value={currency(invoice.sgst)} />
                </>
              ) : (
                <Row label="IGST 18%" value={currency(invoice.igst)} />
              )}
              <Row label="Total" value={currency(invoice.total)} bold />
            </div>
          </div>

          <div className="mt-8 pt-6 border-t border-stone-800 print:border-neutral-300">
            <div className={LABEL}>Amount in words</div>
            <div
              data-testid="invoice-words"
              className="text-sm text-stone-200 print:text-black mt-2"
            >
              {invoice.total_in_words}
            </div>
          </div>

          <p className="text-xs text-stone-500 print:text-neutral-600 mt-8">
            {isNote
              ? "A credit note is issued against the invoice it names; both documents stand."
              : "This is a computer-generated invoice and does not require a signature."}
          </p>
        </div>
      </div>
    </div>,
    document.body,
  );
}
