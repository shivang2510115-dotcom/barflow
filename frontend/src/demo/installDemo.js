/**
 * Offline demo mode.
 *
 * Replaces the axios transport with an in-memory implementation of the BarFlow API so
 * the whole app runs from a single HTML file with no server. Enabled only when
 * REACT_APP_DEMO=1 at build time; a normal build never imports this module's behaviour.
 *
 * Mirrors backend/server.py. When an endpoint's shape changes there, change it here too.
 *
 * State lives in memory only: a refresh restores the seeded evening.
 */
import axios from "axios";
import seed from "./seed.json";
import { DOMAINS, OUTLET } from "@/lib/domains";

const clone = (v) => JSON.parse(JSON.stringify(v));
const uid = () =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });

// "Today" is pinned to the seeded evening so the dashboard is never empty, however
// long after the file was generated someone opens it.
const DEMO_TODAY = "2026-08-07";
const nowIso = () => new Date().toISOString();

// The logins the demo accepts. Declared before resetDb so that resetting restores them
// too: POST /auth/password below changes one, and a reset that put the tables back but
// left the password changed would leave the demo unable to log in as itself.
const SEED_PASSWORDS = Object.freeze({
  "admin@barflow.io": "admin123",
  "manager@barflow.io": "manager123",
  "waiter@barflow.io": "waiter123",
  "kitchen@barflow.io": "kitchen123",
});

let DEMO_PASSWORDS = { ...SEED_PASSWORDS };

let db = null;
function resetDb() {
  db = clone(seed);
  db.users = db.users || [];
  db.payment_transactions = [];
  DEMO_PASSWORDS = { ...SEED_PASSWORDS };
}
resetDb();

// `domains` and `active` are not decoration: the nav filters every domain-scoped item
// against them (components/app/AppLayout.jsx) and the analytics screen offers only the
// domains they name, so a demo user without them arrives to a sidebar of Overview and
// Analytics and nothing else. Mirrors the real /auth/login and /auth/me payloads.
const publicUser = (u) => ({
  id: u.id,
  email: u.email ?? null,
  // Either identifier gets somebody in, so both are returned. The account screen reads
  // this to say who is signed in and would otherwise show a blank for a phone-only user.
  phone: u.phone ?? null,
  name: u.name,
  role: u.role,
  domains: u.domains || [],
  active: u.active !== false,
});

// GET /api/staff, which returns rather more than /auth/me and never a password hash.
// Built key by key, as routers/staff.py::_public does, so a field added to the seed
// cannot leak by being forgotten.
const staffRow = (u) => ({
  id: u.id,
  name: u.name,
  email: u.email ?? null,
  phone: u.phone ?? null,
  role: u.role,
  domains: u.domains || [],
  active: u.active !== false,
  created_at: u.created_at,
});

/* ------------------------------------------------------------------ helpers */

// The canonical phone form, mirroring backend/services/identity.py: E.164, so that
// `9876543210`, `09876543210` and `+91 98765 43210` are one account here too. Cut down
// to what this file needs — the real one carries the reasoning and the refusal messages.
// Returns null for anything that is not an Indian mobile.
function demoPhone(value) {
  if (typeof value !== "string") return null;
  let digits = value.trim().replace(/[\s\-().]/g, "");
  if (digits.startsWith("+")) digits = digits.slice(1);
  if (!/^\d+$/.test(digits)) return null;
  for (const prefix of ["0091", "91", "0"]) {
    if (digits.startsWith(prefix) && digits.length === prefix.length + 10) {
      digits = digits.slice(prefix.length);
      break;
    }
  }
  return /^[6-9]\d{9}$/.test(digits) ? `+91${digits}` : null;
}

// Which of the two was typed at the sign-in box, in the form the account is stored under.
const demoIdentifier = (value) => {
  const typed = (value || "").trim();
  if (typed.includes("@")) return typed.toLowerCase();
  return demoPhone(typed) || typed.toLowerCase();
};

const TAX_RATE = 0.1;

function computeTotals(order) {
  const subtotal = order.items.reduce((s, i) => s + i.price * i.quantity, 0);
  const tax = Math.round(subtotal * TAX_RATE * 100) / 100;
  const discount = order.discount || 0;
  order.subtotal = Math.round(subtotal * 100) / 100;
  order.tax = tax;
  order.total = Math.round((subtotal + tax - discount) * 100) / 100;
  return order;
}

const activeAdmins = () => db.users.filter((u) => u.role === "admin" && u.active !== false);

const settledOrders = () => db.orders.filter((o) => o.status === "settled");
const dayOf = (o) => (o.settled_at || "").slice(0, 10);

function topItemsFrom(orders, limit) {
  const counts = {};
  orders.forEach((o) =>
    o.items.forEach((i) => {
      counts[i.name] = (counts[i.name] || 0) + i.quantity;
    }),
  );
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([name, qty]) => ({ name, qty }));
}

function buildBrief(date) {
  const d = date || DEMO_TODAY;
  const orders = settledOrders().filter((o) => dayOf(o) === d);
  const revenue = orders.reduce((s, o) => s + o.total, 0);
  const itemsSold = orders.reduce(
    (s, o) => s + o.items.reduce((n, i) => n + i.quantity, 0),
    0,
  );

  const pay = {};
  orders.forEach((o) => {
    const m = o.payment_method || "unknown";
    pay[m] = (pay[m] || 0) + o.total;
  });

  const cust = {};
  orders.forEach((o) => {
    const name = (o.customer_name || "").trim();
    if (!name) return;
    const key = (o.customer_phone || "").trim() || name.toLowerCase();
    cust[key] = cust[key] || { name, revenue: 0 };
    cust[key].revenue += o.total;
  });
  const best = Object.values(cust).sort((a, b) => b.revenue - a.revenue)[0] || null;

  const low = db.inventory.filter((i) => i.stock <= i.threshold).map((i) => i.name);
  const top = topItemsFrom(orders, 3);
  const r = (n) => `₹${Math.round(n)}`;

  const message = [
    `🍸 *BarFlow — Daily Brief*  (${d})`,
    "",
    `💰 Revenue: *${r(revenue)}*`,
    `🧾 Bills: ${orders.length}  ·  Items sold: ${itemsSold}`,
    `💳 ${Object.entries(pay)
      .map(([m, v]) => `${m}: ${r(v)}`)
      .join(" · ")}`,
    `🔥 Top: ${top.map((t) => `${t.name} ×${t.qty}`).join(", ")}`,
    best ? `⭐ Best customer: ${best.name} (${r(best.revenue)})` : "",
    low.length ? `📦 Low stock: ${low.join(", ")}` : "",
    "",
    "— sent automatically by BarFlow",
  ]
    .filter(Boolean)
    .join("\n");

  return {
    date: d,
    revenue: Math.round(revenue * 100) / 100,
    bills: orders.length,
    items_sold: itemsSold,
    top_items: top,
    best_customer: best ? { name: best.name, revenue: Math.round(best.revenue * 100) / 100 } : null,
    payment_split: pay,
    low_stock: low,
    message,
  };
}

function analytics(q) {
  const end = q.end || DEMO_TODAY;
  const start = q.start || DEMO_TODAY.slice(0, 8) + "01";
  const granularity = q.granularity === "month" ? "month" : "day";

  const inRange = settledOrders().filter((o) => {
    const d = dayOf(o);
    return d && d >= start && d <= end;
  });

  const revenue = inRange.reduce((s, o) => s + o.total, 0);
  const itemsSold = inRange.reduce(
    (s, o) => s + o.items.reduce((n, i) => n + i.quantity, 0),
    0,
  );

  const buckets = {};
  inRange.forEach((o) => {
    const d = dayOf(o);
    const key = granularity === "month" ? d.slice(0, 7) : d;
    buckets[key] = buckets[key] || { revenue: 0, orders: 0 };
    buckets[key].revenue += o.total;
    buckets[key].orders += 1;
  });

  const itemStats = {};
  inRange.forEach((o) =>
    o.items.forEach((i) => {
      itemStats[i.name] = itemStats[i.name] || { qty: 0, revenue: 0 };
      itemStats[i.name].qty += i.quantity;
      itemStats[i.name].revenue += i.price * i.quantity;
    }),
  );

  const pay = {};
  inRange.forEach((o) => {
    const m = o.payment_method || "unknown";
    pay[m] = pay[m] || { revenue: 0, orders: 0 };
    pay[m].revenue += o.total;
    pay[m].orders += 1;
  });

  const cust = {};
  inRange.forEach((o) => {
    const name = (o.customer_name || "").trim();
    if (!name) return;
    const phone = (o.customer_phone || "").trim();
    const key = phone || name.toLowerCase();
    cust[key] = cust[key] || { name, phone, revenue: 0, orders: 0 };
    cust[key].revenue += o.total;
    cust[key].orders += 1;
  });

  const round = (n) => Math.round(n * 100) / 100;

  return {
    range: { start, end, granularity },
    totals: {
      revenue: round(revenue),
      orders: inRange.length,
      avg_order_value: inRange.length ? round(revenue / inRange.length) : 0,
      items_sold: itemsSold,
    },
    series: Object.entries(buckets)
      .sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .map(([bucket, v]) => ({ bucket, revenue: round(v.revenue), orders: v.orders })),
    top_items: Object.entries(itemStats)
      .map(([name, v]) => ({ name, qty: v.qty, revenue: round(v.revenue) }))
      .sort((a, b) => b.qty - a.qty)
      .slice(0, 10),
    payment_mix: Object.entries(pay)
      .map(([method, v]) => ({ method, revenue: round(v.revenue), orders: v.orders }))
      .sort((a, b) => b.revenue - a.revenue),
    top_customers: Object.values(cust)
      .map((c) => ({ ...c, revenue: round(c.revenue) }))
      .sort((a, b) => b.revenue - a.revenue)
      .slice(0, 10),
  };
}

/** Every calendar day in an inclusive range, as the real endpoint's `by_day` spans it. */
function daysBetween(start, end) {
  const out = [];
  const cur = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  if (isNaN(cur) || isNaN(last)) throw { status: 400, detail: "start and end must be YYYY-MM-DD dates" };
  if (cur > last) throw { status: 400, detail: "start must not be after end" };
  while (cur <= last) {
    out.push(cur.toISOString().slice(0, 10));
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return out;
}

/**
 * GET /api/analytics/revenue — the cross-domain report, shaped exactly as
 * routers/analytics.py returns it: {start, end, domains, total, hotel, outlets, by_day},
 * with `outlets.covers` naming what the outlet figure actually contains.
 *
 * The hotel block is zeros rather than null whenever hotel is selected: this seed has no
 * bookings or folios at all, so there genuinely is no room revenue to report, and a null
 * would make the screen render "—" as though the question had not been asked. Seed some
 * stays here and this is where they would be counted.
 */
function revenueReport(q) {
  const end = q.end || DEMO_TODAY;
  const start = q.start || DEMO_TODAY.slice(0, 8) + "01";
  const asked = String(q.domains || "")
    .split(",")
    .map((d) => d.trim())
    .filter(Boolean);
  for (const d of asked) {
    if (!DOMAINS.includes(d)) throw { status: 422, detail: `Unknown domain: ${d}` };
  }
  // No selection means everything, the way the server defaults to the caller's own
  // domains — and every demo user holds at least one outlet.
  const picked = (asked.length ? asked : DOMAINS).slice().sort();
  const days = daysBetween(start, end);

  const round = (n) => Math.round(n * 100) / 100;
  const hotel = picked.includes("hotel")
    ? { total: 0, room_nights: 0, extras: 0 }
    : null;

  let outlets = null;
  const perDay = {};
  days.forEach((d) => {
    perDay[d] = 0;
  });
  if (picked.some((d) => OUTLET.includes(d))) {
    // One till for the restaurant and the bar, so selecting both must not count twice.
    const inRange = settledOrders().filter((o) => {
      const d = dayOf(o);
      return d && d >= start && d <= end;
    });
    inRange.forEach((o) => {
      if (perDay[dayOf(o)] != null) perDay[dayOf(o)] += o.total;
    });
    outlets = {
      total: round(inRange.reduce((s, o) => s + o.total, 0)),
      orders: inRange.length,
      covers: [...OUTLET],
    };
  }

  return {
    start,
    end,
    domains: picked,
    total: round((hotel ? hotel.total : 0) + (outlets ? outlets.total : 0)),
    hotel,
    outlets,
    by_day: days.map((d) => ({
      date: d,
      hotel: 0,
      outlets: round(outlets ? perDay[d] : 0),
      total: round(outlets ? perDay[d] : 0),
    })),
  };
}

/* ------------------------------------------------------------------- routes */

const ROUTES = [
  // ---- auth
  // `identifier` with `email` still accepted, exactly as the API does it: the field was
  // renamed because it carries a phone number now, and the alias is what keeps every
  // older caller working.
  ["POST", /^\/auth\/login$/, (m, body) => {
    const typed = demoIdentifier(body.identifier ?? body.email);
    const user = db.users.find((u) =>
      typed.includes("@") ? u.email === typed : demoPhone(u.phone) === typed);
    // Keyed on the email because that is what the demo's fixed password table is keyed
    // on. Every seeded demo login has one; a phone-only account created from the staff
    // screen has no password here either way, which is the same as it was before.
    if (!user || DEMO_PASSWORDS[(user.email || "").toLowerCase()] !== body.password) {
      throw { status: 401, detail: "Invalid email, phone number or password" };
    }
    return { token: `demo.${user.id}`, user: publicUser(user) };
  }],
  ["GET", /^\/auth\/me$/, () => publicUser(db.users[0])],
  ["GET", /^\/auth\/staff$/, () => db.users.map(publicUser)],

  // The account screen's "change my own password". Without it the Password control in
  // the sidebar is a button that answers "No demo route", which is worse than not
  // offering it. The demo has no session — /auth/me answers as the first seeded user —
  // so "the caller" is that same user here, which is the convention the rest of this
  // file already follows.
  //
  // It refuses the wrong current password, because that refusal is the point of the
  // screen and a demo that accepted anything would misrepresent it. The strength rule is
  // the length check the demo applies everywhere else; the real denylist lives in
  // backend/services/password.py and is not worth shipping to an offline file.
  ["POST", /^\/auth\/password$/, (m, body) => {
    const user = db.users[0];
    const email = (user.email || "").toLowerCase();
    if (DEMO_PASSWORDS[email] !== body.current_password) {
      throw { status: 401, detail: "That is not your current password" };
    }
    if ((body.new_password || "").length < 8) {
      throw { status: 400, detail: "Password must be at least 8 characters" };
    }
    if (body.new_password === body.current_password) {
      throw {
        status: 400,
        detail: "That is the password you already have. Choose a different one.",
      };
    }
    DEMO_PASSWORDS[email] = body.new_password;
    return { ok: true };
  }],

  // ---- staff (admin console)
  // The demo has no session — /auth/me always answers as the first seeded user — so the
  // "you cannot demote or deactivate yourself" 409s have nothing to compare against and
  // are left to the screen, which already disables those controls on your own row. The
  // rules that do not need an identity are kept, so the demo refuses what the API
  // refuses.
  ["GET", /^\/staff$/, () =>
    db.users
      .slice()
      .sort((a, b) => ((a.name || "") < (b.name || "") ? -1 : 1))
      .map(staffRow),
  ],
  ["POST", /^\/staff$/, (m, body) => {
    const role = body.role || "waiter";
    const domains = [...new Set(body.domains || [])];
    if (role !== "admin" && domains.length === 0) {
      throw { status: 400, detail: "A non-admin needs at least one work domain" };
    }
    if ((body.password || "").length < 8) {
      throw { status: 400, detail: "Password must be at least 8 characters" };
    }
    const email = (body.email || "").toLowerCase().trim() || null;
    // Either identifier, at least one of the two — the rule POST /api/staff applies, and
    // the reason this whole change exists: a waiter with no email address had to be given
    // an invented one, which cannot receive a password reset.
    const typedPhone = (body.phone || "").trim();
    const phone = typedPhone ? demoPhone(typedPhone) : null;
    if (typedPhone && !phone) {
      throw { status: 400, detail: "That is not a phone number this can store." };
    }
    if (!email && !phone) {
      throw {
        status: 400,
        detail: "This account needs an email address or a phone number — with neither, "
          + "there is nothing for them to type at the sign-in box.",
      };
    }
    if (email && db.users.some((u) => u.email === email)) {
      throw { status: 409, detail: "A staff member with this email already exists" };
    }
    if (phone && db.users.some((u) => demoPhone(u.phone) === phone)) {
      throw { status: 409, detail: "A staff member with this phone number already exists" };
    }
    const user = {
      id: uid(), name: (body.name || "").trim(), email, phone, role,
      // An admin created with nothing ticked holds everything, as POST /api/staff does:
      // no route may persist an empty domain list.
      domains: role === "admin" && domains.length === 0 ? [...DOMAINS] : domains,
      active: true, created_at: nowIso(),
    };
    db.users.push(user);
    return staffRow(user);
  }],
  ["PUT", /^\/staff\/([^/]+)$/, (m, body) => {
    const u = db.users.find((x) => x.id === m[1]);
    if (!u) throw { status: 404, detail: "Staff member not found" };
    const role = body.role || u.role;
    const domains = [...new Set(body.domains || [])];
    if (role !== "admin" && domains.length === 0) {
      throw { status: 400, detail: "A non-admin needs at least one work domain" };
    }
    if (u.role === "admin" && role !== "admin" && activeAdmins().length === 1) {
      throw { status: 409, detail: "This would leave the property with no active admin" };
    }
    u.name = (body.name || "").trim();
    u.role = role;
    u.domains = role === "admin" && domains.length === 0 ? [...DOMAINS] : domains;
    return staffRow(u);
  }],
  ["POST", /^\/staff\/([^/]+)\/active$/, (m, body) => {
    const u = db.users.find((x) => x.id === m[1]);
    if (!u) throw { status: 404, detail: "Staff member not found" };
    if (!body.active && u.role === "admin" && activeAdmins().length === 1) {
      throw { status: 409, detail: "This would leave the property with no active admin" };
    }
    u.active = Boolean(body.active);
    return staffRow(u);
  }],
  ["POST", /^\/staff\/([^/]+)\/password$/, (m, body) => {
    const u = db.users.find((x) => x.id === m[1]);
    if (!u) throw { status: 404, detail: "Staff member not found" };
    if ((body.password || "").length < 8) {
      throw { status: 400, detail: "Password must be at least 8 characters" };
    }
    // Accepted and discarded: log-in here checks a fixed table of demo passwords, and
    // rewriting that from the console would lock the next visitor out of the demo.
    return { ok: true };
  }],

  // ---- analytics
  ["GET", /^\/analytics\/revenue$/, (m, body, q) => revenueReport(q)],

  // ---- tables
  ["GET", /^\/tables$/, () => db.tables],
  ["GET", /^\/tables\/public\/([^/]+)$/, (m) => {
    const t = db.tables.find((x) => x.id === m[1]);
    if (!t) throw { status: 404, detail: "Table not found" };
    return { id: t.id, label: t.label, zone: t.zone, capacity: t.capacity };
  }],
  ["POST", /^\/tables$/, (m, body) => {
    const t = { id: uid(), label: body.label, capacity: body.capacity || 4, zone: body.zone || "Bar", status: "free", current_order_id: null };
    db.tables.push(t);
    return t;
  }],
  ["DELETE", /^\/tables\/([^/]+)$/, (m) => {
    db.tables = db.tables.filter((t) => t.id !== m[1]);
    return { ok: true };
  }],

  // ---- reservations
  ["GET", /^\/reservations$/, (m, body, q) =>
    db.reservations
      .filter((r) => (q.date ? r.date === q.date : true))
      .sort((a, b) => (a.time < b.time ? -1 : 1)),
  ],
  ["POST", /^\/reservations$/, (m, body) => {
    const r = {
      id: uid(), guest_name: body.guest_name, phone: body.phone || null,
      party_size: body.party_size || 2, date: body.date, time: body.time,
      table_id: body.table_id || null, table_label: body.table_label || null,
      status: "booked", notes: body.notes || null, created_at: nowIso(),
    };
    db.reservations.push(r);
    if (body.hold_table && r.table_id) {
      const t = db.tables.find((x) => x.id === r.table_id);
      if (t && t.status === "free") t.status = "reserved";
    }
    return r;
  }],
  ["PUT", /^\/reservations\/([^/]+)\/status$/, (m, body) => {
    const r = db.reservations.find((x) => x.id === m[1]);
    if (!r) throw { status: 404, detail: "Reservation not found" };
    r.status = body.status;
    return r;
  }],
  ["DELETE", /^\/reservations\/([^/]+)$/, (m) => {
    db.reservations = db.reservations.filter((r) => r.id !== m[1]);
    return { ok: true };
  }],

  // ---- menu
  ["GET", /^\/menu$/, () => db.menu],
  ["POST", /^\/menu$/, (m, body) => {
    const item = { ...body, id: uid(), available: body.available !== false };
    db.menu.push(item);
    return item;
  }],
  ["PUT", /^\/menu\/([^/]+)$/, (m, body) => {
    const i = db.menu.findIndex((x) => x.id === m[1]);
    if (i < 0) throw { status: 404, detail: "Item not found" };
    db.menu[i] = { ...db.menu[i], ...body, id: m[1] };
    return db.menu[i];
  }],
  ["DELETE", /^\/menu\/([^/]+)$/, (m) => {
    db.menu = db.menu.filter((x) => x.id !== m[1]);
    return { ok: true };
  }],

  // ---- orders
  ["POST", /^\/orders\/table\/([^/]+)\/items$/, (m, body) => {
    const table = db.tables.find((t) => t.id === m[1]);
    if (!table) throw { status: 404, detail: "Table not found" };

    let order = db.orders.find((o) => o.table_id === table.id && o.status === "open");
    if (!order) {
      order = {
        id: uid(), table_id: table.id, table_label: table.label, status: "open",
        items: [], subtotal: 0, tax: 0, discount: 0, total: 0,
        payment_method: null, customer_name: null, customer_phone: null,
        source: body.source || "pos", created_at: nowIso(), settled_at: null,
      };
      db.orders.push(order);
      table.status = "occupied";
      table.current_order_id = order.id;
    }

    (body.items || []).forEach((it) => {
      const mi = db.menu.find((x) => x.id === it.menu_item_id);
      if (!mi) return;
      order.items.push({
        id: uid(), menu_item_id: mi.id, name: mi.name, price: mi.price,
        quantity: it.quantity || 1, station: mi.station, notes: it.notes || "",
        status: "pending", created_at: nowIso(),
      });
    });

    return computeTotals(order);
  }],
  ["GET", /^\/orders\/table\/([^/]+)\/current$/, (m) => {
    const o = db.orders.find((x) => x.table_id === m[1] && x.status === "open");
    return o ? computeTotals(o) : null;
  }],
  ["GET", /^\/orders\/kot$/, () =>
    db.orders
      .filter((o) => o.status === "open")
      .map((o) => ({
        order_id: o.id,
        table_label: o.table_label,
        created_at: o.created_at,
        items: o.items.filter((i) => i.status === "pending" || i.status === "preparing"),
      }))
      .filter((t) => t.items.length > 0),
  ],
  ["PUT", /^\/orders\/([^/]+)\/items\/([^/]+)\/status$/, (m, body) => {
    const o = db.orders.find((x) => x.id === m[1]);
    if (!o) throw { status: 404, detail: "Order not found" };
    const it = o.items.find((x) => x.id === m[2]);
    if (!it) throw { status: 404, detail: "Item not found" };
    it.status = body.status;
    return o;
  }],
  ["POST", /^\/orders\/([^/]+)\/settle$/, (m, body) => {
    const o = db.orders.find((x) => x.id === m[1]);
    if (!o) throw { status: 404, detail: "Order not found" };
    o.discount = body.discount || 0;
    computeTotals(o);
    o.status = "settled";
    o.payment_method = body.payment_method;
    o.customer_name = (body.customer_name || "").trim() || null;
    o.customer_phone = (body.customer_phone || "").trim() || null;
    // Settled bills must land on the seeded day, or the dashboard's "today"
    // totals would ignore every sale made during the demo.
    o.settled_at = `${DEMO_TODAY}T${new Date().toISOString().slice(11)}`;
    const t = db.tables.find((x) => x.id === o.table_id);
    if (t) {
      t.status = "free";
      t.current_order_id = null;
    }
    return o;
  }],
  ["DELETE", /^\/orders\/([^/]+)\/items\/([^/]+)$/, (m) => {
    const o = db.orders.find((x) => x.id === m[1]);
    if (!o) throw { status: 404, detail: "Order not found" };
    o.items = o.items.filter((i) => i.id !== m[2]);
    return computeTotals(o);
  }],

  // ---- inventory
  ["GET", /^\/inventory$/, () => db.inventory],
  ["POST", /^\/inventory$/, (m, body) => {
    const it = { ...body, id: uid() };
    db.inventory.push(it);
    return it;
  }],
  ["PUT", /^\/inventory\/([^/]+)$/, (m, body) => {
    const i = db.inventory.findIndex((x) => x.id === m[1]);
    if (i < 0) throw { status: 404, detail: "Item not found" };
    db.inventory[i] = { ...db.inventory[i], ...body, id: m[1] };
    return db.inventory[i];
  }],
  ["POST", /^\/inventory\/([^/]+)\/adjust$/, (m, body) => {
    const it = db.inventory.find((x) => x.id === m[1]);
    if (!it) throw { status: 404, detail: "Item not found" };
    it.stock = Math.max(0, (it.stock || 0) + (body.delta || 0));
    return it;
  }],
  ["DELETE", /^\/inventory\/([^/]+)$/, (m) => {
    db.inventory = db.inventory.filter((x) => x.id !== m[1]);
    return { ok: true };
  }],

  // ---- reports
  ["GET", /^\/reports\/summary$/, () => {
    const settled = settledOrders();
    const today = settled.filter((o) => dayOf(o) === DEMO_TODAY);
    const days = {};
    settled.forEach((o) => {
      const d = dayOf(o);
      if (d) days[d] = (days[d] || 0) + o.total;
    });
    const daily = Object.entries(days)
      .sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .slice(-7)
      .map(([date, revenue]) => ({ date, revenue: Math.round(revenue * 100) / 100 }));

    return {
      revenue_today: Math.round(today.reduce((s, o) => s + o.total, 0) * 100) / 100,
      revenue_total: Math.round(settled.reduce((s, o) => s + o.total, 0) * 100) / 100,
      orders_today: today.length,
      orders_total: settled.length,
      tables_total: db.tables.length,
      tables_occupied: db.tables.filter((t) => t.status === "occupied").length,
      low_stock_count: db.inventory.filter((i) => i.stock <= i.threshold).length,
      top_items: topItemsFrom(settled, 5),
      daily_revenue: daily,
    };
  }],
  ["GET", /^\/reports\/orders$/, () =>
    settledOrders()
      .slice()
      .sort((a, b) => (a.settled_at < b.settled_at ? 1 : -1))
      .slice(0, 200),
  ],
  ["GET", /^\/reports\/analytics$/, (m, body, q) => analytics(q)],
  ["GET", /^\/reports\/daily-brief$/, (m, body, q) => buildBrief(q.date)],
  ["POST", /^\/reports\/daily-brief\/send$/, (m, body) => ({
    ...buildBrief(body && body.date),
    sent: false,
    // No WhatsApp credentials can exist in a static file, so say so plainly rather
    // than reporting a delivery that never happened.
    detail: "Preview only — this offline demo cannot send WhatsApp messages.",
  })],

  // ---- payments (inert in the demo)
  ["POST", /^\/payments\/checkout\/session$/, () => {
    const id = uid();
    db.payment_transactions.push({ session_id: id, status: "open" });
    return { session_id: id, url: null, demo: true };
  }],
  ["GET", /^\/payments\/checkout\/status\/([^/]+)$/, (m) => ({
    session_id: m[1],
    payment_status: "unpaid",
    status: "open",
    demo: true,
  })],
];

/* ------------------------------------------------------------------ adapter */

function resolvePath(url, baseURL) {
  let p = url || "";
  if (baseURL && p.startsWith(baseURL)) p = p.slice(baseURL.length);
  p = p.replace(/^https?:\/\/[^/]+/, "");
  p = p.replace(/^\/api/, "");
  const qi = p.indexOf("?");
  return qi >= 0 ? p.slice(0, qi) : p;
}

function demoAdapter(config) {
  const method = (config.method || "get").toUpperCase();
  const path = resolvePath(config.url, config.baseURL);

  let body = config.data;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch {
      body = {};
    }
  }
  body = body || {};

  const q = {};
  if (config.params) Object.assign(q, config.params);
  const qi = (config.url || "").indexOf("?");
  if (qi >= 0) {
    new URLSearchParams(config.url.slice(qi + 1)).forEach((v, k) => {
      q[k] = v;
    });
  }

  const respond = (status, data) => ({
    data, status,
    statusText: status === 200 ? "OK" : "Error",
    headers: {}, config,
  });

  return new Promise((resolve, reject) => {
    // A touch of latency so loading states are visible rather than flashing.
    setTimeout(() => {
      for (const [verb, re, handler] of ROUTES) {
        if (verb !== method) continue;
        const match = re.exec(path);
        if (!match) continue;
        try {
          resolve(respond(200, handler(match, body, q)));
        } catch (e) {
          const status = e && e.status ? e.status : 500;
          const err = new Error(e && e.detail ? e.detail : "Demo error");
          err.response = respond(status, { detail: e && e.detail ? e.detail : "Demo error" });
          err.isAxiosError = true;
          reject(err);
        }
        return;
      }
      const err = new Error(`Not found: ${method} ${path}`);
      err.response = respond(404, { detail: `No demo route for ${method} ${path}` });
      err.isAxiosError = true;
      reject(err);
    }, 90);
  });
}

export function installDemo() {
  // Set on axios.defaults so every instance inherits it — including the separate
  // client created inside CustomerMenu.jsx. This module must therefore be imported
  // before any module that calls axios.create() at load time.
  axios.defaults.adapter = demoAdapter;
  if (typeof window !== "undefined") {
    window.__BARFLOW_DEMO__ = { reset: resetDb, db: () => db };
  }
}

export default installDemo;
