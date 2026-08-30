import React, { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import axios from "axios";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { useProperty } from "@/contexts/PropertyContext";
import { gstLabel, gstSettings, outletTotals } from "@/lib/tax";
import { priceLabel, variantsOf } from "@/lib/menu";
import { Plus, Minus, Trash2, Search } from "lucide-react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import BillSuccess from "@/components/app/BillSuccess";
import EmptyState from "@/components/app/EmptyState";

export default function POS() {
  const { tableId } = useParams();
  // The hotel's own GST rate, fetched once for the whole console by AppLayout. Only the
  // bill *foot* is worked out here, and only while a waiter is typing a discount — every
  // total on a saved order comes back from the server, which is the authority on it.
  const property = useProperty();
  const gst = gstSettings(property);
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
  // An occasion the waiter is told about while the card goes through — "it's her birthday
  // on Saturday". Two fields and two chips, shown only once a phone has been typed,
  // because without a number there is nobody to record it against. Nothing is required:
  // a bill settles exactly as it always did if these are left alone, which is the only
  // way a field on this screen survives a Friday night.
  const [occasionLabel, setOccasionLabel] = useState("");
  const [occasionDate, setOccasionDate] = useState("");
  // The dish whose portion the waiter is being asked to pick, or null. Only ever set for
  // an item that is actually sold in more than one — see addItem. A modal in front of
  // every tap would slow down the one screen that is used at speed.
  const [portionFor, setPortionFor] = useState(null);

  // Charge-to-room: pick an in-house guest to bill the order to their folio.
  const [inHouse, setInHouse] = useState([]);
  const [roomQuery, setRoomQuery] = useState("");
  const [debouncedRoomQuery, setDebouncedRoomQuery] = useState("");
  const [chosenFolio, setChosenFolio] = useState(null);
  // What this guest's package still covers. Fetched when a guest is chosen for
  // charge-to-room, because that is the only moment it can matter: a walk-in paying
  // cash has no package, and asking for one would be a request per sale for nothing.
  const [included, setIncluded] = useState(null);
  // line id -> inclusion id, for the lines a waiter has marked as covered. Cleared
  // whenever the guest changes, because an allowance belongs to one booking.
  const [comped, setComped] = useState({});

  useEffect(() => {
    api.get("/tables").then((r) => setTables(r.data));
    api.get("/menu").then((r) => setMenu(r.data));
  }, []);

  // A guest with no package answers an empty list rather than an error, so a failure
  // here is a real one — and it must not block a sale. The strip simply does not
  // appear, and the waiter charges as they always did.
  useEffect(() => {
    const bookingId = chosenFolio?.booking?.id;
    setComped({});
    if (!bookingId) { setIncluded(null); return; }
    let cancelled = false;
    api.get(`/bookings/${bookingId}/entitlements`)
      .then((r) => { if (!cancelled) setIncluded(r.data); })
      .catch(() => { if (!cancelled) setIncluded(null); });
    return () => { cancelled = true; };
  }, [chosenFolio]);

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

  // One place that sends a line, so the POS and the portion sheet cannot disagree about
  // what a variant line looks like. `variantLabel` is null for the overwhelming majority
  // of dishes, which have no portions at all.
  const sendLine = async (menuItemId, variantLabel, quantity = 1) => {
    const { data } = await api.post(`/orders/table/${tableId}/items`, {
      items: [{ menu_item_id: menuItemId, quantity, variant_label: variantLabel ?? null }],
      source: "pos",
    });
    return data;
  };

  const addItem = async (m) => {
    if (!tableId) return toast.error("Pick a table first");
    // A dish sold in more than one portion has to be asked about: the server refuses a
    // line that does not name one rather than guessing, because guessing is charging a
    // guest for a plate nobody chose. Everything else goes straight onto the bill, one
    // tap, exactly as it always has.
    if (m.variants?.length) return setPortionFor(m);
    try {
      setOrder(await sendLine(m.id, null));
      // refresh tables to reflect occupied status
      api.get("/tables").then((r) => setTables(r.data));
    } catch {
      toast.error("Could not add item");
    }
  };

  const addPortion = async (m, variant) => {
    setPortionFor(null);
    try {
      setOrder(await sendLine(m.id, variant.label));
      api.get("/tables").then((r) => setTables(r.data));
    } catch {
      toast.error("Could not add item");
    }
  };

  const changeQty = async (item, delta) => {
    if (!order) return;
    if (delta > 0) {
      // The line's own portion, not the dish's first one: tapping + on a Full must add
      // a Full, and the server refuses the line outright if the word is dropped.
      await sendLine(item.menu_item_id, item.variant_label, delta);
    } else {
      await api.delete(`/orders/${order.id}/items/${item.id}`);
    }
    loadOrder(tableId);
  };

  // Freeze the bill against self-ordering the moment a waiter picks a payment method:
  // from here the guest is looking at a total, and a QR order arriving after that is a
  // line they never agreed to. Silent on failure — this is a safeguard on top of the
  // real settle, and a toast about it would be noise at the till. Staff adding an item
  // clears it server-side, so the total shown is always the total on record.
  const present = () => {
    if (!order?.id || order.presented_at) return;
    api
      .post(`/orders/${order.id}/present`)
      .then((r) => setOrder(r.data))
      .catch(() => {});
  };

  /** An inclusion this guest still has that covers this line, or null.
   *
   * Outlet scope only for now: matching a category or a single item needs the menu
   * item's own category, which the cart line does not carry. Offering a control that
   * the server would then refuse is worse than not offering it, so the narrower rule
   * is the honest one until the line carries what the wider one needs.
   */
  const availableInclusion = (line) => {
    const list = included?.inclusions || [];
    const already = new Set(Object.values(comped));
    return list.find((i) =>
      i.scope === "outlet" &&
      (i.remaining - (already.has(i.id) && comped[line.id] !== i.id ? 1 : 0)) > 0
    ) || null;
  };

  const toggleComp = (line, inclusion) => {
    setComped((c) => {
      const next = { ...c };
      if (next[line.id]) delete next[line.id];
      else next[line.id] = inclusion.id;
      return next;
    });
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
      const finalTotal = outletTotals(order.subtotal || 0, gst, discount).total;
      await api.post(`/orders/${order.id}/settle`, {
        payment_method: pay,
        discount: Number(discount) || 0,
        customer_name: custName.trim() || null,
        customer_phone: custPhone.trim() || null,
        folio_id: pay === "room" ? chosenFolio.folio.id : undefined,
        // Only when charging to a room: an entitlement belongs to a booking, and a
        // walk-in paying cash has none. The server recomputes what each line is worth
        // from the order it holds, so nothing here can name an amount.
        included: pay === "room"
          ? Object.entries(comped).map(([line_id, inclusion_id]) => ({ line_id, inclusion_id }))
          : [],
      });
      toast.success(`Bill settled · ${pay.toUpperCase()}`);

      // After the bill, never before it, and never in its way. Recording an occasion is
      // a separate write against the guest record, so a failure here — a number that is
      // not a mobile, a slow connection — must not make a settled bill look unsettled.
      // The money is already taken and the table is already free; the worst case is that
      // one birthday is not written down, and the waiter is told so quietly.
      if (custPhone.trim() && occasionLabel.trim() && occasionDate) {
        api
          .post("/messaging/occasions", {
            phone: custPhone.trim(),
            name: custName.trim() || null,
            label: occasionLabel.trim(),
            date: occasionDate,
          })
          .then(() => toast.success(`${occasionLabel.trim()} saved for ${custName.trim() || "this customer"}`))
          .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
      }

      setCelebrateAmount(Math.max(0, finalTotal));
      setOrder(null);
      setDiscount(0);
      setCustName("");
      setCustPhone("");
      setOccasionLabel("");
      setOccasionDate("");
      setChosenFolio(null);
      setRoomQuery("");
      api.get("/tables").then((r) => setTables(r.data));
    } catch (e) {
      if (pay === "room" && e.response?.status === 409) {
        toast.error("That folio is no longer open — pick another guest");
      } else {
        toast.error("Could not settle bill");
      }
    }
  };

  // The foot of the bill, re-derived from the lines rather than read off the order, so
  // the total follows the discount box as it is typed. The server prices the same lines
  // the same way when the bill is settled — see backend/services/tax.py, which this
  // mirrors — and it is the server's answer that is stored.
  const foot = outletTotals(order?.subtotal || 0, gst, discount);

  return (
    <div className="p-4 md:p-6 grid lg:grid-cols-[minmax(0,1fr)_420px] gap-4 min-h-screen">
      {/* Left · Menu.
          min-w-0, and minmax(0,1fr) on the track above it, because a grid item's default
          min-width is auto: it refuses to shrink below its widest child. The category
          strip is wider than the screen, so without these two the column grew past the
          viewport, the whole page scrolled sideways, and the bill — which is meant to
          stay put on the right — slid away with it. The strip's own overflow-x-auto
          cannot do anything until its parent is allowed to be narrower than its content. */}
      <div className="min-w-0">
        <div className="flex items-baseline justify-between flex-wrap gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass mb-1">POS</div>
            <h1 className="font-display uppercase text-3xl md:text-4xl leading-none tracking-tight">
              {currentTable ? `Table ${currentTable.label}` : "Pick a table"}
            </h1>
          </div>
          <select
            data-testid="pos-table-select"
            value={tableId || ""}
            onChange={(e) => nav(`/app/pos/${e.target.value}`)}
            className="bg-surface border border-hairline-strong py-2 px-3 text-sm"
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
          <div className="flex items-center border border-hairline px-3 flex-1">
            <Search size={14} className="text-faint" />
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
                cat === c ? "border-brass text-brass bg-surface" : "border-hairline text-muted2 hover:border-hairline-strong"
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
              className="text-left border border-hairline bg-surface/40 hover:border-brass hover:bg-surface overflow-hidden transition-colors active:scale-[0.98]"
            >
              {m.image && (
                <div className="aspect-[16/9] bg-surface overflow-hidden">
                  <img src={m.image} alt="" loading="lazy" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
                </div>
              )}
              <div className="p-4">
                <div className="text-[10px] font-mono uppercase tracking-widest text-faint">{m.category}</div>
                <div className="mt-2 font-medium">{m.name}</div>
                <div className="mt-4 flex items-baseline justify-between gap-2">
                  <span className="font-mono text-brass">{priceLabel(m)}</span>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-faint">
                    {variantsOf(m).length
                      ? variantsOf(m).map((v) => v.label).join(" · ")
                      : m.station}
                  </span>
                </div>
              </div>
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="col-span-full text-faint text-sm font-mono uppercase tracking-widest py-10 text-center">
              No matching items
            </div>
          )}
        </div>
      </div>

      {/* Right · Bill */}
      <aside className="border border-hairline bg-surface/40 p-5 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto min-w-0">
        <div className="flex items-center justify-between mb-4">
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass">Bill</div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-faint">
            {order ? `Order ${order.id.slice(0, 6)}` : "New"}
          </div>
        </div>

        <div className="min-h-[240px] max-h-[440px] overflow-y-auto divide-y divide-hairline" data-testid="bill-items">
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
                  {/* The portion beside the dish, never instead of it: a line from
                      before portions existed carries no label and reads exactly as it
                      always did. */}
                  <div className="text-sm flex items-center gap-2 flex-wrap">
                    {it.name}
                    {it.variant_label && (
                      <span
                        data-testid={`bill-portion-${it.id}`}
                        className="text-[9px] font-mono uppercase tracking-widest border border-brass/60 text-brass px-1.5 py-0.5"
                      >
                        {it.variant_label}
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] font-mono uppercase text-faint mt-0.5">
                    {currency(it.price)} · {it.station} · {it.status}
                  </div>
                  {/* Only offered when this guest actually has something left. The
                      server checks the same thing again — this is so a waiter is not
                      shown a control that will refuse them mid-service. */}
                  {pay === "room" && availableInclusion(it) && (
                    <button
                      onClick={() => toggleComp(it, availableInclusion(it))}
                      className={`mt-1.5 text-[10px] font-mono uppercase tracking-widest px-2 py-1 border transition-colors
                        ${comped[it.id]
                          ? "border-state-free bg-state-free/10 text-state-free"
                          : "border-hairline-strong text-faint hover:border-state-free hover:text-state-free"}`}
                    >
                      {comped[it.id] ? "✓ Included" : "Use included"}
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => changeQty(it, -1)}
                    data-testid={`dec-${it.id}`}
                    className="w-7 h-7 flex items-center justify-center border border-hairline-strong hover:border-brass active:scale-95 transition"
                  >
                    <Minus size={12} />
                  </button>
                  <span className="w-6 text-center font-mono">{it.quantity}</span>
                  <button
                    onClick={() => changeQty(it, +1)}
                    data-testid={`inc-${it.id}`}
                    className="w-7 h-7 flex items-center justify-center border border-hairline-strong hover:border-brass active:scale-95 transition"
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

        <div className="mt-4 space-y-1 border-t border-hairline pt-4 text-sm font-mono">
          {/* "Taxable value" when the rate is inclusive, because the subtotal and the
              total are then the same number and two identical lines with different names
              is how a guest is told the bill is wrong. */}
          <Row
            label={gst.inclusive ? "Taxable value" : "Subtotal"}
            value={currency(gst.inclusive ? foot.taxableValue : foot.subtotal)}
          />
          <Row label={gstLabel(gst)} value={currency(foot.tax)} />
          <div className="flex items-center justify-between text-muted2">
            <span className="text-[10px] uppercase tracking-widest">Discount</span>
            <input
              data-testid="discount-input"
              type="number"
              min={0}
              value={discount}
              onChange={(e) => setDiscount(e.target.value)}
              className="w-24 bg-transparent border-b border-hairline-strong text-right py-0.5 focus-neon"
            />
          </div>
          <Row label="Total" value={currency(foot.total)} bold />
        </div>

        <div className="mt-4">
          <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-2">
            Customer <span className="text-faint normal-case tracking-normal">(optional)</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              data-testid="cust-name"
              value={custName}
              onChange={(e) => setCustName(e.target.value)}
              placeholder="Name"
              className="bg-transparent border-b border-hairline-strong py-1.5 text-sm focus-neon"
            />
            <input
              data-testid="cust-phone"
              value={custPhone}
              onChange={(e) => setCustPhone(e.target.value)}
              placeholder="Phone"
              className="bg-transparent border-b border-hairline-strong py-1.5 text-sm focus-neon"
            />
          </div>

          {/* An occasion, only once there is a number to attach it to. Two taps and a
              date at most — a form in front of a guest waiting to pay is a form nobody
              fills in, so the chips are the whole interaction for the two occasions that
              are almost all of them, and the label stays typeable for the ones that are
              not. Where the greeting goes from is Customers -> Messaging, on the day. */}
          {custPhone.trim() && (
            <div className="mt-4" data-testid="occasion-capture">
              <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-2">
                Occasion <span className="text-faint normal-case tracking-normal">(optional)</span>
              </div>
              <div className="flex flex-wrap gap-2 mb-2">
                {["Birthday", "Anniversary"].map((label) => (
                  <button
                    key={label}
                    type="button"
                    data-testid={`occasion-${label.toLowerCase()}`}
                    onClick={() => setOccasionLabel(occasionLabel === label ? "" : label)}
                    className={`px-3 py-1.5 text-[10px] font-mono uppercase tracking-widest border ${
                      occasionLabel === label
                        ? "border-brass text-brass bg-surface"
                        : "border-hairline text-muted2 hover:border-hairline-strong"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  data-testid="occasion-label"
                  value={occasionLabel}
                  onChange={(e) => setOccasionLabel(e.target.value)}
                  placeholder="Or type one…"
                  className="bg-transparent border-b border-hairline-strong py-1.5 text-sm focus-neon"
                />
                <input
                  data-testid="occasion-date"
                  type="date"
                  value={occasionDate}
                  onChange={(e) => setOccasionDate(e.target.value)}
                  className="bg-transparent border-b border-hairline-strong py-1.5 text-sm focus-neon"
                />
              </div>
              {occasionLabel.trim() && !occasionDate && (
                <div className="text-[10px] font-mono text-faint mt-2">
                  Add the date and it is saved when the bill is settled.
                </div>
              )}
            </div>
          )}
        </div>

        <div className="mt-4">
          <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-2">Payment</div>
          <div className="grid grid-cols-4 gap-2">
            {["cash", "card", "online", "room"].map((p) => (
              <button
                key={p}
                onClick={() => {
                  setPay(p);
                  present();
                }}
                data-testid={`pay-${p}`}
                className={`py-2 text-[10px] font-mono uppercase tracking-widest border ${
                  pay === p ? "border-brass text-brass" : "border-hairline text-muted2 hover:border-hairline-strong"
                }`}
              >
                {p}
              </button>
            ))}
          </div>

          {pay === "room" && (
            <div className="mt-3">
              {chosenFolio ? (
                <>
                <div className="flex items-center justify-between border border-brass/50 bg-brass/10 rounded px-3 py-2">
                  <div>
                    <div className="text-sm text-brass">
                      Room {chosenFolio.room?.number} · {chosenFolio.guest?.name}
                    </div>
                    <div className="text-[10px] font-mono text-faint">{chosenFolio.guest?.phone}</div>
                  </div>
                  <button
                    onClick={() => setChosenFolio(null)}
                    className="text-[10px] font-mono uppercase tracking-widest text-faint hover:text-state-alert"
                  >
                    Change
                  </button>
                </div>

                {/* What this guest's package still covers. Shown before anything is
                    rung up, because the decision it informs — charge or comp — is made
                    while the guest is standing there. An exhausted allowance is shown
                    struck through rather than hidden: "you have used both" is a
                    different and more useful answer than silence. */}
                {included?.inclusions?.length > 0 && (
                  <div className="mt-2 border border-state-free/40 bg-state-free/5 rounded px-3 py-2">
                    <div className="text-[10px] font-mono uppercase tracking-widest text-state-free mb-1.5">
                      {included.package?.name || "Included"}
                    </div>
                    <ul className="space-y-1">
                      {included.inclusions.map((i) => (
                        <li key={i.id} className="flex items-center gap-2 text-[12px]">
                          <span className={`tabular-nums ${i.remaining > 0 ? "text-state-free" : "text-faint line-through"}`}>
                            {i.remaining}
                          </span>
                          <span className={i.remaining > 0 ? "text-ink" : "text-faint line-through"}>
                            left{i.scope === "outlet" ? "" : ` · ${i.ref_id}`}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                </>
              ) : (
                <>
                  <div className="flex items-center border border-hairline px-3">
                    <Search size={14} className="text-faint" />
                    <input
                      data-testid="room-search"
                      value={roomQuery}
                      onChange={(e) => setRoomQuery(e.target.value)}
                      placeholder="Room number, guest name or phone"
                      className="bg-transparent px-3 py-2 flex-1 text-sm focus:outline-none"
                    />
                  </div>
                  <ul className="mt-2 max-h-40 overflow-y-auto divide-y divide-hairline">
                    {inHouse.length === 0 && (
                      <li className="py-2 text-xs text-faint font-mono uppercase tracking-widest">
                        No in-house guest matches.
                      </li>
                    )}
                    {inHouse.map((x) => (
                      <li key={x.folio.id}>
                        <button
                          onClick={() => setChosenFolio(x)}
                          className="w-full text-left py-2 text-sm text-muted2 hover:text-brass"
                        >
                          Room {x.room?.number} · {x.guest?.name}
                          <span className="block text-[10px] font-mono text-faint">
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
          className="mt-5 w-full rounded-full bg-brass hover:bg-brass-deep disabled:opacity-40 text-on-brass px-6 py-3 font-mono uppercase tracking-widest text-xs active:scale-95 transition"
        >
          Settle Bill
        </button>
      </aside>

      {/* Portion sheet · only ever in front of a dish that is sold in more than one */}
      {portionFor && (
        <div
          className="fixed inset-0 z-50 bg-ground/80 backdrop-blur flex items-center justify-center p-6"
          onClick={() => setPortionFor(null)}
          data-testid="portion-sheet"
        >
          <div
            className="border border-brass bg-surface p-6 w-full max-w-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass">
              Portion
            </div>
            <div className="font-display uppercase text-2xl mt-2 leading-none">
              {portionFor.name}
            </div>
            <div className="mt-5 space-y-2">
              {variantsOf(portionFor).map((v) => (
                <button
                  key={v.label}
                  data-testid={`portion-${v.label.replace(/\s+/g, "-")}`}
                  onClick={() => addPortion(portionFor, v)}
                  className="w-full flex items-center justify-between border border-hairline-strong hover:border-brass hover:bg-raised px-4 py-3 text-left active:scale-[0.98] transition"
                >
                  <span className="text-sm">{v.label}</span>
                  <span className="font-mono text-brass">{currency(v.price)}</span>
                </button>
              ))}
            </div>
            <button
              onClick={() => setPortionFor(null)}
              className="mt-5 w-full border border-hairline hover:border-hairline-strong py-2 text-[10px] font-mono uppercase tracking-widest text-muted2"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

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
    <div className={`flex items-center justify-between ${bold ? "text-ink text-base pt-2" : "text-muted2"}`}>
      <span className="text-[10px] uppercase tracking-widest">{label}</span>
      <span>{value}</span>
    </div>
  );
}
