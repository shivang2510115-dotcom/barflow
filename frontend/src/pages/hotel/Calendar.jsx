import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const addDays = (iso, n) =>
  new Date(new Date(iso).getTime() + n * 86400000).toISOString().slice(0, 10);

// Check-in/check-out are calendar dates, never instants — toISOString() converts to
// UTC, which for a user east of UTC between midnight and their UTC offset would make
// "today" resolve to yesterday's date. Build the initial value from local getters
// instead; addDays above operates on an explicit YYYY-MM-DD string and is unaffected.
const toLocalISODate = (d) => {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

export default function Calendar() {
  const [start, setStart] = useState(toLocalISODate(new Date()));
  const [grid, setGrid] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .get("/bookings/calendar", { params: { start, end: addDays(start, 14) } })
      .then((r) => setGrid(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setLoading(false));
  }, [start]);

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-brass mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Occupancy
      </h1>

      <div className="flex gap-3 items-end mb-6">
        <label className="text-xs tracking-widest uppercase text-faint">
          From
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="block mt-2 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
          />
        </label>
        <button onClick={() => setStart(addDays(start, -14))} className="text-xs tracking-widest uppercase text-faint hover:text-brass pb-1">
          ← Earlier
        </button>
        <button onClick={() => setStart(addDays(start, 14))} className="text-xs tracking-widest uppercase text-faint hover:text-brass pb-1">
          Later →
        </button>
      </div>

      {loading ? (
        <p className="text-muted2">Loading occupancy…</p>
      ) : grid.length === 0 ? (
        <p className="text-muted2">No active room types to show.</p>
      ) : (
        <div className="overflow-x-auto border border-hairline rounded">
          <table className="text-sm border-collapse">
            <thead>
              <tr>
                <th className="text-left py-2 px-3 border-b border-hairline text-[11px] tracking-[0.2em] uppercase text-faint sticky left-0 bg-ground">
                  Room type
                </th>
                {(grid[0]?.nights || []).map((n) => (
                  <th key={n.date} className="py-2 px-2 border-b border-hairline text-[10px] font-mono text-faint tabular-nums">
                    {n.date.slice(5)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.map((row) => (
                <tr key={row.room_type.id}>
                  <td className="py-2 px-3 border-b border-hairline whitespace-nowrap sticky left-0 bg-ground">
                    {row.room_type.name}
                  </td>
                  {row.nights.map((n) => (
                    <td
                      key={n.date}
                      title={`${n.occupied} of ${n.total} occupied`}
                      className={`py-2 px-2 border-b border-hairline text-center tabular-nums text-xs ${
                        n.available === 0
                          ? "bg-brass/30 text-brass"
                          : n.occupied > 0
                          ? "bg-raised text-muted2"
                          : "text-faint"
                      }`}
                    >
                      {n.available}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-faint mt-4">Numbers are rooms still free that night.</p>
    </div>
  );
}
