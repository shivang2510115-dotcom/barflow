import React, { useEffect, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { priceLabel, variantsOf } from "@/lib/menu";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

const BLANK = { name: "", category: "Cocktails", price: 10, station: "bar", description: "", image: "", available: true, variants: [] };

/**
 * Rows the admin types portions into: the hotel's own word and what that portion costs.
 *
 * Nothing here knows what a portion should be called. Half/Full on a north Indian card,
 * Small/Large on a coffee list, 30ml/60ml over a bar — the pair is never suggested,
 * defaulted or validated against, because it is the hotel's language and not ours.
 */
function VariantRows({ variants, onChange, idPrefix }) {
  const set = (i, patch) => onChange(variants.map((v, n) => (n === i ? { ...v, ...patch } : v)));
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-2">
        Portions <span className="text-faint normal-case tracking-normal">(optional · leave empty for one price)</span>
      </div>
      <div className="space-y-2">
        {variants.map((v, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              data-testid={`${idPrefix}-variant-label-${i}`}
              value={v.label}
              onChange={(e) => set(i, { label: e.target.value })}
              placeholder="Half"
              className="bg-transparent border-b border-hairline-strong focus-neon py-1 flex-1 text-sm"
            />
            <input
              data-testid={`${idPrefix}-variant-price-${i}`}
              type="number"
              min={0}
              value={v.price}
              onChange={(e) => set(i, { price: e.target.value })}
              placeholder="379"
              className="bg-transparent border-b border-hairline-strong focus-neon py-1 w-28 text-sm text-right"
            />
            <button
              type="button"
              data-testid={`${idPrefix}-variant-del-${i}`}
              onClick={() => onChange(variants.filter((_, n) => n !== i))}
              className="text-faint hover:text-red-500 shrink-0"
              title="Remove this portion"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        data-testid={`${idPrefix}-variant-add`}
        onClick={() => onChange([...variants, { label: "", price: "" }])}
        className="mt-2 text-[10px] font-mono uppercase tracking-widest text-brass hover:text-brass flex items-center gap-1"
      >
        <Plus size={11} /> Add a portion
      </button>
      {variants.length > 0 && (
        <div className="mt-2 text-[10px] font-mono uppercase tracking-widest text-faint">
          The card and the till show the first portion's price
        </div>
      )}
    </div>
  );
}

/** Typed rows into what the API stores. Blank rows are dropped rather than refused —
 *  an admin who tapped "Add a portion" and changed their mind has not made an error. */
function cleanVariants(variants) {
  return variants
    .filter((v) => String(v.label ?? "").trim() !== "")
    .map((v) => ({ label: String(v.label).trim(), price: Number(v.price) || 0 }));
}

export default function MenuManage() {
  const [items, setItems] = useState([]);
  const [form, setForm] = useState(BLANK);
  // The item open for editing and the draft of it. Only one at a time: this is a
  // settings screen, not a spreadsheet.
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState(null);
  // Removing a dish is not undoable, so it asks first — the same inline two-step this
  // codebase uses for cancelling a booking and voiding a folio line.
  const [confirmDelete, setConfirmDelete] = useState(null);

  const load = () => api.get("/menu").then((r) => setItems(r.data));
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    try {
      await api.post("/menu", { ...form, price: Number(form.price), variants: cleanVariants(form.variants) });
      toast.success("Menu item added");
      setForm(BLANK);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  const del = async (id) => {
    try {
      await api.delete(`/menu/${id}`);
      setConfirmDelete(null);
      toast.success("Removed from the menu");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  const toggle = async (m) => {
    await api.put(`/menu/${m.id}`, { ...m, available: !m.available });
    load();
  };

  // One editor for the whole item rather than a portions-only one: a price typo and a
  // wrong photograph are the same job to whoever is fixing the card, and sending them to
  // two different places to do it is how one of them stays wrong.
  const openEdit = (m) => {
    setConfirmDelete(null);
    setEditing(m.id);
    setDraft({ ...m, variants: variantsOf(m).map((v) => ({ ...v })) });
  };

  const saveEdit = async (m) => {
    if (!draft.name.trim()) return toast.error("An item needs a name");
    if (!draft.category.trim()) return toast.error("An item needs a category");
    try {
      // The whole item goes back, portions included: adding, editing and removing are one
      // write, and the server is what decides `price` from what comes out of it.
      await api.put(`/menu/${m.id}`, {
        ...draft,
        price: Number(draft.price) || 0,
        variants: cleanVariants(draft.variants || []),
      });
      toast.success("Saved");
      setEditing(null);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  const grouped = items.reduce((acc, m) => { (acc[m.category] = acc[m.category] || []).push(m); return acc; }, {});

  return (
    <div className="p-6 md:p-10">
      <div>
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass mb-2">Menu</div>
        <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">Manage</h1>
      </div>

      <form onSubmit={add} className="mt-8 border border-hairline bg-surface/40 p-4 grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
        <div className="col-span-2">
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">Name</label>
          <input data-testid="menu-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="bg-transparent border-b border-hairline-strong focus-neon py-1 w-full" />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">Category</label>
          <input data-testid="menu-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="bg-transparent border-b border-hairline-strong focus-neon py-1 w-full" />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">Price</label>
          <input data-testid="menu-price" type="number" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} className="bg-transparent border-b border-hairline-strong focus-neon py-1 w-full" />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">Station</label>
          <select value={form.station} onChange={(e) => setForm({ ...form, station: e.target.value })} className="bg-surface border border-hairline-strong py-1 px-2 text-sm w-full">
            <option value="bar">Bar</option>
            {/* Value stays "kitchen" — it is stored on every menu item and order line,
                and the KOT board routes on it. Only the label is the hotel's word. */}
            <option value="kitchen">Restaurant</option>
          </select>
        </div>
        <button data-testid="menu-add" className="rounded-full bg-brass hover:bg-brass-deep text-on-brass py-2 px-4 text-[10px] font-mono uppercase tracking-widest flex items-center justify-center gap-2">
          <Plus size={12} /> Add
        </button>
        <div className="col-span-2 md:col-span-4">
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">Image URL (optional)</label>
          <input
            data-testid="menu-image"
            value={form.image}
            onChange={(e) => setForm({ ...form, image: e.target.value })}
            placeholder="https://images.unsplash.com/..."
            className="bg-transparent border-b border-hairline-strong focus-neon py-1 w-full text-xs"
          />
        </div>
        <div className="col-span-2 md:col-span-2 flex items-center gap-3">
          {form.image ? (
            <div className="w-16 h-16 border border-hairline overflow-hidden bg-surface">
              <img src={form.image} alt="preview" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
            </div>
          ) : (
            <div className="w-16 h-16 border border-dashed border-hairline flex items-center justify-center text-faint text-[9px] font-mono uppercase tracking-widest">
              Preview
            </div>
          )}
          <div className="text-[10px] font-mono uppercase tracking-widest text-faint">
            Live preview
          </div>
        </div>
        <div className="col-span-2 md:col-span-6 border-t border-hairline pt-4">
          <VariantRows
            idPrefix="menu-new"
            variants={form.variants}
            onChange={(variants) => setForm({ ...form, variants })}
          />
        </div>
      </form>

      <div className="mt-10 space-y-10">
        {Object.entries(grouped).map(([cat, list]) => (
          <div key={cat}>
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-faint mb-3">— {cat}</div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {list.map((m) => (
                <div key={m.id} className={`border ${m.available ? "border-hairline" : "border-hairline opacity-50"} bg-surface/40 overflow-hidden`} data-testid={`menu-mgr-${m.name.replace(/\s+/g,"-")}`}>
                  {m.image && (
                    <div className="aspect-[16/9] bg-surface overflow-hidden">
                      <img src={m.image} alt={m.name} loading="lazy" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
                    </div>
                  )}
                  <div className="p-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="font-medium">{m.name}</div>
                        <div className="text-[10px] font-mono uppercase tracking-widest text-faint mt-1">{m.station}</div>
                      </div>
                      <div className="font-mono text-brass">{priceLabel(m)}</div>
                    </div>
                    {m.description && <div className="text-xs text-muted2 mt-2">{m.description}</div>}

                    {variantsOf(m).length > 0 && editing !== m.id && (
                      <ul className="mt-3 space-y-1" data-testid={`menu-portions-${m.name.replace(/\s+/g,"-")}`}>
                        {variantsOf(m).map((v) => (
                          <li key={v.label} className="flex items-center justify-between text-xs">
                            <span className="font-mono uppercase tracking-widest text-muted2">{v.label}</span>
                            <span className="font-mono text-muted2">{currency(v.price)}</span>
                          </li>
                        ))}
                      </ul>
                    )}

                    {editing === m.id && draft && (
                      <div className="mt-4 border-t border-hairline pt-4 space-y-3">
                        {[
                          ["name", "Name", "text"],
                          ["category", "Category", "text"],
                          ["image", "Image URL", "text"],
                        ].map(([k, label, type]) => (
                          <label key={k} className="block">
                            <span className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint">{label}</span>
                            <input
                              type={type}
                              data-testid={`menu-edit-${k}-${m.id}`}
                              value={draft[k] ?? ""}
                              onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                              className="mt-1 w-full bg-transparent border-b border-hairline-strong py-1.5 text-sm focus:border-brass focus:outline-none"
                            />
                          </label>
                        ))}

                        <label className="block">
                          <span className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint">Description</span>
                          <textarea
                            data-testid={`menu-edit-description-${m.id}`}
                            rows={2}
                            value={draft.description ?? ""}
                            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                            className="mt-1 w-full bg-transparent border-b border-hairline-strong py-1.5 text-sm focus:border-brass focus:outline-none resize-none"
                          />
                        </label>

                        <div className="flex gap-3">
                          <label className="flex-1">
                            {/* Ignored once portions exist — the server takes `price` from
                                the first of them — so it says so rather than sitting there
                                looking editable and doing nothing. */}
                            <span className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint">
                              Price {cleanVariants(draft.variants || []).length > 0 && (
                                <span className="text-faint normal-case tracking-normal">· set by portions</span>
                              )}
                            </span>
                            <input
                              type="number"
                              min="0"
                              step="1"
                              disabled={cleanVariants(draft.variants || []).length > 0}
                              data-testid={`menu-edit-price-${m.id}`}
                              value={draft.price ?? 0}
                              onChange={(e) => setDraft({ ...draft, price: e.target.value })}
                              className="mt-1 w-full bg-transparent border-b border-hairline-strong py-1.5 text-sm focus:border-brass focus:outline-none disabled:opacity-40"
                            />
                          </label>
                          <label className="flex-1">
                            <span className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint">Station</span>
                            <select
                              data-testid={`menu-edit-station-${m.id}`}
                              value={draft.station}
                              onChange={(e) => setDraft({ ...draft, station: e.target.value })}
                              className="mt-1 w-full bg-ground border border-hairline-strong py-1.5 px-2 text-sm focus:border-brass focus:outline-none"
                            >
                              <option value="bar">Bar</option>
                              <option value="kitchen">Restaurant</option>
                            </select>
                          </label>
                        </div>

                        {draft.image && (
                          <div className="aspect-[16/9] bg-surface overflow-hidden border border-hairline">
                            <img src={draft.image} alt="" className="w-full h-full object-cover"
                                 onError={(e) => { e.target.style.display = "none"; }} />
                          </div>
                        )}

                        <div className="pt-1">
                          <VariantRows
                            idPrefix={`menu-edit-${m.id}`}
                            variants={draft.variants || []}
                            onChange={(v) => setDraft({ ...draft, variants: v })}
                          />
                        </div>

                        <div className="flex items-center gap-2 pt-1">
                          <button
                            data-testid={`menu-edit-save-${m.id}`}
                            onClick={() => saveEdit(m)}
                            className="rounded-full bg-brass hover:bg-brass-deep text-on-brass py-1.5 px-4 text-[10px] font-mono uppercase tracking-widest"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => { setEditing(null); setDraft(null); }}
                            className="text-[10px] font-mono uppercase tracking-widest text-faint hover:text-muted2"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

                    {confirmDelete === m.id && (
                      <div className="mt-4 border-t border-red-500/30 pt-4">
                        <p className="text-xs text-muted2">
                          Remove <span className="text-ink">{m.name}</span> from the menu?
                          Bills that already contain it keep it and their totals do not change.
                        </p>
                        <div className="mt-3 flex items-center gap-2">
                          <button
                            data-testid={`menu-delete-confirm-${m.id}`}
                            onClick={() => del(m.id)}
                            className="rounded-full border border-red-500/40 text-red-400 hover:bg-red-500/10 py-1.5 px-4 text-[10px] font-mono uppercase tracking-widest"
                          >
                            Remove
                          </button>
                          <button
                            onClick={() => setConfirmDelete(null)}
                            className="text-[10px] font-mono uppercase tracking-widest text-faint hover:text-muted2"
                          >
                            Keep it
                          </button>
                        </div>
                      </div>
                    )}

                    <div className="mt-4 flex items-center justify-between gap-3">
                      <button onClick={() => toggle(m)} className={`text-[10px] font-mono uppercase tracking-widest ${m.available ? "text-green-400" : "text-faint"}`}>
                        {m.available ? "Available" : "Hidden · tap to show"}
                      </button>
                      <div className="flex items-center gap-3">
                        {editing !== m.id && (
                          <button
                            data-testid={`menu-edit-${m.name.replace(/\s+/g,"-")}`}
                            onClick={() => openEdit(m)}
                            className="text-[10px] font-mono uppercase tracking-widest text-faint hover:text-brass"
                          >
                            Edit
                          </button>
                        )}
                        <button
                          data-testid={`menu-delete-${m.id}`}
                          onClick={() => { setEditing(null); setConfirmDelete(m.id); }}
                          className="text-faint hover:text-red-500"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
