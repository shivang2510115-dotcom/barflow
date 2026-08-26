import { Receipt } from "lucide-react";

import { currency } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { useProperty } from "@/contexts/PropertyContext";
import { showsOverdueBanner } from "@/lib/tenancy";
import { overdueNotice } from "@/lib/subscription";

/**
 * Shown across the app while this business has an invoice past due.
 *
 * The same shape as PendingBanner beside it — one strip above the page, from the property
 * the layout already fetched, with its whole show/hide decision in a pure function in
 * lib/tenancy so it is checkable without a browser. It is not a second pattern.
 *
 * What it is *not* is the pending banner's tone. That one explains a locked button; this
 * one blocks nothing. An overdue business is still trading, on purpose: nothing on the
 * server switches a property off on a date, and the only thing that ends trade is the
 * operator pressing Suspend. So this is stone and amber rather than orange-on-warning, it
 * names a figure and a date and stops, and it says out loud that everything still works —
 * a receptionist who reads it mid-check-in should go on checking the guest in.
 *
 * The figure comes from `subscription` on `GET /api/property`, which the server derives on
 * every read. `payment_note` is deliberately absent from that payload — it is the
 * operator's own memo about how this business pays, not a message to it — so the banner
 * points at the operator rather than inventing bank details.
 *
 * Both banners can be true at once: a business can be pending and overdue. They stack, and
 * neither one's wording assumes the other is absent.
 */
export default function OverdueBanner() {
  const { user } = useAuth();
  const property = useProperty();

  if (!showsOverdueBanner(user, property)) return null;

  const notice = overdueNotice(property, currency);
  // Belt and braces: showsOverdueBanner is true only when `subscription.overdue` is, which
  // the server sets only for a priced property, so this cannot be null in practice.
  if (!notice) return null;

  return (
    <div
      data-testid="overdue-banner"
      className="border-b border-amber-500/40 bg-amber-950/20 px-6 py-4"
    >
      <div className="flex items-start gap-3">
        <Receipt size={16} className="text-amber-400 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="text-[10px] font-mono uppercase tracking-[0.25em] text-amber-400">
            Invoice due
          </div>
          <p className="text-sm text-stone-300 mt-1 tabular-nums">
            Your {notice.schedule} subscription of{" "}
            <span className="text-stone-100">{notice.amount}</span> has been due since{" "}
            <span className="text-stone-100">{notice.since}</span> — {notice.days} ago.
          </p>
          {/* The reassurance is the point of the banner, not a footnote to it. */}
          <p className="text-xs text-stone-400 mt-2">
            Nothing has changed here: bookings, the front desk and the till all work as
            normal, and everyone can still log in. Send the payment the usual way, or talk to
            us if the figure looks wrong.
          </p>
        </div>
      </div>
    </div>
  );
}
