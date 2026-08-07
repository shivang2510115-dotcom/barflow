import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { QrCode, Plus, Trash2, ExternalLink } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";

const STATUS_COLORS = {
  free: "border-stone-700 text-stone-300 hover:border-emerald-500/70",
  occupied: "border-orange-500 text-orange-400 neon-pulse",
  billed: "border-yellow-500 text-yellow-400 shimmer",
  reserved: "border-blue-500 text-blue-400 breathe",
};

const STATUS_DOT = {
  free: "bg-emerald-400",
  occupied: "bg-orange-500",
  billed: "bg-yellow-400",
  reserved: "bg-blue-400",
};

export default function Tables() {
  const { user } = useAuth();
  const [tables, setTables] = useState([]);
  const [qrFor, setQrFor] = useState(null);
  const [newLabel, setNewLabel] = useState("");
  const [zone, setZone] = useState("Bar");
  const canEdit = ["admin", "manager"].includes(user?.role);

  const load = () => api.get("/tables").then((r) => setTables(r.data));
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!newLabel.trim()) return;
    try {
      await api.post("/tables", { label: newLabel, zone, capacity: 4 });
      setNewLabel("");
      toast.success("Table added");
      load();
    } catch {
      toast.error("Could not add table");
    }
  };

  const del = async (id) => {
    try {
      await api.delete(`/tables/${id}`);
      toast.success("Table removed");
      load();
    } catch {
      toast.error("Delete failed");
    }
  };

  const grouped = tables.reduce((acc, t) => {
    (acc[t.zone] = acc[t.zone] || []).push(t);
    return acc;
  }, {});

  const publicUrl = (id) => `${window.location.origin}/t/${id}`;

  return (
    <div className="p-6 md:p-10">
      <div className="flex items-baseline justify-between flex-wrap gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-2">Floor</div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">Tables</h1>
        </div>
        <div className="flex items-center gap-4 text-xs font-mono uppercase tracking-widest text-stone-500">
          <LegendDot color="bg-stone-700" label="Free" />
          <LegendDot color="bg-orange-500" label="Occupied" />
          <LegendDot color="bg-yellow-500" label="Billed" />
        </div>
      </div>

      {canEdit && (
        <form onSubmit={add} className="mt-8 flex flex-wrap gap-3 items-end border border-stone-800 bg-stone-900/40 p-4">
          <div>
            <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Label</label>
            <input
              data-testid="new-table-label"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              placeholder="T10"
              className="bg-transparent border-b border-stone-700 focus-neon py-1 px-1"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Zone</label>
            <select
              value={zone}
              onChange={(e) => setZone(e.target.value)}
              className="bg-stone-900 border border-stone-700 py-1 px-2 text-sm"
            >
              <option>Bar</option>
              <option>Lounge</option>
              <option>Patio</option>
              <option>VIP</option>
            </select>
          </div>
          <button data-testid="add-table-btn" className="rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-5 py-2 font-mono uppercase tracking-widest text-xs flex items-center gap-2">
            <Plus size={14} /> Add Table
          </button>
        </form>
      )}

      {Object.entries(grouped).map(([z, list]) => (
        <div key={z} className="mt-10">
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-stone-500 mb-3">— {z}</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {list.map((t, idx) => (
              <div
                key={t.id}
                data-testid={`table-card-${t.label}`}
                className={`relative border ${STATUS_COLORS[t.status]} bg-stone-900/40 p-4 flex flex-col justify-between min-h-[130px] transition-all duration-200 hover:-translate-y-1 hover:bg-stone-900/70 hover:shadow-[0_10px_30px_-15px_rgba(234,88,12,0.4)]`}
                style={{ animationDelay: `${idx * 40}ms` }}
              >
                <div className="absolute top-2 right-2 flex items-center gap-1.5">
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${STATUS_DOT[t.status]}`} />
                </div>
                <div className="flex items-start justify-between">
                  <div>
                    <div className="font-display text-2xl tracking-tight">{t.label}</div>
                    <div className="text-[10px] uppercase tracking-widest font-mono text-stone-500 mt-1">
                      Seats {t.capacity}
                    </div>
                  </div>
                  <button
                    data-testid={`qr-btn-${t.label}`}
                    onClick={() => setQrFor(t)}
                    className="text-stone-500 hover:text-orange-400 transition-colors"
                    title="Show QR"
                  >
                    <QrCode size={16} />
                  </button>
                </div>
                <div className="mt-3 flex items-center justify-between">
                  <div className="text-[10px] uppercase tracking-widest font-mono">{t.status}</div>
                  <div className="flex items-center gap-2">
                    <Link
                      to={`/app/pos/${t.id}`}
                      data-testid={`open-pos-${t.label}`}
                      className="text-[10px] font-mono uppercase tracking-widest text-orange-400 hover:text-orange-300"
                    >
                      Bill →
                    </Link>
                    {canEdit && (
                      <button onClick={() => del(t.id)} className="text-stone-600 hover:text-red-500">
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      {qrFor && (
        <Dialog open={!!qrFor} onOpenChange={(open) => !open && setQrFor(null)}>
          <DialogContent
            data-testid="qr-modal"
            className="bg-stone-900 border border-stone-800 rounded-none p-8 max-w-sm text-stone-100"
          >
            <DialogTitle className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 font-normal">QR Menu · Table {qrFor.label}</DialogTitle>
            <DialogDescription className="sr-only">Scan this QR code to open the guest menu for this table.</DialogDescription>
            <div className="font-display text-3xl mt-1">{qrFor.label}</div>
            <div className="bg-white p-4 mt-6 flex items-center justify-center">
              <img
                alt="QR"
                src={`https://api.qrserver.com/v1/create-qr-code/?size=260x260&margin=0&data=${encodeURIComponent(publicUrl(qrFor.id))}`}
                className="w-56 h-56"
              />
            </div>
            <div className="font-mono text-[10px] text-stone-500 uppercase tracking-widest mt-4 break-all">
              {publicUrl(qrFor.id)}
            </div>
            <div className="mt-6 flex gap-3">
              <a
                href={publicUrl(qrFor.id)}
                target="_blank"
                rel="noreferrer"
                className="flex-1 inline-flex items-center justify-center gap-2 border border-stone-700 hover:border-orange-500 py-2 text-[10px] font-mono uppercase tracking-widest"
              >
                Open Menu <ExternalLink size={12} />
              </a>
              <button
                data-testid="qr-modal-close"
                onClick={() => setQrFor(null)}
                className="flex-1 rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 py-2 text-[10px] font-mono uppercase tracking-widest"
              >
                Close
              </button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

function LegendDot({ color, label }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`inline-block w-2 h-2 ${color}`} />
      {label}
    </div>
  );
}
