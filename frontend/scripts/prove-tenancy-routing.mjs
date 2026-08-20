/**
 * Proof, against the live API on :8000, that the three logins land where they should.
 *
 * The UI cannot be clicked from here, so the rules are checked instead — and they can be,
 * because every one of them is a pure function in src/lib/tenancy.js that this file
 * imports directly. Nothing is reimplemented below: `routeDecision` is the whole body of
 * `<Protected>`, `showsPendingBanner` is the whole condition on the banner, and
 * `homePathFor` is what Login navigates to. What varies is only the input, and the input
 * comes from real logins against the real server.
 *
 * Two sessions are simulated per account, because they take different paths to the same
 * user object:
 *
 *   fresh login — AuthContext calls /auth/me and falls back to the login response
 *   page reload — only the token survives, so /auth/me is called with nothing to fall
 *                 back to except `operatorFromToken`
 *
 * The second is the one worth checking. /auth/me answers the operator 403 by design, so
 * without that fallback an operator who refreshes /platform is signed out of it.
 *
 *   node frontend/scripts/prove-tenancy-routing.mjs
 */
import {
  homePathFor,
  operatorFromToken,
  readsOwnProperty,
  routeDecision,
  showsPendingBanner,
} from "../src/lib/tenancy.js";

const API = process.env.API || "http://localhost:8000/api";

const ACCOUNTS = [
  ["the new pending hotel's admin", "priya@hilltopretreat.example.com", "hilltop12345"],
  ["a live hotel's admin", "admin@barflow.io", "admin123"],
  ["the platform operator", "owner@barflow.io", "operator12345"],
];

async function call(path, token) {
  const res = await fetch(`${API}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  return { status: res.status, body: await res.json().catch(() => null) };
}

async function login(email, password) {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`login ${email}: ${res.status}`);
  return res.json();
}

/** AuthContext's boot path, exactly: token in hand, no login response to fall back on. */
async function restoreFromToken(token) {
  const me = await call("/auth/me", token);
  if (me.status === 200) return me.body;
  return operatorFromToken(token) || false;
}

/** The banner's own path: ask only if this user is allowed to, then apply the rule. */
async function bannerFor(user, token) {
  if (!readsOwnProperty(user)) return { shows: showsPendingBanner(user, null), property: "not asked (403 for this user)" };
  const p = await call("/property", token);
  const property = p.status === 200 ? p.body : null;
  return {
    shows: showsPendingBanner(user, property),
    property: property ? `${property.name} · ${property.status}` : `refused (${p.status})`,
  };
}

const yn = (b) => (b ? "yes" : "no");

for (const [who, email, password] of ACCOUNTS) {
  const { token, user: fromLogin } = await login(email, password);
  const me = await call("/auth/me", token);
  const fresh = me.status === 200 ? me.body : fromLogin; // AuthContext.login
  const reloaded = await restoreFromToken(token); // AuthContext boot effect

  const banner = await bannerFor(fresh, token);

  // The three routes that matter. `/app/*` is area "hotel" with no roles; `/platform` is
  // the one route declared area "platform"; /app/admin/staff is a role-gated child, kept
  // in to show the existing behaviour is untouched.
  const app = routeDecision(fresh, { area: "hotel" });
  const platform = routeDecision(fresh, { area: "platform" });
  const staff = routeDecision(fresh, { area: "hotel", roles: ["admin"] });
  const platformAfterReload = routeDecision(reloaded, { area: "platform" });

  console.log(`\n${who}  <${email}>`);
  console.log(`  role from the API .......... ${fresh.role}`);
  console.log(`  /auth/me .................. ${me.status}${me.status !== 200 ? " (identity recovered from the token)" : ""}`);
  console.log(`  lands after login ......... ${homePathFor(fresh)}`);
  console.log(`  pending banner ............ ${yn(banner.shows)}   [${banner.property}]`);
  console.log(`  can reach /platform ....... ${yn(platform === null)}${platform ? `   (redirected to ${platform})` : ""}`);
  console.log(`  can reach /app/* .......... ${yn(app === null)}${app ? `   (redirected to ${app})` : ""}`);
  console.log(`  can reach /app/admin/staff  ${yn(staff === null)}${staff ? `   (redirected to ${staff})` : ""}`);
  console.log(`  after a page reload:`);
  console.log(`    still signed in ......... ${yn(Boolean(reloaded))}`);
  console.log(`    can reach /platform ..... ${yn(platformAfterReload === null)}${platformAfterReload ? `   (redirected to ${platformAfterReload})` : ""}`);
}

// The two states that never come from a login, and are the easiest to get wrong.
console.log("\nno session");
console.log(`  guest at /app ............. ${routeDecision(false, { area: "hotel" })}`);
console.log(`  guest at /platform ........ ${routeDecision(false, { area: "platform" })}`);
console.log(`  still loading at /platform  ${routeDecision(null, { area: "platform" })}`);
console.log(`  banner while loading ...... ${yn(showsPendingBanner(null, null))}`);
console.log();
