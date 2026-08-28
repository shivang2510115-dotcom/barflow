import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import RoomGrid from "@/components/app/RoomGrid";
import { nextDay, occupancyState } from "@/lib/roomGrid";
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

// A calendar date, from local getters. `toISOString()` is UTC, and for a hotel in India
// between midnight and 05:30 it would answer yesterday — the night the desk is standing
// in the middle of is the one thing this screen must not get wrong.
const toLocalISODate = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/**
 * The room types, for the code on each tile.
 *
 * `/room-types` names `hotel.bookings` among its screens, so the receptionist who reads
 * this page can read it directly. It used to fall back to `/availability` on a 403 —
 * labelling a floor plan through the pricing engine — which worked but was the wrong
 * shape; the gate was widened instead.
 */
async function loadTypes() {
  return (await api.get("/room-types")).data;
}

export default function Bookings() {
  const nav = useNavigate();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  // The night the plan is about. One night, because a room that changes hands at noon is
  // two different answers over a week, and "who is in 204" is always asked about one.
  const [night, setNight] = useState(() => toLocalISODate(new Date()));
  // Held as one object, for the same reason the new-booking screen does: never a render
  // where the doors come from this night and the guests from the last one.
  const [plan, setPlan] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  // The plan is deliberately not filtered by the search box above it. Typing a guest's
  // name narrows the list to find their reference; it must not empty the building.
  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    Promise.all([
      api.get("/rooms", { signal: controller.signal }),
      api.get("/bookings", {
        params: { start: night, end: nextDay(night) },
        signal: controller.signal,
      }),
      loadTypes(),
    ])
      .then(([rooms, bookings, types]) => {
        if (live) setPlan({ night, rooms: rooms.data, bookings: bookings.data, types });
      })
      .catch((e) => {
        if (axios.isCancel(e) || e.code === "ERR_CANCELED") return;
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [night]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .get("/bookings", { params: { q: debouncedQ, status }, signal: controller.signal })
      .then((r) => setRows(r.data))
      .catch((e) => {
        if (axios.isCancel(e) || e.code === "ERR_CANCELED") return;
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [debouncedQ, status]);

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Bookings
      </h1>

      {/* Who is in which room. The list below answers "where is booking BK-1042"; this
          answers the question the desk is actually asked, which is the other way round —
          somebody rings down from 204 and nobody should have to scan a column of room
          numbers to find out who that is. */}
      <section className="mb-12" data-testid="bookings-plan">
        <div className="flex flex-wrap gap-4 items-end mb-5">
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Night of
            <input
              type="date"
              value={night}
              data-testid="bookings-plan-night"
              onChange={(e) => e.target.value && setNight(e.target.value)}
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
          <p className="text-xs text-stone-500 pb-2 max-w-md">
            A stay is half-open, so a guest leaving on this date is not in the room for
            this night — the door is already the next arrival's.
          </p>
        </div>

        {!plan ? (
          <p className="text-stone-500 text-sm">Loading the building…</p>
        ) : (
          <RoomGrid
            rooms={plan.rooms}
            types={plan.types}
            stateOf={(r) => occupancyState(r, { date: plan.night, bookings: plan.bookings })}
            legendStates={["occupied", "vacant", "blocked", "inactive"]}
            onSelect={(room, view) => nav(`/app/hotel/bookings/${view.booking.id}`)}
            selectable={(room, view) => view.state === "occupied"}
            empty="No rooms are set up yet — add some under Rooms."
            testIdPrefix="bookings-room"
          />
        )}
      </section>

      <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
        Every booking
      </h2>

      <div className="flex flex-wrap gap-4 items-end mb-6">
        <label className="text-xs tracking-widest uppercase text-stone-500">
          Search
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Reference, name or phone"
            className="block mt-2 w-64 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
        </label>
        <label className="text-xs tracking-widest uppercase text-stone-500">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="block mt-2 bg-stone-900 border border-stone-700 text-stone-100 py-1 px-2 rounded"
          >
            <option value="">All</option>
            {Object.keys(STATUS_STYLE).map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading ? (
        <p className="text-stone-400">Loading bookings…</p>
      ) : rows.length === 0 ? (
        <p className="text-stone-400">No bookings match.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                <th className="text-left py-3 px-3 border-b border-stone-800">Reference</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Guest</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Dates</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Room</th>
                <th className="text-left py-3 px-3 border-b border-stone-800">Status</th>
                <th className="text-right py-3 px-3 border-b border-stone-800">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const expired = isExpiredHold(b);
                return (
                  <tr key={b.id} className="hover:bg-stone-900">
                    <td className="py-3 px-3 border-b border-stone-800 font-mono">
                      <Link className="text-orange-400 hover:underline" to={`/app/hotel/bookings/${b.id}`}>
                        {b.reference}
                      </Link>
                    </td>
                    <td className="py-3 px-3 border-b border-stone-800">
                      {b.guest?.name || "—"}
                      <span className="block text-xs text-stone-500">{b.guest?.phone}</span>
                    </td>
                    <td className="py-3 px-3 border-b border-stone-800 font-mono text-xs tabular-nums">
                      {b.check_in} → {b.check_out}
                    </td>
                    {/* "Who still needs a room for tomorrow" is the 9am question, so a
                        booking without one says so rather than showing a blank cell. */}
                    <td className="py-3 px-3 border-b border-stone-800 tabular-nums">
                      {b.room ? (
                        b.room.number
                      ) : (
                        <span className="text-[10px] tracking-widest uppercase text-stone-500">
                          not assigned
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-3 border-b border-stone-800">
                      <span className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 ${STATUS_STYLE[b.status] || ""}`}>
                        {(b.status || "").replace("_", " ")}
                      </span>
                      {b.status === "tentative" && b.hold_expires_at && (
                        expired ? (
                          <span className="block text-[10px] text-red-400 mt-1 font-semibold tracking-widest uppercase">
                            hold expired — {b.hold_expires_at.slice(0, 10)}
                          </span>
                        ) : (
                          <span className="block text-[10px] text-amber-400 mt-1">
                            hold until {b.hold_expires_at.slice(0, 10)}
                          </span>
                        )
                      )}
                    </td>
                    <td className="py-3 px-3 border-b border-stone-800 text-right tabular-nums">
                      {currency(b.quote?.total)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
