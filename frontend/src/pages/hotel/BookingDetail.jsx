import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
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

// A booking that is cancelled, departed or a no-show cannot be extended — the server
// refuses all three — so the control is not offered rather than shown and left to fail.
const EXTENDABLE = ["tentative", "confirmed", "checked_in"];

// `YYYY-MM-DD` plus n days, done in UTC on purpose. A check-out is a calendar date, not
// an instant: building it from a local `new Date(iso)` and reading it back would shift
// the day for anyone west of UTC, and this arithmetic decides what the guest is charged.
const addDays = (iso, n) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
};

export default function BookingDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const isManager = user?.role === "admin" || user?.role === "manager";
  const [b, setB] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");

  // Check-out: same inline confirm pattern as cancel above and the void panel in
  // Folio.jsx. window.prompt is deliberately avoided — unstyleable, blocked by
  // some browsers, and unusable on the tablet this runs on.
  const [folioId, setFolioId] = useState(null);
  const [checkingOut, setCheckingOut] = useState(false);
  const [forcing, setForcing] = useState(false);
  const [forceReason, setForceReason] = useState("");

  // Which physical room this booking holds. Recorded here rather than only at the desk
  // because hotels pre-assign routinely — the returning guest who asks for 204, the
  // family who need adjacent doors — and the alternative is a note on paper.
  const [rooms, setRooms] = useState([]);
  const [picked, setPicked] = useState("");
  const [assigning, setAssigning] = useState(false); // confirming an assignment
  const [clearing, setClearing] = useState(false); // confirming a clear
  const [savingRoom, setSavingRoom] = useState(false);

  // "Can I stay two more nights?" — check-out only, and priced for the added nights
  // alone. There is deliberately no check-in field here: for a guest already in the room
  // the arrival cannot move, and for a future booking moving it is an ordinary edit.
  const [extending, setExtending] = useState(false);
  const [newCheckOut, setNewCheckOut] = useState("");
  const [savingExtend, setSavingExtend] = useState(false);

  const load = () =>
    api
      .get(`/bookings/${id}`)
      .then((r) => setB(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    api
      .get("/rooms")
      .then((r) => setRooms(r.data))
      .catch(() => setRooms([]));
  }, []);

  useEffect(() => {
    if (b?.status !== "checked_in" && b?.status !== "checked_out") return;
    api
      .get("/folios")
      .then((r) => {
        const f = r.data.find((x) => x.booking_id === b.id);
        setFolioId(f ? f.id : null);
      })
      .catch(() => setFolioId(null));
  }, [b?.status, b?.id]);

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

  const checkOut = async () => {
    setCheckingOut(true);
    try {
      await api.post(`/bookings/${id}/check-out`, {});
      toast.success("Checked out");
      load();
    } catch (e) {
      const detail = e.response?.data?.detail;
      // 409 carries the outstanding balance — surface it and point at the force
      // path rather than making the desk guess why it refused.
      if (e.response?.status === 409 && detail?.balance !== undefined) {
        toast.error(`Outstanding balance ${currency(detail.balance)} — use Force check-out`);
      } else {
        toast.error(formatApiErrorDetail(detail));
      }
    } finally {
      setCheckingOut(false);
    }
  };

  const startForceCheckOut = () => {
    setForceReason("");
    setForcing(true);
  };

  const abortForceCheckOut = () => {
    setForcing(false);
    setForceReason("");
  };

  const confirmForceCheckOut = async () => {
    if (!forceReason.trim()) {
      toast.error("A reason is required to force check-out");
      return;
    }
    setCheckingOut(true);
    try {
      await api.post(`/bookings/${id}/check-out`, { force: true, reason: forceReason.trim() });
      toast.success("Checked out");
      setForcing(false);
      setForceReason("");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setCheckingOut(false);
    }
  };

  const startExtend = () => {
    // One more night, pre-filled — the commonest ask, and the only date that is always
    // valid. The desk types over it for a longer stay.
    setNewCheckOut(addDays(b.check_out, 1));
    setExtending(true);
  };

  const abortExtend = () => {
    setExtending(false);
    setNewCheckOut("");
  };

  const confirmExtend = async () => {
    setSavingExtend(true);
    try {
      const { data } = await api.post(`/bookings/${id}/extend`, {
        check_out: newCheckOut,
      });
      const added = data.added?.nights?.length ?? 0;
      toast.success(
        `Extended to ${data.check_out} — ${added} more night${added === 1 ? "" : "s"}, ` +
          `${currency(data.added?.total)}`,
      );
      setExtending(false);
      setNewCheckOut("");
      load();
    } catch (e) {
      // 409 names the booking or the out-of-order block holding the room over the extra
      // nights, 422 the nights no rate covers. Both arrive as { message, … } and
      // formatApiErrorDetail surfaces the message verbatim, so the desk is told what to
      // go and move rather than only that it cannot be done.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSavingExtend(false);
    }
  };

  const startAssign = () => {
    setPicked(b.assigned_room_id || "");
    setAssigning(true);
  };

  const setRoom = async (roomId) => {
    setSavingRoom(true);
    try {
      const { data } = await api.put(`/bookings/${id}/room`, { room_id: roomId });
      toast.success(roomId ? `Room ${data.room.number} assigned` : "Room cleared");
      setAssigning(false);
      setClearing(false);
      setPicked("");
      load();
    } catch (e) {
      // The 409 names the booking already holding the room, so the receptionist can go
      // and move that one — formatApiErrorDetail surfaces detail.message verbatim.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSavingRoom(false);
    }
  };

  if (!b) return <div className="p-6 md:p-10 text-stone-400">Loading booking…</div>;

  const expired = isExpiredHold(b);
  // A cancelled, departed or no-show booking holds no room, and the server refuses to
  // give one — so the panel is not offered rather than shown and left to fail.
  const roomEditable = ["tentative", "confirmed", "checked_in"].includes(b.status);
  const matchingRooms = rooms.filter(
    (r) => r.room_type_id === b.room_type_id && r.active !== false,
  );
  const busyAnywhere = busy || checkingOut || savingRoom || savingExtend;
  const extendable = EXTENDABLE.includes(b.status);
  // Every inline confirm panel on this screen is exclusive: opening one hides the other
  // controls, so the desk is never looking at two half-finished decisions at once.
  const panelOpen = confirming || forcing || assigning || clearing || extending;

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
          ["Room", b.room ? b.room.number : null],
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

      {roomEditable && !confirming && !forcing && !extending && (
        <div className="border border-stone-800 bg-stone-900 rounded p-5 max-w-xl mb-8">
          <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">Room</h2>
          <p className="text-sm mb-4">
            {b.room ? (
              <>
                Holding room <span className="text-orange-400 font-semibold">{b.room.number}</span>
                {b.room.floor ? <span className="text-stone-500"> · floor {b.room.floor}</span> : null}
                {b.status === "checked_in" && <span className="text-stone-500"> · guest in house</span>}
              </>
            ) : (
              <span className="text-stone-400">
                No room yet — this booking holds a {b.room_type?.name || "room type"}, not a door.
              </span>
            )}
          </p>

          {!assigning && !clearing && (
            <div className="flex gap-3 flex-wrap">
              <button
                onClick={startAssign}
                disabled={busyAnywhere}
                className="border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
              >
                {b.room ? "Change room" : "Assign room"}
              </button>
              {/* The server refuses to leave an in-house guest with no room — moving
                  them is a room change, not a clear — so the control is hidden rather
                  than shown and left to 409. */}
              {b.room && b.status !== "checked_in" && (
                <button
                  onClick={() => setClearing(true)}
                  disabled={busyAnywhere}
                  className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
                >
                  Clear room
                </button>
              )}
            </div>
          )}

          {assigning && (
            <div className="border border-orange-500/40 bg-orange-950/10 rounded p-4">
              <p className="text-sm text-stone-300 mb-3">
                A room is held for the whole stay, {b.check_in} → {b.check_out}. If another
                booking already has it, this is refused and says which one.
              </p>
              <select
                autoFocus
                value={picked}
                onChange={(e) => setPicked(e.target.value)}
                className="block bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                <option value="">Choose a room…</option>
                {matchingRooms.length === 0 ? (
                  <option value="" disabled>
                    No active rooms of this type
                  </option>
                ) : (
                  matchingRooms.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.number}
                      {r.floor ? ` · floor ${r.floor}` : ""}
                    </option>
                  ))
                )}
              </select>
              <div className="flex gap-3 mt-4">
                <button
                  onClick={() => setRoom(picked)}
                  disabled={savingRoom || !picked}
                  className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
                >
                  {savingRoom ? "Assigning…" : "Confirm room"}
                </button>
                <button
                  onClick={() => {
                    setAssigning(false);
                    setPicked("");
                  }}
                  disabled={savingRoom}
                  className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
                >
                  Never mind
                </button>
              </div>
            </div>
          )}

          {clearing && (
            <div className="border border-stone-700 bg-stone-950 rounded p-4">
              <p className="text-sm text-stone-300 mb-3">
                This releases room {b.room?.number} for these dates — anyone else may take
                it, and this booking goes back to holding only a room type.
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setRoom(null)}
                  disabled={savingRoom}
                  className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
                >
                  {savingRoom ? "Clearing…" : "Confirm clear"}
                </button>
                <button
                  onClick={() => setClearing(false)}
                  disabled={savingRoom}
                  className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
                >
                  Never mind
                </button>
              </div>
            </div>
          )}
        </div>
      )}

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

      {!panelOpen && (
        <div className="flex gap-3 flex-wrap mb-4">
          {extendable && (
            <button
              onClick={startExtend}
              disabled={busyAnywhere}
              data-testid="extend-stay"
              className="border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Extend stay
            </button>
          )}
          {!["cancelled", "checked_out"].includes(b.status) && (
            <button
              onClick={startCancel}
              disabled={busyAnywhere}
              className="border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel booking
            </button>
          )}
        </div>
      )}

      {extending && (
        <div className="border border-orange-500/40 bg-stone-900 rounded p-5 max-w-xl mb-4">
          <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">
            Extend stay
          </h2>
          <p className="text-sm text-stone-400 mb-4">
            Check-in stays at <span className="font-mono">{b.check_in}</span>. Only the
            added nights are priced — the nights already quoted keep the price the guest
            was given.
          </p>
          <label className="text-xs tracking-widest uppercase text-stone-500">
            New check out
            <input
              type="date"
              autoFocus
              value={newCheckOut}
              min={addDays(b.check_out, 1)}
              onChange={(e) => setNewCheckOut(e.target.value)}
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
          <p className="text-xs text-stone-500 mt-3">
            Currently leaving <span className="font-mono">{b.check_out}</span>
            {newCheckOut > b.check_out && (
              <>
                {" · "}
                {Math.round(
                  (Date.parse(`${newCheckOut}T00:00:00Z`) -
                    Date.parse(`${b.check_out}T00:00:00Z`)) /
                    86400000,
                )}{" "}
                more night(s)
              </>
            )}
          </p>
          <div className="flex gap-3 mt-5">
            <button
              onClick={confirmExtend}
              disabled={savingExtend || !newCheckOut || newCheckOut <= b.check_out}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {savingExtend ? "Extending…" : "Confirm extension"}
            </button>
            <button
              onClick={abortExtend}
              disabled={savingExtend}
              className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Never mind
            </button>
          </div>
        </div>
      )}

      {b.status === "checked_in" && !panelOpen && (
        <div className="flex gap-3 flex-wrap mb-4">
          {folioId && (
            <Link
              to={`/app/hotel/folios/${folioId}`}
              className="border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Open folio
            </Link>
          )}
          <button
            onClick={checkOut}
            disabled={checkingOut || busy}
            className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            {checkingOut ? "Checking out…" : "Check out"}
          </button>
          {/* Forcing is manager-only on the server (403 otherwise), so the control
              is hidden entirely for non-managers rather than shown and left to fail. */}
          {isManager && (
            <button
              onClick={startForceCheckOut}
              disabled={checkingOut || busy}
              className="border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Force check-out
            </button>
          )}
        </div>
      )}

      {forcing && (
        <div className="border border-red-500/40 bg-red-950/20 rounded p-5 max-w-xl mb-4">
          <p className="text-sm text-red-300 mb-3">
            This checks the guest out with an outstanding balance — the folio closes unpaid
            and cannot be undone. Give a reason to confirm.
          </p>
          <textarea
            autoFocus
            value={forceReason}
            onChange={(e) => setForceReason(e.target.value)}
            placeholder="Reason for forcing check-out with a balance"
            rows={3}
            className="w-full bg-stone-950 border border-stone-700 text-stone-100 rounded p-3 text-sm focus:border-red-500 outline-none"
          />
          <div className="flex gap-3 mt-4">
            <button
              onClick={confirmForceCheckOut}
              disabled={checkingOut || !forceReason.trim()}
              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {checkingOut ? "Checking out…" : "Confirm force check-out"}
            </button>
            <button
              onClick={abortForceCheckOut}
              disabled={checkingOut}
              className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Never mind
            </button>
          </div>
        </div>
      )}

      {b.status === "checked_out" && folioId && (
        <div className="mb-4">
          <Link
            to={`/app/hotel/folios/${folioId}`}
            className="border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Open folio
          </Link>
        </div>
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
