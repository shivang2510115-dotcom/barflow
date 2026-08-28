/**
 * Housekeeping on the client: what a status is called, what an alert says, and when the
 * browser is allowed to ask.
 *
 * Pure, dependency-free and deliberately outside React — no imports at all, not even the
 * `@/lib` ones — for the same reason `lib/roomGrid.js` is: the three rules most likely to
 * be wrong here cannot be checked by clicking. A background tab that keeps polling costs
 * function invocations nobody reads, an alert that reappears after somebody acknowledged
 * it trains a receptionist to ignore alerts, and a waiter making this call every fifteen
 * seconds is a bill for a screen they cannot open. All three are answered by functions in
 * this file, which is runnable in node against a real API response.
 *
 * Nothing here is a security boundary. The transition table is
 * `backend/services/housekeeping.py` and it is enforced in the router; what this file
 * does is stop the attendant tapping a button that is going to come back 403.
 */

// ------------------------------ room status ------------------------------
// The same four strings as services/housekeeping.py::STATUSES, in the order a room moves
// through them.
export const CLEAN = "clean";
export const DIRTY = "dirty";
export const INSPECTED = "inspected";
export const OUT_OF_ORDER = "out_of_order";

export const STATUSES = [CLEAN, DIRTY, INSPECTED, OUT_OF_ORDER];

/** What each status is called on a screen somebody is holding in a corridor. */
export const STATUS_LABELS = {
  [CLEAN]: "Clean",
  [DIRTY]: "Dirty",
  [INSPECTED]: "Inspected",
  [OUT_OF_ORDER]: "Out of order",
};

/**
 * What tapping it means, in the attendant's words rather than the model's.
 *
 * On the button, not in a tooltip: this is a phone, there is no hover, and "inspected"
 * is a word two hotels use differently.
 */
export const STATUS_BLURB = {
  [CLEAN]: "Made up and ready for a guest",
  [DIRTY]: "Needs doing",
  [INSPECTED]: "Checked over after cleaning",
  [OUT_OF_ORDER]: "Something is broken — a manager puts it back",
};

/** Ready means a guest can be sent to it. Mirrors `is_ready` in the service. */
export const isReady = (status) => status === CLEAN || status === INSPECTED;

/** The seed stands in for a room the migration has not reached. Mirrors `status_of`. */
export const statusOf = (room) => (room || {}).housekeeping_status || CLEAN;

/**
 * Whether setting this status has to say why. Only `out_of_order` — a room marked broken
 * with no reason is one nobody can fix and nobody can put back, and the API answers 400.
 */
export const noteRequired = (status) => status === OUT_OF_ORDER;

/**
 * Why this room offers no status at all, or null when it offers some.
 *
 * `can_set` comes off the board — the server computes it from the transition table and
 * the caller's role, so this file never has a second opinion about who may do what. What
 * it does have to do is say *why* the list is empty, because a card with no buttons and
 * no sentence reads as a broken screen rather than as a room waiting on somebody else.
 */
export function noOptionsReason(card) {
  if ((card?.can_set || []).length > 0) return null;
  if (statusOf(card) === OUT_OF_ORDER)
    return "Only a manager can take a room back out of out-of-order, once the fault is confirmed fixed.";
  return "You cannot change this room's status.";
}

/**
 * One room as the floor plan draws it: the state key `RoomGrid` looks up, the word for
 * anyone the colour does not reach, and the two facts an attendant is actually working
 * from — is somebody in it, and are they leaving today.
 *
 * The shape is `lib/roomGrid.js`'s, so the same component draws this screen as draws the
 * other three. The state key is the housekeeping status itself.
 */
export function housekeepingState(card) {
  const status = statusOf(card);
  const lines = [];
  if (card?.occupied) lines.push(card?.departing_today ? "In house · departs today" : "In house");
  else if (card?.departing_today) lines.push("Departs today");
  const jobs = card?.jobs || [];
  if (jobs.length) lines.push(`${jobs.length} request${jobs.length === 1 ? "" : "s"}`);

  return {
    state: status,
    label: STATUS_LABELS[status] || status,
    // The note is the whole content of `out_of_order` and is what the attendant needs to
    // read off the door; on any other status it is stale text about a fault that is over.
    note: status === OUT_OF_ORDER ? card?.housekeeping_note || null : null,
    lines,
  };
}

// --------------------------------- requests ---------------------------------
export const OPEN = "open";
export const IN_PROGRESS = "in_progress";
export const DONE = "done";
export const CANCELLED = "cancelled";

export const JOB_STATUS_LABELS = {
  [OPEN]: "Open",
  [IN_PROGRESS]: "Picked up",
  [DONE]: "Done",
  [CANCELLED]: "Called off",
};

/** Most urgent first — the order the list and the alert show them in. */
export const PRIORITIES = ["high", "normal", "low"];
export const DEFAULT_PRIORITY = "normal";
export const PRIORITY_LABELS = { high: "High", normal: "Normal", low: "Low" };

const PRIORITY_RANK = { high: 0, normal: 1, low: 2 };
const rankOf = (p) => (p in PRIORITY_RANK ? PRIORITY_RANK[p] : PRIORITY_RANK[DEFAULT_PRIORITY]);

/**
 * Whether a guest raised this, rather than somebody working here.
 *
 * `source` is the field that says so and `raised_by` being null is the same fact seen
 * from the other side; both are checked because a job written before `source` existed
 * would otherwise read as staff-raised, and "the guest in 204 asked" and "the desk
 * noticed" are different things to the manager reading the list.
 */
export const isGuestRaised = (job) => job?.source === "guest" || !job?.raised_by;

export const jobSourceLabel = (job) => (isGuestRaised(job) ? "Guest" : "Staff");

/** Still waiting on somebody. */
export const jobIsLive = (job) => job?.status === OPEN || job?.status === IN_PROGRESS;

/** Most urgent first, then oldest first — a high-priority job never sinks under a new low one. */
export function compareJobs(a, b) {
  const byPriority = rankOf(a?.priority) - rankOf(b?.priority);
  if (byPriority !== 0) return byPriority;
  return String(a?.created_at || "").localeCompare(String(b?.created_at || ""));
}

// ------------------------------- the alert -------------------------------
/** Roughly every fifteen seconds, per the addendum. */
export const POLL_INTERVAL_MS = 15000;

/**
 * The open requests this alert should be showing, out of what the poll just returned.
 *
 * Three rules, and each of them is a requirement rather than a tidy-up:
 *
 * 1. **An acknowledged job never comes back.** `acknowledged` is a set the caller only
 *    ever adds to, so a job that was picked up half a second before the poll left the
 *    server — and is therefore still `open` in that payload — does not flash back onto
 *    the screen. Acknowledgement is the only thing that makes an alert stop appearing,
 *    and an alert that reappears anyway is one a receptionist learns to ignore.
 * 2. **One row per job.** Ids are de-duplicated, because a payload that repeated one
 *    would put the same room on the screen twice and the acknowledge button would then
 *    only clear one of them.
 * 3. **Most urgent first.** What is drawn first is what gets acted on, and the alert is
 *    read at a glance across a desk.
 *
 * The caller renders these as *one* alert saying how many there are, never as one
 * dismissable box each — see `HousekeepingAlert`.
 */
export function visibleAlerts(jobs, acknowledged) {
  const seen = new Set();
  const has = (id) =>
    acknowledged instanceof Set ? acknowledged.has(id) : (acknowledged || []).includes(id);
  const out = [];
  for (const job of jobs || []) {
    if (!job || !job.id) continue;
    if (has(job.id)) continue;
    if (seen.has(job.id)) continue;
    seen.add(job.id);
    out.push(job);
  }
  return out.sort(compareJobs);
}

/**
 * Whether this browser polls the alert at all.
 *
 * The addendum puts the alert in front of every signed-in user who holds the **hotel**
 * domain, and in front of nobody else: an outlet-only waiter has no room to clean and no
 * screen to open, so a call from their tab every fifteen seconds is an invocation billed
 * for nothing. `domains` is the answer `lib/domains.js::heldDomains` gives for this user
 * *in this property* — passed in rather than imported, so this file keeps no opinion
 * about how a domain is worked out and stays runnable outside a bundler.
 */
export function pollsAlerts(user, domains) {
  if (!user) return false;
  return (domains || []).includes("hotel");
}

/**
 * Poll while somebody is looking, and not otherwise.
 *
 * Free of React and of the DOM — both arrive as arguments — because the behaviour this
 * function exists for is a sequence over time, which is exactly what a component test
 * cannot show and a script with a fake clock can.
 *
 * The contract, in the addendum's words:
 *
 * * a **hidden tab does not poll at all**. A front desk leaves this open all day; a
 *   background tab asking four times a minute forever costs function invocations for a
 *   screen nobody is looking at.
 * * coming back **fetches immediately** and then resumes the interval. Waiting up to
 *   fifteen seconds to find out what happened while you were away is the whole thing the
 *   alert is for.
 * * `stop()` is final. It clears the timer and unsubscribes, and a visibility change
 *   arriving after it does nothing — an unmounted layout must not keep a poll alive.
 *
 * @param poll        () => void — one fetch. Never called while hidden.
 * @param intervalMs  how often, when visible
 * @param isVisible   () => boolean
 * @param subscribe   (fn) => unsubscribe, called on every visibility change
 * @param schedule    (fn, ms) => handle
 * @param cancel      (handle) => void
 */
export function startAlertPolling({
  poll,
  intervalMs = POLL_INTERVAL_MS,
  isVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden",
  subscribe = (fn) => {
    if (typeof document === "undefined") return () => {};
    document.addEventListener("visibilitychange", fn);
    return () => document.removeEventListener("visibilitychange", fn);
  },
  schedule = (fn, ms) => setInterval(fn, ms),
  cancel = (handle) => clearInterval(handle),
}) {
  let timer = null;
  let stopped = false;

  const sync = () => {
    if (stopped) return;
    if (isVisible()) {
      // `timer === null` is what makes this an *edge*: a visibility event that leaves the
      // tab visible does not fire a second fetch, and a hidden→visible one always does.
      if (timer === null) {
        poll();
        timer = schedule(poll, intervalMs);
      }
      return;
    }
    if (timer !== null) {
      cancel(timer);
      timer = null;
    }
  };

  const unsubscribe = subscribe(sync);
  // The first call is the mount: a tab opened in the background never makes the request.
  sync();

  return () => {
    stopped = true;
    if (timer !== null) {
      cancel(timer);
      timer = null;
    }
    if (unsubscribe) unsubscribe();
  };
}
