import React, { useEffect, useState } from "react";
import { api, currency } from "@/lib/api";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

export default function MenuManage() {
  const [items, setItems] = useState([]);
  const [form, setForm] = useState({ name: "", category: "Cocktails", price: 10, station: "bar", description: "", image: "", available: true });

  const load = () => api.get("/menu").then((r) => setItems(r.data));
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    try {
      await api.post("/menu", { ...form, price: Number(form.price) });
      toast.success("Menu item added");
      setForm({ name: "", category: "Cocktails", price: 10, station: "bar", description: "", image: "", available: true });
      load();
    } catch {
      toast.error("Failed");
    }
  };

  const del = async (id) => {
    await api.delete(`/menu/${id}`);
    load();
  };

  const toggle = async (m) => {
    await api.put(`/menu/${m.id}`, { ...m, available: !m.available });
    load();
  };

  const grouped = items.reduce((acc, m) => { (acc[m.category] = acc[m.category] || []).push(m); return acc; }, {});

  return (
    <div className="p-6 md:p-10">
      <div>
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-2">Menu</div>
        <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">Manage</h1>
      </div>

      <form onSubmit={add} className="mt-8 border border-stone-800 bg-stone-900/40 p-4 grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
        <div className="col-span-2">
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Name</label>
          <input data-testid="menu-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="bg-transparent border-b border-stone-700 focus-neon py-1 w-full" />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Category</label>
          <input data-testid="menu-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="bg-transparent border-b border-stone-700 focus-neon py-1 w-full" />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Price</label>
          <input data-testid="menu-price" type="number" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} className="bg-transparent border-b border-stone-700 focus-neon py-1 w-full" />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Station</label>
          <select value={form.station} onChange={(e) => setForm({ ...form, station: e.target.value })} className="bg-stone-900 border border-stone-700 py-1 px-2 text-sm w-full">
            <option value="bar">Bar</option>
            {/* Value stays "kitchen" — it is stored on every menu item and order line,
                and the KOT board routes on it. Only the label is the hotel's word. */}
            <option value="kitchen">Restaurant</option>
          </select>
        </div>
        <button data-testid="menu-add" className="rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 py-2 px-4 text-[10px] font-mono uppercase tracking-widest flex items-center justify-center gap-2">
          <Plus size={12} /> Add
        </button>
        <div className="col-span-2 md:col-span-4">
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Image URL (optional)</label>
          <input
            data-testid="menu-image"
            value={form.image}
            onChange={(e) => setForm({ ...form, image: e.target.value })}
            placeholder="https://images.unsplash.com/..."
            className="bg-transparent border-b border-stone-700 focus-neon py-1 w-full text-xs"
          />
        </div>
        <div className="col-span-2 md:col-span-2 flex items-center gap-3">
          {form.image ? (
            <div className="w-16 h-16 border border-stone-800 overflow-hidden bg-stone-900">
              <img src={form.image} alt="preview" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
            </div>
          ) : (
            <div className="w-16 h-16 border border-dashed border-stone-800 flex items-center justify-center text-stone-600 text-[9px] font-mono uppercase tracking-widest">
              Preview
            </div>
          )}
          <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500">
            Live preview
          </div>
        </div>
      </form>

      <div className="mt-10 space-y-10">
        {Object.entries(grouped).map(([cat, list]) => (
          <div key={cat}>
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-stone-500 mb-3">— {cat}</div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {list.map((m) => (
                <div key={m.id} className={`border ${m.available ? "border-stone-800" : "border-stone-800 opacity-50"} bg-stone-900/40 overflow-hidden`} data-testid={`menu-mgr-${m.name.replace(/\s+/g,"-")}`}>
                  {m.image && (
                    <div className="aspect-[16/9] bg-stone-900 overflow-hidden">
                      <img src={m.image} alt={m.name} loading="lazy" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
                    </div>
                  )}
                  <div className="p-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="font-medium">{m.name}</div>
                        <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500 mt-1">{m.station}</div>
                      </div>
                      <div className="font-mono text-orange-400">{currency(m.price)}</div>
                    </div>
                    {m.description && <div className="text-xs text-stone-400 mt-2">{m.description}</div>}
                    <div className="mt-4 flex items-center justify-between">
                      <button onClick={() => toggle(m)} className={`text-[10px] font-mono uppercase tracking-widest ${m.available ? "text-green-400" : "text-stone-500"}`}>
                        {m.available ? "Available" : "Hidden · tap to show"}
                      </button>
                      <button onClick={() => del(m.id)} className="text-stone-500 hover:text-red-500"><Trash2 size={14} /></button>
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
