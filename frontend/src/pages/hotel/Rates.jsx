import { useEffect, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { useProperty } from "@/contexts/PropertyContext";
import { toast } from "sonner";

export default function Rates() {
  // A property selling one all-inclusive rate has no per-plan pricing to show, so the
  // Rates screen does not ask for any. The plans are still fetched and still stored —
  // switching the setting back on has to bring back exactly what the hotel had — they
  // are simply not part of this screen while the hotel is not selling on them.
  const property = useProperty();
  const mealPlansEnabled = property?.meal_plans_enabled ?? false;
  const [types, setTypes] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [rates, setRates] = useState([]);
  const [plans, setPlans] = useState([]);
  const [draft, setDraft] = useState({
    room_type_id: "",
    period_id: "",
    base_rate: "",
    extra_adult_rate: "",
    extra_child_rate: "",
  });
  const [season, setSeason] = useState({ name: "", start_date: "", end_date: "", priority: 10 });

  const load = () =>
    Promise.all([
      api.get("/room-types"),
      api.get("/rate-periods"),
      api.get("/rates"),
      api.get("/meal-plans"),
    ])
      .then(([t, p, r, m]) => {
        setTypes(t.data);
        setPeriods(p.data);
        setRates(r.data);
        setPlans(m.data);
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  useEffect(() => {
    load();
  }, []);

  const saveSeason = async () => {
    if (!season.name.trim() || !season.start_date || !season.end_date) {
      toast.error("Name, start and end are all required");
      return;
    }
    if (season.end_date <= season.start_date) {
      toast.error("End must be after start");
      return;
    }
    try {
      const { data } = await api.post("/rate-periods", {
        ...season,
        priority: Number(season.priority),
      });
      if (data.overlap_warning) {
        toast.warning(`Overlaps ${data.overlap_warning.join(", ")} at the same priority`);
      } else {
        toast.success("Season saved");
      }
      setSeason({ name: "", start_date: "", end_date: "", priority: 10 });
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  const save = async () => {
    if (!draft.room_type_id || draft.base_rate === "") {
      toast.error("Pick a room type and enter a base rate");
      return;
    }
    try {
      await api.post("/rates", {
        room_type_id: draft.room_type_id,
        period_id: draft.period_id || null,
        base_rate: Number(draft.base_rate),
        extra_adult_rate: Number(draft.extra_adult_rate || 0),
        extra_child_rate: Number(draft.extra_child_rate || 0),
      });
      toast.success("Rate saved");
      setDraft({ ...draft, base_rate: "", extra_adult_rate: "", extra_child_rate: "" });
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  const typeName = (id) => types.find((t) => t.id === id)?.name || "—";
  const periodName = (id) =>
    id ? periods.find((p) => p.id === id)?.name || "—" : "Default";

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Rates
      </h1>

      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-6 max-w-3xl">
        <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-4">Seasons</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Name
            <input
              value={season.name}
              onChange={(e) => setSeason({ ...season, name: e.target.value })}
              placeholder="Peak"
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
          {[["start_date", "Starts"], ["end_date", "Ends"]].map(([k, label]) => (
            <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
              {label}
              <input
                type="date"
                value={season[k]}
                onChange={(e) => setSeason({ ...season, [k]: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          ))}
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Priority
            <input
              type="number"
              value={season.priority}
              onChange={(e) => setSeason({ ...season, priority: e.target.value })}
              className="block mt-2 w-20 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
          <button
            onClick={saveSeason}
            className="bg-orange-600 hover:bg-orange-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Add season
          </button>
        </div>
        {periods.length > 0 && (
          <ul className="mt-4 text-sm text-stone-400 space-y-1">
            {periods.map((p) => (
              <li key={p.id} className="font-mono text-xs">
                {p.name}: {p.start_date} → {p.end_date} (priority {p.priority})
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-stone-500 mt-4">
          End dates are exclusive — a season ending 5 Jan covers the night of 4 Jan, not the
          5th. Higher priority wins where seasons overlap.
        </p>
      </div>

      <div className="border border-stone-800 bg-stone-900 rounded p-5 mb-8 max-w-3xl">
        <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-4">Set a rate</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Room type
            <select
              value={draft.room_type_id}
              onChange={(e) => setDraft({ ...draft, room_type_id: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            >
              <option value="">Choose…</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <label className="text-xs tracking-widest uppercase text-stone-500">
            Period
            <select
              value={draft.period_id}
              onChange={(e) => setDraft({ ...draft, period_id: e.target.value })}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            >
              <option value="">Default (all year)</option>
              {periods.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          {[
            ["base_rate", "Base rate"],
            ["extra_adult_rate", "Extra adult"],
            ["extra_child_rate", "Extra child"],
          ].map(([k, label]) => (
            <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
              {label}
              <input
                type="number"
                min="0"
                value={draft[k]}
                onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
          ))}
          <button
            onClick={save}
            className="bg-orange-600 hover:bg-orange-500 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            Save
          </button>
        </div>
        <p className="text-xs text-stone-500 mt-4">
          Saving a rate for a room type and period that already has one replaces it.
        </p>
      </div>

      <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">Current rates</h2>
      {rates.length === 0 ? (
        <p className="text-stone-400 mb-8">
          No rates yet. A room type with no rate cannot be booked — the system refuses to
          price it at zero, so set at least a default rate before taking bookings.
        </p>
      ) : (
        <div className="overflow-x-auto mb-8">
          <table className="w-full text-sm border-collapse max-w-3xl">
            <thead>
              <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                <th className="text-left py-2 px-3 border-b border-stone-800">Room type</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">Period</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Base</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Extra adult</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Extra child</th>
              </tr>
            </thead>
            <tbody>
              {rates.map((r) => (
                <tr key={r.id}>
                  <td className="py-2 px-3 border-b border-stone-800">{typeName(r.room_type_id)}</td>
                  <td className="py-2 px-3 border-b border-stone-800">{periodName(r.period_id)}</td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(r.base_rate)}</td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(r.extra_adult_rate)}</td>
                  <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums">{currency(r.extra_child_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {mealPlansEnabled ? (
        <>
          <h2 className="text-xs tracking-[0.2em] uppercase text-stone-500 mb-3">
            Meal plans
          </h2>
          <div className="grid gap-3 md:grid-cols-3 max-w-3xl">
            {plans.map((p) => (
              <div key={p.id} className="border border-stone-800 bg-stone-900 rounded p-4">
                <div className="font-mono text-xs text-orange-400">{p.code}</div>
                <div className="mt-1">{p.name}</div>
                <div className="text-xs text-stone-500 mt-2">
                  {currency(p.price_per_adult_per_night)} per adult / night
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="text-xs text-stone-500 max-w-2xl">
          Rooms are sold at one all-inclusive rate, so there is no per-plan pricing to
          set. Anything a guest takes on top goes on their folio as it happens. To quote
          EP, CP and MAP separately instead, turn on meal plans under Admin → Property
          settings.
        </p>
      )}
    </div>
  );
}
