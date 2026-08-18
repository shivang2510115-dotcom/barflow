import { useEffect, useState } from "react";
import { Clock, Lock } from "lucide-react";

import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import {
  LOCKED_UNTIL_APPROVED,
  UNLOCKED_WHILE_PENDING,
  readsOwnProperty,
  showsPendingBanner,
} from "@/lib/tenancy";

/**
 * The caller's own hotel, or null while it is unknown.
 *
 * Asked once per mount of the layout rather than per page, and not asked at all for the
 * platform operator, who is refused it — see `readsOwnProperty`. A failure is swallowed:
 * this drives a banner, and a hotel whose status could not be read is shown no banner,
 * which is the same thing the API will tell them the moment they press a locked button.
 */
export function useOwnProperty() {
  const { user } = useAuth();
  const [property, setProperty] = useState(null);
  const asks = readsOwnProperty(user);

  useEffect(() => {
    if (!asks) {
      setProperty(null);
      return;
    }
    let live = true;
    api
      .get("/property")
      .then((r) => live && setProperty(r.data))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [asks]);

  return property;
}

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
 */
export default function PendingBanner() {
  const { user } = useAuth();
  const property = useOwnProperty();

  if (!showsPendingBanner(user, property)) return null;

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
            reviewed. Set the place up now — {UNLOCKED_WHILE_PENDING.join(", ").toLowerCase()}{" "}
            are all open, and nothing you build here is lost when you go live.
          </p>
          <p className="text-xs text-stone-400 mt-2 flex items-start gap-2">
            <Lock size={12} className="mt-0.5 shrink-0" />
            <span>
              Locked until approved: {LOCKED_UNTIL_APPROVED.join(", ").toLowerCase()}. Those
              screens open, and are refused when you press the button.
            </span>
          </p>
        </div>
      </div>
    </div>
  );
}
