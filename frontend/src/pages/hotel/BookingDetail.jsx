import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const STATUS_STYLE = {
  confirmed: "text-orange-400 border-orange-500/40",
  tentative: "text-amber-300 border-amber-400/40",
  checked_in: "text-emerald-400 border-emerald-500/40",
  checked_out: "text-stone-400 border-stone-600",
  cancelled: "text-stone-500 border-stone-700 line-through",
  no_show: "text-red-400 border-red-500/40",
};

function isExpiredHold(b) {
  return b.status === "tentative" && b.hold_expires_at && new Date(b.hold_expires_at) < new Date();
}

export default function BookingDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [b, setB] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");

  const load = () =>
    api
      .get(`/bookings/${id}`)
      .then((r) => setB(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const startCancel = () => {
    setReason("");
    setConfirming(true);
  };

  const abortCancel = () => {
    setConfirming(false);
    setReason("");
  };

  const confirmCancel = async () => {
    if (!reason.trim()) {
      toast.error("A reason is required to cancel");
      return;
    }
    setBusy(true);
    try {
      await api.post(`/bookings/${id}/cancel`, { reason: reason.trim() });
      toast.success("Booking cancelled");
      setConfirming(false);
      setReason("");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!b) return <div className="p-6 md:p-10 text-stone-400">Loading booking…</div>;

  const expired = isExpiredHold(b);

  return (
    <div className="p-6 md:p-10">
      <button onClick={() => nav("/app/hotel/bookings")} className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 mb-4">
        ← All bookings
      </button>
      <div className="flex items-center gap-3 mb-3">
        <span className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 ${STATUS_STYLE[b.status] || ""}`}>
          {(b.status || "").replace("_", " ")}
        </span>
        {expired && (
          <span className="text-[10px] tracking-widest uppercase text-red-400 font-semibold">
            hold expired — {b.hold_expires_at.slice(0, 10)}
          </span>
        )}
        {!expired && b.status === "tentative" && b.hold_expires_at && (
          <span className="text-[10px] tracking-widest uppercase text-amber-400">
            hold until {b.hold_expires_at.slice(0, 10)}
          </span>
        )}
      </div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        {b.reference}
      </h1>
      <p className="text-stone-400 mb-8">
        {b.guest?.name} · {b.guest?.phone}
      </p>

      <div className="grid gap-4 md:grid-cols-3 mb-8">
        {[
          ["Room type", b.room_type?.name],
          ["Meal plan", b.meal_plan ? `${b.meal_plan.code} · ${b.meal_plan.name}` : null],
          ["Occupancy", `${b.adults} adult${b.adults === 1 ? "" : "s"}, ${b.children} child${b.children === 1 ? "" : "ren"}`],
          ["Check in", b.check_in],
          ["Check out", b.check_out],
          ["Source", b.source],
        ].map(([label, value]) => (
          <div key={label} className="border border-stone-800 bg-stone-900 rounded p-4">
            <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">{label}</div>
            <div className="mt-1 tabular-nums">{value || "—"}</div>
          </div>
        ))}
      </div>

      <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">Price breakdown</h2>
      <div className="overflow-x-auto mb-8">
        <table className="w-full text-sm border-collapse max-w-2xl">
          <thead>
            <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
              <th className="text-left py-2 px-3 border-b border-stone-800">Night</th>
              <th className="text-right py-2 px-3 border-b border-stone-800">Tariff</th>
              <th className="text-right py-2 px-3 border-b border-stone-800">GST</th>
            </tr>
          </thead>
          <tbody>
            {(b.quote?.nights || []).map((n) => (
              <tr key={n.date}>
                <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs">{n.date}</td>
                <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(n.tariff)}</td>
                <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums text-stone-400">
                  {currency(n.gst_amount)} <span className="text-xs">({n.gst_percent}%)</span>
                </td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td className="py-3 px-3">Total</td>
              <td />
              <td className="py-3 px-3 text-right tabular-nums text-orange-400">
                {currency(b.quote?.total)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {!["cancelled", "checked_out"].includes(b.status) && !confirming && (
        <button
          onClick={startCancel}
          disabled={busy}
          className="border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
        >
          Cancel booking
        </button>
      )}

      {confirming && (
        <div className="border border-red-500/40 bg-red-950/20 rounded p-5 max-w-xl">
          <p className="text-sm text-red-300 mb-3">
            This cancels the booking permanently and cannot be undone. Give a reason to confirm.
          </p>
          <textarea
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason for cancelling"
            rows={3}
            className="w-full bg-stone-950 border border-stone-700 text-stone-100 rounded p-3 text-sm focus:border-red-500 outline-none"
          />
          <div className="flex gap-3 mt-4">
            <button
              onClick={confirmCancel}
              disabled={busy || !reason.trim()}
              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Cancelling…" : "Confirm cancellation"}
            </button>
            <button
              onClick={abortCancel}
              disabled={busy}
              className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Never mind
            </button>
          </div>
        </div>
      )}

      {b.status === "cancelled" && b.cancellation_reason && (
        <p className="text-sm text-stone-500">Cancelled — {b.cancellation_reason}</p>
      )}
    </div>
  );
}
