import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, currency } from "@/lib/api";
import { toast } from "sonner";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

// ---- date helpers (local time, YYYY-MM-DD) ----
function toStr(d) {
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60000).toISOString().slice(0, 10);
}
function today() {
  return toStr(new Date());
}
function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return toStr(d);
}
function startOfMonth() {
  const d = new Date();
  return toStr(new Date(d.getFullYear(), d.getMonth(), 1));
}
function startOfYear() {
  const d = new Date();
  return toStr(new Date(d.getFullYear(), 0, 1));
}

const PRESETS = [
  { key: "today", label: "Today", get: () => ({ start: today(), end: today(), granularity: "day" }) },
  { key: "7d", label: "7 Days", get: () => ({ start: daysAgo(6), end: today(), granularity: "day" }) },
  { key: "month", label: "This Month", get: () => ({ start: startOfMonth(), end: today(), granularity: "day" }) },
  { key: "year", label: "This Year", get: () => ({ start: startOfYear(), end: today(), granularity: "month" }) },
  { key: "custom", label: "Custom", get: null },
];

const PAY_COLORS = { cash: "#f97316", card: "#38bdf8", online: "#a78bfa", unknown: "#78716c" };
const ORANGE = "#f97316";
const GRID = "#292524";
const AXIS = "#78716c";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function bucketLabel(bucket, granularity) {
  if (granularity === "month") {
    const [y, m] = bucket.split("-");
    return `${MONTHS[Number(m) - 1]} ${String(y).slice(2)}`;
  }
  const [, m, d] = bucket.split("-");
  return `${m}/${d}`;
}

function ChartTooltip({ active, payload, label, granularity, moneyKeys = ["revenue"] }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="bg-stone-950 border border-stone-700 px-3 py-2 text-xs font-mono">
      {label != null && (
        <div className="text-stone-400 mb-1">{granularity ? bucketLabel(label, granularity) : label}</div>
      )}
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2" style={{ color: p.color || p.fill }}>
          <span className="uppercase tracking-widest">{p.name}:</span>
          <span>{moneyKeys.includes(p.dataKey) ? currency(p.value) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

export default function Reports() {
  const [preset, setPreset] = useState("month");
  const [range, setRange] = useState(PRESETS.find((p) => p.key === "month").get());
  const [data, setData] = useState(null);
  const [todayData, setTodayData] = useState(null);
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [brief, setBrief] = useState(null);
  const [sending, setSending] = useState(false);

  const applyPreset = (key) => {
    setPreset(key);
    const p = PRESETS.find((x) => x.key === key);
    if (p && p.get) setRange(p.get());
  };

  const fetchAnalytics = useCallback(() => {
    setLoading(true);
    const params = { start: range.start, end: range.end, granularity: range.granularity };
    Promise.all([
      api.get("/reports/analytics", { params }),
      api.get("/reports/analytics", { params: { start: today(), end: today(), granularity: "day" } }),
      api.get("/reports/orders"),
      api.get("/reports/daily-brief", { params: { date: today() } }),
    ])
      .then(([a, t, o, b]) => {
        setData(a.data);
        setTodayData(t.data);
        setOrders(o.data);
        setBrief(b.data);
      })
      .finally(() => setLoading(false));
  }, [range]);

  const sendBrief = async () => {
    setSending(true);
    try {
      const { data } = await api.post("/reports/daily-brief/send", { date: today() });
      setBrief(data.brief);
      if (data.delivery?.sent) toast.success("Daily brief sent to owner on WhatsApp");
      else if (data.delivery?.mock)
        toast.success("Brief ready (preview mode). Add WhatsApp creds to send for real.");
      else toast.error(`Send failed: ${data.delivery?.error || "unknown"}`);
    } catch {
      toast.error("Could not send brief");
    } finally {
      setSending(false);
    }
  };

  useEffect(() => {
    fetchAnalytics();
  }, [fetchAnalytics]);

  const totals = data?.totals;
  const series = data?.series || [];
  const topItems = data?.top_items || [];
  const topCustomers = data?.top_customers || [];
  const paymentMix = data?.payment_mix || [];
  const hasSeries = series.length > 0;

  const rangeLabel = useMemo(() => {
    if (!data?.range) return "";
    const { start, end } = data.range;
    return start === end ? start : `${start} → ${end}`;
  }, [data]);

  return (
    <div className="p-6 md:p-10">
      <div className="flex items-baseline justify-between flex-wrap gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-2">Analytics</div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">Sales Analytics</h1>
        </div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-stone-500">
          {loading ? "Loading…" : rangeLabel}
        </div>
      </div>

      {/* Today snapshot */}
      <div className="mt-8 border border-stone-800 bg-stone-900/40 p-5">
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-3">Today · {today()}</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 items-start">
          <Kpi label="Revenue Today" value={currency(todayData?.totals?.revenue)} accent />
          <Kpi label="Orders Today" value={todayData?.totals?.orders ?? "—"} />
          <div className="md:col-span-2">
            <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-2">Top Items Today</div>
            {todayData?.top_items?.length ? (
              <div className="space-y-1.5">
                {todayData.top_items.slice(0, 3).map((it, i) => (
                  <div key={it.name} className="flex items-center justify-between text-sm" data-testid={`top-today-${i}`}>
                    <span className="truncate text-stone-200">
                      <span className="text-stone-600 font-mono mr-2">{i + 1}</span>
                      {it.name}
                    </span>
                    <span className="font-mono text-orange-400 shrink-0 ml-3">×{it.qty}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs font-mono text-stone-600">No sales yet today.</div>
            )}
          </div>
        </div>
      </div>

      {/* Owner Daily Brief (WhatsApp) */}
      <div className="mt-8 grid md:grid-cols-[1fr_360px] gap-5 items-start">
        <div className="border border-stone-800 bg-stone-900/40 p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500">Owner Daily Brief · WhatsApp</div>
            <button
              data-testid="send-brief-btn"
              onClick={sendBrief}
              disabled={sending}
              className="rounded-full bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-stone-950 px-4 py-2 text-[10px] font-mono uppercase tracking-widest transition-colors"
            >
              {sending ? "Sending…" : "Send now"}
            </button>
          </div>
          {/* WhatsApp-style preview bubble */}
          <div className="bg-[#0b141a] border border-stone-800 p-4 rounded-md max-w-md">
            <div className="bg-[#005c4b] text-stone-50 rounded-lg rounded-tr-none p-3 text-sm whitespace-pre-wrap font-sans leading-relaxed">
              {brief?.message || "Loading brief…"}
            </div>
            <div className="text-[9px] font-mono text-stone-600 mt-2 text-right">preview · WhatsApp</div>
          </div>
        </div>
        <div className="border border-stone-800 bg-stone-900/40 p-5">
          <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-2">Automatic</div>
          <div className="text-sm text-stone-300 leading-relaxed">
            Sends to the owner every night at <span className="text-orange-400 font-mono">23:00</span>, no login needed.
          </div>
          <div className="text-xs text-stone-500 mt-3 leading-relaxed">
            Revenue, bills, top items, best customer, and low-stock alerts — straight to WhatsApp.
          </div>
          <div className="text-[10px] font-mono text-stone-600 mt-4 border-t border-stone-800 pt-3">
            {brief && !brief.revenue && brief.bills === 0
              ? "No sales today yet — brief shows live once bills settle."
              : "Preview mode until WhatsApp API creds are set."}
          </div>
        </div>
      </div>

      {/* Range controls */}
      <div className="mt-8 flex flex-wrap items-end gap-4">
        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-2">Range</label>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                data-testid={`preset-${p.key}`}
                onClick={() => applyPreset(p.key)}
                className={`px-3 py-1.5 text-[10px] font-mono uppercase tracking-widest border transition-colors ${
                  preset === p.key
                    ? "border-orange-500 text-orange-400 bg-stone-900"
                    : "border-stone-800 text-stone-400 hover:border-stone-600"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {preset === "custom" && (
          <div className="flex items-end gap-2">
            <div>
              <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">From</label>
              <input
                data-testid="range-start"
                type="date"
                value={range.start}
                max={range.end}
                onChange={(e) => setRange((r) => ({ ...r, start: e.target.value }))}
                className="bg-stone-900 border border-stone-700 py-1.5 px-2 text-sm focus-neon"
              />
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">To</label>
              <input
                data-testid="range-end"
                type="date"
                value={range.end}
                min={range.start}
                max={today()}
                onChange={(e) => setRange((r) => ({ ...r, end: e.target.value }))}
                className="bg-stone-900 border border-stone-700 py-1.5 px-2 text-sm focus-neon"
              />
            </div>
          </div>
        )}

        <div>
          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">Group by</label>
          <div className="flex gap-2">
            {["day", "month"].map((g) => (
              <button
                key={g}
                data-testid={`gran-${g}`}
                onClick={() => setRange((r) => ({ ...r, granularity: g }))}
                className={`px-3 py-1.5 text-[10px] font-mono uppercase tracking-widest border transition-colors ${
                  range.granularity === g
                    ? "border-orange-500 text-orange-400 bg-stone-900"
                    : "border-stone-800 text-stone-400 hover:border-stone-600"
                }`}
              >
                {g}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Range KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-8">
        <Kpi label="Revenue" value={currency(totals?.revenue)} accent />
        <Kpi label="Orders" value={totals?.orders ?? "—"} />
        <Kpi label="Avg Order" value={currency(totals?.avg_order_value)} />
        <Kpi label="Items Sold" value={totals?.items_sold ?? "—"} />
      </div>

      {/* Revenue trend */}
      <Section title={`Revenue Trend · by ${range.granularity}`}>
        {hasSeries ? (
          <div className="h-72" data-testid="revenue-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ORANGE} stopOpacity={0.5} />
                    <stop offset="100%" stopColor={ORANGE} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis
                  dataKey="bucket"
                  tickFormatter={(b) => bucketLabel(b, range.granularity)}
                  tick={{ fill: AXIS, fontSize: 11 }}
                  stroke={GRID}
                />
                <YAxis tick={{ fill: AXIS, fontSize: 11 }} stroke={GRID} tickFormatter={(v) => `₹${v}`} width={56} />
                <Tooltip content={<ChartTooltip granularity={range.granularity} />} />
                <Area type="monotone" dataKey="revenue" name="Revenue" stroke={ORANGE} strokeWidth={2} fill="url(#rev)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty>No sales in this range.</Empty>
        )}
      </Section>

      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        {/* Top items */}
        <Section title="Top Selling Items">
          {topItems.length ? (
            <div className="h-72" data-testid="top-items-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topItems.slice(0, 8)} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" tick={{ fill: AXIS, fontSize: 11 }} stroke={GRID} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    tick={{ fill: AXIS, fontSize: 11 }}
                    stroke={GRID}
                    width={110}
                  />
                  <Tooltip cursor={{ fill: "#ffffff08" }} content={<ChartTooltip moneyKeys={["revenue"]} />} />
                  <Bar dataKey="qty" name="Qty" fill={ORANGE} radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty>No items sold in this range.</Empty>
          )}
        </Section>

        {/* Payment mix */}
        <Section title="Payment Mix">
          {paymentMix.length ? (
            <div className="h-72 flex items-center" data-testid="payment-mix-chart">
              <ResponsiveContainer width="60%" height="100%">
                <PieChart>
                  <Pie
                    data={paymentMix}
                    dataKey="revenue"
                    nameKey="method"
                    innerRadius={55}
                    outerRadius={90}
                    paddingAngle={2}
                    stroke="none"
                  >
                    {paymentMix.map((p) => (
                      <Cell key={p.method} fill={PAY_COLORS[p.method] || PAY_COLORS.unknown} />
                    ))}
                  </Pie>
                  <Tooltip content={<ChartTooltip moneyKeys={["revenue"]} />} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2">
                {paymentMix.map((p) => (
                  <div key={p.method} className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2">
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-full"
                        style={{ background: PAY_COLORS[p.method] || PAY_COLORS.unknown }}
                      />
                      <span className="uppercase font-mono text-xs tracking-widest text-stone-300">{p.method}</span>
                    </span>
                    <span className="font-mono text-orange-400">{currency(p.revenue)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <Empty>No payments in this range.</Empty>
          )}
        </Section>
      </div>

      {/* Top customers */}
      <Section title={`Top 10 Customers · ${preset === "year" ? "this year" : rangeLabel}`}>
        {topCustomers.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-stone-800 text-[10px] uppercase tracking-widest font-mono text-stone-500">
                  <th className="p-3 w-10">#</th>
                  <th className="p-3">Customer</th>
                  <th className="p-3">Phone</th>
                  <th className="p-3 text-right">Orders</th>
                  <th className="p-3 text-right">Spend</th>
                </tr>
              </thead>
              <tbody>
                {topCustomers.map((c, i) => (
                  <tr key={`${c.name}-${c.phone}-${i}`} className="border-b border-stone-800/60" data-testid={`top-customer-${i}`}>
                    <td className="p-3 font-mono text-stone-600">{i + 1}</td>
                    <td className="p-3 text-stone-100">{c.name}</td>
                    <td className="p-3 font-mono text-xs text-stone-500">{c.phone || "—"}</td>
                    <td className="p-3 font-mono text-right text-stone-400">{c.orders}</td>
                    <td className="p-3 font-mono text-right text-orange-400">{currency(c.revenue)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>
            No customer data in this range. Capture a name/phone at billing (POS bill panel) to build this list.
          </Empty>
        )}
      </Section>

      {/* Recent orders */}
      <Section title="Recent Settled Orders">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-stone-800 text-[10px] uppercase tracking-widest font-mono text-stone-500">
                <th className="p-3">Order</th>
                <th className="p-3">Table</th>
                <th className="p-3">Customer</th>
                <th className="p-3">Items</th>
                <th className="p-3">Payment</th>
                <th className="p-3 text-right">Total</th>
                <th className="p-3">Settled</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className="border-b border-stone-800/60" data-testid={`report-order-${o.id.slice(0, 6)}`}>
                  <td className="p-3 font-mono text-xs">#{o.id.slice(0, 6)}</td>
                  <td className="p-3">{o.table_label}</td>
                  <td className="p-3 text-stone-300">{o.customer_name || <span className="text-stone-600">—</span>}</td>
                  <td className="p-3 text-stone-400">{o.items.length}</td>
                  <td className="p-3 font-mono uppercase text-xs">{o.payment_method || "—"}</td>
                  <td className="p-3 font-mono text-right text-orange-400">{currency(o.total)}</td>
                  <td className="p-3 font-mono text-xs text-stone-500">
                    {o.settled_at ? new Date(o.settled_at).toLocaleString() : ""}
                  </td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-stone-500 text-sm font-mono uppercase tracking-widest">
                    No settled orders yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

function Kpi({ label, value, accent }) {
  return (
    <div className="border border-stone-800 bg-stone-900/40 p-5" data-testid={`kpi-${label.toLowerCase().replace(/[^a-z]/g, "-")}`}>
      <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500">{label}</div>
      <div className={`mt-2 font-display text-3xl md:text-4xl tracking-tight ${accent ? "text-orange-400" : ""}`}>{value}</div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="mt-10">
      <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-stone-500 mb-3">{title}</div>
      <div className="border border-stone-800 bg-stone-900/30 p-5">{children}</div>
    </div>
  );
}

function Empty({ children }) {
  return (
    <div className="py-12 text-center text-xs font-mono uppercase tracking-widest text-stone-600">{children}</div>
  );
}
