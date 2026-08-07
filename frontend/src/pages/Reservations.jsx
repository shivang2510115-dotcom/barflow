import React, { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { CalendarClock, Plus, Users, Phone, Check, X, Clock, Trash2 } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";

const STATUS_STYLES = {
  booked: "border-blue-500 text-blue-400 breathe",
  seated: "border-orange-500 text-orange-400 neon-pulse",
  no_show: "border-stone-700 text-stone-500",
  cancelled: "border-stone-800 text-stone-600 line-through",
};

const STATUS_DOT = {
  booked: "bg-blue-400",
  seated: "bg-orange-500",
  no_show: "bg-stone-500",
  cancelled: "bg-stone-700",
};

const STATUS_LABEL = {
  booked: "Booked",
  seated: "Seated",
  no_show: "No-show",
  cancelled: "Cancelled",
};

function todayStr() {
  const d = new Date();
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60000).toISOString().slice(0, 10);
}

export default function Reservations() {
  const { user } = useAuth();
  const canManage = ["admin", "manager", "waiter"].includes(user?.role);

  const [date, setDate] = useState(todayStr());
  const [reservations, setReservations] = useState([]);
  const [tables, setTables] = useState([]);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState({
    guest_name: "",
    phone: "",
    party_size: 2,
    time: "19:00",
    table_id: "",
    hold_table: true,
    notes: "",
  });

  const load = () =>
    api.get("/reservations", { params: { date } }).then((r) => setReservations(r.data));

  useEffect(() => {
    load();
  }, [date]);

  useEffect(() => {
    api.get("/tables").then((r) => setTables(r.data)).catch(() => {});
  }, []);

  const reset = () =>
    setForm({
      guest_name: "",
      phone: "",
      party_size: 2,
      time: "19:00",
      table_id: "",
      hold_table: true,
      notes: "",
    });

  const create = async (e) => {
    e.preventDefault();
    if (!form.guest_name.trim()) return toast.error("Guest name required");
    setSaving(true);
    try {
      const selected = tables.find((t) => t.id === form.table_id);
      await api.post("/reservations", {
        guest_name: form.guest_name.trim(),
        phone: form.phone.trim() || null,
        party_size: Number(form.party_size) || 1,
        date,
        time: form.time,
        table_id: form.table_id || null,
        table_label: selected ? selected.label : null,
        hold_table: !!form.table_id && form.hold_table,
        notes: form.notes.trim() || null,
      });
      toast.success("Reservation booked");
      setOpen(false);
      reset();
      load();
      api.get("/tables").then((r) => setTables(r.data)).catch(() => {});
    } catch {
      toast.error("Could not book reservation");
    } finally {
      setSaving(false);
    }
  };

  const setStatus = async (id, status) => {
    try {
      await api.put(`/reservations/${id}/status`, { status });
      toast.success(`Marked ${STATUS_LABEL[status].toLowerCase()}`);
      load();
      api.get("/tables").then((r) => setTables(r.data)).catch(() => {});
    } catch {
      toast.error("Update failed");
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/reservations/${id}`);
      toast.success("Reservation removed");
      load();
    } catch {
      toast.error("Delete failed");
    }
  };

  const stats = useMemo(() => {
    const active = reservations.filter((r) => r.status === "booked" || r.status === "seated");
    return {
      total: reservations.length,
      covers: active.reduce((s, r) => s + (r.party_size || 0), 0),
      seated: reservations.filter((r) => r.status === "seated").length,
    };
  }, [reservations]);

  const sorted = useMemo(
    () => [...reservations].sort((a, b) => (a.time || "").localeCompare(b.time || "")),
    [reservations]
  );

  return (
    <div className="p-6 md:p-10">
      <div className="flex items-baseline justify-between flex-wrap gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-2">Front of House</div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">Reservations</h1>
        </div>
        <div className="flex items-center gap-3">
          <div>
            <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Date</label>
            <input
              data-testid="reservation-date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="bg-stone-900 border border-stone-700 py-1.5 px-2 text-sm focus-neon"
            />
          </div>
          {canManage && (
            <button
              data-testid="new-reservation-btn"
              onClick={() => setOpen(true)}
              className="rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-5 py-2.5 font-mono uppercase tracking-widest text-xs flex items-center gap-2 self-end"
            >
              <Plus size={14} /> Book
            </button>
          )}
        </div>
      </div>

      {/* Stat strip */}
      <div className="mt-8 grid grid-cols-3 gap-3 max-w-lg">
        <Stat label="Reservations" value={stats.total} />
        <Stat label="Covers" value={stats.covers} accent />
        <Stat label="Seated" value={stats.seated} />
      </div>

      {/* List */}
      <div className="mt-10">
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-stone-500 mb-3">
          — {new Date(date + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
        </div>

        {sorted.length === 0 ? (
          <div className="border border-stone-800 bg-stone-900/40 p-12 text-center">
            <CalendarClock className="mx-auto text-stone-700" size={28} />
            <div className="mt-3 font-mono text-xs uppercase tracking-widest text-stone-500">
              No reservations for this date
            </div>
          </div>
        ) : (
          <div className="border border-stone-800 divide-y divide-stone-800 bg-stone-900/30">
            {sorted.map((r) => (
              <div
                key={r.id}
                data-testid={`reservation-row-${r.id}`}
                className="flex items-center gap-4 p-4 hover:bg-stone-900/60 transition-colors"
              >
                {/* Time block */}
                <div className="w-16 shrink-0 text-center">
                  <div className="font-display text-2xl tracking-tight leading-none">{r.time}</div>
                  <div className="text-[9px] uppercase tracking-widest font-mono text-stone-600 mt-1">
                    {r.table_label ? `Tbl ${r.table_label}` : "Walk-up"}
                  </div>
                </div>

                {/* Status pill */}
                <div className={`shrink-0 border ${STATUS_STYLES[r.status]} px-2.5 py-1 flex items-center gap-1.5`}>
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${STATUS_DOT[r.status]}`} />
                  <span className="text-[10px] font-mono uppercase tracking-widest">{STATUS_LABEL[r.status]}</span>
                </div>

                {/* Guest */}
                <div className="flex-1 min-w-0">
                  <div className="font-display text-lg tracking-tight truncate">{r.guest_name}</div>
                  <div className="flex items-center gap-3 text-[11px] font-mono text-stone-500 mt-0.5">
                    <span className="inline-flex items-center gap-1">
                      <Users size={11} /> {r.party_size}
                    </span>
                    {r.phone && (
                      <span className="inline-flex items-center gap-1">
                        <Phone size={11} /> {r.phone}
                      </span>
                    )}
                    {r.notes && <span className="truncate text-stone-600 italic">“{r.notes}”</span>}
                  </div>
                </div>

                {/* Actions */}
                {canManage && (
                  <div className="flex items-center gap-2 shrink-0">
                    {r.status === "booked" && (
                      <>
                        <button
                          data-testid={`seat-${r.id}`}
                          onClick={() => setStatus(r.id, "seated")}
                          className="inline-flex items-center gap-1 border border-orange-600 text-orange-400 hover:bg-orange-600 hover:text-stone-950 px-3 py-1.5 text-[10px] font-mono uppercase tracking-widest transition-colors"
                        >
                          <Check size={12} /> Seat
                        </button>
                        <button
                          data-testid={`noshow-${r.id}`}
                          onClick={() => setStatus(r.id, "no_show")}
                          title="No-show"
                          className="text-stone-600 hover:text-stone-300 transition-colors"
                        >
                          <Clock size={15} />
                        </button>
                        <button
                          data-testid={`cancel-${r.id}`}
                          onClick={() => setStatus(r.id, "cancelled")}
                          title="Cancel"
                          className="text-stone-600 hover:text-red-500 transition-colors"
                        >
                          <X size={16} />
                        </button>
                      </>
                    )}
                    {(r.status === "cancelled" || r.status === "no_show") &&
                      ["admin", "manager"].includes(user?.role) && (
                        <button
                          onClick={() => remove(r.id)}
                          title="Delete"
                          className="text-stone-700 hover:text-red-500 transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Booking dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          data-testid="reservation-modal"
          className="bg-stone-900 border border-stone-800 rounded-none p-8 max-w-md text-stone-100"
        >
          <DialogTitle className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 font-normal">
            New Reservation
          </DialogTitle>
          <DialogDescription className="sr-only">Book a table reservation for a guest.</DialogDescription>
          <div className="font-display text-3xl mt-1">Book a table</div>

          <form onSubmit={create} className="mt-6 space-y-4">
            <Field label="Guest name">
              <input
                data-testid="res-guest-name"
                value={form.guest_name}
                onChange={(e) => setForm({ ...form, guest_name: e.target.value })}
                placeholder="Jordan Vega"
                className="w-full bg-transparent border-b border-stone-700 focus-neon py-1.5"
                autoFocus
              />
            </Field>

            <div className="grid grid-cols-2 gap-4">
              <Field label="Party size">
                <input
                  data-testid="res-party-size"
                  type="number"
                  min={1}
                  value={form.party_size}
                  onChange={(e) => setForm({ ...form, party_size: e.target.value })}
                  className="w-full bg-transparent border-b border-stone-700 focus-neon py-1.5"
                />
              </Field>
              <Field label="Time">
                <input
                  data-testid="res-time"
                  type="time"
                  value={form.time}
                  onChange={(e) => setForm({ ...form, time: e.target.value })}
                  className="w-full bg-stone-900 border border-stone-700 py-1.5 px-2 text-sm focus-neon"
                />
              </Field>
            </div>

            <Field label="Phone (optional)">
              <input
                data-testid="res-phone"
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
                placeholder="+1 555 0134"
                className="w-full bg-transparent border-b border-stone-700 focus-neon py-1.5"
              />
            </Field>

            <Field label="Assign table (optional)">
              <select
                data-testid="res-table"
                value={form.table_id}
                onChange={(e) => setForm({ ...form, table_id: e.target.value })}
                className="w-full bg-stone-900 border border-stone-700 py-1.5 px-2 text-sm focus-neon"
              >
                <option value="">— None (walk-up) —</option>
                {tables.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label} · {t.zone} · seats {t.capacity}
                  </option>
                ))}
              </select>
            </Field>

            {form.table_id && (
              <label className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-widest text-stone-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.hold_table}
                  onChange={(e) => setForm({ ...form, hold_table: e.target.checked })}
                  className="accent-orange-500"
                />
                Hold table (mark reserved)
              </label>
            )}

            <Field label="Notes (optional)">
              <input
                data-testid="res-notes"
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                placeholder="Window seat, anniversary"
                className="w-full bg-transparent border-b border-stone-700 focus-neon py-1.5"
              />
            </Field>

            <div className="mt-6 flex gap-3">
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="flex-1 border border-stone-700 hover:border-stone-500 py-2.5 text-[10px] font-mono uppercase tracking-widest transition-colors"
              >
                Cancel
              </button>
              <button
                data-testid="res-submit"
                type="submit"
                disabled={saving}
                className="flex-1 rounded-full bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-stone-950 py-2.5 text-[10px] font-mono uppercase tracking-widest transition-colors"
              >
                {saving ? "Booking…" : "Confirm"}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div className="border border-stone-800 bg-stone-900/40 p-4">
      <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500">{label}</div>
      <div className={`font-display text-3xl mt-1 tracking-tight ${accent ? "text-orange-400" : ""}`}>{value}</div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">{label}</label>
      {children}
    </div>
  );
}
