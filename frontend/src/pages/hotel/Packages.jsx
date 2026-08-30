import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Gift } from "lucide-react";

/**
 * What a rate includes beyond the room.
 *
 * A rate points at a package; a package holds inclusions. That is the whole of how an
 * elite room differs from a normal one — the elite rate points at a package with more
 * in it. Nothing anywhere branches on room class.
 *
 * The three periods are the part worth getting right in the wording, because they are
 * the part people get wrong: two spa treatments is two for the *stay*, breakfast is one
 * a *night*, and a gym pass is unlimited *today* and empty again tomorrow.
 */

const PERIODS = [
  { value: "per_stay", label: "for the whole stay" },
  { value: "per_night", label: "for each night" },
  { value: "per_day", label: "each day, resets" },
];

const SCOPES = [
  { value: "outlet", label: "Anything there", hint: "every item the outlet sells" },
  { value: "category", label: "A category", hint: "e.g. Breakfast, Soft drinks" },
  { value: "item", label: "One item", hint: "a single menu item" },
];

const periodLabel = (v) => (PERIODS.find((p) => p.value === v) || {}).label || v;

export default function Packages() {
  const [packages, setPackages] = useState([]);
  const [outlets, setOutlets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(null);
  const [form, setForm] = useState({
    outlet_id: "", scope: "outlet", ref_id: "", quantity: 1, period: "per_stay",
  });

  const load = useCallback(async () => {
    try {
      const [p, o] = await Promise.all([
        api.get("/packages").then((r) => r.data),
        // Only outlets that can charge a room folio: an entitlement is spent by posting
        // there, so the others could never honour one. The API refuses them too — this
        // just keeps them out of a picker where choosing one leads to a 400.
        api.get("/outlets").then((r) => r.data.filter((x) => x.active && x.charges_to_folio)),
      ]);
      setPackages(p);
      setOutlets(o);
      if (o.length && !form.outlet_id) setForm((f) => ({ ...f, outlet_id: o[0].id }));
    } catch {
      toast.error("Could not load packages");
    } finally {
      setLoading(false);
    }
  }, [form.outlet_id]);

  useEffect(() => { load(); }, [load]);

  const createPackage = async (e) => {
    e.preventDefault();
    try {
      await api.post("/packages", { name });
      toast.success(`${name} added`);
      setName("");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not add it");
    }
  };

  const addInclusion = async (packageId) => {
    try {
      await api.post(`/packages/${packageId}/inclusions`, {
        ...form,
        ref_id: form.scope === "outlet" ? null : form.ref_id.trim() || null,
        quantity: Number(form.quantity) || 1,
      });
      toast.success("Included");
      setAdding(null);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not include it");
    }
  };

  const outletName = (id) =>
    (outlets.find((o) => o.id === id) || {}).name || "an outlet";

  return (
    <div className="p-6 md:p-10 max-w-4xl">
      <header className="mb-8">
        <h1 className="font-display text-2xl text-ink">Packages</h1>
        <p className="text-sm text-muted2 mt-1 max-w-prose">
          What a rate includes beyond the room. Point a rate at a package on the Rates
          screen — that is the whole of how an elite room differs from a normal one.
        </p>
      </header>

      <form onSubmit={createPackage}
            className="flex flex-wrap items-end gap-3 bg-surface border border-hairline rounded p-5 mb-8">
        <label className="flex-1 min-w-[14rem]">
          <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-2">
            New package
          </span>
          <input value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Bed & breakfast, Elite, Spa retreat"
                 className="w-full bg-ground border border-hairline rounded px-3 text-[15px] text-ink" />
        </label>
        <button type="submit" disabled={!name.trim()}
                className="inline-flex items-center gap-2 px-5 rounded bg-brass hover:bg-brass-deep
                           text-on-brass text-[13px] font-medium disabled:opacity-40 transition-colors">
          <Plus className="h-4 w-4" aria-hidden="true" /> Add package
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-faint">Loading…</p>
      ) : outlets.length === 0 ? (
        <p className="text-sm text-muted2">
          No outlet here can charge to a room folio yet, so nothing can be included in a
          package. Add one on the Outlets screen first.
        </p>
      ) : packages.length === 0 ? (
        <p className="text-sm text-muted2">
          No packages yet. Add one above, then say what it includes.
        </p>
      ) : (
        <ul className="space-y-4">
          {packages.map((p) => (
            <li key={p.id} className="bg-surface border border-hairline rounded">
              <div className="flex items-center gap-3 px-5 py-4 border-b border-hairline">
                <Gift className="h-4 w-4 text-brass" aria-hidden="true" />
                <h2 className="font-display text-[17px] text-ink flex-1">{p.name}</h2>
                <button onClick={() => setAdding(adding === p.id ? null : p.id)}
                        className="px-3 rounded border border-hairline text-[13px] text-muted2
                                   hover:border-hairline-strong transition-colors">
                  {adding === p.id ? "Cancel" : "Include something"}
                </button>
              </div>

              {p.inclusions.length === 0 ? (
                <p className="px-5 py-4 text-[13px] text-faint">
                  Nothing included yet — this package sells the room alone.
                </p>
              ) : (
                <ul className="divide-y divide-hairline">
                  {p.inclusions.map((i) => (
                    <li key={i.id} className="px-5 py-3 flex items-center gap-3">
                      <span className="tabular-nums text-ink text-[15px] w-8">{i.quantity}×</span>
                      <span className="text-[14px] text-ink flex-1">
                        {i.scope === "outlet"
                          ? `anything at ${outletName(i.outlet_id)}`
                          : `${i.ref_id} at ${outletName(i.outlet_id)}`}
                      </span>
                      <span className="text-[12px] text-faint">{periodLabel(i.period)}</span>
                    </li>
                  ))}
                </ul>
              )}

              {adding === p.id && (
                <div className="px-5 py-4 border-t border-hairline bg-ground/50">
                  <div className="flex flex-wrap gap-3 items-end">
                    <label className="min-w-[9rem]">
                      <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-2">
                        How many
                      </span>
                      <input type="number" min="1" value={form.quantity}
                             onChange={(e) => setForm((f) => ({ ...f, quantity: e.target.value }))}
                             className="w-full bg-surface border border-hairline rounded px-3 text-[15px] text-ink" />
                    </label>
                    <label className="min-w-[12rem] flex-1">
                      <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-2">
                        Where
                      </span>
                      <select value={form.outlet_id}
                              onChange={(e) => setForm((f) => ({ ...f, outlet_id: e.target.value }))}
                              className="w-full bg-surface border border-hairline rounded px-3 text-[15px] text-ink">
                        {outlets.map((o) => (
                          <option key={o.id} value={o.id}>{o.name}</option>
                        ))}
                      </select>
                    </label>
                    <label className="min-w-[12rem]">
                      <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-2">
                        How often
                      </span>
                      <select value={form.period}
                              onChange={(e) => setForm((f) => ({ ...f, period: e.target.value }))}
                              className="w-full bg-surface border border-hairline rounded px-3 text-[15px] text-ink">
                        {PERIODS.map((x) => (
                          <option key={x.value} value={x.value}>{x.label}</option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="flex flex-wrap gap-2 mt-4">
                    {SCOPES.map((sc) => (
                      <button key={sc.value} type="button"
                              onClick={() => setForm((f) => ({ ...f, scope: sc.value }))}
                              aria-pressed={form.scope === sc.value}
                              title={sc.hint}
                              className={`px-4 rounded border text-[13px] transition-colors
                                ${form.scope === sc.value
                                  ? "border-brass bg-brass/10 text-brass"
                                  : "border-hairline text-muted2 hover:border-hairline-strong"}`}>
                        {sc.label}
                      </button>
                    ))}
                  </div>

                  {form.scope !== "outlet" && (
                    <input value={form.ref_id}
                           onChange={(e) => setForm((f) => ({ ...f, ref_id: e.target.value }))}
                           placeholder={form.scope === "category"
                             ? "Category name, exactly as it appears on the menu"
                             : "Menu item id"}
                           className="mt-3 w-full bg-surface border border-hairline rounded px-3 text-[15px] text-ink" />
                  )}

                  <button onClick={() => addInclusion(p.id)}
                          className="mt-4 inline-flex items-center gap-2 px-5 rounded bg-brass
                                     hover:bg-brass-deep text-on-brass text-[13px] font-medium transition-colors">
                    <Plus className="h-4 w-4" aria-hidden="true" /> Include it
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
