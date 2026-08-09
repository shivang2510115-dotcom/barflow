import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function FrontDesk() {
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [rooms, setRooms] = useState([]);
  const [checkingIn, setCheckingIn] = useState(null); // booking being checked in
  const [form, setForm] = useState({ room_id: "", id_proof_type: "Aadhaar", id_proof_number: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    Promise.all([api.get("/front-desk"), api.get("/rooms")])
      .then(([d, r]) => {
        setData(d.data);
        setRooms(r.data);
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const startCheckIn = (booking) => {
    setCheckingIn(booking);
    setForm({ room_id: "", id_proof_type: "Aadhaar", id_proof_number: "" });
  };

  const submitCheckIn = async () => {
    if (!form.room_id) {
      toast.error("Pick a room");
      return;
    }
    if (!form.id_proof_type.trim()) {
      toast.error("ID proof type is required");
      return;
    }
    if (!form.id_proof_number.trim()) {
      toast.error("ID proof number is required");
      return;
    }
    setBusy(true);
    try {
      const { data: res } = await api.post(`/bookings/${checkingIn.id}/check-in`, form);
      toast.success(`Checked in to room ${res.room.number}`);
      setCheckingIn(null);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <div className="p-6 md:p-10 text-stone-400">Loading front desk…</div>;

  const freeRooms = rooms.filter(
    (r) => r.active !== false && r.room_type_id === checkingIn?.room_type_id,
  );

  const Row = ({ b, action }) => (
    <li className="flex items-center justify-between gap-4 py-3 border-b border-stone-800">
      <div className="min-w-0">
        <div className="truncate">{b.guest?.name || "—"}</div>
        <div className="text-xs text-stone-500 font-mono">
          {b.reference} · {b.check_in} → {b.check_out}
          {b.room ? ` · room ${b.room.number}` : ""}
        </div>
      </div>
      {action}
    </li>
  );

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Front desk
      </h1>
      <p className="text-stone-500 font-mono text-xs mb-8">{data.date}</p>

      <div className="grid gap-8 lg:grid-cols-3">
        <section>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            Arrivals · {data.arrivals.length}
          </h2>
          {data.arrivals.length === 0 ? (
            <p className="text-stone-500 text-sm">No arrivals today.</p>
          ) : (
            <ul>
              {data.arrivals.map((b) => (
                <Row
                  key={b.id}
                  b={b}
                  action={
                    <button
                      onClick={() => startCheckIn(b)}
                      className="shrink-0 border border-orange-500/50 text-orange-400 hover:bg-orange-500/10 rounded-full px-4 py-1 text-xs tracking-widest uppercase"
                    >
                      Check in
                    </button>
                  }
                />
              ))}
            </ul>
          )}
        </section>

        <section>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            Departures · {data.departures.length}
          </h2>
          {data.departures.length === 0 ? (
            <p className="text-stone-500 text-sm">No departures today.</p>
          ) : (
            <ul>
              {data.departures.map((b) => (
                <Row
                  key={b.id}
                  b={b}
                  action={
                    <button
                      onClick={() => nav(`/app/hotel/bookings/${b.id}`)}
                      className="shrink-0 border border-stone-700 text-stone-300 hover:border-orange-500 hover:text-orange-400 rounded-full px-4 py-1 text-xs tracking-widest uppercase"
                    >
                      Open
                    </button>
                  }
                />
              ))}
            </ul>
          )}
        </section>

        <section>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            In house · {data.in_house.length}
          </h2>
          {data.in_house.length === 0 ? (
            <p className="text-stone-500 text-sm">Nobody in house.</p>
          ) : (
            <ul>
              {data.in_house.map((b) => (
                <Row
                  key={b.id}
                  b={b}
                  action={
                    <Link
                      to={`/app/hotel/bookings/${b.id}`}
                      className="shrink-0 text-xs tracking-widest uppercase text-orange-400 hover:underline"
                    >
                      Folio
                    </Link>
                  }
                />
              ))}
            </ul>
          )}
        </section>
      </div>

      {checkingIn && (
        <div className="mt-10 border border-stone-800 bg-stone-900 rounded p-5 max-w-xl">
          <h3 className="text-lg font-semibold mb-1">
            Check in {checkingIn.guest?.name}
          </h3>
          <p className="text-xs text-stone-500 font-mono mb-4">
            {checkingIn.reference} · {checkingIn.check_in} → {checkingIn.check_out}
          </p>

          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Room
              <select
                value={form.room_id}
                onChange={(e) => setForm({ ...form, room_id: e.target.value })}
                className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                <option value="">Choose…</option>
                {freeRooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.number}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              ID type
              <select
                value={form.id_proof_type}
                onChange={(e) => setForm({ ...form, id_proof_type: e.target.value })}
                className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
              >
                {["Aadhaar", "Passport", "Driving Licence", "Voter ID"].map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              ID number
              <input
                value={form.id_proof_number}
                onChange={(e) => setForm({ ...form, id_proof_number: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          </div>

          <p className="text-xs text-stone-500 mt-4">
            ID capture is a legal requirement for Indian hotels and is recorded against the
            guest, not the booking.
          </p>

          <div className="flex gap-3 mt-5">
            <button
              onClick={submitCheckIn}
              disabled={busy}
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Checking in…" : "Confirm check in"}
            </button>
            <button
              onClick={() => setCheckingIn(null)}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
