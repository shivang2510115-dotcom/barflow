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
 * Four filters narrow the nav, all of them mirroring the server rather than inventing a
 * second rule (see backend/services/access.py::can_access):
 *
 *   property   — what this tenant *is*: an outlet has no rooms, so no hotel screen and
 *                no Hotel section exist for anybody in it, its owner included
 *   role       — a kitchen hand has no business on the rates screen
 *   domain     — a hotel-only manager is 403'd by the outlet endpoints
 *   permission — the screen keys ticked on the staff screen
 *
 * They narrow in that order and none of them replaces another. The property filter is
 * first for the same reason the server checks the property ahead of the admin bypass: the
 * other three all wave an admin through, and an outlet's owner is still an admin.
 *
 * A menu entry that 403s when clicked is worse than no menu entry, so anything the API
 * would refuse is absent.
 */
import { OUTLET, propertyDomains } from "@/lib/domains";

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

/**
 * Whether this property runs the part of the business a nav item serves.
 *
 * No admin bypass, deliberately — this is not about the person. A restaurant that never
 * had a room does not acquire one because the owner is signed in.
 *
 * An item with no `domains` sits behind a `shared` endpoint (Guests, Inventory, the admin
 * console) and is kept for every property, exactly as `can_access` exempts `shared`.
 */
export function propertyRuns(item, runs) {
  if (!item.to) return true; // section headings: kept or dropped by dropEmptySections
  if (!item.domains) return true;
  return item.domains.some((d) => runs.includes(d));
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
 * A link with no `screen` fails closed for everyone else: "no key" must never read as
 * "no check", because that is how a screen added later without a key would quietly
 * become public.
 *
 * `open: true` is the one way past that, and it is a claim about the *endpoints*, not a
 * convenience: it says the routes behind this link declare no screen key at all, so
 * everybody who reaches this far in the filtering already reaches the API. The planner is
 * the case it exists for — `routers/planner.py::READ` declares the domain alone, because
 * a fire drill on Thursday is posted for everybody who works here and `ROLE_SCREENS` is
 * frozen, so a key would hide it from every waiter for good. It is spelt out per item and
 * greppable for exactly the reason the fail-closed default is: `grep -n "open: true"`
 * over the nav is the whole audit, and an item that carries it without the API agreeing
 * is a link that 403s.
 */
export function holdsScreen(item, user) {
  if (!item.to) return true;
  if (user?.role === "admin") return true;
  if (item.open) return true;
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
 * property, role, domain and permission, then with any heading left holding nothing
 * dropped.
 *
 * Returns [] for a section this property does not run, and for one the user's domains do
 * not cover, so the caller never has to ask either question separately.
 *
 * `property` is the `GET /api/property` payload, or null while it is still unknown — and
 * null narrows to nothing rather than to everything. The sidebar is briefly empty on a
 * cold load, which is a beat of quiet; the alternative is a Hotel section appearing for a
 * restaurant and then disappearing, which is the bug this argument exists to fix.
 */
export function navForSection(nav, user, sectionKey, property = null) {
  const section = sectionByKey(sectionKey);
  if (!section || !user) return [];
  // Nothing at all until the property is known — Admin included, even though it spans the
  // business and would otherwise survive. Offering Admin alone for the half-second before
  // the property lands would leave an admin with exactly one section, and one section is
  // never asked about: they would be redirected into the console before the chooser had
  // the information to ask.
  if (!property) return [];
  const runs = propertyDomains(property);
  // A section is only offered when the property has one of its domains. Admin has none —
  // it spans the business — so it survives here and is gated by its items' roles.
  if (section.domains && !section.domains.some((d) => runs.includes(d))) return [];
  if (!holdsAnyDomain(user, section.domains)) return [];

  let heading = null;
  const kept = [];
  for (const item of nav) {
    if (item.section) heading = item.section;
    if (!section.headings.includes(heading)) continue;
    if (!propertyRuns(item, runs)) continue;
    if (item.roles && !item.roles.includes(user.role)) continue;
    if (!visibleFor(item, user)) continue;
    if (!holdsScreen(item, user)) continue;
    kept.push(item);
  }
  return dropEmptySections(kept);
}

/**
 * Everything this user can reach, in one list, across every section.
 *
 * The sidebar used to be empty until somebody picked a section, which was defensible
 * while the chooser was the landing screen and indefensible the moment it was not: a
 * bookmark, a shared link, or cleared session storage all landed a person on a working
 * page with no navigation at all and no way out but the top bar.
 *
 * Every filter is the one `navForSection` applies — same roles, same domains, same
 * screen keys, same property gating — run over all sections rather than one. So this
 * cannot show a link that section-scoped navigation would have hidden; it is the union
 * of what the sections would each have offered, not a wider grant.
 */
export function navAcrossSections(nav, user, property = null) {
  if (!user || !property) return [];
  const runs = propertyDomains(property);

  let heading = null;
  const kept = [];
  for (const item of nav) {
    if (item.section) heading = item.section;
    if (!propertyRuns(item, runs)) continue;
    if (item.roles && !item.roles.includes(user.role)) continue;
    if (!visibleFor(item, user)) continue;
    if (!holdsScreen(item, user)) continue;
    kept.push(item);
  }
  return dropEmptySections(kept);
}

/** Every section this user has at least one reachable screen in, in SECTIONS order. */
export function availableSections(nav, user, property = null) {
  return SECTIONS.filter((s) => navForSection(nav, user, s.key, property).some((i) => i.to));
}

/** The path a section opens on: its first reachable screen, or null if it has none. */
export function firstPathIn(nav, user, sectionKey, property = null) {
  const first = navForSection(nav, user, sectionKey, property).find((i) => i.to);
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
export function landingFor(nav, user, property = null) {
  // Neither a chooser nor a redirect: the property has not arrived, so the question of
  // which sections exist has no answer yet. Said out loud for the same reason
  // `routeDecision` says "loading" — a caller that reads it as "none" sends somebody who
  // has everything to a screen telling them they have nothing.
  if (!property) return { kind: "loading", sections: [] };
  const sections = availableSections(nav, user, property);
  if (sections.length === 0) return { kind: "none", sections };
  if (sections.length === 1) {
    const key = sections[0].key;
    return {
      kind: "redirect", sections, section: key,
      to: firstPathIn(nav, user, key, property),
    };
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
export function resolveSection(nav, user, stored, property = null) {
  const sections = availableSections(nav, user, property);
  if (stored && sections.some((s) => s.key === stored)) return stored;
  if (sections.length === 1) return sections[0].key;
  return null;
}
