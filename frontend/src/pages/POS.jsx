import React, { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import axios from "axios";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { Plus, Minus, Trash2, Search } from "lucide-react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import BillSuccess from "@/components/app/BillSuccess";
import EmptyState from "@/components/app/EmptyState";

export default function POS() {
  const { tableId } = useParams();
  const nav = useNavigate();
  const [tables, setTables] = useState([]);
  const [menu, setMenu] = useState([]);
  const [order, setOrder] = useState(null);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("All");
  const [discount, setDiscount] = useState(0);
  const [pay, setPay] = useState("cash");
  const [custName, setCustName] = useState("");
  const [custPhone, setCustPhone] = useState("");
  const [celebrateAmount, setCelebrateAmount] = useState(null);

  // Charge-to-room: pick an in-house guest to bill the order to their folio.
  const [inHouse, setInHouse] = useState([]);
  const [roomQuery, setRoomQuery] = useState("");
  const [debouncedRoomQuery, setDebouncedRoomQuery] = useState("");
  const [chosenFolio, setChosenFolio] = useState(null);

  useEffect(() => {
    api.get("/tables").then((r) => setTables(r.data));
    api.get("/menu").then((r) => setMenu(r.data));
  }, []);

  // Debounce the in-house search input, mirroring Bookings.jsx.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedRoomQuery(roomQuery), 300);
    return () => clearTimeout(t);
  }, [roomQuery]);

  useEffect(() => {
    if (pay !== "room") return;
    const controller = new AbortController();
    api
      .get("/in-house", { params: { q: debouncedRoomQuery }, signal: controller.signal })
      .then((r) => setInHouse(r.data))
      .catch((e) => {
        if (axios.isCancel(e) || e.code === "ERR_CANCELED") return;
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      });
    return () => controller.abort();
  }, [pay, debouncedRoomQuery]);

  useEffect(() => {
    if (pay !== "room") {
      setChosenFolio(null);
      setRoomQuery("");
    }
  }, [pay]);

  const loadOrder = (id) => {
    if (!id) return setOrder(null);
    api.get(`/orders/table/${id}/current`).then((r) => setOrder(r.data));
  };

  useEffect(() => {
    loadOrder(tableId);
  }, [tableId]);

  const cats = useMemo(() => ["All", ...Array.from(new Set(menu.map((m) => m.category)))], [menu]);
  const filtered = menu.filter(
    (m) =>
      m.available &&
      (cat === "All" || m.category === cat) &&
      (q === "" || m.name.toLowerCase().includes(q.toLowerCase()))
  );

  const currentTable = tables.find((t) => t.id === tableId);

  const addItem = async (m) => {
    if (!tableId) return toast.error("Pick a table first");
    try {
      const { data } = await api.post(`/orders/table/${tableId}/items`, {
        items: [{ menu_item_id: m.id, quantity: 1 }],
        source: "pos",
      });
      setOrder(data);
      // refresh tables to reflect occupied status
      api.get("/tables").then((r) => setTables(r.data));
    } catch {
      toast.error("Could not add item");
    }
  };

  const changeQty = async (item, delta) => {
    if (!order) return;
    if (delta > 0) {
      await api.post(`/orders/table/${tableId}/items`, {
        items: [{ menu_item_id: item.menu_item_id, quantity: delta }],
      });
    } else {
      await api.delete(`/orders/${order.id}/items/${item.id}`);
    }
    loadOrder(tableId);
  };

  const settle = async () => {
    if (!order) return;
    // The server 400s on payment_method "room" without a folio_id, but that error
    // isn't self-explanatory mid-service — catch it here instead.
    if (pay === "room" && !chosenFolio) {
      toast.error("Pick the in-house guest to charge");
      return;
    }
    try {
      const finalTotal = (order.subtotal || 0) + (order.tax || 0) - Number(discount || 0);
      await api.post(`/orders/${order.id}/settle`, {
        payment_method: pay,
        discount: Number(discount) || 0,
        customer_name: custName.trim() || null,
        customer_phone: custPhone.trim() || null,
        folio_id: pay === "room" ? chosenFolio.folio.id : undefined,
      });
      toast.success(`Bill settled · ${pay.toUpperCase()}`);
      setCelebrateAmount(Math.max(0, finalTotal));
      setOrder(null);
      setDiscount(0);
      setCustName("");
      setCustPhone("");
      setChosenFolio(null);
      setRoomQuery("");
      api.get("/tables").then((r) => setTables(r.data));
    } catch (e) {
      if (e.response?.status === 409) {
        toast.error("That folio is no longer open — pick another guest");
      } else {
        toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Could not settle bill");
      }
    }
  };

  return (
    <div className="p-4 md:p-6 grid lg:grid-cols-[1fr_420px] gap-4 min-h-screen">
      {/* Left · Menu */}
      <div>
        <div className="flex items-baseline justify-between flex-wrap gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-1">POS</div>
            <h1 className="font-display uppercase text-3xl md:text-4xl leading-none tracking-tight">
              {currentTable ? `Table ${currentTable.label}` : "Pick a table"}
            </h1>
          </div>
          <select
            data-testid="pos-table-select"
            value={tableId || ""}
            onChange={(e) => nav(`/app/pos/${e.target.value}`)}
            className="bg-stone-900 border border-stone-700 py-2 px-3 text-sm"
          >
            <option value="">Select table…</option>
            {tables.map((t) => (
              <option key={t.id} value={t.id}>
                {t.status !== "free"
                  ? `${t.label} · ${t.zone} · ${t.status}`
                  : `${t.label} · ${t.zone}`}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-6 flex items-center gap-3">
          <div className="flex items-center border border-stone-800 px-3 flex-1">
            <Search size={14} className="text-stone-500" />
            <input
              data-testid="menu-search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search drinks & plates…"
              className="bg-transparent px-3 py-2 flex-1 focus:outline-none"
            />
          </div>
        </div>

        <div className="mt-4 flex overflow-x-auto no-scrollbar gap-2">
          {cats.map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              data-testid={`cat-${c.toLowerCase().replace(/\s+/g,"-")}`}
              className={`px-4 py-2 text-[10px] font-mono uppercase tracking-widest whitespace-nowrap border ${
                cat === c ? "border-orange-500 text-orange-400 bg-stone-900" : "border-stone-800 text-stone-400 hover:border-stone-600"
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="mt-4 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
          {filtered.map((m) => (
            <button
              key={m.id}
              data-testid={`menu-item-${m.name.replace(/\s+/g,"-")}`}
              onClick={() => addItem(m)}
              className="text-left border border-stone-800 bg-stone-900/40 hover:border-orange-500 hover:bg-stone-900 overflow-hidden transition-colors active:scale-[0.98]"
            >
              {m.image && (
                <div className="aspect-[16/9] bg-stone-900 overflow-hidden">
                  <img src={m.image} alt="" loading="lazy" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
                </div>
              )}
              <div className="p-4">
                <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500">{m.category}</div>
                <div className="mt-2 font-medium">{m.name}</div>
                <div className="mt-4 flex items-baseline justify-between">
                  <span className="font-mono text-orange-400">{currency(m.price)}</span>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-stone-500">
                    {m.station}
                  </span>
                </div>
              </div>
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="col-span-full text-stone-600 text-sm font-mono uppercase tracking-widest py-10 text-center">
              No matching items
            </div>
          )}
        </div>
      </div>

      {/* Right · Bill */}
      <aside className="border border-stone-800 bg-stone-900/40 p-5 sticky lg:top-4 lg:self-start">
        <div className="flex items-center justify-between mb-4">
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500">Bill</div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500">
            {order ? `Order ${order.id.slice(0, 6)}` : "New"}
          </div>
        </div>

        <div className="min-h-[240px] max-h-[440px] overflow-y-auto divide-y divide-stone-800" data-testid="bill-items">
          {order?.items?.length ? (
            <AnimatePresence initial={false}>
              {order.items.map((it) => (
                <motion.div
                  key={it.id}
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -24 }}
                  transition={{ duration: 0.25 }}
                  className="py-3 flex items-center gap-3"
                >
                <div className="flex-1">
                  <div className="text-sm">{it.name}</div>
                  <div className="text-[10px] font-mono uppercase text-stone-500 mt-0.5">
                    {currency(it.price)} · {it.station} · {it.status}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => changeQty(it, -1)}
                    data-testid={`dec-${it.id}`}
                    className="w-7 h-7 flex items-center justify-center border border-stone-700 hover:border-orange-500 active:scale-95 transition"
                  >
                    <Minus size={12} />
                  </button>
                  <span className="w-6 text-center font-mono">{it.quantity}</span>
                  <button
                    onClick={() => changeQty(it, +1)}
                    data-testid={`inc-${it.id}`}
                    className="w-7 h-7 flex items-center justify-center border border-stone-700 hover:border-orange-500 active:scale-95 transition"
                  >
                    <Plus size={12} />
                  </button>
                </div>
                </motion.div>
              ))}
            </AnimatePresence>
          ) : (
            <EmptyState
              title={tableId ? "Ready to pour." : "Pick a table."}
              subtitle={tableId ? "Add drinks & plates from the left." : "Then start the tab."}
            />
          )}
        </div>

        <div className="mt-4 space-y-1 border-t border-stone-800 pt-4 text-sm font-mono">
          <Row label="Subtotal" value={currency(order?.subtotal || 0)} />
          <Row label="Tax (10%)" value={currency(order?.tax || 0)} />
          <div className="flex items-center justify-between text-stone-400">
            <span className="text-[10px] uppercase tracking-widest">Discount</span>
            <input
              data-testid="discount-input"
              type="number"
              min={0}
              value={discount}
              onChange={(e) => setDiscount(e.target.value)}
              className="w-24 bg-transparent border-b border-stone-700 text-right py-0.5 focus-neon"
            />
          </div>
          <Row label="Total" value={currency((order?.subtotal || 0) + (order?.tax || 0) - Number(discount || 0))} bold />
        </div>

        <div className="mt-4">
          <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-2">
            Customer <span className="text-stone-600 normal-case tracking-normal">(optional)</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              data-testid="cust-name"
              value={custName}
              onChange={(e) => setCustName(e.target.value)}
              placeholder="Name"
              className="bg-transparent border-b border-stone-700 py-1.5 text-sm focus-neon"
            />
            <input
              data-testid="cust-phone"
              value={custPhone}
              onChange={(e) => setCustPhone(e.target.value)}
              placeholder="Phone"
              className="bg-transparent border-b border-stone-700 py-1.5 text-sm focus-neon"
            />
          </div>
        </div>

        <div className="mt-4">
          <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-2">Payment</div>
          <div className="grid grid-cols-4 gap-2">
            {["cash", "card", "online", "room"].map((p) => (
              <button
                key={p}
                onClick={() => setPay(p)}
                data-testid={`pay-${p}`}
                className={`py-2 text-[10px] font-mono uppercase tracking-widest border ${
                  pay === p ? "border-orange-500 text-orange-400" : "border-stone-800 text-stone-400 hover:border-stone-600"
                }`}
              >
                {p}
              </button>
            ))}
          </div>

          {pay === "room" && (
            <div className="mt-3">
              {chosenFolio ? (
                <div className="flex items-center justify-between border border-orange-500/50 bg-orange-500/10 rounded px-3 py-2">
                  <div>
                    <div className="text-sm text-orange-400">
                      Room {chosenFolio.room?.number} · {chosenFolio.guest?.name}
                    </div>
                    <div className="text-[10px] font-mono text-stone-500">{chosenFolio.guest?.phone}</div>
                  </div>
                  <button
                    onClick={() => setChosenFolio(null)}
                    className="text-[10px] font-mono uppercase tracking-widest text-stone-500 hover:text-red-400"
                  >
                    Change
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex items-center border border-stone-800 px-3">
                    <Search size={14} className="text-stone-500" />
                    <input
                      data-testid="room-search"
                      value={roomQuery}
                      onChange={(e) => setRoomQuery(e.target.value)}
                      placeholder="Room number, guest name or phone"
                      className="bg-transparent px-3 py-2 flex-1 text-sm focus:outline-none"
                    />
                  </div>
                  <ul className="mt-2 max-h-40 overflow-y-auto divide-y divide-stone-800">
                    {inHouse.length === 0 && (
                      <li className="py-2 text-xs text-stone-500 font-mono uppercase tracking-widest">
                        No in-house guest matches.
                      </li>
                    )}
                    {inHouse.map((x) => (
                      <li key={x.folio.id}>
                        <button
                          onClick={() => setChosenFolio(x)}
                          className="w-full text-left py-2 text-sm text-stone-300 hover:text-orange-400"
                        >
                          Room {x.room?.number} · {x.guest?.name}
                          <span className="block text-[10px] font-mono text-stone-500">
                            {x.guest?.phone}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>

        <button
          data-testid="settle-btn"
          onClick={settle}
          disabled={!order?.items?.length}
          className="mt-5 w-full rounded-full bg-orange-600 hover:bg-orange-500 disabled:opacity-40 text-stone-950 px-6 py-3 font-mono uppercase tracking-widest text-xs active:scale-95 transition"
        >
          Settle Bill
        </button>
      </aside>

      <BillSuccess
        open={celebrateAmount !== null}
        amount={celebrateAmount || 0}
        onClose={() => setCelebrateAmount(null)}
      />
    </div>
  );
}

function Row({ label, value, bold }) {
  return (
    <div className={`flex items-center justify-between ${bold ? "text-stone-100 text-base pt-2" : "text-stone-400"}`}>
      <span className="text-[10px] uppercase tracking-widest">{label}</span>
      <span>{value}</span>
    </div>
  );
}
