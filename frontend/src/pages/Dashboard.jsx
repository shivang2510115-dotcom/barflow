import React, { useEffect, useState } from "react";
import { api, currency } from "@/lib/api";
import {
  BarChart,
  Bar,
  ResponsiveContainer,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";
import { TrendingUp, Grid3x3, ShoppingBag, AlertTriangle } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { OUTLET, heldDomains } from "@/lib/domains";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import AnimatedNumber from "@/components/app/AnimatedNumber";
import AmbientBG from "@/components/app/AmbientBG";

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Late night, Chief";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 22) return "Good evening";
  return "Late night";
}

function Stat({ label, value, icon: Icon, hint, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.5, ease: "easeOut" }}
      whileHover={{ y: -4, transition: { duration: 0.15 } }}
      className="relative border border-stone-800 hover:border-orange-500/60 bg-stone-900/40 p-6 group"
      data-testid={`stat-${label.toLowerCase().replace(/\s+/g,"-")}`}
    >
      <div className="flex items-center justify-between text-stone-500">
        <div className="text-[10px] uppercase tracking-[0.25em] font-mono">{label}</div>
        <Icon size={16} className="text-orange-500 group-hover:rotate-6 transition-transform" />
      </div>
      <div className="mt-4 font-display text-4xl tracking-tight">{value}</div>
      {hint && <div className="mt-2 text-xs font-mono uppercase tracking-widest text-stone-500">{hint}</div>}
    </motion.div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const [summary, setSummary] = useState(null);
  const canSeeReports = ["admin", "manager"].includes(user?.role);

  useEffect(() => {
    if (!canSeeReports) return;
    api.get("/reports/summary").then((r) => setSummary(r.data)).catch(() => {});
  }, [canSeeReports]);

  if (!canSeeReports) {
    // This is a landing page, so every link on it has to be one this person can actually
    // open. /api/tables and /api/orders/kot are require_access(OUTLET, ...): a front desk
    // user holding only `hotel` gets a 403 on both, and has no Overview nav entry to
    // escape to, so they would land here on a dead end. The rule below is the server's
    // own — an admin is never domain-checked, everyone else needs the domain — read from
    // the same list the API is written against rather than a second one invented here.
    // Role as well as domain: both have to pass on the server, and they exclude
    // different people. A kitchen hand holding `restaurant` is refused the floor map by
    // role; a receptionist holding `hotel` is refused it by domain.
    const held = heldDomains(user);
    const inOutlet = held.some((d) => OUTLET.includes(d));
    const inHotel = held.includes("hotel");
    const has = (...roles) => roles.includes(user?.role);

    const canTables = inOutlet && has("admin", "manager", "waiter");
    const canKot = inOutlet && has("admin", "manager", "waiter", "kitchen");
    const canDesk = inHotel && has("admin", "manager", "front_desk");

    const blurb = () => {
      if (canTables && canDesk)
        return "Take a table from the floor map, or pick up today's arrivals at the front desk.";
      if (canTables) return "Grab a table from the floor map to start a bill.";
      if (canKot) return "Head to the KOT board — tickets will land there as guests order.";
      if (canDesk)
        return "Today's arrivals, departures and in-house guests are on the front desk board.";
      // Domains are assigned by an admin on the staff screen; an account with none can
      // reach nothing, so say that plainly rather than offering a link that 403s.
      return "Nothing is assigned to this account yet — ask an admin to set your work areas on the staff screen.";
    };

    const primary =
      "rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-6 py-3 text-xs font-mono uppercase tracking-widest";
    const secondary =
      "border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-6 py-3 text-xs font-mono uppercase tracking-widest";

    return (
      <div className="p-8 md:p-12">
        <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-3">
          Welcome, {user?.name}
        </div>
        <h1 className="font-display uppercase text-4xl md:text-6xl leading-none tracking-tight">
          Ready for service.
        </h1>
        <p className="text-stone-400 mt-4 max-w-md">{blurb()}</p>
        <div className="mt-8 flex gap-3 flex-wrap">
          {canTables && (
            <Link to="/app/tables" className={primary}>
              Open Tables
            </Link>
          )}
          {canKot && (
            <Link to="/app/kot" className={canTables ? secondary : primary}>
              KOT Board
            </Link>
          )}
          {canDesk && (
            <>
              <Link to="/app/hotel/front-desk" className={canTables || canKot ? secondary : primary}>
                Front Desk
              </Link>
              {/* The front desk board is today; Bookings is the rest of the diary. */}
              <Link to="/app/hotel/bookings" className={secondary}>
                Bookings
              </Link>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 md:p-10 relative min-h-screen">
      <AmbientBG />
      <div className="relative z-10">
      <div className="flex items-baseline justify-between flex-wrap gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-orange-500 mb-2">
            {greeting()} · {user?.name?.split(" ")[0]}
          </div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight">
            Tonight&apos;s service.
          </h1>
        </div>
        <div className="text-xs font-mono uppercase tracking-widest text-stone-500">
          {new Date().toLocaleString()}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-8">
        <Stat delay={0.05} label="Revenue · Today" value={<><span className="text-orange-400">$</span><AnimatedNumber value={summary?.revenue_today || 0} decimals={2} /></>} icon={TrendingUp} />
        <Stat delay={0.15} label="Orders · Today" value={<AnimatedNumber value={summary?.orders_today || 0} />} icon={ShoppingBag} />
        <Stat delay={0.25} label="Tables Occupied" value={<><AnimatedNumber value={summary?.tables_occupied || 0} /> / <AnimatedNumber value={summary?.tables_total || 0} /></>} icon={Grid3x3} />
        <Stat delay={0.35} label="Low Stock" value={<AnimatedNumber value={summary?.low_stock_count || 0} />} icon={AlertTriangle} hint="Items below threshold" />
      </div>

      <div className="grid lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2 border border-stone-800 bg-stone-900/40 p-6">
          <div className="flex items-center justify-between mb-6">
            <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500">Last 7 days · Revenue</div>
          </div>
          <div className="h-64" data-testid="revenue-chart">
            {summary?.daily_revenue?.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={summary.daily_revenue}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#292524" />
                  <XAxis dataKey="date" stroke="#78716c" tick={{ fontFamily: "JetBrains Mono", fontSize: 10 }} />
                  <YAxis stroke="#78716c" tick={{ fontFamily: "JetBrains Mono", fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: "#1c1917", border: "1px solid #292524", borderRadius: 0 }} />
                  <Bar dataKey="revenue" fill="#ea580c">
                    {summary.daily_revenue.map((_, i) => (
                      <Cell key={i} fill="#ea580c" />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-stone-600 text-sm font-mono uppercase tracking-widest">
                No settled orders yet
              </div>
            )}
          </div>
        </div>

        <div className="border border-stone-800 bg-stone-900/40 p-6">
          <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-6">Top items</div>
          {summary?.top_items?.length ? (
            <ul className="space-y-4">
              {summary.top_items.map((it, i) => (
                <li key={it.name} className="flex items-center gap-4">
                  <div className="font-mono text-orange-500 text-xs w-6">#{i + 1}</div>
                  <div className="flex-1 text-sm">{it.name}</div>
                  <div className="font-mono text-xs text-stone-400">{it.qty}×</div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-stone-600 text-sm font-mono uppercase tracking-widest">
              No data yet
            </div>
          )}
        </div>
      </div>
      </div>
    </div>
  );
}
