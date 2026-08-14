/**
 * Sections: which half of the business somebody is working in.
 *
 * A property running a hotel and a restaurant has two sets of screens that share nothing
 * but the front door. Showing both at once is what made the desk scroll past the KOT
 * board to reach Bookings, so `/app` asks first and the sidebar answers with one section
 * at a time.
 *
 * This module is pure — no React, no icons, no DOM — because the section a person lands
 * in and the sidebar they get are the two rules most likely to be wrong for somebody
 * whose account nobody thought about, and rules that can only be checked by clicking are
 * rules that get checked once. Everything here takes the nav array and a `/auth/me`
 * payload and returns data.
 *
 * Three filters narrow the nav, all of them mirroring the server rather than inventing a
 * second rule (see backend/services/access.py::can_access):
 *
 *   role       — as before; a kitchen hand has no business on the rates screen
 *   domain     — as before; a hotel-only manager is 403'd by the outlet endpoints
 *   permission — new; the screen keys ticked on the staff screen
 *
 * They narrow in that order and none of them replaces another. A menu entry that 403s
 * when clicked is worse than no menu entry, so anything the API would refuse is absent.
 */
import { OUTLET } from "@/lib/domains";

/**
 * The sections, in the order the chooser and the switcher show them.
 *
 * `domains` is what a person's *job* has to cover for the section to be offered at all —
 * the design's rule that domains drive the chooser and permissions decide the screens
 * within it. Without it a hotel-only manager who holds `outlet.inventory` (a screen
 * behind a `shared` endpoint, filed under Restaurant for presentation) would be offered
 * a "Restaurant" section containing one unrelated link.
 *
 * `headings` is which of the nav's own `section` headings belong here. Admin has no
 * domains because it spans them: analytics is grantable to a manager without making them
 * an admin, and the role filter on those items is what keeps everyone else out.
 *
 * "Property" appears under both Hotel and Restaurant on purpose. Guests and Inventory sit
 * behind `shared` endpoints — one store room supplies the kitchen, the bar and
 * housekeeping, and a bar regular and a hotel guest are the same person — so filing them
 * in one section would take Guests away from the outlet that charges a bill to a room.
 * The catalogue files them under a section for the staff screen's benefit; the sidebar
 * shows them wherever the person is standing.
 */
export const SECTIONS = [
  { key: "hotel", label: "Hotel", domains: ["hotel"], headings: ["Hotel", "Property"] },
  { key: "restaurant", label: "Restaurant", domains: OUTLET, headings: ["Restaurant", "Property"] },
  { key: "admin", label: "Admin", domains: null, headings: ["Admin"] },
];

export const sectionByKey = (key) => SECTIONS.find((s) => s.key === key) || null;

/**
 * Where the chosen section is remembered, keyed per user id.
 *
 * Two people share the terminal behind the bar. Keying this on the browser alone would
 * hand the second one whatever the first was doing, and a section is a claim about who
 * you are, not about which machine you sat at.
 */
export const sectionStorageKey = (userId) => `barflow_section_${userId}`;

/**
 * Whether ticking a screen for someone working in `held` could ever take effect.
 *
 * The client's copy of services/access.py::permission_in_domains, and the reason the
 * catalogue sends each screen's domains: the API answers 400 for a tick outside the
 * person's work areas, so the staff screen shows it as unavailable rather than letting it
 * be ticked and refused on save. `shared` means the endpoints behind the screen are not
 * domain-scoped at all — anybody working anywhere can reach them.
 */
export function screenInDomains(screen, held) {
  const required = screen?.domains || [];
  const has = held || [];
  if (has.length === 0) return false;
  if (required.length === 1 && required[0] === "shared") return true;
  return required.some((d) => has.includes(d));
}

/** Whether this user may work in a section at all, by work domain. Admin bypasses. */
export function holdsAnyDomain(user, domains) {
  if (user?.role === "admin") return true;
  if (!domains) return true; // a section that spans the business, e.g. Admin
  const held = user?.domains || [];
  return domains.some((d) => held.includes(d));
}

/**
 * A nav item is visible when the user is an admin, or holds any domain the item serves.
 * This mirrors the server's rule rather than inventing a second one — the API is the real
 * boundary, and a mismatch here shows a menu entry that 403s when clicked.
 */
export function visibleFor(item, user) {
  if (!item.to) return true; // section headings: kept or dropped by dropEmptySections
  if (user?.role === "admin") return true;
  if (!item.domains) return true; // unscoped items, e.g. the shared screens
  const held = user?.domains || [];
  return item.domains.some((d) => held.includes(d));
}

/**
 * Whether the user holds the screen key this nav item leads to.
 *
 * An admin reaches every screen regardless, exactly as they are never domain-checked.
 * A link with no `screen` fails closed for everyone else: the only one is the admin
 * console, which is already role-gated, and "no key" must never read as "no check" — that
 * is how a screen added later without a key would quietly become public.
 */
export function holdsScreen(item, user) {
  if (!item.to) return true;
  if (user?.role === "admin") return true;
  if (!item.screen) return false;
  return (user?.permissions || []).includes(item.screen);
}

/**
 * A heading whose items were all filtered out must not render — a "Hotel" label with
 * nothing under it reads as a bug. A heading survives only if a link follows it before
 * the next heading, i.e. the very next entry is a link.
 */
export function dropEmptySections(list) {
  return list.filter((item, i) => {
    if (!item.section) return true;
    const next = list[i + 1];
    return Boolean(next) && !next.section;
  });
}

/**
 * The sidebar for one section: the nav narrowed to that section's headings, then by
 * role, domain and permission, then with any heading left holding nothing dropped.
 *
 * Returns [] for a section the user's domains do not cover, so the caller never has to
 * ask that question separately.
 */
export function navForSection(nav, user, sectionKey) {
  const section = sectionByKey(sectionKey);
  if (!section || !user) return [];
  if (!holdsAnyDomain(user, section.domains)) return [];

  let heading = null;
  const kept = [];
  for (const item of nav) {
    if (item.section) heading = item.section;
    if (!section.headings.includes(heading)) continue;
    if (item.roles && !item.roles.includes(user.role)) continue;
    if (!visibleFor(item, user)) continue;
    if (!holdsScreen(item, user)) continue;
    kept.push(item);
  }
  return dropEmptySections(kept);
}

/** Every section this user has at least one reachable screen in, in SECTIONS order. */
export function availableSections(nav, user) {
  return SECTIONS.filter((s) => navForSection(nav, user, s.key).some((i) => i.to));
}

/** The path a section opens on: its first reachable screen, or null if it has none. */
export function firstPathIn(nav, user, sectionKey) {
  const first = navForSection(nav, user, sectionKey).find((i) => i.to);
  return first ? first.to : null;
}

/**
 * What `/app` should do for this person.
 *
 * `chooser` — two or more sections; the cards are the page.
 * `redirect` — exactly one; a one-option menu is a click that teaches nothing, so they
 *              go straight to their first screen and never see the chooser.
 * `none`     — nothing reachable. The backfill prevents it, so this is a message naming
 *              who to ask rather than an empty frame.
 */
export function landingFor(nav, user) {
  const sections = availableSections(nav, user);
  if (sections.length === 0) return { kind: "none", sections };
  if (sections.length === 1) {
    const key = sections[0].key;
    return { kind: "redirect", sections, section: key, to: firstPathIn(nav, user, key) };
  }
  return { kind: "chooser", sections };
}

/**
 * The section to start a session in: the one remembered for this user if they can still
 * work in it, otherwise the only one they have, otherwise none — which leaves the sidebar
 * empty and the chooser as the page, which is the point of asking.
 *
 * A remembered section that is no longer theirs is dropped rather than honoured: ticks
 * get taken away, and a stale key would leave them staring at an empty sidebar with no
 * heading to explain it.
 */
export function resolveSection(nav, user, stored) {
  const sections = availableSections(nav, user);
  if (stored && sections.some((s) => s.key === stored)) return stored;
  if (sections.length === 1) return sections[0].key;
  return null;
}
