import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";
// One definition of "this month" across the two money screens. It is computed from local
// Date parts on purpose — slicing an ISO UTC timestamp puts the property a day out for
// most of its evening, and the backend attributes money to the property's local day.
import { monthRange } from "@/pages/admin/Analytics";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  LabelList,
} from "recharts";

// The same palette Analytics.jsx and Reports.jsx draw with, so the three money screens
// read as one product. Orange is the accented series and this screen's subject is
// expenditure, so expenditure wears it; income is the recessive neutral beside it.
//
// The pair was run through the categorical-palette checks against this surface: adjacent
// separation ΔE 14.0 under deuteranopia and 17.8 in normal vision, both comfortably over
// the floors, and both clear 3:1 against #0c0a09. Stone is deliberately near-neutral —
// that is what makes it read as the background series rather than a second accent — and a
// legend plus direct labels carry identity anyway, so colour is never doing the job alone.
const ORANGE = "#f97316";
const STONE = "#a8a29e";
const GRID = "#292524";
const AXIS = "#78716c";

// How the money left, and how each is written for a person. Mirrors PAYMENT_METHODS in
// backend/services/expenses.py — the API refuses anything else with a 422, so a value
// here that is not there could only ever produce an error toast.
const PAYMENT_METHODS = [
  ["cash", "Cash"],
  ["upi", "UPI"],
  ["bank_transfer", "Bank transfer"],
  ["card", "Card"],
  ["cheque", "Cheque"],
  ["other", "Other"],
];
const methodLabel = (m) => (PAYMENT_METHODS.find(([k]) => k === m) || [m, m])[1];

// "2026-03-05" → "03/05", matching the day buckets on Analytics.jsx and Reports.jsx.
const dayLabel = (d) => {
  const [, m, day] = String(d).split("-");
  return `${m}/${day}`;
};

/**
 * Today, as the property's calendar day, from the browser's own local parts.
 *
 * Never `toISOString().slice(0, 10)`. At 01:00 in Kolkata the UTC date is still
 * yesterday's, so the naive slice prefills the form with the wrong day for the first five
 * and a half hours of every night — which is exactly when a bar's bills get entered.
 * Leaving the field blank asks the server instead, and `services/clock.py` answers the
 * same way; this only has to agree with it.
 */
export function localToday(now = new Date()) {
  const p = (v) => String(v).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/**
 * An axis tick: money, short.
 *
 * It still goes through `currency()`, so the symbol and the Indian digit grouping come
 * from the one place that knows them — ₹1,23,456 and not ₹123,456, and never a literal
 * symbol written here. Only the trailing paise are dropped, because an axis of
 * "₹1,23,456.00" is unreadable at tick size and the exact figures are in the tooltip and
 * the table below.
 */
export function axisMoney(v) {
  return currency(Math.round(Number(v) || 0)).replace(/\.00$/, "");
}

// Analytics.jsx's tooltip, to the same markup: money always goes through currency(),
// because a bare number on a money chart is unreadable.
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="bg-stone-950 border border-stone-700 px-3 py-2 text-xs font-mono">
      {label != null && <div className="text-stone-400 mb-1">{label}</div>}
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2" style={{ color: p.color || p.fill }}>
          <span className="uppercase tracking-widest">{p.name}:</span>
          <span className="tabular-nums">{currency(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function Expenses() {
  const { user } = useAuth();
  const mayRecord = user?.role === "admin" || user?.role === "manager";
  const mayNameCategories = user?.role === "admin";

  const [range, setRange] = useState(monthRange);
  const [report, setReport] = useState(null);
  const [rows, setRows] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({ category_id: "", payment_method: "", q: "" });
  const [sort, setSort] = useState({ key: "date", direction: -1 });
  // Changing the range quickly fires overlapping requests; without this the slower one
  // can land last and paint figures that do not match the controls above them.
  const latest = useRef(0);

  const fail = (e) => toast.error(formatApiErrorDetail(e.response?.data?.detail));

  const loadCategories = useCallback(() => {
    api
      .get("/expense-categories", { params: { include_inactive: mayNameCategories } })
      .then((r) => setCategories(r.data))
      .catch(fail);
  }, [mayNameCategories]);

  const load = useCallback(() => {
    const seq = ++latest.current;
    setLoading(true);
    Promise.all([
      api.get("/expenses/report", { params: { start: range.start, end: range.end } }),
      api.get("/expenses", {
        params: {
          start: range.start,
          end: range.end,
          category_id: filters.category_id || undefined,
          payment_method: filters.payment_method || undefined,
          q: filters.q || undefined,
          sort: sort.key,
          direction: sort.direction,
        },
      }),
    ])
      .then(([r, list]) => {
        if (seq !== latest.current) return;
        setReport(r.data);
        setRows(list.data);
      })
      .catch((e) => {
        if (seq !== latest.current) return;
        // Cleared rather than left showing the last good answer: stale figures sitting
        // under changed controls read as the answer to the new question.
        setReport(null);
        setRows([]);
        fail(e);
      })
      .finally(() => {
        if (seq === latest.current) setLoading(false);
      });
  }, [range.start, range.end, filters.category_id, filters.payment_method, filters.q,
      sort.key, sort.direction]);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);
  useEffect(() => {
    load();
  }, [load]);

  const totals = report?.totals;
  const spent = report?.expenses;
  const breakdown = useMemo(
    () => (spent?.by_category || []).map((c) => ({ ...c, label: c.name })),
    [spent],
  );
  // Tall enough for the rows it has, and never so short that the labels collide.
  const breakdownHeight = Math.max(140, breakdown.length * 42 + 24);

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Expenses
      </h1>
      <p className="text-stone-500 font-mono text-xs mb-8">
        {report ? `${report.start} → ${report.end}` : "—"}
      </p>

      <div className="flex flex-wrap gap-6 items-end mb-8">
        {[
          ["start", "From"],
          ["end", "To"],
        ].map(([k, text]) => (
          <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
            {text}
            <input
              type="date"
              data-testid={`range-${k}`}
              value={range[k]}
              max={k === "start" ? range.end : undefined}
              min={k === "end" ? range.start : undefined}
              onChange={(e) => setRange((r) => ({ ...r, [k]: e.target.value }))}
              className="block mt-2 bg-stone-950 border border-stone-700 text-stone-100 py-1 px-2 rounded"
            />
          </label>
        ))}
      </div>

      {/* The three figures the owner came for. `net` is drawn in orange when the property
          is ahead and in red when it is not — a loss shown in the same colour as a profit
          is a number somebody reads past. */}
      <div className="grid gap-4 sm:grid-cols-3 max-w-4xl mb-10">
        <Figure label="Earned" value={totals?.earned} />
        <Figure label="Spent" value={totals?.spent} sub={spent ? `${spent.count} recorded` : null} />
        <Figure
          label="What's left"
          value={totals?.net}
          accent
          negative={totals != null && totals.net < 0}
          sub={
            report?.domains?.length && report.domains.length < 3
              ? `Income covers ${report.domains.join(", ")}`
              : null
          }
        />
      </div>

      {report && (
        <>
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Income against expenditure
          </h2>
          {/* Grouped, not stacked: these are two measures of the same thing, not parts of
              one whole, and stacking them would draw a bar whose height is income plus
              spending — a number that means nothing. One axis for both, because both are
              rupees. `by_day` spans every day in the range, so the rows go straight in with
              no gap-filling. The min-width keeps the days apart on a narrow screen, the way
              wide content scrolls sideways elsewhere in the app. */}
          <div className="overflow-x-auto">
            <div className="h-72 min-w-[520px]" data-testid="by-day">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={report.by_day} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={dayLabel}
                    tick={{ fill: AXIS, fontSize: 11 }}
                    stroke={GRID}
                  />
                  <YAxis
                    tick={{ fill: AXIS, fontSize: 11 }}
                    stroke={GRID}
                    tickFormatter={axisMoney}
                    width={86}
                  />
                  <Tooltip cursor={{ fill: "#ffffff08" }} content={<ChartTooltip />} />
                  <Bar dataKey="income" name="Income" fill={STONE} radius={[2, 2, 0, 0]} />
                  <Bar dataKey="expenditure" name="Spent" fill={ORANGE} radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          {/* Hand-rolled, as Analytics.jsx does, because it also carries the worst day —
              the one an owner scrolls the chart looking for. */}
          <div className="flex flex-wrap gap-5 mt-3 text-[10px] tracking-widest uppercase text-stone-500">
            <span>
              <span className="inline-block w-3 h-2 mr-2" style={{ background: STONE }} />
              Income
            </span>
            <span>
              <span className="inline-block w-3 h-2 mr-2" style={{ background: ORANGE }} />
              Spent
            </span>
            <WorstDay days={report.by_day} />
          </div>

          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mt-12 mb-4">
            Where it goes
          </h2>
          {breakdown.length === 0 ? (
            <p className="text-stone-500 text-sm">Nothing recorded in this range.</p>
          ) : (
            <>
              {/* Ranked horizontal bars rather than a pie: the question is "where does the
                  money go", and the answer is read top-down. One hue, because this is one
                  measure ranked by size and not eight identities — a colour per category
                  would be eight hues carrying no information the label does not already
                  carry. The share is direct-labelled on each bar, so identity and
                  magnitude never depend on colour at all. */}
              <div className="overflow-x-auto">
                <div style={{ height: breakdownHeight }} className="min-w-[480px]" data-testid="by-category">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={breakdown}
                      layout="vertical"
                      margin={{ top: 0, right: 64, left: 0, bottom: 0 }}
                    >
                      <CartesianGrid stroke={GRID} horizontal={false} />
                      <XAxis
                        type="number"
                        tick={{ fill: AXIS, fontSize: 11 }}
                        stroke={GRID}
                        tickFormatter={axisMoney}
                      />
                      <YAxis
                        type="category"
                        dataKey="label"
                        tick={{ fill: AXIS, fontSize: 11 }}
                        stroke={GRID}
                        width={168}
                      />
                      <Tooltip cursor={{ fill: "#ffffff08" }} content={<ChartTooltip />} />
                      <Bar dataKey="amount" name="Spent" fill={ORANGE} radius={[0, 2, 2, 0]}>
                        <LabelList
                          dataKey="share"
                          position="right"
                          formatter={(v) => `${v}%`}
                          fill={AXIS}
                          fontSize={11}
                        />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
              {/* The table view of the same chart. Present for the same reason the chart
                  is: a share read off a bar is an estimate, and somebody reconciling
                  against a bank statement needs the figure. */}
              <table className="mt-4 w-full max-w-2xl text-sm" data-testid="category-table">
                <tbody>
                  {breakdown.map((c) => (
                    <tr key={c.category_id || "none"} className="border-t border-stone-800">
                      <td className="py-2 pr-4 text-stone-300">{c.name}</td>
                      <td className="py-2 pr-4 text-right tabular-nums text-stone-100">
                        {currency(c.amount)}
                      </td>
                      <td className="py-2 text-right tabular-nums text-stone-500 w-16">
                        {c.share}%
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t border-stone-700">
                    <td className="py-2 pr-4 text-[10px] tracking-widest uppercase text-stone-500">
                      Total
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums text-orange-400 font-bold">
                      {currency(spent.total)}
                    </td>
                    <td />
                  </tr>
                </tbody>
              </table>
            </>
          )}
        </>
      )}

      {mayRecord && (
        <RecordExpense
          categories={categories.filter((c) => c.active !== false)}
          onDone={load}
          onError={fail}
        />
      )}

      <Transactions
        rows={rows}
        categories={categories}
        filters={filters}
        setFilters={setFilters}
        sort={sort}
        setSort={setSort}
        mayRecord={mayRecord}
        onVoided={load}
        onError={fail}
      />

      {mayNameCategories && (
        <CategoryManager categories={categories} onChanged={() => { loadCategories(); load(); }} onError={fail} />
      )}

      {loading && <p className="text-stone-500 text-sm mt-6">Loading…</p>}
    </div>
  );
}

function Figure({ label, value, sub, accent, negative }) {
  return (
    <div
      data-testid={`figure-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}
      className={`border rounded p-5 ${
        accent
          ? negative
            ? "border-red-500/40 bg-red-500/5"
            : "border-orange-500/40 bg-orange-500/5"
          : "border-stone-800 bg-stone-900"
      }`}
    >
      <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500 mb-2">{label}</div>
      {/* Nothing loaded yet reads "—". Rendering it as ₹0.00 would claim the property
          spent nothing, which is a different statement from not knowing yet. */}
      <div
        className={`text-2xl font-bold tabular-nums ${
          negative ? "text-red-400" : "text-stone-100"
        }`}
      >
        {value == null ? "—" : currency(value)}
      </div>
      {sub && <div className="text-xs text-stone-500 mt-2 tabular-nums">{sub}</div>}
    </div>
  );
}

/** The day the property lost the most. Null when it never did — an owner reading "worst
 *  day · ₹0.00" would think something had been computed wrongly. */
function WorstDay({ days }) {
  const worst = (days || []).reduce((a, d) => (a == null || d.net < a.net ? d : a), null);
  if (!worst || worst.net >= 0) return null;
  return (
    <span className="tabular-nums">
      Worst day · {worst.date} · {currency(worst.net)}
    </span>
  );
}

// ------------------------------- recording an expense -------------------------------
const BLANK = {
  amount: "",
  category_id: "",
  spent_on: "",
  description: "",
  payment_method: "cash",
  payee: "",
  reference: "",
};

function RecordExpense({ categories, onDone, onError }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(() => ({ ...BLANK, spent_on: localToday() }));
  const [saving, setSaving] = useState(false);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = (e) => {
    e.preventDefault();
    setSaving(true);
    api
      .post("/expenses", {
        ...form,
        amount: Number(form.amount),
        // Blank means "today at the property", which the server answers from its own
        // clock. Sending an empty string instead would be a date that is not a date.
        spent_on: form.spent_on || null,
      })
      .then(() => {
        toast.success("Expense recorded");
        setForm({ ...BLANK, spent_on: localToday() });
        onDone();
      })
      .catch(onError)
      .finally(() => setSaving(false));
  };

  const field = "bg-stone-950 border border-stone-700 text-stone-100 py-2 px-3 rounded w-full";
  const legend = "block text-[10px] tracking-widest uppercase text-stone-500 mb-2";

  return (
    <section className="mt-12">
      <button
        type="button"
        data-testid="record-expense-toggle"
        onClick={() => setOpen((o) => !o)}
        className="border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-4 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
      >
        {open ? "Close" : "Record an expense"}
      </button>

      {open && (
        <form onSubmit={submit} data-testid="record-expense-form" className="mt-6 grid gap-5 sm:grid-cols-2 max-w-3xl">
          <label>
            <span className={legend}>Amount</span>
            {/* A number, not a formatted string: the symbol and the grouping are the
                screen's job on the way out, never the form's on the way in. */}
            <input
              type="number"
              step="0.01"
              min="0.01"
              required
              data-testid="expense-amount"
              value={form.amount}
              onChange={set("amount")}
              className={field}
            />
          </label>
          <label>
            <span className={legend}>Category</span>
            <select
              required
              data-testid="expense-category"
              value={form.category_id}
              onChange={set("category_id")}
              className={field}
            >
              <option value="">Choose…</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className={legend}>Date</span>
            <input
              type="date"
              data-testid="expense-date"
              value={form.spent_on}
              onChange={set("spent_on")}
              className={field}
            />
          </label>
          <label>
            <span className={legend}>Paid by</span>
            <select
              data-testid="expense-method"
              value={form.payment_method}
              onChange={set("payment_method")}
              className={field}
            >
              {PAYMENT_METHODS.map(([k, text]) => (
                <option key={k} value={k}>
                  {text}
                </option>
              ))}
            </select>
          </label>
          <label className="sm:col-span-2">
            <span className={legend}>Description</span>
            <input
              data-testid="expense-description"
              value={form.description}
              onChange={set("description")}
              className={field}
            />
          </label>
          <label>
            <span className={legend}>Supplier or payee</span>
            <input
              data-testid="expense-payee"
              value={form.payee}
              onChange={set("payee")}
              className={field}
            />
          </label>
          <label>
            <span className={legend}>Bill number</span>
            <input
              data-testid="expense-reference"
              value={form.reference}
              onChange={set("reference")}
              className={field}
            />
          </label>
          <div className="sm:col-span-2 flex items-center gap-4">
            <button
              type="submit"
              disabled={saving}
              data-testid="expense-save"
              className="border border-orange-500 text-orange-400 hover:bg-orange-500/10 px-5 py-2 text-xs font-mono uppercase tracking-widest transition-colors disabled:opacity-40"
            >
              {saving ? "Saving…" : "Record"}
            </button>
            <p className="text-xs text-stone-500 max-w-sm">
              Recorded expenses are not edited or deleted. A mistake is reversed and the
              right one recorded beside it, so the books read the same today as they did
              when somebody last looked at them.
            </p>
          </div>
        </form>
      )}
    </section>
  );
}

// --------------------------------- the transactions ---------------------------------
const COLUMNS = [
  ["date", "Date"],
  ["category", "Category"],
  [null, "Description"],
  [null, "Payee"],
  [null, "Bill no."],
  [null, "Paid by"],
  [null, "Recorded by"],
  ["amount", "Amount"],
];

function Transactions({ rows, categories, filters, setFilters, sort, setSort, mayRecord,
                        onVoided, onError }) {
  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const by = (key) =>
    setSort((s) => ({ key, direction: s.key === key ? -s.direction : -1 }));

  const control = "bg-stone-950 border border-stone-700 text-stone-100 py-1.5 px-2 rounded text-sm";

  const reverse = (row) => {
    const reason = window.prompt(`Reverse ${currency(row.amount)} — why?`);
    if (reason === null) return;
    api
      .post(`/expenses/${row.id}/void`, { reason })
      .then(() => {
        toast.success("Expense reversed");
        onVoided();
      })
      .catch(onError);
  };

  return (
    <section className="mt-14">
      <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
        Transactions
      </h2>

      {/* Filters in one row above the table, as elsewhere in the app. */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select data-testid="filter-category" value={filters.category_id} onChange={set("category_id")} className={control}>
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <select data-testid="filter-method" value={filters.payment_method} onChange={set("payment_method")} className={control}>
          <option value="">Any payment method</option>
          {PAYMENT_METHODS.map(([k, text]) => (
            <option key={k} value={k}>
              {text}
            </option>
          ))}
        </select>
        <input
          data-testid="filter-search"
          value={filters.q}
          onChange={set("q")}
          placeholder="Description, payee or bill number"
          className={`${control} min-w-[16rem]`}
        />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[900px]" data-testid="transactions">
          <thead>
            <tr className="text-[10px] tracking-widest uppercase text-stone-500">
              {COLUMNS.map(([key, text]) => (
                <th
                  key={text}
                  className={`py-2 pr-4 font-normal ${text === "Amount" ? "text-right" : "text-left"}`}
                >
                  {key ? (
                    <button
                      type="button"
                      data-testid={`sort-${key}`}
                      onClick={() => by(key)}
                      className={`uppercase tracking-widest hover:text-orange-400 ${
                        sort.key === key ? "text-orange-400" : ""
                      }`}
                    >
                      {text}
                      {sort.key === key ? (sort.direction < 0 ? " ↓" : " ↑") : ""}
                    </button>
                  ) : (
                    text
                  )}
                </th>
              ))}
              {mayRecord && <th className="py-2 font-normal" />}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={9} className="py-6 text-stone-500">
                  Nothing recorded in this range.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.id}
                data-testid={`expense-${r.id}`}
                className={`border-t border-stone-800 ${r.voided_at ? "text-stone-600" : "text-stone-300"}`}
              >
                <td className="py-2 pr-4 font-mono text-xs whitespace-nowrap">{r.spent_on}</td>
                <td className="py-2 pr-4">{r.category_name}</td>
                <td className="py-2 pr-4">
                  <span className={r.voided_at ? "line-through" : ""}>{r.description || "—"}</span>
                  {r.voided_at && (
                    <span
                      title={r.void_reason || ""}
                      className="ml-2 text-[10px] tracking-widest uppercase text-red-400/80 border border-red-500/30 px-1.5 py-0.5"
                    >
                      Reversed
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4">{r.payee || "—"}</td>
                <td className="py-2 pr-4 font-mono text-xs">{r.reference || "—"}</td>
                <td className="py-2 pr-4">{methodLabel(r.payment_method)}</td>
                <td className="py-2 pr-4 text-stone-500">{r.recorded_by_name || "—"}</td>
                <td className={`py-2 pr-4 text-right tabular-nums ${r.voided_at ? "line-through" : "text-stone-100"}`}>
                  {currency(r.amount)}
                </td>
                {mayRecord && (
                  <td className="py-2 text-right">
                    {!r.voided_at && (
                      <button
                        type="button"
                        data-testid={`reverse-${r.id}`}
                        onClick={() => reverse(r)}
                        className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-red-400"
                      >
                        Reverse
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ---------------------------------- the categories ----------------------------------
function CategoryManager({ categories, onChanged, onError }) {
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState("");

  const add = (e) => {
    e.preventDefault();
    if (!adding.trim()) return;
    api
      .post("/expense-categories", { name: adding })
      .then(() => {
        setAdding("");
        onChanged();
      })
      .catch(onError);
  };

  const rename = (c) => {
    const name = window.prompt("Rename this category", c.name);
    if (name === null || name === c.name) return;
    api.put(`/expense-categories/${c.id}`, { name, active: c.active !== false })
      .then(onChanged)
      .catch(onError);
  };

  const setActive = (c, active) =>
    api.put(`/expense-categories/${c.id}`, { name: c.name, active })
      .then(onChanged)
      .catch(onError);

  const remove = (c) =>
    api.delete(`/expense-categories/${c.id}`).then(onChanged).catch(onError);

  const control = "bg-stone-950 border border-stone-700 text-stone-100 py-1.5 px-2 rounded text-sm";
  const action = "text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400";

  return (
    <section className="mt-14">
      <button
        type="button"
        data-testid="categories-toggle"
        onClick={() => setOpen((o) => !o)}
        className="text-[11px] tracking-[0.2em] uppercase text-stone-500 hover:text-orange-400"
      >
        Categories {open ? "▾" : "▸"}
      </button>

      {open && (
        <div className="mt-4 max-w-2xl">
          <p className="text-xs text-stone-500 mb-4">
            These are your names, not ours. Renaming one renames it on every report you
            have already read. A category something has been spent against cannot be
            deleted — retire it instead and it stops being offered without changing the
            past.
          </p>
          <form onSubmit={add} className="flex gap-3 mb-5">
            <input
              data-testid="category-name"
              value={adding}
              onChange={(e) => setAdding(e.target.value)}
              placeholder="Diesel for the genset"
              className={`${control} flex-1`}
            />
            <button
              type="submit"
              data-testid="category-add"
              className="border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-4 text-xs font-mono uppercase tracking-widest transition-colors"
            >
              Add
            </button>
          </form>
          <table className="w-full text-sm" data-testid="categories">
            <tbody>
              {categories.map((c) => (
                <tr key={c.id} className="border-t border-stone-800">
                  <td className={`py-2 pr-4 ${c.active === false ? "text-stone-600" : "text-stone-300"}`}>
                    {c.name}
                    {c.active === false && (
                      <span className="ml-2 text-[10px] tracking-widest uppercase text-stone-600">
                        Retired
                      </span>
                    )}
                  </td>
                  <td className="py-2 text-right space-x-4 whitespace-nowrap">
                    <button type="button" className={action} onClick={() => rename(c)}>
                      Rename
                    </button>
                    <button
                      type="button"
                      className={action}
                      onClick={() => setActive(c, c.active === false)}
                    >
                      {c.active === false ? "Restore" : "Retire"}
                    </button>
                    <button type="button" className={action} onClick={() => remove(c)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
