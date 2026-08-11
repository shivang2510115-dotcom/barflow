import { Link } from "react-router-dom";
import { BedDouble, TrendingUp, ShieldCheck, UtensilsCrossed } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

const DOORS = [
  {
    to: "/app/hotel/front-desk",
    icon: BedDouble,
    label: "Hotel",
    detail: "Front desk, bookings, rooms, rates and guest folios.",
  },
  {
    to: "/app/tables",
    icon: UtensilsCrossed,
    label: "Bar & Restaurant",
    detail: "Tables, POS, kitchen board, menu and stock.",
  },
  {
    to: "/app/admin/staff",
    icon: ShieldCheck,
    label: "Staff",
    detail: "Add people, set what they can reach, reset a password.",
  },
  {
    to: "/app/admin/analytics",
    icon: TrendingUp,
    label: "Analytics",
    detail: "Revenue across the property — hotel, outlets, or both.",
  },
];

export default function Console() {
  const { user } = useAuth();
  // Time-neutral on purpose. A greeting that names a part of the day is wrong for most of
  // the day unless something guarantees which clock it is reading, and nothing here does.
  const first = user?.name ? user.name.split(" ")[0] : "";

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        {first ? `Hello, ${first}` : "Console"}
      </h1>
      <p className="text-stone-400 mb-10">Pick where you want to work.</p>

      <div className="grid gap-5 sm:grid-cols-2 max-w-4xl">
        {DOORS.map(({ to, icon: Icon, label, detail }) => (
          <Link
            key={to}
            to={to}
            data-testid={`door-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}
            className="group border border-stone-800 bg-stone-900 rounded p-6 hover:border-orange-500/60 transition-colors"
          >
            <Icon className="w-7 h-7 text-orange-500 mb-4" strokeWidth={1.5} />
            <div className="text-lg font-bold uppercase tracking-wide text-stone-100 group-hover:text-orange-400">
              {label}
            </div>
            <p className="text-sm text-stone-400 mt-2">{detail}</p>
          </Link>
        ))}
      </div>

      {/* The cards are a shortcut, not a boundary: every one of these screens is gated by
          the API on the caller's role and work domains, whatever the menu shows. */}
      <p className="text-xs text-stone-500 mt-10 max-w-2xl">
        Everything here is also in the menu on the left. What each person can actually open
        is decided by the API from their role and work domains, not by this page.
      </p>
    </div>
  );
}
