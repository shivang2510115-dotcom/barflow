"""Pure identifier-format tests — no server, no database.

The rule this file exists for: `+91 98765 43210`, `09876543210` and `9876543210` are one
person. An owner who types their waiter's number one way on the staff screen and another
way at the sign-in box must reach the same account, so there has to be exactly one stored
form and one function that produces it.
"""
import pytest

from services.identity import (
    NEITHER_IDENTIFIER, PHONE_SHAPE, looks_like_email, normalise_email,
    normalise_identifier, normalise_phone,
)

CANONICAL = "+919876543210"


# --------------------------- one number, many spellings ---------------------------
@pytest.mark.parametrize("typed", [
    "9876543210",           # what a waiter recites
    "09876543210",          # the trunk prefix, as it is dialled
    "+919876543210",        # E.164, as a phone stores it
    "+91 98765 43210",      # pasted out of a contact card
    "+91-98765-43210",
    "0091 98765 43210",     # the international prefix as an operator dials it
    "91 9876543210",
    "  9876543210  ",
    "(98765) 43210",
])
def test_every_way_of_writing_one_number_gives_one_stored_form(typed):
    assert normalise_phone(typed) == CANONICAL


def test_the_canonical_form_is_stable_under_a_second_pass():
    """Normalising an already-normalised value must not change it — the write path and
    the lookup path both call this, and a function that only settles after two passes
    would store one thing and search for another."""
    assert normalise_phone(normalise_phone("098765 43210")) == CANONICAL


# ------------------------------- what is refused -------------------------------
@pytest.mark.parametrize("bad", [
    "",
    "   ",
    None,
    "98765",                 # half a number
    "98765432100",           # eleven digits that do not start with a trunk 0
    "1234567890",            # ten digits, but no Indian mobile starts with 1
    "5876543210",            # nor with 5
    "+14155550123",          # a US number: this platform is India-only, see the module
    "not a phone",
    "98765-4321o",           # a letter where a digit belongs
])
def test_anything_that_is_not_an_indian_mobile_is_refused(bad):
    assert normalise_phone(bad) is None


def test_the_shape_message_says_what_to_type():
    assert "10" in PHONE_SHAPE or "ten" in PHONE_SHAPE.lower()


# ---------------------------------- email ----------------------------------
def test_an_email_is_recognised_and_lowercased():
    assert looks_like_email("Waiter@Bar.example") is True
    assert normalise_email("  Waiter@Bar.example  ") == "waiter@bar.example"


def test_a_phone_number_is_not_an_email():
    assert looks_like_email("9876543210") is False
    assert looks_like_email("+91 98765 43210") is False


def test_a_blank_email_is_nothing_rather_than_an_empty_string():
    """Stored as None, never "": two accounts with `email: ""` would collide with each
    other under the uniqueness check for a value neither of them has."""
    assert normalise_email("") is None
    assert normalise_email("   ") is None
    assert normalise_email(None) is None


# ------------------------------ the login lookup ------------------------------
def test_the_identifier_typed_at_the_door_resolves_the_same_way():
    """One function decides what was typed, and both the account lookup and the
    rate limiter's per-identifier bucket key on its answer."""
    assert normalise_identifier("ADMIN@Barflow.io") == "admin@barflow.io"
    assert normalise_identifier("09876543210") == CANONICAL
    assert normalise_identifier("+91 98765 43210") == CANONICAL


def test_an_unreadable_identifier_still_gets_a_stable_bucket_key():
    """It matches no account and will be refused — but it must key on *something*
    consistent, or a guesser gets a fresh rate-limit allowance per spelling."""
    assert normalise_identifier(" Nonsense ") == "nonsense"
    assert normalise_identifier("nonsense") == "nonsense"


def test_the_refusal_for_an_account_with_neither_says_why():
    assert "email" in NEITHER_IDENTIFIER.lower()
    assert "phone" in NEITHER_IDENTIFIER.lower()
