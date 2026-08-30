import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Power, Store, Wine, Scissors, Dumbbell, Shirt, Package } from "lucide-react";

/**
 * The places this property serves guests: its restaurants, bars, salon, gym, laundry.
 *
 * This screen is what replaced a hardcoded tuple in the backend. A hotel adds its own
 * outlets rather than waiting for the platform operator, which is the same reason
 * signup is self-serve — a hotel waiting on us to add a salon is a support ticket that
 * scales with the customer count.
 *
 * Nothing is deleted here, only switched off. A past order names the outlet it was
 * rung up in, and a row that vanishes takes the label off every one of them.
 */

// The kinds the backend accepts, with the icon each wears. Kept in step with
// services/outlets.py::KINDS — the API refuses anything else with a message naming it,
// so a drift shows up as a 400 the user can read rather than as a silent wrong icon.
const KINDS = [
  { value: "restaurant", label: "Restaurant", icon: Store },
  { value: "bar", label: "Bar", icon: Wine },
  { value: "salon", label: "Salon", icon: Scissors },
  { value: "gym", label: "Gym", icon: Dumbbell },
  { value: "laundry", label: "Laundry", icon: Shirt },
  { value: "other", label: "Other", icon: Package },
];

// The same defaults the backend uses, so the name field is never blank.
const DEFAULT_NAMES = {
  restaurant: "Restaurant", bar: "Bar", salon: "Salon",
  gym: "Gym", laundry: "Laundry", other: "Outlet",
};

const iconFor = (kind) => (KINDS.find((k) => k.value === kind) || KINDS[5]).icon;

export default function Outlets() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  // Which row is mid-confirmation. Confirmation happens in the row rather than in a
  // browser dialog: this codebase confirms destructive things in place everywhere else,
  // and window.confirm is unstyleable and awkward on the tablet this actually runs on.
  const [confirming, setConfirming] = useState(null);
  const [form, setForm] = useState({
    name: "Salon", kind: "salon",
    charges_to_folio: true, takes_direct_payment: true,
  });

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/outlets");
      setRows(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not load outlets");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const pickKind = (kind) =>
    setForm((f) => ({
      ...f,
      kind,
      // Only overwrite a name the person has not edited away from a default, so
      // switching kind twice does not discard what they typed.
      name: Object.values(DEFAULT_NAMES).includes(f.name) ? DEFAULT_NAMES[kind] : f.name,
    }));

  const create = async (e) => {
    e.preventDefault();
    setAdding(true);
    try {
      await api.post("/outlets", form);
      toast.success(`${form.name} added`);
      setForm({ name: "Salon", kind: "salon", charges_to_folio: true, takes_direct_payment: true });
      load();
    } catch (e) {
      // Shown verbatim: outlet_problem is written to be read by the person who typed
      // the form, and paraphrasing it here would make it worse.
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not add the outlet");
    } finally {
      setAdding(false);
    }
  };

  const setActive = async (row, active) => {
    setConfirming(null);
    try {
      await api.patch(`/outlets/${row.id}`, { active });
      toast.success(active ? `${row.name} is back on` : `${row.name} switched off`);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not change the outlet");
    }
  };

  return (
    <div className="max-w-4xl">
      <header className="mb-8">
        <h1 className="font-display text-2xl text-ink">Outlets</h1>
        <p className="text-sm text-muted2 mt-1 max-w-prose">
          Every place this property serves a guest. Staff are assigned to outlets on the
          Staff screen, and only the outlets that exist here appear in anyone's sidebar.
        </p>
      </header>

      <form onSubmit={create} className="bg-surface border border-hairline rounded p-5 mb-8">
        <div className="flex flex-wrap gap-2 mb-5">
          {KINDS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              onClick={() => pickKind(value)}
              aria-pressed={form.kind === value}
              className={`inline-flex items-center gap-2 px-4 rounded border text-[13px] transition-colors
                ${form.kind === value
                  ? "border-brass bg-brass/10 text-brass"
                  : "border-hairline text-muted2 hover:border-hairline-strong"}`}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <label className="flex-1 min-w-[14rem]">
            <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-2">
              Name
            </span>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="w-full bg-ground border border-hairline rounded px-3 text-[15px] text-ink"
              placeholder="Serenity Salon"
            />
          </label>

          <button
            type="submit"
            disabled={adding}
            className="inline-flex items-center gap-2 px-5 rounded bg-brass hover:bg-brass-deep
                       text-on-brass text-[13px] font-medium disabled:opacity-40 transition-colors"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {adding ? "Adding…" : "Add outlet"}
          </button>
        </div>

        <fieldset className="mt-5 pt-5 border-t border-hairline">
          <legend className="sr-only">How this outlet takes money</legend>
          <div className="flex flex-wrap gap-6">
            {[
              ["charges_to_folio", "Charge to a room folio"],
              ["takes_direct_payment", "Take direct payment"],
            ].map(([key, label]) => (
              <label key={key} className="inline-flex items-center gap-2 text-[13px] text-muted2">
                <input
                  type="checkbox"
                  data-compact
                  checked={form[key]}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.checked }))}
                  className="accent-brass h-4 w-4"
                />
                {label}
              </label>
            ))}
          </div>
          <p className="text-[12px] text-faint mt-3">
            An outlet needs at least one. Without either it cannot complete a sale.
          </p>
        </fieldset>
      </form>

      {loading ? (
        <p className="text-sm text-faint">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted2">
          No outlets yet. Add the first one above — a restaurant, a bar, or a salon.
        </p>
      ) : (
        <ul className="divide-y divide-hairline border border-hairline rounded bg-surface">
          {rows.map((row) => {
            const Icon = iconFor(row.kind);
            return (
              <li key={row.id} className="p-4">
                <div className="flex items-center gap-4">
                  <Icon
                    className={`h-5 w-5 shrink-0 ${row.active ? "text-brass" : "text-faint"}`}
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <p className={`text-[15px] truncate ${row.active ? "text-ink" : "text-faint"}`}>
                      {row.name}
                    </p>
                    <p className="text-[12px] text-faint mt-0.5">
                      {row.kind}
                      {" · "}
                      {[row.charges_to_folio && "charges to room",
                        row.takes_direct_payment && "takes payment"]
                        .filter(Boolean).join(", ")}
                      {!row.active && " · switched off"}
                    </p>
                  </div>

                  {confirming === row.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] text-muted2">Switch off?</span>
                      <button
                        onClick={() => setActive(row, false)}
                        className="px-3 rounded bg-state-alert/10 border border-state-alert/60
                                   text-state-alert text-[13px]"
                      >
                        Switch off
                      </button>
                      <button
                        onClick={() => setConfirming(null)}
                        className="px-3 rounded border border-hairline text-muted2 text-[13px]"
                      >
                        Keep
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => (row.active ? setConfirming(row.id) : setActive(row, true))}
                      className="inline-flex items-center gap-2 px-3 rounded border border-hairline
                                 text-muted2 hover:border-hairline-strong text-[13px] transition-colors"
                    >
                      <Power className="h-4 w-4" aria-hidden="true" />
                      {row.active ? "Switch off" : "Switch on"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
