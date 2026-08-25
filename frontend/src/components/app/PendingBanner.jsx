import { Clock, Lock } from "lucide-react";

import { useAuth } from "@/contexts/AuthContext";
import { useProperty } from "@/contexts/PropertyContext";
import {
  lockedUntilApproved,
  showsPendingBanner,
  unlockedWhilePending,
} from "@/lib/tenancy";

/**
 * Shown across the app while the hotel is `pending`, so a locked button is explained
 * rather than merely broken.
 *
 * It says both halves on purpose. "Awaiting approval" alone reads as "wait and do
 * nothing", which is the opposite of what a pending hotel should be doing — the whole
 * point of the state is that they build their rooms and rates now. So the banner leads
 * with what is open and names what is not, in the words of the job rather than the
 * endpoint.
 *
 * It renders nothing for a `live` property and nothing for the operator, who has no
 * property at all. Both of those are decided by `showsPendingBanner`, which is pure and
 * checkable without a browser.
 *
 * The property is read from the context AppLayout fills rather than fetched here. This
 * component used to own that request; it now shares it with the sidebar and the staff
 * screen, which need the same record to know what kind of business this is.
 */
export default function PendingBanner() {
  const { user } = useAuth();
  const property = useProperty();

  if (!showsPendingBanner(user, property)) return null;

  // The two lists follow what this property is: a restaurant is not waiting on approval
  // to build its room types, it will never have any.
  const open = unlockedWhilePending(property.property_type);
  const locked = lockedUntilApproved(property.property_type);

  return (
    <div
      data-testid="pending-banner"
      className="border-b border-orange-500/40 bg-orange-950/20 px-6 py-4"
    >
      <div className="flex items-start gap-3">
        <Clock size={16} className="text-orange-400 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="text-[10px] font-mono uppercase tracking-[0.25em] text-orange-400">
            Awaiting approval
          </div>
          <p className="text-sm text-stone-300 mt-1">
            <span className="text-stone-100">{property.name}</span> is registered and being
            reviewed. Set the place up now — {open.join(", ").toLowerCase()}{" "}
            are all open, and nothing you build here is lost when you go live.
          </p>
          <p className="text-xs text-stone-400 mt-2 flex items-start gap-2">
            <Lock size={12} className="mt-0.5 shrink-0" />
            <span>
              Locked until approved: {locked.join(", ").toLowerCase()}. Those
              screens open, and are refused when you press the button.
            </span>
          </p>
        </div>
      </div>
    </div>
  );
}
