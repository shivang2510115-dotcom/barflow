import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { BedDouble, UtensilsCrossed, ShieldCheck } from "lucide-react";

import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { useProperty } from "@/contexts/PropertyContext";
import { useSection } from "@/contexts/SectionContext";

/**
 * `/app`: which half of the business are you working in?
 *
 * Only someone with more than one section is asked. One section is a one-option menu —
 * a click that teaches nothing — so they are sent straight to their first screen, and
 * nobody at all is a message naming who to ask rather than an empty frame.
 *
 * Each card carries a live figure so the choice is useful rather than decorative. Every
 * figure comes from an endpoint the section itself is gated on, which means a person who
 * cannot read it is a person who would have been refused anyway: the card drops to its
 * label and the choice still works. Nothing here retries and nothing toasts — a 403 on
 * the count is not an error the user did anything about.
 */

// icon, and the endpoint each figure comes from, per section key.
const FACE = {
  hotel: { icon: BedDouble, blurb: "Front desk, bookings, rooms, rates and guest folios." },
  restaurant: { icon: UtensilsCrossed, blurb: "Tables, POS, kitchen board, menu and stock." },
  admin: { icon: ShieldCheck, blurb: "Staff, screen permissions and revenue analytics." },
};

const plural = (n, one, many) => (n === 1 ? one : many);

export default function SectionChooser() {
  const { user } = useAuth();
  const property = useProperty();
  const { sections, setSection, pathFor } = useSection();
  const nav = useNavigate();
  const [figures, setFigures] = useState({});

  const keys = sections.map((s) => s.key).join(",");

  useEffect(() => {
    // Only ask for a figure the person has a card for. `.catch` swallows on purpose: a
    // 403 here is the same answer as "no figure", and an error toast on a landing page
    // that is otherwise working is noise.
    let live = true;
    const put = (k, v) => live && setFigures((f) => ({ ...f, [k]: v }));
    const has = (k) => keys.split(",").includes(k);

    if (has("hotel")) {
      api
        .get("/front-desk")
        .then((r) => {
          const inHouse = (r.data?.in_house || []).length;
          const arrivals = (r.data?.arrivals || []).length;
          put("hotel", {
            value: String(inHouse),
            sub: `in house · ${arrivals} ${plural(arrivals, "arrival", "arrivals")} today`,
          });
        })
        .catch(() => {});
    }
    if (has("restaurant")) {
      api
        .get("/tables")
        .then((r) => {
          const tables = r.data || [];
          const open = tables.filter((t) => t.status === "occupied").length;
          put("restaurant", {
            value: `${open} / ${tables.length}`,
            sub: `${plural(open, "table", "tables")} with a bill running`,
          });
        })
        .catch(() => {});
    }
    if (has("admin")) {
      api
        .get("/staff")
        .then((r) => {
          const rows = r.data || [];
          const active = rows.filter((u) => u.active).length;
          put("admin", {
            value: String(active),
            sub: `active ${plural(active, "person", "people")} · ${rows.length - active} deactivated`,
          });
        })
        .catch(() => {});
    }
    return () => {
      live = false;
    };
  }, [keys]);

  // Which sections exist depends on what this property is, and that has not arrived yet.
  // Answered ahead of both branches below, because neither is right without it: an outlet
  // would be offered the Hotel card, and a hotel would be told it has nothing.
  if (!property) {
    return (
      <div className="p-8 md:p-12 text-stone-500 font-mono text-xs uppercase tracking-widest">
        Loading…
      </div>
    );
  }

  // One section: never ask. `resolveSection` has already selected it, so the sidebar is
  // populated by the time this lands.
  if (sections.length === 1) {
    const to = pathFor(sections[0].key);
    if (to) return <Navigate to={to} replace />;
  }

  if (sections.length === 0) {
    return (
      <div className="p-8 md:p-12">
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-3">
          Signed in as {user?.name}
        </div>
        <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">
          Nothing assigned yet.
        </h1>
        <p className="text-stone-400 mt-4 max-w-lg">
          This account has no screens it can open. An admin sets both the work areas and the
          screens on <span className="text-stone-200">Admin → Staff</span> — ask whoever
          administers BarFlow here to add yours, and sign in again.
        </p>
      </div>
    );
  }

  const open = (key) => {
    setSection(key);
    const to = pathFor(key);
    if (to) nav(to);
  };

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">
        Signed in as {user?.name}
      </div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Where are you working?
      </h1>
      <p className="text-stone-400 mb-10 max-w-xl">
        Pick a section and the menu follows you into it. You can switch from the bar at the
        top at any time — it does not sign you out.
      </p>

      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 max-w-5xl">
        {sections.map((s) => {
          const { icon: Icon, blurb } = FACE[s.key] || {};
          const figure = figures[s.key];
          return (
            <button
              key={s.key}
              type="button"
              onClick={() => open(s.key)}
              data-testid={`section-card-${s.key}`}
              className="group text-left border border-stone-800 bg-stone-900 rounded p-6 hover:border-orange-500/60 transition-colors"
            >
              {Icon && <Icon className="w-7 h-7 text-orange-500 mb-4" strokeWidth={1.5} />}
              <div className="text-lg font-bold uppercase tracking-wide text-stone-100 group-hover:text-orange-400">
                {s.label}
              </div>
              {/* The figure is the point of the card. Until it lands — or if the caller
                  is refused it — the card is just its label, which is still a door. */}
              <div className="mt-4 min-h-[3.25rem]">
                {figure && (
                  <>
                    <div className="text-2xl font-bold tabular-nums text-stone-100">
                      {figure.value}
                    </div>
                    <div className="text-[11px] tracking-widest uppercase text-stone-500 mt-1">
                      {figure.sub}
                    </div>
                  </>
                )}
              </div>
              <p className="text-sm text-stone-400 mt-2">{blurb}</p>
            </button>
          );
        })}
      </div>

      <p className="text-xs text-stone-500 mt-10 max-w-2xl">
        What you can open is decided by the API from your role, work areas and the screens
        ticked for you — not by this page. Editing configuration (rooms, rates, the menu and
        stock levels) is restricted to an administrator.
      </p>
    </div>
  );
}
