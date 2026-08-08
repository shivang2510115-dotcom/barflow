import { useEffect, useState } from "react";
import axios from "axios";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function Guests() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .get("/guests", { params: { q: debouncedQ }, signal: controller.signal })
      .then((r) => setRows(r.data))
      .catch((e) => {
        if (axios.isCancel(e) || e.code === "ERR_CANCELED") return;
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [debouncedQ]);

  const open = (id) =>
    api
      .get(`/guests/${id}`)
      .then((r) => setSelected(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Guests
      </h1>

      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by name or phone"
        className="mb-6 w-full max-w-md bg-transparent border-b border-stone-700 text-stone-100 py-2 focus:border-orange-500 outline-none"
      />

      <div className="grid gap-8 lg:grid-cols-2">
        <div>
          {loading ? (
            <p className="text-stone-400">Searching…</p>
          ) : rows.length === 0 ? (
            <p className="text-stone-400">No guests match.</p>
          ) : (
            <ul className="divide-y divide-stone-800">
              {rows.map((g) => (
                <li key={g.id}>
                  <button
                    onClick={() => open(g.id)}
                    className={`w-full text-left py-3 hover:text-orange-400 ${selected?.id === g.id ? "text-orange-400" : ""}`}
                  >
                    {g.name}
                    <span className="block text-xs text-stone-500 font-mono">{g.phone}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {selected && (
          <div className="border border-stone-800 bg-stone-900 rounded p-5 h-fit">
            <h2 className="text-lg font-semibold">{selected.name}</h2>
            <p className="text-xs text-stone-500 font-mono mb-4">{selected.phone}</p>

            <div className="grid grid-cols-2 gap-3 mb-5">
              <div>
                <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">Stays</div>
                <div className="text-2xl font-semibold">{selected.stays?.length || 0}</div>
              </div>
              <div>
                <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                  Bar & restaurant
                </div>
                <div className="text-2xl font-semibold text-orange-400">
                  {currency(selected.outlet_spend)}
                </div>
                <div className="text-xs text-stone-500">
                  {selected.outlet_orders} bill{selected.outlet_orders === 1 ? "" : "s"}
                </div>
              </div>
            </div>

            {(selected.stays || []).length > 0 && (
              <>
                <div className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
                  Stay history
                </div>
                <ul className="text-sm space-y-1">
                  {selected.stays.map((s) => (
                    <li key={s.id} className="font-mono text-xs text-stone-400">
                      {s.check_in} → {s.check_out} · {s.reference} · {s.status}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
