import { useCallback, useEffect, useRef, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { useProperty } from "@/contexts/PropertyContext";
import { toast } from "sonner";
import { DOMAINS, DOMAIN_LABELS, heldDomains } from "@/lib/domains";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

// One copy of the vocabulary, shared with the nav and the staff screen. DOMAINS is
// already in display order, so it doubles as the order the tick-boxes and the picked
// list come out in.
const DOMAIN_ORDER = DOMAINS;

// Same palette Reports.jsx draws its charts with, so the two revenue screens read as one
// product: orange for the accented series, stone for the grid and axes.
const ORANGE = "#f97316";
const STONE = "#78716c";
const GRID = "#292524";
const AXIS = "#78716c";

// "2026-03-05" → "03/05", matching the day buckets on Reports.jsx.
function dayLabel(date) {
  const [, m, d] = String(date).split("-");
  return `${m}/${d}`;
}

// Reports.jsx's tooltip, kept to the same markup: money always goes through currency(),
// because a bare number on a revenue chart is unreadable.
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

// `heldDomains` comes from lib/domains: only offer what the signed-in person actually
// holds, because asking for a domain you do not hold is a 403, not a narrowed answer, so
// a tick-box for it could only ever produce an error toast.
//
// It is passed the property as well as the user, so the same is true of a domain the
// *property* does not have. An admin holds every domain by the request-time rule, but an
// outlet property has no hotel side for them to hold one in — the server refuses those
// endpoints ahead of the admin bypass — so a Hotel tick-box here would be an error toast
// for the one person who could reach every other figure on the screen.

const label = (d) => DOMAIN_LABELS[d] || d;

// "restaurant and bar", "hotel, restaurant and bar" — built from the list rather than
// written out, so the sentences below follow the data instead of a hardcoded pair.
function joinWords(words, conjunction = "and") {
  if (words.length <= 1) return words[0] || "";
  return `${words.slice(0, -1).join(", ")} ${conjunction} ${words[words.length - 1]}`;
}

/**
 * The caveat under the outlets figure, rendered from the response's `covers` — what the
 * figure contains, not what the user ticked. This property's restaurant and bar share one
 * till and one set of orders, so a manager holding only `bar` still receives every
 * restaurant rupee and the screen has to say so. Split the tills and the server sends a
 * one-entry `covers`, this returns null, and the caveat disappears on its own.
 */
export function coversSentence(covers) {
  const names = (covers || []).map((c) => label(c).toLowerCase());
  if (names.length < 2) return null;
  return `Covers ${joinWords(names)} together: they share one till here, so this figure is the same whichever you tick.`;
}

// The heading over that figure, from the same array: "Restaurant & bar", or just "Bar".
export function coversHeading(covers) {
  const names = (covers || []).map(label);
  if (names.length === 0) return "Outlets";
  const joined = joinWords(names, "&");
  return joined.charAt(0) + joined.slice(1).toLowerCase();
}

// Default to the current calendar month, computed from local Date parts. Slicing an ISO
// UTC timestamp puts the property a day out for most of its evening, and the backend
// attributes revenue to the property's local day — a screen that disagreed with it would
// show figures that do not reconcile.
export function monthRange(now = new Date()) {
  const p = (v) => String(v).padStart(2, "0");
  const month = `${now.getFullYear()}-${p(now.getMonth() + 1)}`;
  return { start: `${month}-01`, end: `${month}-${p(now.getDate())}` };
}

export default function Analytics() {
  const { user } = useAuth();
  const property = useProperty();
  const held = heldDomains(user, property);
  const [range, setRange] = useState(monthRange);
  // Empty to begin with, because `held` is empty until the property lands and the guard
  // in `load` treats nothing ticked as no question to ask — so no request is fired at a
  // domain this property may turn out not to have.
  const [picked, setPicked] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  // Toggling quickly fires overlapping requests; without this the slower one can land
  // last and paint figures that do not match the tick-boxes.
  const latest = useRef(0);

  const chosen = picked.join(",");
  const heldKey = held.join(",");

  // Start on everything this person may ask for, the moment that is known. Keyed on the
  // identities rather than the array so a re-render does not undo a tick made a second
  // ago; it runs once, when the property arrives and `held` stops being empty.
  useEffect(() => {
    setPicked(heldKey ? heldKey.split(",") : []);
  }, [heldKey]);

  const load = useCallback(() => {
    // Nothing ticked means there is no question to ask, so no request is made: this is
    // the only place the screen calls the endpoint, and the guard sits above the call.
    if (!chosen) {
      setData(null);
      setLoading(false);
      return;
    }
    const seq = ++latest.current;
    setLoading(true);
    api
      .get("/analytics/revenue", {
        params: { start: range.start, end: range.end, domains: chosen },
      })
      .then((r) => {
        if (seq === latest.current) setData(r.data);
      })
      .catch((e) => {
        if (seq !== latest.current) return;
        // Clear rather than keep the last good answer: stale figures sitting under
        // changed controls read as the answer to the new question.
        setData(null);
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      })
      .finally(() => {
        if (seq === latest.current) setLoading(false);
      });
  }, [range.start, range.end, chosen]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (k) =>
    setPicked((p) =>
      p.includes(k) ? p.filter((x) => x !== k) : DOMAIN_ORDER.filter((d) => d === k || p.includes(d)),
    );

  const showHotel = held.includes("hotel");
  const heldOutlets = held.filter((d) => d !== "hotel");
  const outletsHeading = coversHeading(data?.outlets?.covers || heldOutlets);
  const caveat = coversSentence(data?.outlets?.covers);
  const peak = Math.max(1, ...(data?.by_day || []).map((d) => d.total));

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Analytics
      </h1>
      <p className="text-stone-500 font-mono text-xs mb-8">
        {data ? `${data.start} → ${data.end}` : "—"}
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
        <div className="text-xs tracking-widest uppercase text-stone-500">
          Include
          <div className="flex gap-2 mt-2">
            {held.map((d) => (
              <button
                key={d}
                type="button"
                data-testid={`domain-${d}`}
                onClick={() => toggle(d)}
                aria-pressed={picked.includes(d)}
                className={`text-[10px] tracking-widest uppercase border rounded-full px-4 py-1.5 transition-colors ${
                  picked.includes(d)
                    ? "border-orange-500 text-orange-400 bg-orange-500/10"
                    : "border-stone-700 text-stone-500 hover:border-stone-500"
                }`}
              >
                {label(d)}
              </button>
            ))}
          </div>
        </div>
      </div>

      {picked.length === 0 && (
        <p className="text-stone-400">Tick at least one area to see figures.</p>
      )}

      {data && (
        <>
          <div className="grid gap-4 sm:grid-cols-3 max-w-4xl mb-6">
            <Figure label="Total" value={data.total} accent />
            {showHotel && (
              <Figure
                label="Hotel"
                value={data.hotel?.total}
                sub={
                  data.hotel
                    ? `${currency(data.hotel.room_nights)} rooms · ${currency(data.hotel.extras)} extras`
                    : null
                }
              />
            )}
            {heldOutlets.length > 0 && (
              <Figure
                label={outletsHeading}
                value={data.outlets?.total}
                sub={
                  data.outlets
                    ? `${data.outlets.orders} ${data.outlets.orders === 1 ? "order" : "orders"}`
                    : null
                }
              />
            )}
          </div>

          {caveat && <p className="text-xs text-stone-500 mb-6 max-w-2xl">{caveat}</p>}

          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mt-10 mb-4">
            By day
          </h2>
          {/* recharts, as on Reports.jsx — `by_day` spans every day in the range, so the
              rows go straight in as the chart's data with no gap-filling. Stacked, so a
              bar's height is that day's total. A block the user did not ask for comes
              back null and gets no series at all: a flat zero series would claim the
              property took nothing there, which is the same lie the cards avoid with "—".
              The min-width keeps the days apart on a narrow screen, the way wide content
              scrolls sideways elsewhere in the app. */}
          <div className="overflow-x-auto">
            <div className="h-72 min-w-[480px]" data-testid="by-day">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.by_day} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
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
                    tickFormatter={(v) => `₹${v}`}
                    width={56}
                  />
                  <Tooltip cursor={{ fill: "#ffffff08" }} content={<ChartTooltip />} />
                  {data.hotel && (
                    <Bar dataKey="hotel" name="Hotel" stackId="day" fill={ORANGE} />
                  )}
                  {data.outlets && (
                    <Bar dataKey="outlets" name={outletsHeading} stackId="day" fill={STONE} />
                  )}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          {/* One legend only: hand-rolled, as Reports.jsx does for its payment mix, since
              it also carries the peak-day figure. No recharts <Legend> to duplicate it. */}
          <div className="flex gap-5 mt-3 text-[10px] tracking-widest uppercase text-stone-500">
            {data.hotel && (
              <span>
                <span className="inline-block w-3 h-2 bg-orange-500 mr-2" />
                Hotel
              </span>
            )}
            {data.outlets && (
              <span>
                <span className="inline-block w-3 h-2 bg-stone-500 mr-2" />
                {outletsHeading}
              </span>
            )}
            <span className="tabular-nums">Peak day · {currency(peak)}</span>
          </div>
        </>
      )}

      {loading && <p className="text-stone-500 text-sm mt-6">Loading…</p>}
    </div>
  );
}

function Figure({ label: text, value, sub, accent }) {
  return (
    <div
      data-testid={`figure-${text.toLowerCase().replace(/[^a-z]+/g, "-")}`}
      className={`border rounded p-5 ${
        accent ? "border-orange-500/40 bg-orange-500/5" : "border-stone-800 bg-stone-900"
      }`}
    >
      <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500 mb-2">{text}</div>
      {/* A block that was not asked for comes back null, and reads "—". Rendering it as
          ₹0.00 would claim the property took nothing there. */}
      <div className="text-2xl font-bold tabular-nums text-stone-100">
        {value == null ? "—" : currency(value)}
      </div>
      {sub && <div className="text-xs text-stone-500 mt-2 tabular-nums">{sub}</div>}
    </div>
  );
}
