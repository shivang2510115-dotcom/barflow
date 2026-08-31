import { useCallback, useEffect, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { X, Printer, Trash2, Percent } from "lucide-react";

/**
 * The bill a guest is shown at checkout, before they pay.
 *
 * Everything they spent — the room nights and every charge sent to it from the
 * restaurant, the bar, the salon — on one page, with the two things a receptionist
 * actually needs before taking money: take a line off, or knock something off the total.
 *
 * **Excluding a line voids it, with a reason.** The charge leaves the bill and the
 * ledger keeps both it and the void, which is how every other correction in this product
 * works. A bill that simply hid a charge would disagree with the folio about what is
 * owed, and that disagreement is the thing the ledger rules exist to prevent.
 *
 * Nothing here is new plumbing: voiding, discounting, reading the folio, checking out and
 * drawing the bill all already existed. This is the screen that puts them in the order a
 * checkout actually happens in.
 */

const KIND_WORDS = {
  room_night: "Room",
  outlet: "Restaurant / bar",
  misc_charge: "Charge",
  payment: "Paid",
  discount: "Discount",
  refund: "Refund",
};

export default function CheckOutPanel({ bookingId, folioId, onClose, onDone }) {
  const [folio, setFolio] = useState(null);
  const [busy, setBusy] = useState(false);
  // Which line is mid-removal, and the reason being typed. Confirmed in the row rather
  // than in a browser dialog, like every other destructive action here.
  const [removing, setRemoving] = useState(null);
  const [reason, setReason] = useState("");
  const [discount, setDiscount] = useState("");

  const load = useCallback(async () => {
    try {
      setFolio((await api.get(`/folios/${folioId}`)).data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not load the bill");
    }
  }, [folioId]);

  useEffect(() => { load(); }, [load]);

  const act = async (fn, done) => {
    setBusy(true);
    try { await fn(); if (done) toast.success(done); await load(); }
    catch (e) { toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "That did not work"); }
    finally { setBusy(false); }
  };

  const exclude = (entry) => act(async () => {
    await api.post(`/folios/${folioId}/entries/${entry.id}/void`,
                   { reason: reason.trim() || "Removed at checkout" });
    setRemoving(null); setReason("");
  }, "Removed from the bill");

  const applyDiscount = () => {
    const amount = Number(discount);
    if (!amount || amount <= 0) return;
    return act(async () => {
      await api.post(`/folios/${folioId}/payments`, {
        amount, kind: "discount", method: "cash", description: "Discount at checkout" });
      setDiscount("");
    }, "Discount applied");
  };

  const settle = () => act(async () => {
    await api.post(`/bookings/${bookingId}/check-out`, {});
    const { data } = await api.post(`/folios/${folioId}/bill`, {});
    onDone?.(data);
  }, "Checked out");

  if (!folio) {
    return (
      <div className="fixed inset-0 z-[80] bg-black/40 flex items-center justify-center">
        <p className="text-sm text-faint">Loading the bill…</p>
      </div>
    );
  }

  // A void removes the entry it points at rather than showing as a line of its own —
  // the guest reads what they owe, not the hotel's correction history.
  const voided = new Set(folio.entries.filter((e) => e.kind === "void")
                                      .map((e) => e.ref_entry_id));
  const lines = folio.entries.filter((e) => e.kind !== "void" && !voided.has(e.id));
  const charges = lines.filter((e) => ["room_night", "outlet", "misc_charge"].includes(e.kind));
  const credits = lines.filter((e) => ["payment", "discount", "refund"].includes(e.kind));

  return (
    <div className="fixed inset-0 z-[80] bg-black/40 flex items-start justify-center
                    overflow-y-auto py-8 px-4 print:static print:bg-transparent print:p-0"
         onClick={onClose} role="presentation">
      <div className="w-full max-w-2xl bg-surface border border-hairline rounded print:border-0"
           onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
           aria-label="Checkout bill">

        <header className="flex items-center gap-3 px-6 py-4 border-b border-hairline print:hidden">
          <div className="flex-1">
            <h2 className="font-display text-lg text-ink">
              {folio.guest?.name || "Guest"}
            </h2>
            <p className="text-[12px] text-faint">
              Room {folio.booking?.assigned_room_id ? "" : ""}
              {folio.booking?.check_in} → {folio.booking?.check_out}
            </p>
          </div>
          <button onClick={() => window.print()} aria-label="Print"
                  className="px-3 rounded border border-hairline text-muted2">
            <Printer className="h-4 w-4" aria-hidden="true" />
          </button>
          <button onClick={onClose} aria-label="Close"
                  className="px-3 rounded border border-hairline text-muted2">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="p-6">
          <table className="w-full text-[14px]">
            <caption className="sr-only">Everything charged to this stay</caption>
            <tbody>
              {charges.map((e) => (
                <tr key={e.id} className="border-b border-hairline align-top">
                  <td className="py-2">
                    <span className="text-ink">{e.description}</span>
                    <span className="block text-[11px] text-faint">
                      {KIND_WORDS[e.kind] || e.kind}
                      {e.charge_date ? ` · ${e.charge_date}` : ""}
                    </span>
                    {removing === e.id && (
                      <div className="mt-2 flex flex-wrap items-center gap-2 print:hidden">
                        <input
                          value={reason}
                          onChange={(ev) => setReason(ev.target.value)}
                          placeholder="Why is this coming off?"
                          aria-label="Reason for removing"
                          className="flex-1 min-w-[12rem] bg-ground border border-hairline rounded px-3 text-[13px] text-ink"
                        />
                        <button onClick={() => exclude(e)} disabled={busy}
                                className="px-3 rounded bg-state-alert/10 border border-state-alert/60
                                           text-state-alert text-[13px]">
                          Remove
                        </button>
                        <button onClick={() => { setRemoving(null); setReason(""); }}
                                className="px-3 rounded border border-hairline text-muted2 text-[13px]">
                          Keep
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="py-2 text-right tabular-nums text-ink whitespace-nowrap">
                    {currency(e.amount)}
                  </td>
                  <td className="py-2 pl-3 print:hidden">
                    {removing !== e.id && (
                      <button onClick={() => setRemoving(e.id)}
                              aria-label={`Remove ${e.description}`}
                              className="text-faint hover:text-state-alert transition-colors">
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {charges.length === 0 && (
                <tr><td colSpan={3} className="py-4 text-faint">Nothing charged to this stay.</td></tr>
              )}
            </tbody>
            <tbody className="border-t-2 border-hairline-strong">
              {credits.map((e) => (
                <tr key={e.id}>
                  <td className="py-1 text-muted2">{KIND_WORDS[e.kind]} · {e.description}</td>
                  <td className="py-1 text-right tabular-nums text-state-free whitespace-nowrap">
                    −{currency(e.amount).replace("−", "")}
                  </td>
                  <td className="print:hidden" />
                </tr>
              ))}
              <tr className="border-t border-hairline">
                <td className="pt-3 font-display text-[16px] text-ink">
                  {folio.balance > 0.005 ? "Balance due" : "Settled"}
                </td>
                <td className="pt-3 text-right font-display text-[19px] tabular-nums text-ink">
                  {currency(folio.balance)}
                </td>
                <td className="print:hidden" />
              </tr>
            </tbody>
          </table>

          <div className="mt-6 flex flex-wrap items-end gap-3 print:hidden">
            <label className="flex-1 min-w-[10rem]">
              <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-1.5">
                Knock something off
              </span>
              <input type="number" min="0" value={discount}
                     onChange={(e) => setDiscount(e.target.value)}
                     placeholder="0"
                     className="w-full bg-ground border border-hairline rounded px-3 text-[15px] text-ink" />
            </label>
            <button onClick={applyDiscount} disabled={busy || !discount}
                    className="inline-flex items-center gap-2 px-4 rounded border border-hairline
                               text-muted2 text-[13px] hover:border-brass hover:text-brass
                               disabled:opacity-40 transition-colors">
              <Percent className="h-4 w-4" aria-hidden="true" /> Apply discount
            </button>
          </div>

          <button onClick={settle} disabled={busy}
                  className="mt-6 w-full rounded bg-brass hover:bg-brass-deep text-on-brass
                             text-[14px] font-medium disabled:opacity-40 transition-colors print:hidden">
            {busy ? "Working…" : folio.balance > 0.005
              ? `Take ${currency(folio.balance)} and check out`
              : "Check out"}
          </button>

          <p className="mt-4 text-[11px] text-faint print:hidden">
            A removed line stays in the folio with the reason it was removed — the bill
            shows what is owed, the ledger explains itself.
          </p>
        </div>
      </div>
    </div>
  );
}
