"""Format checks for the identifiers a property is registered under.

Pure functions over plain values — no database, no request — so the rules are testable
on their own and the router is left holding nothing but the HTTP shape.

Format only, never a checksum or a government lookup. Verifying a GSTIN against the
registry needs a paid API and a network call on every save; it would turn a form
submission into something that can time out. The realistic error is a typo copied off a
certificate, and a positional check catches that.
"""
import re

# 15 characters, in fixed positions:
#   27      state code, 2 digits
#   AAPFU   the holder's PAN, 5 letters
#   0939    the PAN's 4 digits
#   F       the PAN's trailing letter
#   1       entity code — how many registrations this PAN holds in the state
#   Z       a literal Z on every GSTIN issued; it carries no information
#   V       a checksum character
# Anchored, so a 15-character run buried in a longer string is not a match.
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

# 14 digits and nothing else — no spaces, no dashes, however it is printed on the licence.
FSSAI_RE = re.compile(r"^[0-9]{14}$")

# Quoted verbatim in the 400 a malformed value gets. The expected shape belongs next to
# the pattern that enforces it, so the two cannot drift into disagreeing with each other.
GSTIN_SHAPE = (
    "15 characters: 2 digits, 5 letters, 4 digits, 1 letter, 1 letter or digit, "
    "the letter Z, then 1 letter or digit (for example 27AAPFU0939F1ZV)"
)
FSSAI_SHAPE = "14 digits (for example 12345678901234)"


def _normalise(value) -> str | None:
    """The comparable form of a typed identifier, or None if there is nothing to check.

    Blank is None here rather than "": the property record is seeded blank and must be
    saveable long before every certificate has been found, so an empty field means "not
    filled in yet", not "filled in wrongly". Anything that is not a string is refused
    rather than coerced — `str(27)` would silently turn a wrong type into a plausible
    value, and every real caller comes through Pydantic with a string in hand.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return ""
    # Surrounding space comes free with a paste out of a PDF and is not the admin's
    # mistake. Space *inside* the value is left alone, so it fails the length check.
    trimmed = value.strip()
    if not trimmed:
        return None
    # Both identifiers are issued in upper case; a lower-case paste is the same number.
    return trimmed.upper()


def validate_gstin(value) -> bool:
    """True when `value` could be a GSTIN — or is blank, which is not yet an error."""
    candidate = _normalise(value)
    if candidate is None:
        return True
    return bool(GSTIN_RE.match(candidate))


def validate_fssai(value) -> bool:
    """True when `value` could be an FSSAI licence number — or is blank."""
    candidate = _normalise(value)
    if candidate is None:
        return True
    return bool(FSSAI_RE.match(candidate))
