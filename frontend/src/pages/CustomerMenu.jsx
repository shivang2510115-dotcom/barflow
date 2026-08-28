import React, { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
import { API, currency } from "@/lib/api";
import { gstLabel, gstSettings, outletTotals } from "@/lib/tax";
import { priceLabel, variantsOf } from "@/lib/menu";
import { Plus, Minus, ShoppingBag, X, Wine } from "lucide-react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import CocktailLoader from "@/components/app/CocktailLoader";
import FlyToCart from "@/components/app/FlyToCart";

const publicApi = axios.create({ baseURL: API });

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
      setCart({});
      setOpenCart(false);
      toast.success("Order placed · staff notified");
    } catch {
      toast.error("Could not place order");
    } finally {
      setPlacing(false);
    }
  };

  if (table === false)
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-950 text-stone-400 p-6 text-center font-mono uppercase tracking-widest text-xs">
        Invalid table QR
      </div>
    );

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 relative z-[2] pb-32">
      {/* Glass clink welcome */}
      <AnimatePresence>
        {welcome && table && (
          <motion.div
            key="welcome"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4 }}
            className="fixed inset-0 z-[70] bg-stone-950 flex flex-col items-center justify-center"
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
              <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500">Welcome</div>
              <div className="font-display uppercase text-4xl mt-2">Table {table.label}</div>
              <div className="text-stone-500 text-xs font-mono uppercase tracking-widest mt-3">
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
        <div className="absolute inset-0 bg-gradient-to-b from-stone-950/40 via-stone-950/60 to-stone-950" />
        <div className="relative h-full flex items-end p-6">
          <div>
            <div className="flex items-center gap-2 text-orange-500 text-[10px] uppercase tracking-[0.4em] font-mono">
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

      {/* Category tabs */}
      <div className="sticky top-0 bg-stone-950/90 backdrop-blur-xl border-b border-stone-800 z-20">
        <div className="flex overflow-x-auto no-scrollbar">
          {cats.map((c) => (
            <button
              key={c}
              data-testid={`cmenu-cat-${c.replace(/\s+/g,"-")}`}
              onClick={() => setCat(c)}
              className={`px-5 py-4 text-[10px] font-mono uppercase tracking-widest whitespace-nowrap border-b-2 ${
                cat === c ? "text-orange-400 border-orange-500" : "text-stone-500 border-transparent"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* Items */}
      <ul className="divide-y divide-stone-800">
        {shown.map((m, idx) => (
          <motion.li
            key={m.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(idx * 0.04, 0.4), duration: 0.35 }}
            className="p-4 flex items-start gap-4 hover:bg-stone-900/40 transition-colors"
            data-testid={`cmenu-item-${m.name.replace(/\s+/g,"-")}`}
          >
            {/* Thumbnail */}
            <div className="relative w-24 h-24 shrink-0 overflow-hidden bg-stone-900 border border-stone-800">
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
                <div className="w-full h-full flex items-center justify-center text-stone-700">
                  <Wine size={22} />
                </div>
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-stone-950/60 via-transparent to-transparent" />
            </div>

            <div className="flex-1 min-w-0">
              <div className="font-medium text-base leading-snug">{m.name}</div>
              {m.description && (
                <div className="text-xs text-stone-400 mt-1 leading-relaxed line-clamp-2">
                  {m.description}
                </div>
              )}
              <div className="mt-3 font-mono text-orange-400">{priceLabel(m)}</div>
              {variantsOf(m).length > 0 && (
                <div className="mt-1 text-[10px] font-mono uppercase tracking-widest text-stone-500">
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
                  className="rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-4 py-2 text-[10px] font-mono uppercase tracking-widest active:scale-90 transition"
                >
                  {inCart(m) ? `Add · ${inCart(m)}` : "Choose"}
                </button>
              ) : cart[m.id] ? (
                <>
                  <button
                    data-testid={`cmenu-dec-${m.name.replace(/\s+/g,"-")}`}
                    onClick={() => dec(m.id)}
                    className="w-8 h-8 border border-stone-700 hover:border-orange-500 flex items-center justify-center active:scale-90 transition"
                  >
                    <Minus size={14} />
                  </button>
                  <span className="w-6 text-center font-mono">{cart[m.id].qty}</span>
                  <button
                    data-testid={`cmenu-inc-${m.name.replace(/\s+/g,"-")}`}
                    onClick={(e) => flyToCart(m, e)}
                    className="w-8 h-8 border border-orange-500 text-orange-400 flex items-center justify-center active:scale-90 transition"
                  >
                    <Plus size={14} />
                  </button>
                </>
              ) : (
                <button
                  data-testid={`cmenu-add-${m.name.replace(/\s+/g,"-")}`}
                  onClick={(e) => flyToCart(m, e)}
                  className="rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-4 py-2 text-[10px] font-mono uppercase tracking-widest active:scale-90 transition"
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
          className="fixed inset-0 z-50 bg-stone-950/80 backdrop-blur flex items-end sm:items-center justify-center"
          onClick={() => setPortionFor(null)}
          data-testid="cmenu-portion-sheet"
        >
          <div
            className="w-full sm:max-w-sm bg-stone-900 border-t sm:border border-orange-500/60 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500">
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
                  className="w-full flex items-center justify-between border border-stone-700 hover:border-orange-500 px-4 py-3 text-left active:scale-[0.98] transition"
                >
                  <span className="text-sm">{v.label}</span>
                  <span className="font-mono text-orange-400">{currency(v.price)}</span>
                </button>
              ))}
            </div>
            <button
              onClick={() => setPortionFor(null)}
              className="mt-5 w-full border border-stone-800 py-2 text-[10px] font-mono uppercase tracking-widest text-stone-400"
            >
              Cancel
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
          className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-6 py-3 font-mono uppercase tracking-widest text-xs flex items-center gap-3 shadow-[0_0_30px_rgba(234,88,12,0.5)] z-30"
        >
          <ShoppingBag size={14} />
          {cartCount} item{cartCount > 1 ? "s" : ""} · {currency(total)}
          <span className="opacity-70">Review</span>
        </motion.button>
      )}

      {/* Cart sheet */}
      {openCart && (
        <div className="fixed inset-0 z-40 bg-stone-950/70 backdrop-blur" onClick={() => setOpenCart(false)}>
          <div className="absolute bottom-0 inset-x-0 bg-stone-900 border-t border-stone-800 p-6 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="font-display uppercase text-2xl">Your Order</div>
              <button onClick={() => setOpenCart(false)}><X size={18} /></button>
            </div>
            <ul className="divide-y divide-stone-800">
              {cartItems.map((it) => (
                <li key={it.key} className="py-3 flex items-center gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      {it.name}
                      {it.variant_label && (
                        <span className="text-[9px] font-mono uppercase tracking-widest border border-orange-500/60 text-orange-400 px-1.5 py-0.5">
                          {it.variant_label}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500">{currency(it.price)}</div>
                  </div>
                  <button onClick={() => dec(it.key)} className="w-7 h-7 border border-stone-700 flex items-center justify-center"><Minus size={12} /></button>
                  <span className="w-6 text-center font-mono">{it.qty}</span>
                  <button onClick={() => add(it.id, it.variant_label)} className="w-7 h-7 border border-orange-500 text-orange-400 flex items-center justify-center"><Plus size={12} /></button>
                </li>
              ))}
            </ul>
            <div className="mt-4 font-mono text-sm space-y-1 border-t border-stone-800 pt-4">
              <div className="flex justify-between text-stone-400"><span>{gst.inclusive ? "Taxable value" : "Subtotal"}</span><span>{currency(gst.inclusive ? taxableValue : subtotal)}</span></div>
              <div className="flex justify-between text-stone-400"><span>{gstLabel(gst)}</span><span>{currency(tax)}</span></div>
              <div className="flex justify-between text-base pt-2"><span>Total</span><span className="text-orange-400">{currency(total)}</span></div>
            </div>

            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-2">Payment preference</div>
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
                      pay === p.k ? "border-orange-500 text-orange-400" : "border-stone-800 text-stone-400"
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
              className="mt-5 w-full rounded-full bg-orange-600 hover:bg-orange-500 disabled:opacity-40 text-stone-950 py-3 font-mono uppercase tracking-widest text-xs"
            >
              {placing ? "Sending…" : "Place Order"}
            </button>
          </div>
        </div>
      )}

      {/* Placed toast card */}
      {placed && (
        <div className="fixed inset-0 z-50 bg-stone-950/85 backdrop-blur flex items-center justify-center p-6" onClick={() => setPlaced(null)}>
          <div className="border border-orange-500 bg-stone-900 p-8 max-w-md text-center" onClick={(e) => e.stopPropagation()}>
            <div className="text-orange-500 text-[10px] uppercase tracking-[0.4em] font-mono">Order confirmed</div>
            <div className="font-display text-4xl uppercase mt-2">Cheers!</div>
            <div className="text-stone-400 text-sm mt-3">
              Order <span className="font-mono text-orange-400">#{placed.order.id.slice(0, 6)}</span> sent to the bar.
              {placed.pay === "counter" ? " Pay at the counter when you're done." : ""}
            </div>
            <div className="mt-6 font-mono text-2xl text-orange-400">{currency(placed.order.total)}</div>
            <button onClick={() => setPlaced(null)} className="mt-8 rounded-full border border-stone-700 hover:border-orange-500 px-6 py-2 text-[10px] font-mono uppercase tracking-widest">
              Keep browsing
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
