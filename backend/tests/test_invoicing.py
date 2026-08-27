"""The platform's own GST invoice, as arithmetic: no database, no server.

Four rules, and every one of them is a statutory answer rather than a preference:

* **place of supply** decides the split. Same state as the platform → CGST 9% + SGST 9%;
  a different state → IGST 18%. The same 18% either way, and a wrong split makes the
  invoice useless to the hotel's accountant, who cannot claim against a head that was
  never charged.
* **the halves add up.** CGST and SGST are not each 9% of the taxable value computed
  independently — that is out by a paisa often enough to matter — they are halves of the
  one tax figure.
* **numbering is sequential per financial year**, and the Indian financial year starts on
  1 April. A gap in an invoice series is a question from an auditor.
* **the total in words**, which Indian invoices conventionally carry, in the Indian
  system: lakhs and crores, not millions.
"""
import pytest

from services.invoicing import (
    CGST_RATE, GST_RATE, IGST_RATE, INTER_STATE, INTRA_STATE, InvoiceError,
    SGST_RATE, amount_in_words, financial_year, invoice_number, next_sequence,
    normalise_state, parse_invoice_number, place_of_supply, tax_split,
)


# ----------------------------- the financial year -----------------------------
@pytest.mark.parametrize("day, fy", [
    ("2026-04-01", "2026-27"),   # the first day of one
    ("2026-08-27", "2026-27"),
    ("2027-03-31", "2026-27"),   # the last day of the same one
    ("2027-04-01", "2027-28"),   # and over the boundary
    ("2026-03-31", "2025-26"),
    ("2026-01-15", "2025-26"),   # January is in the year that began last April
])
def test_the_financial_year_starts_on_the_first_of_april(day, fy):
    assert financial_year(day) == fy


def test_a_day_that_is_not_a_day_is_refused():
    """Guessing here would number an invoice into the wrong year's series, which is the
    one thing about a series that cannot be corrected afterwards."""
    for bad in ("", None, "2026-13-01", "yesterday"):
        with pytest.raises(InvoiceError):
            financial_year(bad)


# -------------------------------- the number ---------------------------------
def test_the_number_reads_the_way_the_design_says():
    assert invoice_number("BF", "2026-27", 1) == "BF/2026-27/0001"
    assert invoice_number("BF", "2026-27", 42) == "BF/2026-27/0042"


def test_a_series_past_four_digits_grows_rather_than_wrapping():
    """Zero-padding is presentation. Wrapping to 0000 would reuse a number."""
    assert invoice_number("BF", "2026-27", 10000) == "BF/2026-27/10000"


def test_a_number_can_be_read_back():
    assert parse_invoice_number("BF/2026-27/0042") == ("BF", "2026-27", 42)
    assert parse_invoice_number("BF/CN/2026-27/0007") == ("BF/CN", "2026-27", 7)


def test_something_that_is_not_one_of_our_numbers_reads_as_nothing():
    for bad in ("", None, "INV-1", "BF/2026-27/", "BF//0001"):
        assert parse_invoice_number(bad) is None


def test_the_first_invoice_of_a_year_is_one():
    assert next_sequence([], "BF", "2026-27") == 1


def test_the_next_invoice_follows_the_highest_issued_not_the_count():
    """The count is the wrong question. If an insert ever failed between two successes
    the count would hand back a number already on a document."""
    issued = ["BF/2026-27/0001", "BF/2026-27/0002", "BF/2026-27/0003"]
    assert next_sequence(issued, "BF", "2026-27") == 4
    assert next_sequence(["BF/2026-27/0009", "BF/2026-27/0002"], "BF", "2026-27") == 10


def test_the_series_restarts_at_one_in_a_new_financial_year():
    issued = ["BF/2026-27/0001", "BF/2026-27/0002"]
    assert next_sequence(issued, "BF", "2027-28") == 1


def test_the_credit_note_series_is_its_own(  ):
    """A credit note does not consume an invoice number. Sharing one series would leave
    the invoice run with holes in it where the corrections sat."""
    issued = ["BF/2026-27/0001", "BF/CN/2026-27/0001", "BF/CN/2026-27/0002"]
    assert next_sequence(issued, "BF", "2026-27") == 2
    assert next_sequence(issued, "BF/CN", "2026-27") == 3


def test_a_number_from_another_prefix_or_year_is_not_counted():
    issued = ["XX/2026-27/0099", "BF/2025-26/0099", "not a number at all"]
    assert next_sequence(issued, "BF", "2026-27") == 1


# ------------------------------ place of supply -------------------------------
def test_the_same_state_is_cgst_and_sgst():
    assert place_of_supply("Maharashtra", "Maharashtra") == INTRA_STATE


def test_a_different_state_is_igst():
    assert place_of_supply("Maharashtra", "Karnataka") == INTER_STATE


@pytest.mark.parametrize("customer", [
    "maharashtra", "  Maharashtra ", "MAHARASHTRA", "Maha rashtra".replace(" ", ""),
])
def test_spelling_and_case_do_not_change_the_split(customer):
    """A hotel typing its own state into a form types it however it likes. Reading
    "maharashtra" as a different state from "Maharashtra" would put IGST on an invoice
    that should have carried CGST and SGST."""
    assert place_of_supply("Maharashtra", customer) == INTRA_STATE


def test_a_renamed_state_is_the_same_state():
    assert place_of_supply("Odisha", "Orissa") == INTRA_STATE
    assert place_of_supply("Puducherry", "Pondicherry") == INTRA_STATE


def test_an_unknown_state_on_either_side_is_refused_not_guessed():
    """Both defaults are wrong in a way that costs somebody. Guessing intra-state puts
    CGST and SGST on an invoice for another state, which the hotel's accountant cannot
    claim; guessing inter-state does the reverse."""
    for supplier, customer in (("", "Karnataka"), ("Maharashtra", ""),
                               ("Maharashtra", None), (None, None)):
        with pytest.raises(InvoiceError):
            place_of_supply(supplier, customer)


def test_the_operator_may_override_the_rule():
    """There are cases the rule does not cover, and the operator is the one who knows."""
    assert place_of_supply("Maharashtra", "Maharashtra",
                           override=INTER_STATE) == INTER_STATE
    assert place_of_supply("Maharashtra", "Karnataka",
                           override=INTRA_STATE) == INTRA_STATE


def test_an_override_that_is_not_one_of_the_two_is_refused():
    with pytest.raises(InvoiceError):
        place_of_supply("Maharashtra", "Karnataka", override="cgst")


def test_an_override_answers_even_when_a_state_is_missing():
    """Which is what it is for: a hotel whose state nobody has filled in yet can still
    be invoiced, deliberately, by somebody saying which it is."""
    assert place_of_supply("Maharashtra", "", override=INTER_STATE) == INTER_STATE


# --------------------------------- the split ----------------------------------
def test_the_rates_are_the_statutory_ones():
    assert GST_RATE == 18.0
    assert CGST_RATE == SGST_RATE == 9.0
    assert IGST_RATE == 18.0


def test_a_same_state_invoice_splits_into_cgst_and_sgst():
    split = tax_split(10000.0, INTRA_STATE)
    assert split["cgst"] == 900.0 and split["sgst"] == 900.0
    assert split["igst"] == 0.0
    assert split["tax_total"] == 1800.0


def test_a_different_state_invoice_is_one_igst_line():
    split = tax_split(10000.0, INTER_STATE)
    assert split["igst"] == 1800.0
    assert split["cgst"] == 0.0 and split["sgst"] == 0.0
    assert split["tax_total"] == 1800.0


def test_both_splits_come_to_the_same_eighteen_percent():
    for taxable in (10169.49, 12000.0, 999.99, 1.0):
        intra = tax_split(taxable, INTRA_STATE)
        inter = tax_split(taxable, INTER_STATE)
        assert intra["tax_total"] == inter["tax_total"], taxable


def test_the_two_halves_add_up_to_the_tax_they_are_halves_of():
    """The paisa that two independent 9% calculations lose. 9% of ₹10,169.49 is
    ₹915.2541 twice, which rounds to ₹1,830.50 — and 18% of it is ₹1,830.51."""
    split = tax_split(10169.49, INTRA_STATE)
    assert split["tax_total"] == 1830.51
    assert round(split["cgst"] + split["sgst"], 2) == 1830.51


def test_an_unknown_place_of_supply_is_refused():
    with pytest.raises(InvoiceError):
        tax_split(100.0, "somewhere")


# ------------------------------- amount in words ------------------------------
@pytest.mark.parametrize("amount, words", [
    (0, "Rupees Zero Only"),
    (1, "Rupees One Only"),
    (12000, "Rupees Twelve Thousand Only"),
    (100, "Rupees One Hundred Only"),
    (105, "Rupees One Hundred and Five Only"),
    (1000, "Rupees One Thousand Only"),
    (100000, "Rupees One Lakh Only"),
    (10000000, "Rupees One Crore Only"),
    (14160, "Rupees Fourteen Thousand One Hundred and Sixty Only"),
    (123456789, "Rupees Twelve Crore Thirty Four Lakh Fifty Six Thousand Seven "
                "Hundred and Eighty Nine Only"),
])
def test_whole_rupees_are_written_in_the_indian_system(amount, words):
    assert amount_in_words(amount) == words


def test_paise_are_named_as_paise():
    assert amount_in_words(12000.50) == "Rupees Twelve Thousand and Fifty Paise Only"
    assert amount_in_words(1830.51) == (
        "Rupees One Thousand Eight Hundred and Thirty and Fifty One Paise Only")


def test_paise_round_rather_than_truncate():
    assert amount_in_words(99.999) == "Rupees One Hundred Only"


def test_a_negative_amount_says_so():
    """A credit note is a negative document, and "Rupees Minus" is the only honest way
    to write one — dropping the sign would read as a second invoice."""
    assert amount_in_words(-500).startswith("Rupees Minus Five Hundred")
