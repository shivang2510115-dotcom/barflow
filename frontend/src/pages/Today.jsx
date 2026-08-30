import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, currency } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { LogIn, LogOut, BedDouble, Sparkles, Command } from "lucide-react";

/**
 * The screen you open in the morning.
 *
 * It replaced "Where are you working?" — a question the software asked before it would
 * tell you anything. This tells you something first: who is arriving, who is leaving,
 * which rooms are not ready, and how the rooms side is doing. The sections you might
 * want are one keystroke away rather than one decision away.
 *
 * Everything comes from a single GET. This is a screen somebody opens with a guest
 * already at the desk, and it must not arrive in four instalments.
 */

/** A figure that may honestly be unknown. A dash is the truth; 0 would be a claim. */
function Stat({ label, value, hint }) {
  return (
    <div className="bg-surface border border-hairline rounded p-4">
      <div className="text-[11px] uppercase tracking-[0.2em] text-faint">{label}</div>
      <div className="font-display text-2xl text-ink mt-2 tabular-nums">
        {value ?? "—"}
      </div>
      {hint && <div className="text-[12px] text-faint mt-1">{hint}</div>}
    </div>
  );
}

function List({ icon: Icon, title, rows, empty }) {
  return (
    <section className="bg-surface border border-hairline rounded">
      <h2 className="flex items-center gap-2 px-4 py-3 border-b border-hairline
                     text-[11px] uppercase tracking-[0.2em] text-faint">
        <Icon className="h-4 w-4" aria-hidden="true" />
        {title}
        <span className="ml-auto tabular-nums text-ink">{rows.length}</span>
      </h2>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-[13px] text-faint">{empty}</p>
      ) : (
        <ul className="divide-y divide-hairline">
          {rows.map((r) => (
            <li key={r.booking_id} className="px-4 py-3 flex items-center gap-3">
              <span className="text-[15px] text-ink truncate flex-1">{r.guest_name}</span>
              {r.room_number ? (
                <span className="font-mono text-[13px] text-muted2">{r.room_number}</span>
              ) : (
                <span className="text-[12px] text-state-dirty">no room yet</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function Today() {
  const { user } = useAuth();
  const [board, setBoard] = useState(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/today");
      setBoard(data);
    } catch {
      // A board that cannot load says so rather than showing zeroes, which would read
      // as a quiet morning instead of a broken request.
      setFailed(true);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (failed) {
    return <p className="text-sm text-muted2">Could not load today. Try reloading.</p>;
  }
  if (!board) return <p className="text-sm text-faint">Loading…</p>;

  const m = board.metrics;
  const ready = board.rooms.total - board.rooms.not_ready;

  return (
    <div className="p-6 md:p-10 max-w-5xl">
      <header className="mb-8">
        <p className="text-[11px] uppercase tracking-[0.3em] text-brass">
          {new Date(board.date + "T00:00:00").toLocaleDateString(undefined, {
            weekday: "long", day: "numeric", month: "long" })}
        </p>
        <h1 className="font-display text-3xl text-ink mt-1">
          Good day{user?.name ? `, ${user.name.split(" ")[0]}` : ""}.
        </h1>
        <p className="text-sm text-muted2 mt-2 flex items-center gap-2">
          <Command className="h-3.5 w-3.5" aria-hidden="true" />
          Press <kbd className="font-mono text-[12px] text-ink">⌘K</kbd> to jump anywhere
          — a room number, a guest, a table.
        </p>
      </header>

      {m && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 mb-8">
          <Stat label="Occupancy" hint={`${m.nights_sold} of ${m.nights_available} rooms`}
                value={m.occupancy === null ? null : `${m.occupancy}%`} />
          {/* ADR is null when nothing sold. Rendering 0 would claim rooms were given
              away free, which is a different and much worse statement. */}
          <Stat label="ADR" hint="per room sold"
                value={m.adr === null ? null : currency(m.adr)} />
          <Stat label="RevPAR" hint="per room available"
                value={m.revpar === null ? null : currency(m.revpar)} />
          <Stat label="Room revenue" hint="today"
                value={currency(m.room_revenue)} />
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 mb-8">
        <Stat label="In house" value={board.in_house_count} hint="staying tonight" />
        <Stat label="Arriving" value={board.arrivals.length} hint="expected today" />
        <Stat label="Leaving" value={board.departures.length} hint="checking out" />
        <Stat label="Rooms ready" value={`${ready}/${board.rooms.total}`}
              hint={board.rooms.not_ready
                ? `${board.rooms.not_ready} being turned around`
                : "all turned around"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <List icon={LogIn} title="Arriving today" rows={board.arrivals}
              empty="Nobody expected today." />
        <List icon={LogOut} title="Leaving today" rows={board.departures}
              empty="Nobody checking out today." />
      </div>

      {board.rooms.not_ready > 0 && (
        <section className="mt-4 bg-surface border border-hairline rounded p-4">
          <h2 className="flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-faint mb-3">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            Not ready yet
          </h2>
          <div className="flex flex-wrap gap-2">
            {board.rooms.not_ready_numbers.map((n) => (
              <span key={n} className="font-mono text-[13px] px-2 py-1 rounded
                                       bg-state-dirty/10 text-state-dirty border border-state-dirty/40">
                {n}
              </span>
            ))}
            {board.rooms.not_ready > board.rooms.not_ready_numbers.length && (
              <span className="text-[13px] text-faint self-center">
                +{board.rooms.not_ready - board.rooms.not_ready_numbers.length} more
              </span>
            )}
          </div>
          <Link to="/app/hotel/housekeeping"
                className="inline-block mt-4 text-[13px] text-brass hover:underline">
            Open housekeeping
          </Link>
        </section>
      )}

      <p className="mt-8 text-[12px] text-faint flex items-center gap-2">
        <BedDouble className="h-3.5 w-3.5" aria-hidden="true" />
        {m
          ? "Figures cover today only, on this property's own calendar day."
          : "Revenue figures are shown to managers and administrators."}
      </p>
    </div>
  );
}
