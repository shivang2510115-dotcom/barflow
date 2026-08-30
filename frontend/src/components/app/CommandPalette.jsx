import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { Search, CornerDownLeft } from "lucide-react";

/**
 * Jump anywhere by typing. Cmd-K, or Ctrl-K.
 *
 * This exists because the sidebar was load-bearing and getting heavier: nineteen
 * screens behind a section chooser, and one entry per outlet still to come. Every
 * feature added made navigating worse. A search box does not — it gets *better* as
 * there is more to find.
 *
 * Three kinds of result, in one list:
 *   a screen    "housekeeping", "rates"
 *   a room      "103", "DLX-02"
 *   a guest     "sharma"
 *
 * Rooms and guests are fetched once when the palette first opens and kept for the
 * session. A property has a few hundred of each, which is nothing to hold and far
 * better than a request per keystroke — the palette has to feel instant or people
 * stop reaching for it, and going back to the network on every letter guarantees it
 * will not.
 */

// The screens, as the sidebar knows them. Kept here rather than derived from the nav
// array because a palette entry wants the words somebody would actually type: a
// receptionist looking for the front desk types "checkin", not "Front desk".
const SCREENS = [
  { to: "/app/today", label: "Today", keywords: "home dashboard board morning" },
  { to: "/app/hotel/front-desk", label: "Front desk", keywords: "checkin check in arrivals desk" },
  { to: "/app/hotel/bookings", label: "Bookings", keywords: "reservations stays" },
  { to: "/app/hotel/bookings/new", label: "New booking", keywords: "book create reserve" },
  { to: "/app/hotel/calendar", label: "Occupancy", keywords: "availability calendar" },
  { to: "/app/hotel/rooms", label: "Rooms", keywords: "inventory room list" },
  { to: "/app/hotel/rates", label: "Rates", keywords: "pricing tariff" },
  { to: "/app/hotel/packages", label: "Packages", keywords: "inclusions included elite breakfast entitlement" },
  { to: "/app/hotel/guests", label: "Guests", keywords: "customers people" },
  { to: "/app/hotel/bills", label: "Bills", keywords: "invoice bill checkout receipt" },
  { to: "/app/hotel/housekeeping", label: "Housekeeping", keywords: "cleaning dirty turn" },
  { to: "/app/pos", label: "POS", keywords: "bill order till sell" },
  { to: "/app/tables", label: "Tables", keywords: "floor covers" },
  { to: "/app/kot", label: "Kitchen board", keywords: "kot kitchen tickets" },
  { to: "/app/menu", label: "Menu", keywords: "dishes items food" },
  { to: "/app/inventory", label: "Inventory", keywords: "stock store" },
  { to: "/app/reservations", label: "Reservations", keywords: "table booking" },
  { to: "/app/planner", label: "Planner", keywords: "calendar events tasks" },
  { to: "/app/messaging", label: "Messaging", keywords: "whatsapp occasions" },
  { to: "/app/reports", label: "Reports", keywords: "daily brief" },
  { to: "/app/admin/analytics", label: "Analytics", keywords: "revenue money numbers" },
  { to: "/app/admin/expenses", label: "Expenses", keywords: "spending costs" },
  { to: "/app/admin/staff", label: "Staff", keywords: "team people permissions" },
  { to: "/app/admin/outlets", label: "Outlets", keywords: "salon gym laundry bar restaurant" },
];

const MAX = 8;

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [data, setData] = useState(null);
  const inputRef = useRef(null);
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Fetched on first open, not at mount: somebody who never presses the shortcut should
  // not pay two requests for it on every page load.
  useEffect(() => {
    if (!open || data) return;
    let cancelled = false;
    (async () => {
      const [rooms, guests] = await Promise.all([
        api.get("/rooms").then((r) => r.data).catch(() => []),
        api.get("/guests").then((r) => r.data).catch(() => []),
      ]);
      if (!cancelled) setData({ rooms, guests });
    })();
    return () => { cancelled = true; };
  }, [open, data]);

  useEffect(() => {
    if (open) { setQ(""); setCursor(0); setTimeout(() => inputRef.current?.focus(), 0); }
  }, [open]);

  const results = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return SCREENS.slice(0, MAX).map((s) => ({ ...s, kind: "screen" }));

    const screens = SCREENS
      .filter((s) => `${s.label} ${s.keywords}`.toLowerCase().includes(term))
      .map((s) => ({ ...s, kind: "screen" }));

    const rooms = (data?.rooms || [])
      .filter((r) => (r.number || "").toLowerCase().includes(term))
      .slice(0, 5)
      .map((r) => ({ kind: "room", label: `Room ${r.number}`,
                     to: "/app/hotel/rooms", hint: r.housekeeping_status }));

    const guests = (data?.guests || [])
      .filter((g) => (g.name || "").toLowerCase().includes(term))
      .slice(0, 5)
      .map((g) => ({ kind: "guest", label: g.name,
                     to: "/app/hotel/guests", hint: g.phone }));

    return [...rooms, ...guests, ...screens].slice(0, MAX);
  }, [q, data]);

  const go = useCallback((item) => {
    if (!item) return;
    setOpen(false);
    navigate(item.to);
  }, [navigate]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[90] bg-black/40 flex items-start justify-center pt-[12vh] px-4"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        className="w-full max-w-xl bg-surface border border-hairline rounded shadow-lg overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Jump to"
      >
        <div className="flex items-center gap-3 px-4 border-b border-hairline">
          <Search className="h-4 w-4 text-faint shrink-0" aria-hidden="true" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); setCursor(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, results.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
              if (e.key === "Enter") { e.preventDefault(); go(results[cursor]); }
            }}
            placeholder="Room, guest, or screen…"
            className="flex-1 bg-transparent text-[15px] text-ink placeholder:text-faint outline-none"
            aria-label="Search rooms, guests and screens"
          />
          <kbd className="font-mono text-[11px] text-faint">esc</kbd>
        </div>

        {results.length === 0 ? (
          <p className="px-4 py-6 text-[13px] text-faint">
            Nothing matches “{q}”.
          </p>
        ) : (
          <ul className="max-h-[50vh] overflow-y-auto">
            {results.map((r, i) => (
              <li key={`${r.kind}-${r.label}-${i}`}>
                <button
                  onClick={() => go(r)}
                  onMouseEnter={() => setCursor(i)}
                  className={`w-full text-left px-4 flex items-center gap-3 transition-colors
                    ${i === cursor ? "bg-brass/10" : ""}`}
                >
                  <span className="text-[11px] uppercase tracking-wider text-faint w-14 shrink-0">
                    {r.kind}
                  </span>
                  <span className="text-[15px] text-ink truncate flex-1">{r.label}</span>
                  {r.hint && <span className="text-[12px] text-faint">{r.hint}</span>}
                  {i === cursor && (
                    <CornerDownLeft className="h-3.5 w-3.5 text-brass shrink-0" aria-hidden="true" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
