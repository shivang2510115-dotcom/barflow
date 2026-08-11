/**
 * The work-domain vocabulary, once.
 *
 * The backend keeps its single copy in `backend/services/access.py` and guards its one
 * restatement (staff.py's pydantic Literal) with an import-time equality check. The
 * client had three copies — the nav, the staff screen and the analytics screen each
 * declaring their own — which is three chances for the day a fourth domain is added to
 * leave one screen quietly showing the old three. This module is the client's copy;
 * everything else imports it.
 *
 * Keep in step with services/access.py: DOMAINS there, OUTLET there.
 */

// Every area a staff member can be assigned to, in the order screens display them.
export const DOMAINS = ["hotel", "restaurant", "bar"];

// This property's restaurant and bar share one POS, one till and one set of orders, so
// endpoints serving them declare both and holding either grants access. Mirrors
// services/access.py::OUTLET.
export const OUTLET = ["restaurant", "bar"];

// How a domain is written when it is shown to a person.
export const DOMAIN_LABELS = { hotel: "Hotel", restaurant: "Restaurant", bar: "Bar" };

/**
 * Every domain this user can act in — the client's reading of the server's rule in
 * `services/access.py::can_access`. An admin is never domain-checked, so they hold all
 * of them whatever their stored list happens to say. Unknown values are dropped rather
 * than passed on, and the result keeps DOMAINS' order rather than the user's.
 */
export function heldDomains(user) {
  if (!user) return [];
  if (user.role === "admin") return [...DOMAINS];
  const held = user.domains || [];
  return DOMAINS.filter((d) => held.includes(d));
}
