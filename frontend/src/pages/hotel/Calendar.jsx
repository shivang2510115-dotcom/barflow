import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const addDays = (iso, n) =>
  new Date(new Date(iso).getTime() + n * 86400000).toISOString().slice(0, 10);

export default function Calendar() {
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
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
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Occupancy
      </h1>

      <div className="flex gap-3 items-end mb-6">
        <label className="text-xs tracking-widest uppercase text-stone-500">
          From
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
        </label>
        <button onClick={() => setStart(addDays(start, -14))} className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 pb-1">
          ← Earlier
        </button>
        <button onClick={() => setStart(addDays(start, 14))} className="text-xs tracking-widest uppercase text-stone-500 hover:text-orange-400 pb-1">
          Later →
        </button>
      </div>

      {loading ? (
        <p className="text-stone-400">Loading occupancy…</p>
      ) : grid.length === 0 ? (
        <p className="text-stone-400">No active room types to show.</p>
      ) : (
        <div className="overflow-x-auto border border-stone-800 rounded">
          <table className="text-sm border-collapse">
            <thead>
              <tr>
                <th className="text-left py-2 px-3 border-b border-stone-800 text-[11px] tracking-[0.2em] uppercase text-stone-500 sticky left-0 bg-stone-950">
                  Room type
                </th>
                {(grid[0]?.nights || []).map((n) => (
                  <th key={n.date} className="py-2 px-2 border-b border-stone-800 text-[10px] font-mono text-stone-500 tabular-nums">
                    {n.date.slice(5)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.map((row) => (
                <tr key={row.room_type.id}>
                  <td className="py-2 px-3 border-b border-stone-800 whitespace-nowrap sticky left-0 bg-stone-950">
                    {row.room_type.name}
                  </td>
                  {row.nights.map((n) => (
                    <td
                      key={n.date}
                      title={`${n.occupied} of ${n.total} occupied`}
                      className={`py-2 px-2 border-b border-stone-800 text-center tabular-nums text-xs ${
                        n.available === 0
                          ? "bg-orange-600/30 text-orange-200"
                          : n.occupied > 0
                          ? "bg-stone-800 text-stone-300"
                          : "text-stone-600"
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
      <p className="text-xs text-stone-500 mt-4">Numbers are rooms still free that night.</p>
    </div>
  );
}
