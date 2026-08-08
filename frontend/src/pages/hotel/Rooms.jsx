import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function Rooms() {
  const [types, setTypes] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = () =>
    Promise.all([api.get("/room-types"), api.get("/rooms")])
      .then(([t, r]) => {
        setTypes(t.data);
        setRooms(r.data);
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  if (loading) return <div className="p-6 md:p-10 text-stone-400">Loading rooms…</div>;

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Rooms
      </h1>

      {types.length === 0 ? (
        <p className="text-stone-400">
          No room types yet. Add one to start taking bookings.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {types.map((t) => {
            const count = rooms.filter((r) => r.room_type_id === t.id).length;
            return (
              <div key={t.id} className="border border-stone-800 bg-stone-900 rounded p-5">
                <div className="flex items-baseline justify-between">
                  <h3 className="text-lg font-semibold">{t.name}</h3>
                  <span className="text-xs font-mono text-stone-500">{t.code}</span>
                </div>
                <p className="text-sm text-stone-400 mt-2">
                  Sleeps {t.base_occupancy}, up to {t.max_occupancy}
                  {t.max_extra_beds ? ` plus ${t.max_extra_beds} extra bed` : ""}
                </p>
                <p className="text-sm text-orange-400 mt-3 font-mono">
                  {count} room{count === 1 ? "" : "s"}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
