import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Check, Clock, Utensils, Wine, Flame } from "lucide-react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import EmptyState from "@/components/app/EmptyState";

function elapsed(iso) {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const m = Math.floor(s / 60);
  return m < 1 ? `${s}s` : `${m}m ${s % 60}s`;
}

export default function KOT() {
  const [tickets, setTickets] = useState([]);
  const [, setTick] = useState(0);
  const [filter, setFilter] = useState("all");

  const load = () => api.get("/orders/kot").then((r) => setTickets(r.data));

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    const tickIv = setInterval(() => setTick((v) => v + 1), 1000);
    return () => {
      clearInterval(iv);
      clearInterval(tickIv);
    };
  }, []);

  const advance = async (orderId, itemId, current) => {
    const next = current === "pending" ? "preparing" : current === "preparing" ? "ready" : "served";
    try {
      await api.put(`/orders/${orderId}/items/${itemId}/status`, { status: next });
      toast.success(`Marked ${next}`);
      load();
    } catch {
      toast.error("Update failed");
    }
  };

  const shown = tickets
    .map((t) => ({
      ...t,
      items: t.items.filter((i) => filter === "all" || i.station === filter),
    }))
    .filter((t) => t.items.length);

  return (
    <div className="p-6 md:p-10">
      <div className="flex items-baseline justify-between flex-wrap gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-2">Live · Auto refresh</div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">KOT Board</h1>
        </div>
        <div className="flex gap-2">
          {[
            { k: "all", l: "All" },
            { k: "bar", l: "Bar", icon: Wine },
            { k: "kitchen", l: "Restaurant", icon: Utensils },
          ].map((f) => (
            <button
              key={f.k}
              data-testid={`kot-filter-${f.k}`}
              onClick={() => setFilter(f.k)}
              className={`px-4 py-2 text-[10px] font-mono uppercase tracking-widest border flex items-center gap-2 ${
                filter === f.k ? "border-orange-500 text-orange-400" : "border-stone-800 text-stone-400 hover:border-stone-600"
              }`}
            >
              {f.icon && <f.icon size={12} />} {f.l}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4" data-testid="kot-grid">
        <AnimatePresence initial={false}>
        {shown.map((t) => (
          <motion.div
            key={t.order_id}
            layout
            initial={{ opacity: 0, x: 40, scale: 0.98 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: -40 }}
            transition={{ duration: 0.35, ease: "easeOut" }}
            className="border border-stone-800 bg-stone-900/40 p-4 hover:border-orange-500/50 transition-colors"
          >
            <div className="flex items-center justify-between border-b border-stone-800 pb-3 mb-3">
              <div>
                <div className="font-display text-2xl">{t.table_label}</div>
                <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500">
                  #{t.order_id.slice(0, 6)}
                </div>
              </div>
              <div className="flex items-center gap-1 text-orange-400 font-mono text-xs">
                <Clock size={12} /> {elapsed(t.created_at)}
              </div>
            </div>
            <ul className="space-y-3">
              {t.items.map((it) => (
                <li key={it.id} className="flex items-start gap-2">
                  <button
                    data-testid={`advance-${it.id}`}
                    onClick={() => advance(t.order_id, it.id, it.status)}
                    className={`shrink-0 w-6 h-6 border flex items-center justify-center transition-colors active:scale-90 ${
                      it.status === "preparing"
                        ? "border-yellow-500 text-yellow-400"
                        : it.status === "ready"
                        ? "border-green-500 text-green-400"
                        : "border-stone-700 text-stone-500 hover:border-orange-500"
                    }`}
                    title={`Mark next (${it.status})`}
                  >
                    <Check size={12} />
                  </button>
                  <div className="flex-1">
                    <div className="text-sm flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-orange-400 mr-1">{it.quantity}×</span>
                      {it.name}
                      {/* "Butter Chicken" alone tells the kitchen nothing when the dish
                          is cooked half or full. A ticket from before portions existed
                          carries no label and prints exactly as it always did. */}
                      {it.variant_label && (
                        <span
                          data-testid={`kot-portion-${it.id}`}
                          className="text-[10px] font-mono uppercase tracking-widest border border-orange-500 text-orange-400 px-1.5 py-0.5"
                        >
                          {it.variant_label}
                        </span>
                      )}
                      {it.status === "preparing" && (
                        <motion.span
                          animate={{ scale: [1, 1.15, 1], opacity: [0.7, 1, 0.7] }}
                          transition={{ duration: 1.2, repeat: Infinity }}
                          className="text-orange-500"
                        >
                          <Flame size={12} />
                        </motion.span>
                      )}
                    </div>
                    <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500 mt-0.5">
                      {it.station} · {it.status}
                    </div>
                    {it.notes && (
                      <div className="text-[10px] text-stone-400 mt-1 italic">&ldquo;{it.notes}&rdquo;</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </motion.div>
        ))}
        </AnimatePresence>
        {shown.length === 0 && (
          <div className="col-span-full">
            <EmptyState
              title="All caught up."
              subtitle="No open tickets · relax before the rush."
            />
          </div>
        )}
      </div>
    </div>
  );
}
