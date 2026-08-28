/**
 * How an account is identified, on the client side.
 *
 * The backend keeps the rules in `backend/services/identity.py` — that is where a phone
 * number is turned into the one form it is stored and compared in, and it is the only
 * place that decides whether something is a number at all. Nothing here re-implements
 * that: a client-side copy of the canonical form is a second answer to a question that
 * must have exactly one, and the day the two disagree the owner is refused by their own
 * record with no error message that explains it.
 *
 * What lives here is presentation and the one courtesy check the forms make before a
 * round trip. Both are safe to be approximate — being wrong means an extra request or a
 * number displayed with the spaces in the wrong place, not an account nobody can reach.
 */

// A stored number is E.164 with no separators: +919876543210. Shown with the grouping an
// Indian number is read aloud in, because a fourteen-character run of digits is the sort
// of thing an owner checks character by character against a scrap of paper.
export function formatPhone(stored) {
  if (!stored) return "";
  const match = /^\+91(\d{5})(\d{5})$/.exec(stored);
  return match ? `+91 ${match[1]} ${match[2]}` : stored;
}

/**
 * What to show for somebody, whichever identifier they actually have.
 *
 * The email wins when there is one — it is what an owner recognises a colleague by, and
 * the record can hold both. A phone-only account falls through to its number rather than
 * to an empty cell: "Signed in as " followed by nothing is the sort of blank that reads
 * as a broken page rather than as an account without an address.
 */
export function displayIdentifier(user) {
  if (!user) return "";
  return user.email || formatPhone(user.phone) || "";
}

// Whether the two fields, between them, name an account that could ever sign in. The
// server refuses the same thing with a 400 and its message is the one worth reading; this
// only stops the form making a round trip to be told what it already knows.
export function hasAnIdentifier({ email, phone }) {
  return Boolean((email || "").trim() || (phone || "").trim());
}
