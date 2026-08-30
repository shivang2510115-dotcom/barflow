import { useState } from "react";
import { BellRing, ChevronDown, ChevronUp, Check, User } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/contexts/AuthContext";
import { useProperty } from "@/contexts/PropertyContext";
import { heldDomains } from "@/lib/domains";
import { formatApiErrorDetail } from "@/lib/api";
import {
  PRIORITY_LABELS,
  isGuestRaised,
  pollsAlerts,
} from "@/lib/housekeeping";
import useHousekeepingAlerts from "@/hooks/useHousekeepingAlerts";

/**
 * The requests nobody has picked up yet, on every screen of the hotel console.
 *
 * Three things about it are requirements rather than styling:
 *
 * **One alert, never a stack.** Five open requests are one panel that says five, with the
 * rooms listed inside it. A dismissable box per request is a wall a receptionist clears
 * one at a time, and the second time they do that they stop reading them.
 *
 * **Acknowledging is the only way out.** There is no dismiss and no close — collapsing
 * shrinks the panel to a line that still says how many are waiting. A request that can be
 * swiped away is a request that gets swiped away, and nobody comes.
 *
 * **It does not poll for people it is not for.** `pollsAlerts` gates the whole hook on the
 * hotel domain, so a waiter in an outlet makes no request at all — not one whose answer is
 * thrown away. See `lib/housekeeping.js`.
 */
export default function HousekeepingAlert() {
  const { user } = useAuth();
  const property = useProperty();
  const [collapsed, setCollapsed] = useState(false);
  const [busy, setBusy] = useState(null);

  // The property narrows this as well as the person: an outlet has no rooms, so its own
  // owner — an admin, who is never domain-checked — is not polling for them either.
  const enabled = pollsAlerts(user, heldDomains(user, property));
  const { jobs, acknowledge } = useHousekeepingAlerts(enabled);

  if (!enabled || jobs.length === 0) return null;

  const pick = async (job) => {
    setBusy(job.id);
    const { ok, error } = await acknowledge(job.id);
    setBusy(null);
    if (ok) toast.success(job.room_number ? `Room ${job.room_number} picked up` : "Picked up");
    else toast.error(formatApiErrorDetail(error?.response?.data?.detail) || "Could not acknowledge that");
  };

  return (
    <div
      data-testid="housekeeping-alert"
      // Bottom right on a laptop, across the bottom of a phone, and never over the
      // navigation: this sits on top of whatever screen somebody is working on, so it has
      // to be small enough to ignore and close enough to reach with a thumb.
      className="hk-alert fixed z-40 bottom-0 inset-x-0 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-80
                 border-t sm:border border-brass/40 bg-surface/95 backdrop-blur-xl
                 shadow-2xl shadow-black/50"
      role="status"
      aria-live="polite"
    >
      <button
        type="button"
        data-testid="housekeeping-alert-toggle"
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
        className="w-full flex items-center gap-2 px-4 py-3 text-left"
      >
        <BellRing size={14} className="text-brass shrink-0" aria-hidden="true" />
        <span className="text-[10px] font-mono uppercase tracking-[0.25em] text-brass">
          Housekeeping
        </span>
        {/* The count is the whole message when this is collapsed, and it is why several
            requests are one alert: "3 waiting" is a thing to act on, three identical
            boxes are a thing to clear. */}
        <span className="ml-auto text-xs font-mono text-muted2" data-testid="housekeeping-alert-count">
          {jobs.length} waiting
        </span>
        {collapsed ? (
          <ChevronUp size={14} className="text-faint" aria-hidden="true" />
        ) : (
          <ChevronDown size={14} className="text-faint" aria-hidden="true" />
        )}
      </button>

      {!collapsed && (
        // Scrolls rather than grows: a hotel having a bad morning must not end up with an
        // alert taller than the screen behind it.
        <ul className="max-h-64 overflow-y-auto border-t border-hairline">
          {jobs.map((job) => (
            <li
              key={job.id}
              data-testid={`housekeeping-alert-job-${job.id}`}
              className="px-4 py-3 border-b border-hairline last:border-b-0"
            >
              <div className="flex items-center gap-2">
                <span className="font-mono tabular-nums text-lg leading-none">
                  {job.room_number || "—"}
                </span>
                <span
                  className={`text-[9px] font-mono uppercase tracking-widest px-1.5 py-0.5 border ${
                    job.priority === "high"
                      ? "border-red-500/60 text-red-300"
                      : "border-hairline-strong text-muted2"
                  }`}
                >
                  {PRIORITY_LABELS[job.priority] || job.priority}
                </span>
                {/* A guest asked, or the desk noticed. Different facts, so they are told
                    apart by a word and an icon rather than by a shade of grey. */}
                {isGuestRaised(job) && (
                  <span
                    className="inline-flex items-center gap-1 text-[9px] font-mono uppercase tracking-widest text-brass"
                    data-testid="alert-guest-badge"
                  >
                    <User size={10} aria-hidden="true" /> Guest
                  </span>
                )}
                <button
                  type="button"
                  data-testid={`acknowledge-${job.id}`}
                  disabled={busy === job.id}
                  onClick={() => pick(job)}
                  className="ml-auto shrink-0 inline-flex items-center gap-1 border border-brass/50 text-brass
                             hover:bg-brass-deep/10 disabled:opacity-50 rounded-full px-3 py-1.5
                             text-[10px] font-mono uppercase tracking-widest"
                >
                  <Check size={12} aria-hidden="true" />
                  {busy === job.id ? "…" : "On it"}
                </button>
              </div>
              {job.reason ? (
                // `whitespace-pre-line`: a guest who pressed twice has their second
                // sentence appended on a new line by the merge rule, not concatenated.
                <p className="mt-1.5 text-xs text-muted2 whitespace-pre-line break-words">
                  {job.reason}
                </p>
              ) : (
                <p className="mt-1.5 text-xs text-faint italic">No reason given</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
