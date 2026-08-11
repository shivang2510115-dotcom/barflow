import React from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import {
  LayoutGrid,
  Grid3x3,
  CalendarClock,
  Receipt,
  ChefHat,
  Boxes,
  BookOpen,
  LineChart,
  LogOut,
  Wine,
  BedDouble,
  UserCheck,
  CalendarPlus,
  CalendarRange,
  ClipboardList,
  Tags,
  Users,
  ShieldCheck,
  LayoutDashboard,
  TrendingUp,
} from "lucide-react";

const OUTLET = ["restaurant", "bar"];

const NAV = [
  // Overview carries no domains: it is the landing page every signed-in role gets.
  { to: "/app", label: "Overview", icon: LayoutGrid, roles: ["admin", "manager", "waiter", "kitchen"], end: true },
  { section: "Restaurant", roles: ["admin", "manager", "waiter", "kitchen"] },
  { to: "/app/tables", label: "Tables", icon: Grid3x3, roles: ["admin", "manager", "waiter"], domains: OUTLET },
  { to: "/app/reservations", label: "Reservations", icon: CalendarClock, roles: ["admin", "manager", "waiter"], domains: OUTLET },
  { to: "/app/pos", label: "POS / Bill", icon: Receipt, roles: ["admin", "manager", "waiter"], domains: OUTLET },
  { to: "/app/kot", label: "KOT Board", icon: ChefHat, roles: ["admin", "manager", "kitchen", "waiter"], domains: OUTLET },
  { to: "/app/inventory", label: "Inventory", icon: Boxes, roles: ["admin", "manager", "kitchen"], domains: OUTLET },
  { to: "/app/menu", label: "Menu", icon: BookOpen, roles: ["admin", "manager"], domains: OUTLET },
  { to: "/app/reports", label: "Reports", icon: LineChart, roles: ["admin", "manager"], domains: OUTLET },
  { section: "Hotel", roles: ["admin", "manager", "front_desk"] },
  { to: "/app/hotel/front-desk", label: "Front desk", icon: UserCheck, roles: ["admin", "manager", "front_desk"], domains: ["hotel"] },
  { to: "/app/hotel/rooms", label: "Rooms", icon: BedDouble, roles: ["admin", "manager"], domains: ["hotel"] },
  { to: "/app/hotel/bookings/new", label: "New booking", icon: CalendarPlus, roles: ["admin", "manager", "front_desk"], domains: ["hotel"] },
  // "Bookings" is a path-prefix of "New booking" (/app/hotel/bookings/new starts with
  // /app/hotel/bookings) — exclude that sibling so the two links don't both light up.
  { to: "/app/hotel/bookings", label: "Bookings", icon: ClipboardList, roles: ["admin", "manager", "front_desk"], domains: ["hotel"], exclude: ["/app/hotel/bookings/new"] },
  { to: "/app/hotel/calendar", label: "Occupancy", icon: CalendarRange, roles: ["admin", "manager", "front_desk"], domains: ["hotel"] },
  { to: "/app/hotel/rates", label: "Rates", icon: Tags, roles: ["admin", "manager"], domains: ["hotel"] },
  { to: "/app/hotel/guests", label: "Guests", icon: Users, roles: ["admin", "manager", "front_desk"], domains: ["hotel"] },
  // Analytics is open to managers as well as admins, so the heading over it has to be
  // too — otherwise a manager's Analytics link renders with the Staff heading dropped and
  // appears to belong to the Hotel group above it.
  { section: "Admin", roles: ["admin", "manager"] },
  // "Console" is a path-prefix of both admin children, so exclude them: the same reason
  // "Bookings" excludes "New booking" above.
  { to: "/app/admin", label: "Console", icon: LayoutDashboard, roles: ["admin"], exclude: ["/app/admin/staff", "/app/admin/analytics"] },
  { to: "/app/admin/staff", label: "Staff", icon: ShieldCheck, roles: ["admin"] },
  // No `domains`: analytics spans them, and the server answers whichever ones the caller
  // holds. A manager with any single domain still has a report to read.
  { to: "/app/admin/analytics", label: "Analytics", icon: TrendingUp, roles: ["admin", "manager"] },
];

// A nav item is visible when the user is an admin, or holds any domain the item serves.
// This mirrors the server's rule rather than inventing a second one — the API is the real
// boundary, and a mismatch here shows a menu entry that 403s when clicked.
function visibleFor(item, user) {
  if (!item.to) return true; // section headings: kept or dropped by dropEmptySections
  if (user?.role === "admin") return true;
  if (!item.domains) return true; // unscoped items, e.g. Overview
  const held = user?.domains || [];
  return item.domains.some((d) => held.includes(d));
}

// A heading whose items were all filtered out must not render — a "Hotel" label with
// nothing under it reads as a bug. A heading survives only if a link follows it before
// the next heading, i.e. the very next entry is a link.
function dropEmptySections(list) {
  return list.filter((item, i) => {
    if (!item.section) return true;
    const next = list[i + 1];
    return Boolean(next) && !next.section;
  });
}

// Both renderings below map over this, so the filtering lives here only once.
export function visibleNavFor(user) {
  const allowed = NAV.filter(
    (n) => (!user || n.roles.includes(user.role)) && visibleFor(n, user)
  );
  return dropEmptySections(allowed);
}

function isNavItemActive(item, pathname) {
  const matches = item.end ? pathname === item.to : pathname.startsWith(item.to);
  if (!matches) return false;
  return !(item.exclude || []).some((p) => pathname.startsWith(p));
}

export default function AppLayout({ children }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();

  const items = visibleNavFor(user);

  return (
    <div className="min-h-screen flex bg-stone-950 text-stone-100 relative z-[2]">
      {/* Sidebar */}
      <aside className="hidden md:flex flex-col w-60 border-r border-stone-800 bg-stone-950/80 backdrop-blur-2xl sticky top-0 h-screen">
        <div className="p-6 border-b border-stone-800 flex items-center gap-2">
          <Wine className="text-orange-500" size={22} />
          <div>
            <div className="font-display text-lg tracking-tight uppercase leading-none">BarFlow</div>
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500 mt-1">
              Ops Console
            </div>
          </div>
        </div>
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
          <button
            data-testid="logout-button"
            onClick={() => {
              logout();
              nav("/login");
            }}
            className="mt-4 w-full flex items-center justify-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-3 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
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
          <button
            data-testid="logout-button-mobile"
            onClick={() => {
              logout();
              nav("/login");
            }}
            className="text-xs font-mono uppercase text-stone-400"
          >
            Sign out
          </button>
        </div>
        <div className="flex overflow-x-auto no-scrollbar border-t border-stone-800">
          {items.filter((item) => item.to).map((item) => {
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

      <main className="flex-1 md:ml-0 pt-24 md:pt-0">{children}</main>
    </div>
  );
}
