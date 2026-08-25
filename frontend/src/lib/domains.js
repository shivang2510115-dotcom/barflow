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
 * Keep in step with services/access.py: DOMAINS there, OUTLET there, and the property
 * type mapping below mirrors `domains_for_property_type` / `property_domains` there.
 */

// Every area a staff member can be assigned to, in the order screens display them.
export const DOMAINS = ["hotel", "restaurant", "bar"];

// This property's restaurant and bar share one POS, one till and one set of orders, so
// endpoints serving them declare both and holding either grants access. Mirrors
// services/access.py::OUTLET.
export const OUTLET = ["restaurant", "bar"];

// How a domain is written when it is shown to a person.
export const DOMAIN_LABELS = { hotel: "Hotel", restaurant: "Restaurant", bar: "Bar" };

// ------------------------------ what the business is ------------------------------
// A tenant is a hotel, an outlet (a restaurant or a bar, with no rooms), or both. Chosen
// at signup, stored on the property record, and read here from `GET /api/property` —
// never inferred from the signed-in person's own domains, because an outlet property with
// a single-domain manager and a hotel property with the same manager are different things
// and only one of them has a front desk.

export const PROPERTY_HOTEL = "hotel";
export const PROPERTY_OUTLET = "outlet";
export const PROPERTY_BOTH = "both";

/** What a property that has never said is taken to be — see the startup migration. */
export const DEFAULT_PROPERTY_TYPE = PROPERTY_BOTH;

const TYPE_DOMAINS = {
  [PROPERTY_HOTEL]: DOMAINS,
  [PROPERTY_OUTLET]: OUTLET,
  [PROPERTY_BOTH]: DOMAINS,
};

/**
 * How each type is offered on the signup form: what to call it, and what it includes.
 *
 * The blurb is not decoration. "Outlet" is our word, not the trade's, and a restaurant
 * owner picking between three nouns needs to know that the middle one means no rooms
 * screens at all rather than rooms they can ignore — the choice is not reversible from
 * inside the app.
 */
export const PROPERTY_TYPE_CHOICES = [
  {
    key: PROPERTY_HOTEL,
    label: "Hotel or resort",
    blurb: "Rooms, bookings, the front desk and folios — plus a restaurant and bar if you run them.",
  },
  {
    key: PROPERTY_OUTLET,
    label: "Restaurant or bar",
    blurb: "Tables, POS, the kitchen board, menu and stock. No rooms, and no front desk.",
  },
  {
    key: PROPERTY_BOTH,
    label: "Both",
    blurb: "A hotel and its outlets on one console, with one guest list and one store room.",
  },
];

/** The domains a property of this type has. Mirrors `domains_for_property_type`. */
export function domainsForPropertyType(type) {
  return TYPE_DOMAINS[type] || [];
}

/**
 * The domains a `GET /api/property` payload says its property has.
 *
 * `null` — the request is still in flight — is the empty list, not every domain: a Hotel
 * section that appears for a restaurant and then vanishes is the thing this work exists
 * to remove, and half a second of a quiet sidebar is the cheaper mistake. Callers that
 * need to tell "loading" from "nothing" ask whether the property is null themselves.
 *
 * A record with no type reads as `both`, matching the server for the window before the
 * startup migration has stamped it.
 */
export function propertyDomains(property) {
  if (!property) return [];
  return domainsForPropertyType(property.property_type || DEFAULT_PROPERTY_TYPE);
}

/**
 * Every domain this user can act in — the client's reading of the server's rule in
 * `services/access.py::can_access`. An admin is never domain-checked, so they hold all
 * of them whatever their stored list happens to say. Unknown values are dropped rather
 * than passed on, and the result keeps DOMAINS' order rather than the user's.
 *
 * `property` narrows the answer to what the property actually runs, and it narrows the
 * admin too — that is the point. The server checks the property ahead of the admin
 * bypass, so an outlet's owner is refused the hotel endpoints exactly like everybody
 * else there, and a screen that offered them a hotel figure would be offering a 403.
 * Omit it where there is no property in hand and the old, wider answer is wanted.
 */
export function heldDomains(user, property = undefined) {
  if (!user) return [];
  const runs = property === undefined ? DOMAINS : propertyDomains(property);
  if (user.role === "admin") return DOMAINS.filter((d) => runs.includes(d));
  const held = user.domains || [];
  return DOMAINS.filter((d) => held.includes(d) && runs.includes(d));
}
