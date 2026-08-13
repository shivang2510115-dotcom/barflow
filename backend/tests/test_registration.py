"""Pure registration-format tests — no server, no database."""
import pytest

from services.registration import (
    FSSAI_SHAPE, GSTIN_SHAPE, validate_fssai, validate_gstin,
)

# A real-shaped GSTIN: 27 | AAPFU | 0939 | F | 1 | Z | V
GSTIN = "27AAPFU0939F1ZV"
FSSAI = "12345678901234"


# ------------------------------ GSTIN ------------------------------
def test_a_well_formed_gstin_is_accepted():
    assert validate_gstin(GSTIN) is True


def test_a_fourteen_character_gstin_is_rejected():
    # The realistic typo: one character dropped while retyping off a certificate.
    assert validate_gstin(GSTIN[:-1]) is False


def test_a_sixteen_character_gstin_is_rejected():
    assert validate_gstin(GSTIN + "V") is False


def test_each_position_is_checked_not_just_the_length():
    assert validate_gstin("AAAAPFU0939F1ZV") is False   # letters in the state code
    assert validate_gstin("27AAPF00939F1ZV") is False   # a digit inside the PAN letters
    assert validate_gstin("27AAPFU093AF1ZV") is False   # a letter inside the PAN digits
    assert validate_gstin("27AAPFU093911ZV") is False   # a digit where the PAN letter goes


def test_the_z_is_fixed_and_must_be_a_z():
    # Position 14 is a literal Z on every GSTIN issued; anything else is a mistyped one.
    assert validate_gstin("27AAPFU0939F1AV") is False


def test_the_two_alphanumeric_positions_take_either_a_letter_or_a_digit():
    assert validate_gstin("27AAPFU0939FAZV") is True    # entity code as a letter
    assert validate_gstin("27AAPFU0939F1Z9") is True    # checksum as a digit


def test_lowercase_is_accepted_and_surrounding_space_ignored():
    # A GSTIN is printed uppercase, but refusing a lowercase paste as "malformed" tells
    # the admin their number is wrong when it is only their shift key.
    assert validate_gstin(GSTIN.lower()) is True
    assert validate_gstin(f"  {GSTIN.lower()}  ") is True


def test_a_gstin_with_punctuation_is_rejected():
    # Not stripped internally: a value with a space in the middle is not 15 characters.
    assert validate_gstin("27AAPFU 939F1ZV") is False
    assert validate_gstin("27-AAPFU0939F1Z") is False


# ------------------------------ FSSAI ------------------------------
def test_a_fourteen_digit_fssai_is_accepted():
    assert validate_fssai(FSSAI) is True


def test_an_fssai_of_the_wrong_length_is_rejected():
    assert validate_fssai("1234567890123") is False     # 13
    assert validate_fssai("123456789012345") is False   # 15


def test_an_fssai_with_a_letter_is_rejected():
    assert validate_fssai("1234567890123A") is False


def test_an_fssai_with_separators_is_rejected():
    assert validate_fssai("1234-5678-9012-34") is False


def test_fssai_ignores_surrounding_space():
    assert validate_fssai(f" {FSSAI} ") is True


# --------------------------- blank is valid ---------------------------
# The property record is seeded blank and has to be saveable long before the owner has
# dug the FSSAI certificate out of a drawer. A blank field is "not filled in yet", not
# "filled in wrongly", so it must never raise a 400.
@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_blank_is_valid_for_both(blank):
    assert validate_gstin(blank) is True
    assert validate_fssai(blank) is True


def test_a_non_string_is_rejected_rather_than_raising():
    # Whatever arrives off the wire, these answer True or False and never explode.
    assert validate_gstin(27) is False
    assert validate_fssai(12345678901234) is False
    assert validate_gstin(["27AAPFU0939F1ZV"]) is False
    assert validate_fssai({"n": 1}) is False


# ------------------------- the messages -------------------------
def test_the_shapes_are_stated_in_words_for_the_400():
    # The API answers a malformed value by naming the field and quoting these, so a
    # bare "invalid" never reaches the admin. Kept here so the wording has one home.
    assert "15" in GSTIN_SHAPE
    assert "14 digits" in FSSAI_SHAPE
