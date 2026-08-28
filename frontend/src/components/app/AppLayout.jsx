import React, { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import {
  Grid3x3,
  CalendarClock,
  Receipt,
  ChefHat,
  Boxes,
  BookOpen,
  LineChart,
  LogOut,
  KeyRound,
  Wine,
  BedDouble,
  UserCheck,
  CalendarPlus,
  CalendarRange,
  ClipboardList,
  Tags,
  Users,
  Cake,
  ShieldCheck,
  LayoutDashboard,
  MessageSquare,
  Percent,
  Sparkles,
  TrendingUp,
} from "lucide-react";

import { OUTLET } from "@/lib/domains";
import { SectionProvider } from "@/contexts/SectionContext";
import { PropertyProvider, useOwnProperty } from "@/contexts/PropertyContext";
import PendingBanner from "@/components/app/PendingBanner";
import OverdueBanner from "@/components/app/OverdueBanner";
import {
  availableSections,
  firstPathIn,
  navForSection,
  resolveSection,
  sectionByKey,
  sectionStorageKey,
} from "@/lib/sections";

// Every item carries the screen key from GET /api/permissions that the endpoints behind
// it are declared with. The key is the join between this array and the catalogue, not a
// second copy of it: the catalogue says what exists and what it is called, this says
// which route opens it. A key that no longer exists in the catalogue takes its link with
// it — nobody holds a retired key, so the item is filtered out for everyone but an admin.
//
// The Overview entry is gone: /app is the section chooser now, and the top bar's section
// switch is how you go back to it.
const NAV = [
  { section: "Restaurant", roles: ["admin", "manager", "waiter", "kitchen"] },
  { to: "/app/tables", label: "Tables", icon: Grid3x3, roles: ["admin", "manager", "waiter"], domains: OUTLET, screen: "outlet.tables" },
  { to: "/app/reservations", label: "Reservations", icon: CalendarClock, roles: ["admin", "manager", "waiter"], domains: OUTLET, screen: "outlet.reservations" },
  { to: "/app/pos", label: "POS / Bill", icon: Receipt, roles: ["admin", "manager", "waiter"], domains: OUTLET, screen: "outlet.pos" },
  { to: "/app/kot", label: "KOT Board", icon: ChefHat, roles: ["admin", "manager", "kitchen", "waiter"], domains: OUTLET, screen: "outlet.kot" },
  { to: "/app/menu", label: "Menu", icon: BookOpen, roles: ["admin", "manager"], domains: OUTLET, screen: "outlet.menu" },
  { to: "/app/reports", label: "Reports", icon: LineChart, roles: ["admin", "manager"], domains: OUTLET, screen: "outlet.reports" },
  // `housekeeping` is on the heading as well as on its one link below. Without it an
  // attendant's sidebar would render that link with no heading over it, because the
  // heading is filtered by role exactly as the links are.
  { section: "Hotel", roles: ["admin", "manager", "front_desk", "housekeeping"] },
  { to: "/app/hotel/front-desk", label: "Front desk", icon: UserCheck, roles: ["admin", "manager", "front_desk"], domains: ["hotel"], screen: "hotel.front_desk" },
  { to: "/app/hotel/rooms", label: "Rooms", icon: BedDouble, roles: ["admin", "manager"], domains: ["hotel"], screen: "hotel.rooms" },
  { to: "/app/hotel/bookings/new", label: "New booking", icon: CalendarPlus, roles: ["admin", "manager", "front_desk"], domains: ["hotel"], screen: "hotel.bookings" },
  // "Bookings" is a path-prefix of "New booking" (/app/hotel/bookings/new starts with
  // /app/hotel/bookings) — exclude that sibling so the two links don't both light up.
  { to: "/app/hotel/bookings", label: "Bookings", icon: ClipboardList, roles: ["admin", "manager", "front_desk"], domains: ["hotel"], screen: "hotel.bookings", exclude: ["/app/hotel/bookings/new"] },
  { to: "/app/hotel/calendar", label: "Occupancy", icon: CalendarRange, roles: ["admin", "manager", "front_desk"], domains: ["hotel"], screen: "hotel.calendar" },
  // The attendant's screen, and the one link a `housekeeping` account has anywhere in the
  // app — `ROLE_SCREENS` gives that role exactly this key. `front_desk` is in the role
  // list because the endpoints behind it are, but they do not hold the key by default, so
  // the desk sees this only once an owner ticks it: the nav must not offer a link the API
  // would refuse, and `holdsScreen` is what decides that.
  { to: "/app/hotel/housekeeping", label: "Housekeeping", icon: Sparkles, roles: ["admin", "manager", "front_desk", "housekeeping"], domains: ["hotel"], screen: "hotel.housekeeping" },
  { to: "/app/hotel/rates", label: "Rates", icon: Tags, roles: ["admin", "manager"], domains: ["hotel"], screen: "hotel.rates" },
  // Endpoints the server declares SHARED, so they carry no `domains` here either — the
  // nav must not hide a route that answers the caller 200. They keep a heading of their
  // own rather than sitting under Restaurant or Hotel, because a shared item filed under
  // a domain heading is what produced the bug: "Guests" under Hotel read as hotel-only
  // and was tagged that way. Under section scoping the heading earns its keep twice
  // over — lib/sections.js shows this group in both the Hotel and the Restaurant
  // sidebar, so the desk keeps the store room and the bar keeps the guest list.
  { section: "Property", roles: ["admin", "manager", "front_desk", "kitchen"] },
  // guests.py declares SHARED: a bar regular and a hotel guest are the same person, and
  // splitting the records by domain would stop the desk seeing an arrival's bar history.
  { to: "/app/hotel/guests", label: "Guests", icon: Users, roles: ["admin", "manager", "front_desk"], screen: "hotel.guests" },
  // Occasions and the message log. Filed under Property beside Guests and keyed on
  // the same screen, because that is what it shows: guest names, guest numbers and
  // what has been sent to them. The endpoints behind it also name `waiter`, whose
  // own path into this feature is the POS — they record an occasion while settling
  // a bill, and a waiter ticked for Guests gets this link too.
  { to: "/app/messaging", label: "Messaging", icon: Cake, roles: ["admin", "manager", "front_desk"], screen: "hotel.guests" },
  // inventory.py declares SHARED too — one store room supplies the kitchen, the bar and
  // housekeeping.
  { to: "/app/inventory", label: "Inventory", icon: Boxes, roles: ["admin", "manager", "kitchen"], screen: "outlet.inventory" },
  // Analytics is open to managers as well as admins, so the heading over it has to be
  // too — otherwise a manager's Analytics link renders with the Staff heading dropped and
  // appears to belong to the Hotel group above it.
  { section: "Admin", roles: ["admin", "manager"] },
  // "Console" is a path-prefix of both admin children, so exclude them: the same reason
  // "Bookings" excludes "New booking" above. It is the one link with no screen key —
  // there is none for it in the catalogue — so it is admin-only twice over, by its role
  // list and by the fail-closed rule in holdsScreen.
  { to: "/app/admin", label: "Console", icon: LayoutDashboard, roles: ["admin"], exclude: ["/app/admin/staff", "/app/admin/analytics", "/app/admin/notifications", "/app/admin/settings"] },
  { to: "/app/admin/staff", label: "Staff", icon: ShieldCheck, roles: ["admin"], screen: "admin.staff" },
  // No `domains`: analytics spans them, and the server answers whichever ones the caller
  // holds. A manager with any single domain still has a report to read.
  { to: "/app/admin/analytics", label: "Analytics", icon: TrendingUp, roles: ["admin", "manager"], screen: "admin.analytics" },
  // No `screen`: it is admin-only by role, and adding a catalogue key would mean a
  // migration to grant it to every existing admin before the link appeared for anyone.
  { to: "/app/admin/notifications", label: "Notifications", icon: MessageSquare, roles: ["admin"] },
  // The outlet's GST rate. No `screen` for the same reason as Notifications above — a
  // catalogue key would need a migration to grant it to every existing admin before the
  // link appeared for anyone — and no `domains`: a property with no restaurant still has
  // the setting, it simply has no bill to put it on, and hiding it would leave an outlet
  // that later opens one with no way to reach it.
  { to: "/app/admin/settings", label: "Tax settings", icon: Percent, roles: ["admin"] },
];

export { NAV };

// The nav, narrowed for this user, in this section, of this property. Both renderings
// below map over it, so the filtering lives in lib/sections.js and is called once here.
//
// `property` is what makes the hotel half genuinely absent rather than merely unticked:
// an outlet has no Hotel heading and no hotel link at all, for its owner as much as for
// its waiters, because the endpoints behind them refuse everybody there.
export function visibleNavFor(user, section, property) {
  return section ? navForSection(NAV, user, section, property) : [];
}

function isNavItemActive(item, pathname) {
  const matches = item.end ? pathname === item.to : pathname.startsWith(item.to);
  if (!matches) return false;
  return !(item.exclude || []).some((p) => pathname.startsWith(p));
}

/**
 * The chosen section, remembered per user id so two people sharing the terminal behind
 * the bar do not inherit each other's shift.
 *
 * Reading localStorage in a lazy initialiser would be wrong here: `user` arrives after
 * the first render, when /auth/me answers, so the key to read under is not known yet.
 */
function useSectionState(user, property) {
  const sections = useMemo(
    () => availableSections(NAV, user, property), [user, property]);
  const [section, setStored] = useState(null);
  const id = user?.id;
  // The identities of the sections, not the array, so a re-render that rebuilds the same
  // list does not re-run the effect and throw away a choice made a moment ago. It is also
  // what re-runs it when the property lands: until then every section is filtered out, so
  // the remembered one has to be resolved again against the real list.
  const keys = sections.map((s) => s.key).join(",");

  useEffect(() => {
    if (!id) {
      setStored(null);
      return;
    }
    let remembered = null;
    try {
      remembered = localStorage.getItem(sectionStorageKey(id));
    } catch {
      // Private browsing, a full quota, a locked-down device: the section is a
      // convenience, so losing it costs a click and must never cost the app.
    }
    setStored(resolveSection(NAV, user, remembered, property));
    // `user` and `property` are read inside but `id` and `keys` are what change the
    // answer; depending on the objects themselves would re-run this on every render of
    // the provider.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, keys]);

  const setSection = useCallback(
    (key) => {
      setStored(key);
      if (!id) return;
      try {
        if (key) localStorage.setItem(sectionStorageKey(id), key);
        else localStorage.removeItem(sectionStorageKey(id));
      } catch {
        // As above.
      }
    },
    [id],
  );

  const pathFor = useCallback(
    (key) => firstPathIn(NAV, user, key, property), [user, property]);

  return useMemo(
    () => ({ section, sections, setSection, pathFor }),
    [section, sections, setSection, pathFor],
  );
}

/**
 * Switching sections without logging out. Rendered only when there is more than one to
 * switch between — a single pill that cannot be unpicked is furniture, not a control.
 */
function SectionSwitch({ value, sections, onPick, className = "" }) {
  if (sections.length < 2) return null;
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <span className="text-[10px] font-mono uppercase tracking-[0.25em] text-stone-600 hidden sm:inline">
        Section
      </span>
      {sections.map((s) => (
        <button
          key={s.key}
          type="button"
          data-testid={`section-switch-${s.key}`}
          aria-pressed={value === s.key}
          onClick={() => onPick(s.key)}
          className={`text-[10px] font-mono uppercase tracking-widest border rounded-full px-3 py-1 transition-colors ${
            value === s.key
              ? "border-orange-500 text-orange-400 bg-orange-500/10"
              : "border-stone-700 text-stone-500 hover:border-stone-500 hover:text-stone-300"
          }`}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}

export default function AppLayout({ children }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  // Read once, here, and handed to everything below: the sidebar, the section chooser,
  // the pending banner and the staff screen all need to know what this property is, and
  // four fetches would be four moments at which they could disagree about it.
  const property = useOwnProperty();
  const value = useSectionState(user, property);
  const { section, sections, setSection, pathFor } = value;

  const items = visibleNavFor(user, section, property);
  const current = sectionByKey(section);

  // Picking a section from the top bar takes you into it, rather than leaving you on a
  // page belonging to the section you just left with a sidebar that no longer lists it.
  const pick = (key) => {
    setSection(key);
    const to = pathFor(key);
    if (to) nav(to);
  };

  const signOut = () => {
    logout();
    nav("/login");
  };

  return (
    <PropertyProvider value={property}>
    <SectionProvider value={value}>
      <div className="min-h-screen flex bg-stone-950 text-stone-100 relative z-[2]">
        {/* Sidebar */}
        <aside className="hidden md:flex flex-col w-60 border-r border-stone-800 bg-stone-950/80 backdrop-blur-2xl sticky top-0 h-screen">
          <div className="p-6 border-b border-stone-800 flex items-center gap-2">
            <Wine className="text-orange-500" size={22} />
            <div>
              <div className="font-display text-lg tracking-tight uppercase leading-none">BarFlow</div>
              <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500 mt-1">
                {current ? current.label : "Ops Console"}
              </div>
            </div>
          </div>
          {/* Empty until a section is chosen: the chooser is the page, and a sidebar
              offering everything defeats the point of asking. */}
          <nav className="flex-1 py-4">
            {items.map((item) => {
              if (item.section) {
                return (
                  <div
                    key={`section-${item.section}`}
                    className="px-6 pt-6 pb-2 text-[10px] font-mono uppercase tracking-[0.3em] text-stone-600"
                  >
                    {item.section}
                  </div>
                );
              }
              const { to, label, icon: Icon, end } = item;
              const active = isNavItemActive(item, loc.pathname);
              return (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  data-testid={`nav-${label.toLowerCase().replace(/[^a-z]/g, "-")}`}
                  className={() =>
                    `flex items-center gap-3 px-6 py-3 text-sm border-l-2 transition-colors ${
                      active
                        ? "border-orange-500 bg-stone-900 text-orange-400"
                        : "border-transparent text-stone-400 hover:text-stone-100 hover:bg-stone-900/50"
                    }`
                  }
                >
                  <Icon size={16} />
                  <span className="font-mono uppercase tracking-widest text-xs">{label}</span>
                </NavLink>
              );
            })}
          </nav>
          <div className="p-4 border-t border-stone-800">
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500">
              Signed in as
            </div>
            <div className="mt-1 text-sm">{user?.name}</div>
            <div className="text-xs font-mono text-orange-500 uppercase mt-0.5">{user?.role}</div>
            {/* Under the name rather than in the nav above it: changing your own password
                is not a screen of the business, it is a thing about the person signed in,
                and this block is the only part of the sidebar that is about them. It has
                no screen key and no role list — everybody has a password. */}
            <NavLink
              to="/account"
              data-testid="account-link"
              className="mt-4 w-full flex items-center justify-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-3 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
            >
              <KeyRound size={14} /> Password
            </NavLink>
            <button
              data-testid="logout-button"
              onClick={signOut}
              className="mt-2 w-full flex items-center justify-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-3 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
            >
              <LogOut size={14} /> Sign out
            </button>
          </div>
        </aside>

        {/* Mobile top bar */}
        <div className="md:hidden fixed top-0 inset-x-0 z-20 bg-stone-950/90 backdrop-blur-xl border-b border-stone-800">
          <div className="flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2">
              <Wine className="text-orange-500" size={18} />
              <span className="font-display uppercase">BarFlow</span>
            </div>
            {/* The mobile bar has no account block to hang this under, so it sits beside
                the sign-out it is the neighbour of everywhere else. Both shells and both
                widths reach the same page. */}
            <div className="flex items-center gap-4">
              <NavLink
                to="/account"
                data-testid="account-link-mobile"
                className="text-xs font-mono uppercase text-stone-400"
              >
                Password
              </NavLink>
              <button
                data-testid="logout-button-mobile"
                onClick={signOut}
                className="text-xs font-mono uppercase text-stone-400"
              >
                Sign out
              </button>
            </div>
          </div>
          {sections.length > 1 && (
            <div className="px-4 pb-3 overflow-x-auto no-scrollbar">
              <SectionSwitch value={section} sections={sections} onPick={pick} />
            </div>
          )}
          <div className="flex overflow-x-auto no-scrollbar border-t border-stone-800">
            {items
              .filter((item) => item.to)
              .map((item) => {
                const { to, label, end } = item;
                const active = isNavItemActive(item, loc.pathname);
                return (
                  <NavLink
                    key={to}
                    to={to}
                    end={end}
                    className={`px-4 py-3 text-[10px] font-mono uppercase tracking-widest whitespace-nowrap border-b-2 ${
                      active ? "text-orange-400 border-orange-500" : "text-stone-500 border-transparent"
                    }`}
                  >
                    {label}
                  </NavLink>
                );
              })}
          </div>
        </div>

        <main className="flex-1 min-w-0 pt-24 md:pt-0">
          {/* The desktop counterpart of the mobile switch above. Both render the same
              control, so a section can be changed without signing out on either. */}
          {sections.length > 1 && (
            <div className="hidden md:flex items-center justify-between gap-4 border-b border-stone-800 bg-stone-950/80 backdrop-blur-xl px-6 py-3 sticky top-0 z-10">
              <SectionSwitch value={section} sections={sections} onPick={pick} />
              <button
                type="button"
                data-testid="section-chooser-link"
                onClick={() => nav("/app")}
                className="text-[10px] font-mono uppercase tracking-widest text-stone-500 hover:text-orange-400"
              >
                All sections
              </button>
            </div>
          )}
          {/* Above every page rather than on one of them: the locked buttons are spread
              across the front desk, the booking form and the till, and an explanation
              that only appears on the screen you were not looking at explains nothing.
              It renders null for a live property and for anyone with no property at all —
              see lib/tenancy.js::showsPendingBanner. */}
          <PendingBanner />
          {/* Same reasoning, same place, and it blocks nothing: an overdue business is
              still trading and this is a note about an invoice, not a lock. It sits under
              the pending banner because both can be true at once and neither is written as
              though the other were absent. */}
          <OverdueBanner />
          {children}
        </main>
      </div>
    </SectionProvider>
    </PropertyProvider>
  );
}
