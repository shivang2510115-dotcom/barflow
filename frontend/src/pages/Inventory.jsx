import React, { useEffect, useState } from "react";
import { api, currency } from "@/lib/api";
import { Plus, Minus, Trash2, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/contexts/AuthContext";
import InventoryImport from "@/components/app/InventoryImport";

export default function Inventory() {
  const { user } = useAuth();
  const canEdit = ["admin", "manager"].includes(user?.role);
  // Not `canEdit`. Creating and repricing stock items is admin-only on the API
  // (routers/inventory.py uses require_configuration), and an import creates and reprices
  // two hundred of them at once. A manager reads this screen and adjusts stock on a
  // shift; showing them an upload the server would refuse is offering a door that does
  // not open.
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState([]);
  const [form, setForm] = useState({ name: "", unit: "bottle", stock: 0, threshold: 5, cost_per_unit: 0, category: "spirits" });

  const load = () => api.get("/inventory").then((r) => setItems(r.data));
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    try {
      await api.post("/inventory", { ...form, stock: Number(form.stock), threshold: Number(form.threshold), cost_per_unit: Number(form.cost_per_unit) });
      toast.success("Added");
      setForm({ name: "", unit: "bottle", stock: 0, threshold: 5, cost_per_unit: 0, category: "spirits" });
      load();
    } catch {
      toast.error("Failed");
    }
  };

  const adjust = async (id, delta) => {
    await api.post(`/inventory/${id}/adjust`, { delta });
    load();
  };

  const del = async (id) => {
    await api.delete(`/inventory/${id}`);
    load();
  };

  return (
    <div className="p-6 md:p-10">
      <div>
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass mb-2">Stock</div>
        <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">Inventory</h1>
      </div>

      {canEdit && (
        <form onSubmit={add} className="mt-8 border border-hairline bg-surface/40 p-4 grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
          {[
            ["name", "Name", "text"],
            ["stock", "Stock", "number"],
            ["threshold", "Alert @", "number"],
            ["cost_per_unit", "Cost", "number"],
          ].map(([k, l, type]) => (
            <div key={k}>
              <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">{l}</label>
              <input
                data-testid={`inv-${k}`}
                type={type}
                value={form[k]}
                onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                className="bg-transparent border-b border-hairline-strong focus-neon py-1 w-full"
              />
            </div>
          ))}
          <div>
            <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">Unit</label>
            <select value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} className="bg-surface border border-hairline-strong py-1 px-2 text-sm w-full">
              <option>bottle</option><option>keg</option><option>case</option><option>kg</option><option>litre</option>
            </select>
          </div>
          <button data-testid="inv-add" className="rounded-full bg-brass hover:bg-brass-deep text-on-brass py-2 px-4 text-[10px] font-mono uppercase tracking-widest">
            Add Stock
          </button>
        </form>
      )}

      {isAdmin && <InventoryImport items={items} onApplied={load} />}

      <div className="mt-8 border border-hairline bg-surface/40 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-hairline text-[10px] uppercase tracking-widest font-mono text-faint">
              <th className="p-3">Item</th>
              <th className="p-3">Category</th>
              <th className="p-3">Stock</th>
              <th className="p-3">Threshold</th>
              <th className="p-3">Cost</th>
              <th className="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const low = it.stock <= it.threshold;
              return (
                <tr key={it.id} className="border-b border-hairline/60" data-testid={`inv-row-${it.name.replace(/\s+/g,"-")}`}>
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      {low && <AlertTriangle size={14} className="text-yellow-500" />}
                      {it.name}
                    </div>
                  </td>
                  <td className="p-3 font-mono text-xs uppercase tracking-widest text-faint">{it.category}</td>
                  <td className={`p-3 font-mono ${low ? "text-yellow-400" : "text-ink"}`}>{it.stock} <span className="text-faint text-xs">{it.unit}</span></td>
                  <td className="p-3 font-mono text-muted2">{it.threshold}</td>
                  {/* Rupees, through currency(), like the rest of the app. This column
                      was printing a dollar sign at a product sold in India. */}
                  <td className="p-3 font-mono text-muted2">{currency(it.cost_per_unit)}</td>
                  <td className="p-3">
                    <div className="flex items-center gap-2 justify-end">
                      <button onClick={() => adjust(it.id, -1)} data-testid={`inv-dec-${it.name.replace(/\s+/g,"-")}`} className="w-7 h-7 border border-hairline-strong hover:border-brass flex items-center justify-center"><Minus size={12} /></button>
                      <button onClick={() => adjust(it.id, +1)} data-testid={`inv-inc-${it.name.replace(/\s+/g,"-")}`} className="w-7 h-7 border border-hairline-strong hover:border-brass flex items-center justify-center"><Plus size={12} /></button>
                      {canEdit && (
                        <button onClick={() => del(it.id)} className="text-faint hover:text-red-500 ml-1"><Trash2 size={14} /></button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {items.length === 0 && (
              <tr><td colSpan={6} className="p-8 text-center text-faint text-sm font-mono uppercase tracking-widest">No inventory yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
