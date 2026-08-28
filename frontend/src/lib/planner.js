/**
 * The planner's date arithmetic: which days a view covers, and how to step between them.
 *
 * Pure — no React, no DOM, no network — for the reason lib/sections.js gives about itself:
 * the rules most likely to be wrong are the ones that can only be checked by clicking, and
 * rules that can only be checked by clicking get checked once. Everything here takes
 * strings and returns strings.
 *
 * **Every date in this module is a `YYYY-MM-DD` string, and it is never an instant.**
 * `new Date(iso).toISOString().slice(0, 10)` is the bug this whole feature was written
 * around: it converts to UTC, so for a user east of Greenwich between midnight and their
 * offset it answers *yesterday*. The backend has `services/clock.py` for the same reason
 * and `pages/hotel/Calendar.jsx` already carries the note. So dates are built from local
 * getters, and stepping is done on the parts rather than on a timestamp.
 *
 * No date library. `date-fns` and `dayjs` are both in package.json and neither is imported
 * here on purpose: this is about sixty lines of arithmetic that the month grid depends on
 * completely, and it is worth being able to read all of it in one place.
 */

/** The days of the week, starting Monday — the day a hotel's planning week starts. */
export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const VIEWS = ["month", "week", "day"];

// Month is the default because that is how a manager plans: the question is "what is
// happening this month", and the week and the day are how you look closer at the answer.
export const DEFAULT_VIEW = "month";

const pad = (n) => String(n).padStart(2, "0");

/** A `Date` as the calendar day it is *locally*, never the UTC day it maps onto. */
export function toLocalISODate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Today, as this browser's calendar day. Used only until the server says what the
 *  property's today is — the two can differ, and the server's answer wins. */
export const todayLocal = () => toLocalISODate(new Date());

/** `YYYY-MM-DD` → `[year, month, day]` as numbers, month 1-based. */
const parts = (iso) => String(iso).split("-").map(Number);

/**
 * A `Date` in local noon on this calendar day.
 *
 * Noon, not midnight, and that is the whole trick: constructing at midnight and then
 * adding days crosses a daylight-saving boundary in a timezone that has one and lands on
 * 23:00 the previous day. India has none, but the browser doing the arithmetic may be
 * anywhere, and a grid that skips a day for a manager travelling is a bug nobody would
 * ever reproduce at the property.
 */
function atNoon(iso) {
  const [y, m, d] = parts(iso);
  return new Date(y, m - 1, d, 12, 0, 0, 0);
}

export function addDays(iso, n) {
  const d = atNoon(iso);
  d.setDate(d.getDate() + n);
  return toLocalISODate(d);
}

/**
 * `iso` moved by whole months, clamped to the last day of the month it lands in.
 *
 * Clamped, unlike the backend's *recurrence* rule, which skips a month with no such day.
 * They are different questions and the difference is deliberate: a repeat on the 31st
 * means the 31st and moving it would announce an event on a day nobody chose, whereas
 * this is only "which month am I looking at" — stepping forward from the 31st of January
 * must show February, not skip it.
 */
export function addMonths(iso, n) {
  const [y, m, d] = parts(iso);
  const total = y * 12 + (m - 1) + n;
  const year = Math.floor(total / 12);
  const month = total - year * 12; // 0-based
  const lastDay = new Date(year, month + 1, 0).getDate();
  return `${year}-${pad(month + 1)}-${pad(Math.min(d, lastDay))}`;
}

/** The Monday of the week `iso` falls in. */
export function startOfWeek(iso) {
  const d = atNoon(iso);
  // getDay() is 0 for Sunday; the planning week starts on Monday, so Sunday is day 6.
  const offset = (d.getDay() + 6) % 7;
  return addDays(iso, -offset);
}

export const startOfMonth = (iso) => `${iso.slice(0, 7)}-01`;

export function endOfMonth(iso) {
  const [y, m] = parts(iso);
  return `${y}-${pad(m)}-${pad(new Date(y, m, 0).getDate())}`;
}

/** Every day from `start` to `end` inclusive. */
export function daysBetween(start, end) {
  const out = [];
  for (let day = start; day <= end; day = addDays(day, 1)) out.push(day);
  return out;
}

/**
 * The window a view covers, which is exactly what the API is asked for.
 *
 * The month view asks for its *padded* range — the whole weeks the month is drawn in — so
 * that the leading and trailing cells from the neighbouring months show what is on them
 * rather than reading as empty days.
 */
export function rangeFor(view, anchor) {
  if (view === "day") return { start: anchor, end: anchor };
  if (view === "week") {
    const start = startOfWeek(anchor);
    return { start, end: addDays(start, 6) };
  }
  const start = startOfWeek(startOfMonth(anchor));
  // Six rows always. Five would be enough for most months and would make the grid change
  // height as you page through the year, which moves everything below it under the cursor.
  return { start, end: addDays(start, 41) };
}

/** The month grid: six rows of seven days, as arrays of `YYYY-MM-DD`. */
export function monthGrid(anchor) {
  const { start } = rangeFor("month", anchor);
  return Array.from({ length: 6 }, (_, row) =>
    Array.from({ length: 7 }, (_, col) => addDays(start, row * 7 + col)),
  );
}

/** Whether this day belongs to the month being looked at, rather than to a neighbour. */
export const inMonth = (iso, anchor) => iso.slice(0, 7) === anchor.slice(0, 7);

/**
 * Stepping between periods. `view` is untouched — moving between periods must not lose
 * the view you were in, which is what this signature is for: nothing here can change it.
 */
export function step(view, anchor, delta) {
  if (view === "day") return addDays(anchor, delta);
  if (view === "week") return addDays(anchor, delta * 7);
  return addMonths(anchor, delta);
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

/** `2026-08-04` → `4 August 2026`. */
export function longDate(iso) {
  const [y, m, d] = parts(iso);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** What the heading over the grid says for this view and anchor. */
export function periodLabel(view, anchor) {
  if (view === "day") return longDate(anchor);
  if (view === "week") {
    const start = startOfWeek(anchor);
    const end = addDays(start, 6);
    // "3 – 9 August 2026" when one month, "31 August – 6 September 2026" when two.
    if (start.slice(0, 7) === end.slice(0, 7)) {
      return `${Number(start.slice(8))} – ${longDate(end)}`;
    }
    const [, sm, sd] = parts(start);
    return `${sd} ${MONTHS[sm - 1]} – ${longDate(end)}`;
  }
  const [y, m] = parts(anchor);
  return `${MONTHS[m - 1]} ${y}`;
}

/** Occurrences filed under the day they fall on. The API already sorts them. */
export function byDay(events) {
  const out = {};
  for (const e of events || []) {
    (out[e.occurrence_date] ||= []).push(e);
  }
  return out;
}

/** Categories by id, for colouring a chip without searching the list per event. */
export function categoriesById(categories) {
  return Object.fromEntries((categories || []).map((c) => [c.id, c]));
}

/**
 * How an event reads on one line: "16:00", "16:00 – 17:30", or "All day".
 *
 * A missing time is a state with a name, not a blank — the whole reason the API sends
 * `all_day` rather than leaving the screen to infer it from an empty string.
 */
export function timeLabel(event) {
  if (event.all_day) return "All day";
  if (event.end_time) return `${event.start_time} – ${event.end_time}`;
  return event.start_time;
}

/** What a repeating event says about itself. Null when it does not repeat. */
export function repeatLabel(event) {
  if (!event.repeat) return null;
  const every = event.repeat === "weekly" ? "Every week" : "Every month";
  return event.repeat_until ? `${every}, until ${longDate(event.repeat_until)}` : every;
}
