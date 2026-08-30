import { useCallback, useEffect, useMemo, useState } from "react";
import { api, currency } from "@/lib/api";
import { toast } from "sonner";
import { Search, Printer, X } from "lucide-react";

/**
 * Every bill this property has issued, and the bill itself behind each row.
 *
 * A bill is a snapshot taken at checkout — see routers/bills.py for why it is not a
 * live view of the folio. So nothing on this screen edits anything: it lists, it
 * opens, it prints. A wrong bill is corrected by issuing another, not by changing one.
 */

function Pill({ paid }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px]
      ${paid ? "bg-state-free/10 text-state-free" : "bg-state-dirty/10 text-state-dirty"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${paid ? "bg-state-free" : "bg-state-dirty"}`} />
      {paid ? "Paid" : "Unpaid"}
    </span>
  );
}

/** The document, as the guest sees it. Printed straight from the browser. */
function BillSheet({ bill, onClose }) {
  return (
    <div className="fixed inset-0 z-[80] bg-black/40 flex items-start justify-center
                    overflow-y-auto py-8 px-4 print:static print:bg-transparent print:p-0"
         onClick={onClose} role="presentation">
      <div className="w-full max-w-2xl bg-surface border border-hairline rounded
                      print:border-0 print:max-w-none"
           onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
           aria-label={`Bill ${bill.number}`}>
        <div className="flex items-center gap-3 px-6 py-4 border-b border-hairline print:hidden">
          <h2 className="font-display text-lg text-ink flex-1">Bill {bill.number}</h2>
          <button onClick={() => window.print()}
                  className="inline-flex items-center gap-2 px-4 rounded border border-hairline
                             text-[13px] text-muted2 hover:border-hairline-strong transition-colors">
            <Printer className="h-4 w-4" aria-hidden="true" /> Print
          </button>
          <button onClick={onClose} aria-label="Close"
                  className="px-3 rounded border border-hairline text-muted2">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="p-6 md:p-8">
          <header className="flex flex-wrap gap-6 justify-between mb-8">
            <div>
              <p className="text-[11px] uppercase tracking-[0.2em] text-faint">Billed to</p>
              <p className="text-[17px] text-ink mt-1">{bill.guest_name}</p>
              {bill.guest_phone && (
                <p className="text-[13px] text-muted2">{bill.guest_phone}</p>
              )}
            </div>
            <dl className="text-[13px] text-muted2 space-y-1 text-right">
              <div><dt className="inline text-faint">Bill </dt>
                   <dd className="inline font-mono text-ink">{bill.number}</dd></div>
              <div><dt className="inline text-faint">Issued </dt>
                   <dd className="inline">{bill.issued_on}</dd></div>
              {bill.room_number && (
                <div><dt className="inline text-faint">Room </dt>
                     <dd className="inline font-mono">{bill.room_number}</dd></div>
              )}
              {bill.nights > 0 && (
                <div><dt className="inline text-faint">Nights </dt>
                     <dd className="inline tabular-nums">{bill.nights}</dd></div>
              )}
            </dl>
          </header>

          <table className="w-full text-[14px]">
            <caption className="sr-only">Charges and payments</caption>
            <tbody>
              {bill.charges.map((l, i) => (
                <tr key={`c${i}`} className="border-b border-hairline">
                  <td className="py-2 text-ink">{l.description}</td>
                  <td className="py-2 text-faint text-[12px] whitespace-nowrap">{l.date}</td>
                  <td className="py-2 text-right tabular-nums text-ink">{currency(l.amount)}</td>
                </tr>
              ))}
              {bill.charges.length === 0 && (
                <tr><td colSpan={3} className="py-4 text-faint">No charges on this bill.</td></tr>
              )}
            </tbody>
            <tbody className="border-t-2 border-hairline-strong">
              <tr>
                <td className="pt-3 text-muted2" colSpan={2}>Total charges</td>
                <td className="pt-3 text-right tabular-nums text-ink">
                  {currency(bill.charges_total)}
                </td>
              </tr>
              {bill.payments.map((l, i) => (
                <tr key={`p${i}`}>
                  <td className="py-1 text-muted2" colSpan={2}>{l.description}</td>
                  <td className="py-1 text-right tabular-nums text-state-free">
                    −{currency(l.amount).replace("−", "")}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-hairline">
                <td className="pt-3 font-display text-[16px] text-ink" colSpan={2}>
                  {bill.balance > 0 ? "Balance due" : "Settled"}
                </td>
                <td className="pt-3 text-right font-display text-[18px] tabular-nums text-ink">
                  {currency(bill.balance)}
                </td>
              </tr>
            </tbody>
          </table>

          <p className="mt-8 text-[11px] text-faint">
            Issued by {bill.issued_by}. A charge added after this bill was drawn appears
            on a later one — this document does not change.
          </p>
        </div>
      </div>
    </div>
  );
}

export default function Bills() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/bills", { params: status ? { status } : {} });
      setRows(data);
    } catch {
      toast.error("Could not load bills");
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  const shown = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((b) =>
      `${b.guest_name} ${b.number} ${b.room_number}`.toLowerCase().includes(term));
  }, [rows, q]);

  return (
    <div className="p-6 md:p-10">
      <header className="mb-6">
        <h1 className="font-display text-2xl text-ink">Bills</h1>
        <p className="text-sm text-muted2 mt-1">
          Every bill issued at checkout. A bill is fixed once drawn — a later charge
          appears on a new one.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div className="flex items-center gap-2 px-3 border border-hairline rounded bg-surface flex-1 min-w-[14rem]">
          <Search className="h-4 w-4 text-faint shrink-0" aria-hidden="true" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Guest, bill number, room"
                 aria-label="Search bills"
                 className="flex-1 bg-transparent text-[14px] text-ink placeholder:text-faint outline-none" />
        </div>
        {[["", "All"], ["unpaid", "Unpaid"], ["paid", "Paid"]].map(([v, label]) => (
          <button key={v} onClick={() => setStatus(v)} aria-pressed={status === v}
                  className={`px-4 rounded border text-[13px] transition-colors
                    ${status === v ? "border-brass bg-brass/10 text-brass"
                                   : "border-hairline text-muted2 hover:border-hairline-strong"}`}>
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-faint">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted2">
          {rows.length === 0
            ? "No bills yet. One is drawn when a guest checks out."
            : `Nothing matches “${q}”.`}
        </p>
      ) : (
        <div className="border border-hairline rounded bg-surface overflow-x-auto">
          <table className="w-full text-[14px] min-w-[42rem]">
            <thead>
              <tr className="text-[11px] uppercase tracking-[0.2em] text-faint">
                <th className="text-left font-normal px-4 py-3">Guest</th>
                <th className="text-left font-normal px-4 py-3">Bill</th>
                <th className="text-left font-normal px-4 py-3">Room</th>
                <th className="text-left font-normal px-4 py-3">Issued</th>
                <th className="text-right font-normal px-4 py-3">Total</th>
                <th className="text-right font-normal px-4 py-3">Balance</th>
                <th className="px-4 py-3"><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {shown.map((b) => (
                <tr key={b.id}>
                  <td className="px-4 py-3 text-ink">{b.guest_name}</td>
                  <td className="px-4 py-3 font-mono text-[13px] text-muted2">{b.number}</td>
                  <td className="px-4 py-3 font-mono text-[13px] text-muted2">{b.room_number || "—"}</td>
                  <td className="px-4 py-3 text-muted2">{b.issued_on}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-ink">
                    {currency(b.charges_total)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Pill paid={Math.abs(b.balance) < 0.005} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => setOpen(b)}
                            className="px-3 rounded border border-hairline text-[13px] text-muted2
                                       hover:border-hairline-strong transition-colors">
                      Open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && <BillSheet bill={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
