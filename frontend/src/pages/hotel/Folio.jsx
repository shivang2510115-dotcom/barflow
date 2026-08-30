import { Fragment, useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";

const KIND_LABEL = {
  room_night: "Room night",
  outlet: "Bar / restaurant",
  misc_charge: "Charge",
  payment: "Payment",
  refund: "Refund",
  discount: "Discount",
  void: "Void",
};

export default function Folio() {
  const { id } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const isManager = user?.role === "admin" || user?.role === "manager";

  const [folio, setFolio] = useState(null);
  const [charge, setCharge] = useState({ amount: "", description: "" });
  const [payment, setPayment] = useState({ amount: "", method: "cash", kind: "payment" });
  const [busy, setBusy] = useState(false);

  // Inline void confirmation — mirrors the cancel-booking pattern in BookingDetail.jsx.
  // window.prompt is deliberately avoided: it can't be styled, is blocked by some
  // browsers, and is unusable on the tablets this screen actually runs on.
  const [voidTarget, setVoidTarget] = useState(null); // the entry being voided, or null
  const [voidReason, setVoidReason] = useState("");

  const load = useCallback(() => {
    return api
      .get(`/folios/${id}`)
      .then((r) => setFolio(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const post = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const addCharge = () =>
    post(async () => {
      if (!charge.amount || Number(charge.amount) <= 0) {
        toast.error("Enter an amount greater than zero");
        return;
      }
      if (!charge.description.trim()) {
        toast.error("A description is required");
        return;
      }
      await api.post(`/folios/${id}/charges`, {
        amount: Number(charge.amount),
        description: charge.description.trim(),
      });
      setCharge({ amount: "", description: "" });
      toast.success("Charge posted");
    });

  const takePayment = () =>
    post(async () => {
      if (!payment.amount || Number(payment.amount) <= 0) {
        toast.error("Enter an amount greater than zero");
        return;
      }
      await api.post(`/folios/${id}/payments`, {
        amount: Number(payment.amount),
        method: payment.method,
        kind: payment.kind,
      });
      setPayment({ amount: "", method: "cash", kind: "payment" });
      toast.success(payment.kind === "refund" ? "Refund recorded" : "Recorded");
    });

  const startVoid = (entry) => {
    setVoidTarget(entry);
    setVoidReason("");
  };

  const abortVoid = () => {
    setVoidTarget(null);
    setVoidReason("");
  };

  const confirmVoid = async () => {
    if (!voidReason.trim()) {
      toast.error("A reason is required to void this entry");
      return;
    }
    setBusy(true);
    try {
      await api.post(`/folios/${id}/entries/${voidTarget.id}/void`, {
        reason: voidReason.trim(),
      });
      toast.success("Voided");
      setVoidTarget(null);
      setVoidReason("");
      await load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!folio) return <div className="p-6 md:p-10 text-muted2">Loading folio…</div>;

  const voided = new Set(
    folio.entries.filter((e) => e.kind === "void").map((e) => e.ref_entry_id),
  );
  const open = folio.status === "open";

  return (
    <div className="p-6 md:p-10">
      <button
        onClick={() => nav(`/app/hotel/bookings/${folio.booking_id}`)}
        className="text-xs tracking-widest uppercase text-faint hover:text-brass mb-4"
      >
        ← Booking
      </button>
      <div className="text-xs tracking-[0.4em] uppercase text-brass mb-3">
        Folio · {folio.status.replace("_", " ")}
      </div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        {folio.guest?.name}
      </h1>
      <p className="text-muted2 mb-8">
        {folio.booking?.reference} · {folio.booking?.check_in} → {folio.booking?.check_out}
      </p>

      <div className="border border-hairline bg-surface rounded p-5 mb-8 max-w-sm">
        <div className="text-[11px] tracking-[0.2em] uppercase text-faint">Balance</div>
        <div
          className={`text-4xl font-extrabold tabular-nums mt-1 ${
            folio.balance > 0 ? "text-brass" : "text-ink"
          }`}
        >
          {currency(folio.balance)}
        </div>
        <div className="text-xs text-faint mt-1">
          {folio.balance > 0 ? "Outstanding" : folio.balance < 0 ? "In credit" : "Settled"}
        </div>
      </div>

      <h2 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-3">Ledger</h2>
      <div className="overflow-x-auto mb-10">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-faint">
              <th className="text-left py-2 px-3 border-b border-hairline">Posted</th>
              <th className="text-left py-2 px-3 border-b border-hairline">Kind</th>
              <th className="text-left py-2 px-3 border-b border-hairline">Description</th>
              <th className="text-right py-2 px-3 border-b border-hairline">Debit</th>
              <th className="text-right py-2 px-3 border-b border-hairline">Credit</th>
              <th className="border-b border-hairline" />
            </tr>
          </thead>
          <tbody>
            {folio.entries.length === 0 && (
              <tr>
                <td colSpan={6} className="py-4 px-3 text-faint">
                  Nothing posted yet.
                </td>
              </tr>
            )}
            {folio.entries.map((e) => {
              const isVoided = voided.has(e.id);
              const canVoid =
                open && isManager && e.kind !== "void" && !isVoided && !voidTarget;
              return (
                <Fragment key={e.id}>
                  <tr className={isVoided ? "opacity-50 line-through" : ""}>
                    <td className="py-2 px-3 border-b border-hairline font-mono text-xs">
                      {(e.posted_at || "").slice(0, 10)}
                    </td>
                    <td className="py-2 px-3 border-b border-hairline text-muted2">
                      {KIND_LABEL[e.kind] || e.kind}
                    </td>
                    <td className="py-2 px-3 border-b border-hairline">{e.description}</td>
                    <td className="py-2 px-3 border-b border-hairline text-right tabular-nums">
                      {e.direction === "debit" ? currency(e.amount) : ""}
                    </td>
                    <td className="py-2 px-3 border-b border-hairline text-right tabular-nums text-muted2">
                      {e.direction === "credit" ? currency(e.amount) : ""}
                    </td>
                    <td className="py-2 px-3 border-b border-hairline text-right">
                      {canVoid && (
                        <button
                          onClick={() => startVoid(e)}
                          disabled={busy}
                          className="text-[10px] tracking-widest uppercase text-faint hover:text-red-400 disabled:opacity-50"
                        >
                          Void
                        </button>
                      )}
                    </td>
                  </tr>
                  {voidTarget?.id === e.id && (
                    <tr>
                      <td colSpan={6} className="py-0 px-0 border-b border-hairline">
                        <div className="border border-red-500/40 bg-red-950/20 rounded p-4 my-3 mx-3">
                          <p className="text-sm text-red-300 mb-3">
                            This voids “{e.description}” permanently and cannot be undone.
                            Give a reason to confirm.
                          </p>
                          <textarea
                            autoFocus
                            value={voidReason}
                            onChange={(ev) => setVoidReason(ev.target.value)}
                            placeholder="Reason for voiding this entry"
                            rows={2}
                            className="w-full bg-ground border border-hairline-strong text-ink rounded p-3 text-sm focus:border-red-500 outline-none"
                          />
                          <div className="flex gap-3 mt-3">
                            <button
                              onClick={confirmVoid}
                              disabled={busy || !voidReason.trim()}
                              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white rounded-full px-5 py-2 text-xs tracking-widest uppercase"
                            >
                              {busy ? "Voiding…" : "Confirm void"}
                            </button>
                            <button
                              onClick={abortVoid}
                              disabled={busy}
                              className="border border-hairline-strong text-muted2 hover:border-hairline-strong disabled:opacity-50 rounded-full px-5 py-2 text-xs tracking-widest uppercase"
                            >
                              Never mind
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {open && (
        <div className="grid gap-6 md:grid-cols-2 max-w-3xl">
          <div className="border border-hairline bg-surface rounded p-5">
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
              Add a charge
            </h3>
            <div className="flex flex-wrap gap-4 items-end">
              <label className="text-xs tracking-widest uppercase text-faint">
                Amount
                <input
                  type="number"
                  min="0"
                  value={charge.amount}
                  onChange={(e) => setCharge({ ...charge, amount: e.target.value })}
                  className="block mt-2 w-28 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
                />
              </label>
              <label className="text-xs tracking-widest uppercase text-faint flex-1 min-w-[10rem]">
                Description
                <input
                  value={charge.description}
                  onChange={(e) => setCharge({ ...charge, description: e.target.value })}
                  placeholder="Laundry"
                  className="block mt-2 w-full bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
                />
              </label>
              <button
                onClick={addCharge}
                disabled={busy}
                className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-5 py-2 text-xs tracking-widest uppercase"
              >
                Post
              </button>
            </div>
          </div>

          <div className="border border-hairline bg-surface rounded p-5">
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
              Take a payment
            </h3>
            <div className="flex flex-wrap gap-4 items-end">
              <label className="text-xs tracking-widest uppercase text-faint">
                Amount
                <input
                  type="number"
                  min="0"
                  value={payment.amount}
                  onChange={(e) => setPayment({ ...payment, amount: e.target.value })}
                  className="block mt-2 w-28 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
                />
              </label>
              <label className="text-xs tracking-widest uppercase text-faint">
                Method
                <select
                  value={payment.method}
                  onChange={(e) => setPayment({ ...payment, method: e.target.value })}
                  className="block mt-2 bg-ground border border-hairline-strong text-ink py-1 px-2 rounded"
                >
                  {["cash", "card", "online"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs tracking-widest uppercase text-faint">
                Type
                <select
                  value={payment.kind}
                  onChange={(e) => setPayment({ ...payment, kind: e.target.value })}
                  className="block mt-2 bg-ground border border-hairline-strong text-ink py-1 px-2 rounded"
                >
                  <option value="payment">Payment</option>
                  {isManager && <option value="discount">Discount</option>}
                  {isManager && <option value="refund">Refund</option>}
                </select>
              </label>
              <button
                onClick={takePayment}
                disabled={busy}
                className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-5 py-2 text-xs tracking-widest uppercase"
              >
                Record
              </button>
            </div>
            <p className="text-xs text-faint mt-4">
              A refund hands money back, so it increases the balance. A discount forgives
              part of it without cash changing hands.
              {!isManager && " Both are managers only."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
