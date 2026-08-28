import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { startAlertPolling, visibleAlerts } from "@/lib/housekeeping";

/**
 * The open housekeeping requests, kept current by polling, for whoever this browser
 * belongs to.
 *
 * The two rules that make this worth having are both in `lib/housekeeping.js`, where they
 * can be run without a browser: `startAlertPolling` stops the moment the tab is hidden
 * and fetches immediately on return, and `visibleAlerts` never hands back a job that has
 * been acknowledged. This hook is the wiring between them and `GET /housekeeping/alerts`.
 *
 * `enabled` is the caller's answer to "does this person hold the hotel domain" — see
 * `pollsAlerts`. False means no request is ever made, not a request whose answer is
 * ignored: an outlet-only waiter must not be calling this every fifteen seconds.
 */
export default function useHousekeepingAlerts(enabled) {
  const [jobs, setJobs] = useState([]);
  // Every job this browser has acknowledged. A ref rather than state because it is read
  // inside the poll and must never be a stale copy, and it is only ever added to: a poll
  // already in flight when the button was pressed still carries the job as `open`, and
  // without this it would flash back onto the screen a moment after it was cleared.
  const acknowledged = useRef(new Set());

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/housekeeping/alerts");
      setJobs(visibleAlerts(data?.jobs, acknowledged.current));
    } catch {
      // A failed poll leaves what is on screen alone. Hotel wifi drops; the requests did
      // not go anywhere, and blanking the alert would read as "nothing to do".
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setJobs([]);
      return undefined;
    }
    return startAlertPolling({ poll: load });
  }, [enabled, load]);

  /**
   * Somebody has picked this up. It leaves the screen now and does not come back.
   *
   * Hidden before the request rather than after it, because the alert is the thing being
   * acted on and a button that waits on a round trip gets pressed twice. If the request
   * genuinely fails the job is un-hidden and the caller is told — a request nobody picked
   * up that has silently vanished from every screen is worse than one shown twice.
   */
  const acknowledge = useCallback(
    async (id) => {
      acknowledged.current.add(id);
      setJobs((current) => current.filter((job) => job.id !== id));
      try {
        await api.post(`/housekeeping/jobs/${id}/acknowledge`);
        return { ok: true };
      } catch (e) {
        acknowledged.current.delete(id);
        load();
        return { ok: false, error: e };
      }
    },
    [load],
  );

  return { jobs, acknowledge, refresh: load };
}
