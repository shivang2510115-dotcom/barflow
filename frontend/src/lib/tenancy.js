/**
 * Tenancy: which console a login belongs in, and whether its hotel may trade yet.
 *
 * Pure — no React, no axios, no DOM — for the same reason lib/sections.js is. The two
 * rules here are the ones most easily got wrong for an account nobody clicked through:
 * the platform operator must never be dropped into the hotel app, whose every endpoint
 * refuses them, and the pending banner must appear for exactly the people whose buttons
 * are locked. Rules that can only be checked by clicking get checked once.
 *
 * Everything below takes a `/auth/me`-shaped user, or a `GET /api/property` payload, and
 * returns data. None of it is a security boundary: the API is (see
 * backend/services/access.py::can_access). These functions decide which of two consoles
 * to render, and every request behind either is still checked server-side.
 */

export const PLATFORM_ADMIN = "platform_admin";

export const PENDING = "pending";
export const LIVE = "live";
export const SUSPENDED = "suspended";

/** The statuses the platform console filters and labels, in the order it shows them. */
export const STATUSES = [PENDING, LIVE, SUSPENDED];

export const STATUS_BLURB = {
  [PENDING]: "Signed up, setting up. Cannot take a booking or settle a bill yet.",
  [LIVE]: "Approved and trading.",
  [SUSPENDED]: "Switched off. Every login of this hotel is refused; nothing is deleted.",
};

/** The operator: belongs to no hotel, and is refused every hotel endpoint by the API. */
export const isOperator = (user) => user?.role === PLATFORM_ADMIN;

/**
 * Which of the two consoles this login belongs in.
 *
 * There are exactly two and they share no screen. A hotel user is 403'd by every
 * `/api/platform/*` route; the operator is 403'd by every hotel route, including
 * `/api/property` and `/auth/me`. Either one rendered inside the other's shell sees a
 * page whose every request fails, so the router sends them home instead.
 */
export const consoleFor = (user) => (isOperator(user) ? "platform" : "hotel");

/** Where a signed-in user lands: after login, and from any route that is not theirs. */
export function homePathFor(user) {
  if (!user) return "/login";
  return consoleFor(user) === "platform" ? "/platform" : "/app";
}

/** Whether this user may open `/platform`. */
export const canOpenPlatform = (user) => isOperator(user);

/**
 * What a guarded route should do: a path to redirect to, or null to render it.
 *
 * The whole of `<Protected>`'s decision, pulled out here so it can be checked without a
 * browser. `user` is `null` while `/auth/me` is in flight and `false` for a guest, exactly
 * as AuthContext holds it, and both are answered rather than assumed away — "still
 * loading" and "not signed in" sending someone to the same place is how a slow network
 * turns into a bounce to the login screen.
 *
 * Returns the string "loading" for the first of those, because it is neither a redirect
 * nor a render and a caller that treats it as either is wrong.
 */
export function routeDecision(user, { area = "hotel", roles = null } = {}) {
  if (user === null) return "loading";
  if (!user) return "/login";
  if (consoleFor(user) !== area) return homePathFor(user);
  if (roles && !roles.includes(user.role)) return homePathFor(user);
  return null;
}

/**
 * Whether to ask `GET /api/property` for this user at all.
 *
 * The operator is not merely uninterested in the answer — they are refused it, and a 403
 * fired on every page load to learn something already known is noise in the log and a
 * failed request in the console.
 */
export const readsOwnProperty = (user) => Boolean(user) && !isOperator(user);

/**
 * Whether the pending banner shows.
 *
 * Not shown for a `live` property, and not shown for the operator, who has none. It is
 * deliberately false while the property is still loading (`property` null): a banner that
 * flashes on every navigation and then vanishes reads as a glitch, and being told a moment
 * late that the hotel is pending costs nothing.
 */
export function showsPendingBanner(user, property) {
  if (!readsOwnProperty(user)) return false;
  return property?.status === PENDING;
}

/** What a pending hotel can do now, and what waits for approval. Shown on both screens. */
export const UNLOCKED_WHILE_PENDING = [
  "Property details",
  "Room types",
  "Rooms",
  "Rates and seasons",
  "The menu and tables",
  "Staff logins",
];

export const LOCKED_UNTIL_APPROVED = [
  "Taking a booking",
  "Checking a guest in or out",
  "Opening or settling a bill",
];

