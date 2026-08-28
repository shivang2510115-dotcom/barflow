"""The two things an account can be signed in under, and the one form each is stored in.

Pure functions over plain values — no database, no request — the same shape as
services/registration.py, so the rules are testable on their own and each router is left
holding nothing but the HTTP status code it turns a refusal into.

**Why a phone number at all.** A waiter or a kitchen hand in India very often has no
email address and always has a phone. Requiring an email meant the owner invented
`waiter1@fake.com`: an address that cannot receive a password reset, and one that
collides with the identical invention at the property down the road.

**The canonical form is E.164 — `+919876543210`.** Everything is stored and compared as
that, and nothing else. The alternative considered was the bare ten digits, which reads
better in a table; it was dropped because it cannot say which country a number is in, so
the day this platform has one tenant outside India the stored values become ambiguous
and there is no migration back — `9876543210` no longer tells you what it once meant.
E.164 is what a phone's own contact list stores, what every SMS gateway wants, and it
survives that day untouched. It is displayed with a space in it and stored without.

**No `phonenumbers` dependency.** The library is right when you accept the world: it
carries per-country metadata that is genuinely too large to hand-write and that changes
under you. This accepts one country's mobile numbers, which is four rules and fits on a
screen — and the cost of the dependency is not the download, it is that the backend
deploys as a Firebase Function where every megabyte is cold-start latency on a hotel's
first request of the morning. If this platform ever takes a tenant outside India, that
is the change that should bring the library in, and `normalise_phone` is the one function
that would have to be replaced.

**Landlines are deliberately refused.** A staff login is a person, and the number that
reaches a person is a mobile. Requiring a mobile also refuses `1234567890`, which is
what somebody types to get past a field they do not want to fill in — exactly the
fake-email problem this change exists to end, in its new spelling.
"""
import re

# What a mobile number is here, once every separator has been dropped: ten digits, the
# first of which is 6, 7, 8 or 9. That leading-digit rule is the Indian numbering plan's,
# and it is the whole reason a made-up `1234567890` is refused.
_MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")

# The country this platform sells in. One constant rather than the literal "91" scattered
# through the branches below, so the day a second country is added the reader can see
# exactly what has to change.
_INDIA = "91"

# Quoted verbatim in the 400 a malformed number gets. The expected shape belongs next to
# the pattern that enforces it, so the two cannot drift into disagreeing with each other.
PHONE_SHAPE = (
    "a 10-digit Indian mobile number starting 6, 7, 8 or 9 — for example 98765 43210, "
    "09876543210 or +91 98765 43210, which are all the same number"
)

# What an account with neither identifier is told. It names the reason rather than the
# field, because the caller did not leave out a required field — they left out both
# halves of an either/or, and the useful thing to say is what the account could not then
# do. Used by POST /api/staff and POST /api/signup, which is why it lives here and not in
# either of them.
NEITHER_IDENTIFIER = (
    "This account needs an email address or a phone number — with neither, there is "
    "nothing for them to type at the sign-in box and they can never get in. Either one "
    "on its own is enough."
)


def normalise_phone(value) -> str | None:
    """The stored and compared form of a typed phone number, or None if it is not one.

    None means "this is not a number I can store", and every caller turns that into its
    own refusal: a 400 naming `PHONE_SHAPE` when somebody typed it into a form, and a
    plain no-match at the login door, which must not tell a caller whether the thing they
    typed was even well-formed.

    The four spellings that all mean the same number:

        9876543210          ten digits, as it is recited
        09876543210         with the domestic trunk prefix
        919876543210        with the country code, however it was pasted
        0091 9876543210     with the international access prefix

    Separators are dropped rather than rejected — spaces, dashes and brackets come free
    with a paste out of a contact card and are not the owner's mistake. A letter is *not*
    a separator: `98765-4321o` becomes nine digits and is refused, rather than silently
    losing a character and storing a number that reaches somebody else.
    """
    if not isinstance(value, str):
        return None
    # Separators only. Anything else that is not a digit — a letter, an @ — is left in
    # place by this and then fails the length check below, which is the point.
    digits = re.sub(r"[\s\-().]", "", value.strip())
    if digits.startswith("+"):
        digits = digits[1:]
    if not digits.isdigit():
        return None

    # Peeled longest prefix first: `0091…` starts with `00` and would also match the bare
    # `0` trunk rule, and only one of the two readings is the number the person meant.
    for prefix in (f"00{_INDIA}", _INDIA, "0"):
        if digits.startswith(prefix) and len(digits) == len(prefix) + 10:
            digits = digits[len(prefix):]
            break

    if not _MOBILE_RE.match(digits):
        return None
    return f"+{_INDIA}{digits}"


def looks_like_email(value) -> bool:
    """Whether what was typed is an attempt at an email address rather than a number.

    The `@` and nothing more. This is not validation — `EmailStr` does that where a form
    is being filled in — it is the fork that decides which of the two lookups the login
    door performs, and for that the only question is which kind of thing the caller
    meant. A mistyped address gets the same refusal a wrong password gets, from the
    email branch, which is where it belongs.
    """
    return isinstance(value, str) and "@" in value


def normalise_email(value) -> str | None:
    """The stored and compared form of an email address, or None if there is none.

    None and never "", because uniqueness is checked against the stored value: two
    phone-only accounts both holding `email: ""` would be a duplicate of an address that
    neither of them has.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed or None


def normalise_identifier(value) -> str:
    """What the sign-in box typed, in the form the account is stored under.

    One function, because two things key on its answer and they must agree: the account
    lookup, and the login rate limiter's per-identifier bucket. If the bucket keyed on the
    raw text, a guesser would earn a fresh allowance for every spelling of one number —
    ten more tries for the dash, ten more for the `+91`.

    Never None. An identifier this cannot read is returned trimmed and lowercased rather
    than discarded: it will match no account and be refused, but it still has to count
    against a stable bucket on the way out.
    """
    if not isinstance(value, str):
        return ""
    if looks_like_email(value):
        return normalise_email(value) or ""
    return normalise_phone(value) or value.strip().lower()
