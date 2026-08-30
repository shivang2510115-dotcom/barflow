import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
import { API, currency } from "@/lib/api";
import { gstLabel, gstSettings, outletTotals } from "@/lib/tax";
import { priceLabel, variantsOf } from "@/lib/menu";
import { Plus, Minus, Receipt, ShoppingBag, X, Wine } from "lucide-react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import CocktailLoader from "@/components/app/CocktailLoader";
import FlyToCart from "@/components/app/FlyToCart";

const publicApi = axios.create({ baseURL: API });

// The kitchen's word for where a line has got to, in the words of the person waiting for
// it. "pending" is a queue state and means nothing to a guest; "Ordered" is what they
// actually want to know — that it is on the list and not lost.
const ITEM_STATUS = {
  pending: "Ordered",
  preparing: "Being made",
  ready: "Ready",
  served: "Served",
};

export default function CustomerMenu() {
  const { tableId } = useParams();
  const [table, setTable] = useState(null);
  const [menu, setMenu] = useState([]);
  const [cat, setCat] = useState("");
  const [cart, setCart] = useState({});
  const [openCart, setOpenCart] = useState(false);
  const [placed, setPlaced] = useState(null);
  const [pay, setPay] = useState("counter");
  const [placing, setPlacing] = useState(false);
  const [payingOnline, setPayingOnline] = useState(false);
  const [welcome, setWelcome] = useState(true);
  const [flights, setFlights] = useState([]);
  const [pulseCart, setPulseCart] = useState(0);
  // The dish whose portion the guest is being asked to pick, or null. Only ever set for
  // a dish that is genuinely sold in more than one.
  const [portionFor, setPortionFor] = useState(null);
  // The bill already open on this table, or null when nothing is. Everything the kitchen
  // has, as opposed to `cart`, which is what this guest is still choosing.
  const [running, setRunning] = useState(null);
  const [openRunning, setOpenRunning] = useState(false);
  const cartPillRef = useRef(null);

  useEffect(() => {
    const t = setTimeout(() => setWelcome(false), 1800);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    publicApi.get(`/tables/public/${tableId}`).then((r) => setTable(r.data)).catch(() => setTable(false));
    // The table id says which hotel's card this is: the QR page has no login, so the
    // scanned table is the only thing that names the tenant. Without it the API can only
    // fall back to the founding property, which is another hotel's menu.
    publicApi.get("/menu", { params: { table_id: tableId } }).then((r) => {
      const arr = r.data.filter((m) => m.available);
      setMenu(arr);
      if (arr.length && !cat) setCat(arr[0].category);
    });

  }, [tableId]);

  /**
   * Everything already ordered to this table, read from the server every time.
   *
   * **Deliberately not cached on the phone.** The bill belongs to the *table*, not to
   * this browser: the other guest at the table orders from their own phone, a waiter
   * adds a round at the till, and the whole thing ceases to exist the second the bill is
   * settled. A `localStorage` copy would be wrong at each of those moments, and a guest
   * reading a stale total is worse off than a guest reading none — they would argue with
   * the waiter about a figure no record anywhere agrees with. So the page holds no
   * memory of the order at all; it asks.
   *
   * `GET /orders/table/{id}/current` needs no account — the scanned table id is the only
   * thing that names the tenant — and answers `null` when the table has no bill open.
   */
  const loadRunning = useCallback(async () => {
    try {
      const { data } = await publicApi.get(`/orders/table/${tableId}/current`);
      // Anything other than an open bill is nothing, as far as a phone is concerned.
      // Settling clears the table's pointer at the same moment it closes the order, so
      // a settled bill does not normally reach here at all — but "a guest must never see
      // a bill that has been paid" is worth being true twice, and this is the cheaper of
      // the two places to say it.
      setRunning(data && data.status === "open" ? data : null);
    } catch {
      // A refresh that fails leaves what is on screen alone rather than blanking it.
      // Hotel wifi drops constantly; the order did not go anywhere.
    }
  }, [tableId]);

  useEffect(() => {
    loadRunning();
  }, [loadRunning]);

  /**
   * Refresh when the guest comes back to the page, and at no other time.
   *
   * A table locks their phone, talks for half an hour, and unlocks it — the tab is still
   * open and every figure on it is half an hour old. `visibilitychange` fires at exactly
   * the moment somebody starts looking again, which is the only moment the number needs
   * to be right.
   *
   * There is no polling loop here on purpose. This runs on a phone on hotel wifi, in a
   * basement, on a battery that has to last the evening; a timer asking every few seconds
   * for an answer nobody is reading is a cost paid by the guest for nothing.
   */
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") loadRunning();
    };
    // Back-navigation restores the old DOM out of the browser's cache without ever
    // firing `visibilitychange` — coming back from the Stripe checkout page, say.
    const onShow = (e) => {
      if (e.persisted) loadRunning();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onShow);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onShow);
    };
  }, [loadRunning]);

  const cats = useMemo(() => Array.from(new Set(menu.map((m) => m.category))), [menu]);
  const shown = menu.filter((m) => m.category === cat);
  // The cart is keyed by dish *and portion*, not by dish: a guest ordering one Half and
  // one Full of the same curry is ordering two different things at two different prices,
  // and collapsing them onto one key would charge one of them wrongly. A dish with no
  // portions keys on its id alone, which is what every key was before this.
  const cartKey = (id, label) => (label ? `${id}::${label}` : id);
  const cartItems = useMemo(
    () =>
      Object.entries(cart)
        .map(([key, entry]) => {
          const m = menu.find((x) => x.id === entry.menu_item_id);
          if (!m) return null;
          // The price the guest is shown here is the price the server charges for that
          // portion — resolved from the same list, so the two cannot drift apart.
          const chosen = variantsOf(m).find((v) => v.label === entry.variant_label);
          if (entry.variant_label && !chosen) return null;
          return {
            ...m,
            key,
            price: chosen ? chosen.price : m.price,
            variant_label: entry.variant_label || null,
            qty: entry.qty,
          };
        })
        .filter(Boolean),
    [cart, menu]
  );
  // The outlet's own GST, carried on the table this QR code points at — see
  // backend/routers/tables.py::get_table_public. This preview used to be a hardcoded 10%,
  // which is not an Indian GST rate, so the guest watched their cart add up to one figure
  // and were handed a bill with another. `gstSettings` answers 5% exclusive while the
  // table is still loading, which is what the server bills an unset property at.
  const gst = gstSettings(table || null);
  const cartTotals = outletTotals(
    cartItems.reduce((s, i) => s + i.price * i.qty, 0),
    gst,
  );
  const { subtotal, taxableValue, tax, total } = cartTotals;
  const cartCount = cartItems.reduce((s, i) => s + i.qty, 0);

  // --- the bill already open on the table ---
  const runningItems = running?.items || [];
  const runningCount = runningItems.reduce((s, i) => s + i.quantity, 0);
  // The rate *this bill* was priced at, taken off the bill itself rather than off the
  // property's settings today. That is the whole reason the server stamps `gst_rate` onto
  // an order: a bill rung up at 5% has to keep saying 5% after the hotel moves to 18%, or
  // the guest is reading a tax line that does not explain the total underneath it. The
  // table's own setting is the fallback only for a record written before that field
  // existed, which is what `gstSettings` is already careful about.
  const runningGst = gstSettings(
    running && running.gst_rate !== null && running.gst_rate !== undefined
      ? { outlet_gst_rate: running.gst_rate, gst_inclusive: running.gst_inclusive }
      : table || null,
  );
  // Never recomputed on the client. Every figure below is the one the server priced and
  // the one the printed bill will carry; `outletTotals` above exists only for the cart,
  // where no order exists yet and there is nothing to ask.
  const showRunning = () => {
    setOpenCart(false);
    setOpenRunning(true);
  };
  // How many of this dish are in the cart across every portion of it — what the counter
  // on the row shows for a dish sold by portion.
  const inCart = (m) =>
    cartItems.reduce((s, i) => (i.id === m.id ? s + i.qty : s), 0);

  const add = (id, label = null) =>
    setCart((c) => {
      const key = cartKey(id, label);
      const existing = c[key];
      return {
        ...c,
        [key]: { menu_item_id: id, variant_label: label, qty: (existing?.qty || 0) + 1 },
      };
    });
  const dec = (key) =>
    setCart((c) => {
      const n = { ...c };
      if (n[key]?.qty > 1) n[key] = { ...n[key], qty: n[key].qty - 1 };
      else delete n[key];
      return n;
    });

  const choosePortion = (m, variant) => {
    setPortionFor(null);
    add(m.id, variant.label);
    setPulseCart((v) => v + 1);
  };

  const flyToCart = (menuItem, evt) => {
    // A dish sold in more than one portion is asked about first — the server refuses a
    // line that does not name one, and it is right to: the guest has to be charged for
    // the plate they picked, not for whichever one the card happened to list first.
    if (variantsOf(menuItem).length) return setPortionFor(menuItem);
    const btn = evt.currentTarget;
    const card = btn.closest("li");
    const imgEl = card?.querySelector("img[data-menu-thumb]");
    const source = imgEl || btn;
    const r = source.getBoundingClientRect();
    // fallback target: bottom-center cart pill
    const cartEl = cartPillRef.current || document.querySelector('[data-testid="cart-open"]');
    const c = cartEl?.getBoundingClientRect() || {
      left: window.innerWidth / 2 - 12,
      top: window.innerHeight - 40,
    };
    setFlights((prev) => [
      ...prev,
      {
        id: `${menuItem.id}-${Date.now()}`,
        from: { x: r.left, y: r.top, w: Math.min(r.width, 120), h: Math.min(r.height, 120) },
        to: { x: c.left + 20, y: c.top + 10 },
        image: menuItem.image,
      },
    ]);
    setPulseCart((v) => v + 1);
    add(menuItem.id);
  };

  const removeFlight = (id) => setFlights((prev) => prev.filter((f) => f.id !== id));

  const placeOrder = async () => {
    if (!cartItems.length) return;
    setPlacing(true);
    try {
      const items = cartItems.map((i) => ({
        menu_item_id: i.id,
        quantity: i.qty,
        variant_label: i.variant_label,
      }));
      const { data } = await publicApi.post(`/orders/table/${tableId}/items`, { items, source: "qr" });

      if (pay === "online") {
        setPayingOnline(true);
        try {
          const origin = window.location.origin;
          const { data: sess } = await publicApi.post(`/payments/checkout/session`, {
            order_id: data.id,
            origin_url: origin,
          });
          window.location.href = sess.url;
          return;
        } catch (err) {
          setPayingOnline(false);
          toast.error("Online payment unavailable — order placed, pay at counter");
        }
      }

      setPlaced({ order: data, pay });
      // The response *is* the table's bill with these lines on it, so the running order
      // is current without a second round trip. `loadRunning` still owns the general
      // case; this is only the one moment the page already holds the answer.
      setRunning(data && data.status === "open" ? data : null);
      setCart({});
      setOpenCart(false);
      toast.success("Order placed · staff notified");
    } catch {
      toast.error("Could not place order");
      // Something went wrong between this phone and the bill, and the page can no longer
      // say which side of it the order landed on. Ask.
      loadRunning();
    } finally {
      setPlacing(false);
    }
  };

  if (table === false)
    return (
      <div className="min-h-screen flex items-center justify-center bg-ground text-muted2 p-6 text-center font-mono uppercase tracking-widest text-xs">
        Invalid table QR
      </div>
    );

  return (
    <div className="min-h-screen bg-ground text-ink relative z-[2] pb-32">
      {/* Glass clink welcome */}
      <AnimatePresence>
        {welcome && table && (
          <motion.div
            key="welcome"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4 }}
            className="fixed inset-0 z-[70] bg-ground flex flex-col items-center justify-center"
            data-testid="cmenu-welcome"
          >
            <motion.div
              className="relative"
              initial={{ y: 20, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ delay: 0.1, duration: 0.5 }}
            >
              <motion.div
                animate={{ rotate: [0, -8, 8, -4, 4, 0] }}
                transition={{ duration: 1.0, delay: 0.4 }}
                className="text-6xl"
              >
                🥂
              </motion.div>
            </motion.div>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.9, duration: 0.4 }}
              className="mt-6 text-center"
            >
              <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass">Welcome</div>
              <div className="font-display uppercase text-4xl mt-2">Table {table.label}</div>
              <div className="text-faint text-xs font-mono uppercase tracking-widest mt-3">
                Ready to order?
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Online-pay loader overlay */}
      {payingOnline && <CocktailLoader overlay label="Redirecting to secure checkout" />}

      {/* Header hero */}
      <header className="relative h-56 overflow-hidden">
        <img
          src="https://images.unsplash.com/photo-1696062985889-de626efe0148?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA0MTJ8MHwxfHNlYXJjaHwyfHxwcmVtaXVtJTIwZGltbHklMjBsaXQlMjBiYXIlMjBpbnRlcmlvcnxlbnwwfHx8fDE3ODQyMTc5NjV8MA&ixlib=rb-4.1.0&q=85"
          alt=""
          className="absolute inset-0 w-full h-full object-cover opacity-50"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-ground/40 via-ground/60 to-ground" />
        <div className="relative h-full flex items-end p-6">
          <div>
            <div className="flex items-center gap-2 text-brass text-[10px] uppercase tracking-[0.4em] font-mono">
              <Wine size={14} /> BarFlow · Table {table?.label || "…"}
            </div>
            <h1 className="mt-3 font-display uppercase text-4xl leading-none tracking-tight">
              Order at
              <br />
              your table.
            </h1>
          </div>
        </div>
      </header>

      {/* What is already coming. Above the card and above the tabs, because "did my order
          actually go through?" is the question a guest reopening this page came back to
          answer, and it should not need scrolling for. Absent entirely when the table has
          no bill open, so a first scan looks exactly as it always did. */}
      {running && runningCount > 0 && (
        <button
          type="button"
          data-testid="cmenu-running-banner"
          onClick={showRunning}
          className="w-full flex items-center justify-between gap-4 px-5 py-4 bg-surface border-y border-brass/30 text-left active:bg-raised/70 transition"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-brass text-[10px] uppercase tracking-[0.3em] font-mono">
              <Receipt size={13} /> Already ordered
            </div>
            <div className="mt-1.5 text-sm text-muted2">
              {runningCount} item{runningCount === 1 ? "" : "s"} on this table&rsquo;s bill
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="font-mono text-brass">{currency(running.total)}</div>
            <div className="text-[9px] font-mono uppercase tracking-widest text-faint mt-0.5">
              View
            </div>
          </div>
        </button>
      )}

      {/* Category tabs */}
      <div className="sticky top-0 bg-ground/90 backdrop-blur-xl border-b border-hairline z-20">
        <div className="flex overflow-x-auto no-scrollbar">
          {cats.map((c) => (
            <button
              key={c}
              data-testid={`cmenu-cat-${c.replace(/\s+/g,"-")}`}
              onClick={() => setCat(c)}
              className={`px-5 py-4 text-[10px] font-mono uppercase tracking-widest whitespace-nowrap border-b-2 ${
                cat === c ? "text-brass border-brass" : "text-faint border-transparent"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* Items */}
      <ul className="divide-y divide-hairline">
        {shown.map((m, idx) => (
          <motion.li
            key={m.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(idx * 0.04, 0.4), duration: 0.35 }}
            className="p-4 flex items-start gap-4 hover:bg-surface/40 transition-colors"
            data-testid={`cmenu-item-${m.name.replace(/\s+/g,"-")}`}
          >
            {/* Thumbnail */}
            <div className="relative w-24 h-24 shrink-0 overflow-hidden bg-surface border border-hairline">
              {m.image ? (
                <img
                  src={m.image}
                  alt={m.name}
                  data-menu-thumb
                  loading="lazy"
                  className="w-full h-full object-cover transition-transform duration-500 hover:scale-105"
                  onError={(e) => {
                    e.target.style.display = "none";
                  }}
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-faint">
                  <Wine size={22} />
                </div>
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-ground/60 via-transparent to-transparent" />
            </div>

            <div className="flex-1 min-w-0">
              <div className="font-medium text-base leading-snug">{m.name}</div>
              {m.description && (
                <div className="text-xs text-muted2 mt-1 leading-relaxed line-clamp-2">
                  {m.description}
                </div>
              )}
              <div className="mt-3 font-mono text-brass">{priceLabel(m)}</div>
              {variantsOf(m).length > 0 && (
                <div className="mt-1 text-[10px] font-mono uppercase tracking-widest text-faint">
                  {variantsOf(m).map((v) => v.label).join(" · ")}
                </div>
              )}
            </div>

            <div className="flex items-center gap-2 shrink-0 self-center">
              {/* A dish sold by portion keeps one button whatever is in the cart: the
                  minus would have to ask "which portion?", and the cart sheet below
                  already answers that a line at a time. */}
              {variantsOf(m).length ? (
                <button
                  data-testid={`cmenu-add-${m.name.replace(/\s+/g,"-")}`}
                  onClick={(e) => flyToCart(m, e)}
                  className="rounded-full bg-brass hover:bg-brass-deep text-on-brass px-4 py-2 text-[10px] font-mono uppercase tracking-widest active:scale-90 transition"
                >
                  {inCart(m) ? `Add · ${inCart(m)}` : "Choose"}
                </button>
              ) : cart[m.id] ? (
                <>
                  <button
                    data-testid={`cmenu-dec-${m.name.replace(/\s+/g,"-")}`}
                    onClick={() => dec(m.id)}
                    className="w-8 h-8 border border-hairline-strong hover:border-brass flex items-center justify-center active:scale-90 transition"
                  >
                    <Minus size={14} />
                  </button>
                  <span className="w-6 text-center font-mono">{cart[m.id].qty}</span>
                  <button
                    data-testid={`cmenu-inc-${m.name.replace(/\s+/g,"-")}`}
                    onClick={(e) => flyToCart(m, e)}
                    className="w-8 h-8 border border-brass text-brass flex items-center justify-center active:scale-90 transition"
                  >
                    <Plus size={14} />
                  </button>
                </>
              ) : (
                <button
                  data-testid={`cmenu-add-${m.name.replace(/\s+/g,"-")}`}
                  onClick={(e) => flyToCart(m, e)}
                  className="rounded-full bg-brass hover:bg-brass-deep text-on-brass px-4 py-2 text-[10px] font-mono uppercase tracking-widest active:scale-90 transition"
                >
                  Add
                </button>
              )}
            </div>
          </motion.li>
        ))}
      </ul>

      <FlyToCart flights={flights} onDone={removeFlight} />

      {/* Portion sheet · only in front of a dish that is genuinely sold in more than one.
          The price on each button is the price the server charges for that portion. */}
      {portionFor && (
        <div
          className="fixed inset-0 z-50 bg-ground/80 backdrop-blur flex items-end sm:items-center justify-center"
          onClick={() => setPortionFor(null)}
          data-testid="cmenu-portion-sheet"
        >
          <div
            className="w-full sm:max-w-sm bg-surface border-t sm:border border-brass/60 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-brass">
              Choose a portion
            </div>
            <div className="font-display uppercase text-2xl mt-2 leading-none">
              {portionFor.name}
            </div>
            <div className="mt-5 space-y-2">
              {variantsOf(portionFor).map((v) => (
                <button
                  key={v.label}
                  data-testid={`cmenu-portion-${v.label.replace(/\s+/g, "-")}`}
                  onClick={() => choosePortion(portionFor, v)}
                  className="w-full flex items-center justify-between border border-hairline-strong hover:border-brass px-4 py-3 text-left active:scale-[0.98] transition"
                >
                  <span className="text-sm">{v.label}</span>
                  <span className="font-mono text-brass">{currency(v.price)}</span>
                </button>
              ))}
            </div>
            <button
              onClick={() => setPortionFor(null)}
              className="mt-5 w-full border border-hairline py-2 text-[10px] font-mono uppercase tracking-widest text-muted2"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Running-order pill · only while the cart is empty, so the thumb reaches exactly
          one thing at the bottom of the screen. With items in the cart the cart pill has
          that spot — finishing the order in hand is the live task — and the running order
          stays one tap away from the banner above and from inside the cart sheet. */}
      {running && runningCount > 0 && cartCount === 0 && !openRunning && (
        <motion.button
          data-testid="running-open"
          onClick={showRunning}
          initial={{ y: 40, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 0.35 }}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-full bg-surface border border-brass/60 text-brass px-6 py-3 font-mono uppercase tracking-widest text-xs flex items-center gap-3 shadow-[0_0_24px_rgba(0,0,0,0.6)] z-30"
        >
          <Receipt size={14} />
          Your order · {currency(running.total)}
        </motion.button>
      )}

      {/* Running-order sheet · what the kitchen already has. Never the cart: these are
          lines that have been sent, cannot be edited from a phone, and are on a bill. */}
      {openRunning && running && (
        <div
          className="fixed inset-0 z-40 bg-ground/70 backdrop-blur"
          onClick={() => setOpenRunning(false)}
        >
          <div
            data-testid="cmenu-running-sheet"
            className="absolute bottom-0 inset-x-0 bg-surface border-t border-hairline p-6 max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-display uppercase text-2xl leading-none">Already ordered</div>
                <div className="mt-2 text-[10px] font-mono uppercase tracking-widest text-faint">
                  Table {table?.label || "…"} · sent to the kitchen
                </div>
              </div>
              <button onClick={() => setOpenRunning(false)} aria-label="Close">
                <X size={18} />
              </button>
            </div>

            {/* Said out loud, because it is the one thing about this screen that surprises
                people: the bill is the table's, not the phone's. A guest who sees a dish
                they did not order should read this and not call a waiter over. */}
            <p className="mt-4 text-xs text-faint leading-relaxed">
              One bill for the table — anything ordered from another phone here, or added
              by a waiter, is on it too.
            </p>

            <ul className="divide-y divide-hairline mt-4">
              {runningItems.map((it) => (
                <li key={it.id} className="py-3 flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm">{it.name}</span>
                      {/* A line with no portion simply has no badge, which is every line
                          on a card that does not sell dishes that way. */}
                      {it.variant_label && (
                        <span className="text-[9px] font-mono uppercase tracking-widest border border-brass/60 text-brass px-1.5 py-0.5">
                          {it.variant_label}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-[10px] font-mono uppercase tracking-widest text-faint">
                      {it.quantity} × {currency(it.price)}
                      <span className="text-faint"> · </span>
                      {ITEM_STATUS[it.status] || it.status}
                    </div>
                  </div>
                  <div className="font-mono text-sm shrink-0">
                    {currency(it.price * it.quantity)}
                  </div>
                </li>
              ))}
            </ul>

            {/* The foot of the bill, in the bill's own figures — the server priced these
                and the printed slip will carry them, so nothing here is recomputed. */}
            <div className="mt-4 font-mono text-sm space-y-1 border-t border-hairline pt-4">
              <div className="flex justify-between text-muted2">
                <span>{runningGst.inclusive ? "Taxable value" : "Subtotal"}</span>
                <span>
                  {currency(runningGst.inclusive ? running.taxable_value : running.subtotal)}
                </span>
              </div>
              <div className="flex justify-between text-muted2">
                <span>{gstLabel(runningGst)}</span>
                <span>{currency(running.tax)}</span>
              </div>
              {running.discount > 0 && (
                <div className="flex justify-between text-muted2">
                  <span>Discount</span>
                  <span>{currency(-running.discount)}</span>
                </div>
              )}
              <div className="flex justify-between text-base pt-2">
                <span>Running total</span>
                <span className="text-brass" data-testid="cmenu-running-total">
                  {currency(running.total)}
                </span>
              </div>
            </div>

            {/* The bill has been totalled and shown. The server refuses self-ordering from
                here — see `_bill_locked` — so saying so beats letting the guest fill a
                cart and be turned away at the last tap. */}
            {running.presented_at && (
              <p className="mt-4 border border-hairline-strong px-4 py-3 text-xs text-muted2 leading-relaxed">
                Your bill has been totalled. Please ask a member of staff to add anything
                else.
              </p>
            )}

            <button
              onClick={() => setOpenRunning(false)}
              className="mt-5 w-full border border-hairline py-3 text-[10px] font-mono uppercase tracking-widest text-muted2"
            >
              Back to the menu
            </button>
          </div>
        </div>
      )}

      {/* Cart pill */}
      {cartCount > 0 && !openCart && (
        <motion.button
          data-testid="cart-open"
          ref={cartPillRef}
          onClick={() => setOpenCart(true)}
          key={pulseCart}
          initial={{ y: 40, opacity: 0 }}
          animate={{ y: 0, opacity: 1, scale: [1, 1.08, 1] }}
          transition={{ duration: 0.45, scale: { duration: 0.35 } }}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-full bg-brass hover:bg-brass-deep text-on-brass px-6 py-3 font-mono uppercase tracking-widest text-xs flex items-center gap-3 shadow-[0_0_30px_rgba(234,88,12,0.5)] z-30"
        >
          <ShoppingBag size={14} />
          {cartCount} item{cartCount > 1 ? "s" : ""} · {currency(total)}
          <span className="opacity-70">Review</span>
        </motion.button>
      )}

      {/* Cart sheet */}
      {openCart && (
        <div className="fixed inset-0 z-40 bg-ground/70 backdrop-blur" onClick={() => setOpenCart(false)}>
          <div className="absolute bottom-0 inset-x-0 bg-surface border-t border-hairline p-6 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            {/* "Your Order" until this sheet had a sibling. Two screens both called that
                is how a guest ends up believing they have been charged twice, or that the
                round they sent twenty minutes ago is still sitting in a cart. This one is
                what has not been sent; the other is what the kitchen has. */}
            <div className="flex items-start justify-between gap-3 mb-4">
              <div>
                <div className="font-display uppercase text-2xl leading-none">Adding now</div>
                <div className="mt-2 text-[10px] font-mono uppercase tracking-widest text-faint">
                  Not sent to the kitchen yet
                </div>
              </div>
              <button onClick={() => setOpenCart(false)} aria-label="Close"><X size={18} /></button>
            </div>
            <ul className="divide-y divide-hairline">
              {cartItems.map((it) => (
                <li key={it.key} className="py-3 flex items-center gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      {it.name}
                      {it.variant_label && (
                        <span className="text-[9px] font-mono uppercase tracking-widest border border-brass/60 text-brass px-1.5 py-0.5">
                          {it.variant_label}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] font-mono uppercase tracking-widest text-faint">{currency(it.price)}</div>
                  </div>
                  <button onClick={() => dec(it.key)} className="w-7 h-7 border border-hairline-strong flex items-center justify-center"><Minus size={12} /></button>
                  <span className="w-6 text-center font-mono">{it.qty}</span>
                  <button onClick={() => add(it.id, it.variant_label)} className="w-7 h-7 border border-brass text-brass flex items-center justify-center"><Plus size={12} /></button>
                </li>
              ))}
            </ul>
            <div className="mt-4 font-mono text-sm space-y-1 border-t border-hairline pt-4">
              <div className="flex justify-between text-muted2"><span>{gst.inclusive ? "Taxable value" : "Subtotal"}</span><span>{currency(gst.inclusive ? taxableValue : subtotal)}</span></div>
              <div className="flex justify-between text-muted2"><span>{gstLabel(gst)}</span><span>{currency(tax)}</span></div>
              {/* "Total for these items", not "Total": with a bill already open on the
                  table this figure is not what the guest owes, and a screen that says
                  "Total" beside a number smaller than the bill is a screen that lies. */}
              <div className="flex justify-between text-base pt-2"><span>{running && runningCount > 0 ? "Total for these items" : "Total"}</span><span className="text-brass">{currency(total)}</span></div>
            </div>

            {/* And what that total is *not* counting, one tap from reading it in full.
                A guest looking at ₹430 needs to know whether the ₹1,120 already on the
                table is inside it. It is not. */}
            {running && runningCount > 0 && (
              <button
                type="button"
                data-testid="cmenu-cart-running-link"
                onClick={showRunning}
                className="mt-4 w-full flex items-center justify-between gap-3 border border-hairline px-4 py-3 text-left active:bg-raised/60 transition"
              >
                <div className="min-w-0">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-faint">
                    Not counted above
                  </div>
                  <div className="mt-1 text-xs text-muted2">
                    Already ordered · {runningCount} item{runningCount === 1 ? "" : "s"}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-mono text-sm text-muted2">{currency(running.total)}</div>
                  <div className="text-[9px] font-mono uppercase tracking-widest text-faint mt-0.5">
                    View
                  </div>
                </div>
              </button>
            )}

            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-2">Payment preference</div>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { k: "counter", l: "Pay at counter" },
                  { k: "online", l: "Pay online · Stripe" },
                ].map((p) => (
                  <button
                    key={p.k}
                    data-testid={`cmenu-pay-${p.k}`}
                    onClick={() => setPay(p.k)}
                    className={`py-3 text-[10px] font-mono uppercase tracking-widest border ${
                      pay === p.k ? "border-brass text-brass" : "border-hairline text-muted2"
                    }`}
                  >
                    {p.l}
                  </button>
                ))}
              </div>
            </div>

            <button
              data-testid="cmenu-place"
              disabled={placing || !cartItems.length}
              onClick={placeOrder}
              className="mt-5 w-full rounded-full bg-brass hover:bg-brass-deep disabled:opacity-40 text-on-brass py-3 font-mono uppercase tracking-widest text-xs"
            >
              {placing ? "Sending…" : "Place Order"}
            </button>
          </div>
        </div>
      )}

      {/* Placed toast card */}
      {placed && (
        <div className="fixed inset-0 z-50 bg-ground/85 backdrop-blur flex items-center justify-center p-6" onClick={() => setPlaced(null)}>
          <div className="border border-brass bg-surface p-8 max-w-md text-center" onClick={(e) => e.stopPropagation()}>
            <div className="text-brass text-[10px] uppercase tracking-[0.4em] font-mono">Order confirmed</div>
            <div className="font-display text-4xl uppercase mt-2">Cheers!</div>
            <div className="text-muted2 text-sm mt-3">
              Order <span className="font-mono text-brass">#{placed.order.id.slice(0, 6)}</span> sent to the bar.
              {placed.pay === "counter" ? " Pay at the counter when you're done." : ""}
            </div>
            {/* This figure is the whole table's bill, not the round just sent — the
                response is the order with these lines added to it. It has always been
                that number and never said so, which reads as a wrong price for the round
                whenever anything was ordered before it. */}
            <div className="mt-6 text-[10px] uppercase tracking-[0.3em] font-mono text-faint">
              Bill so far
            </div>
            <div className="mt-1 font-mono text-2xl text-brass">{currency(placed.order.total)}</div>
            <div className="mt-8 flex items-center justify-center gap-3">
              <button
                data-testid="cmenu-placed-view"
                onClick={() => { setPlaced(null); showRunning(); }}
                className="rounded-full border border-brass text-brass px-6 py-2 text-[10px] font-mono uppercase tracking-widest"
              >
                See the bill
              </button>
              <button onClick={() => setPlaced(null)} className="rounded-full border border-hairline-strong hover:border-brass px-6 py-2 text-[10px] font-mono uppercase tracking-widest">
                Keep browsing
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
