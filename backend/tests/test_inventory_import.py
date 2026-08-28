"""The stock upload: reading a supplier's spreadsheet, and refusing to guess.

The parser is a pure function over bytes — no server, no database, no request — so
everything about *reading* a file is tested by calling it, and every one of these tests
runs in milliseconds without anything being started.
"""
import io

import pytest

from services import inventory_import as imp


# ------------------------------ the world ------------------------------
def existing_stock():
    """What the property already holds. Two items, one of which a file will match."""
    return [
        {"id": "inv-gin", "name": "Gin 750ml", "unit": "bottle", "stock": 8.0,
         "threshold": 4.0, "cost_per_unit": 2400.0, "category": "spirits"},
        {"id": "inv-lager", "name": "House Lager Keg", "unit": "keg", "stock": 4.0,
         "threshold": 2.0, "cost_per_unit": 9800.0, "category": "beer"},
    ]


def plan(text: str, existing=None, filename: str = "stock.csv"):
    return imp.plan_upload(text.encode("utf-8"), filename, existing or [])


def row_at(report: dict, number: int) -> dict:
    for row in report["rows"]:
        if row["row"] == number:
            return row
    raise AssertionError(f"no row {number} in {[r['row'] for r in report['rows']]}")


CLEAN = (
    "name,unit,stock,threshold,cost_per_unit,category\n"
    "Bourbon 750ml,bottle,12,4,3500,spirits\n"
    "Tonic Water,bottle,48,12,60,mixers\n"
)


# ------------------------------ a clean file ------------------------------
def test_a_clean_file_reads_as_two_new_items_and_nothing_else():
    report = plan(CLEAN, existing_stock())
    assert report["file_errors"] == []
    assert [r["row"] for r in report["rows"]] == [2, 3]
    assert [r["kind"] for r in report["rows"]] == ["new", "new"]
    assert [r["action"] for r in report["rows"]] == ["create", "create"]
    assert report["summary"] == {"total": 2, "new": 2, "update": 0, "duplicate": 0,
                                 "blocked": 0}
    first = row_at(report, 2)
    assert (first["name"], first["unit"], first["stock"]) == ("Bourbon 750ml", "bottle", 12.0)
    assert (first["threshold"], first["cost_per_unit"]) == (4.0, 3500.0)
    assert first["category"] == "spirits"
    assert first["existing"] is None and first["errors"] == []


def test_the_template_is_a_file_this_parser_accepts():
    # The format is published, so the format has to be one the reader agrees with. A
    # template that does not round-trip is the bug this feature dies of.
    report = plan(imp.template_csv())
    assert report["file_errors"] == []
    assert len(report["rows"]) == 1
    assert report["rows"][0]["errors"] == []
    assert report["rows"][0]["kind"] == "new"


def test_the_template_names_every_column_the_owner_has_to_fill_in():
    header = imp.template_csv().splitlines()[0].split(",")
    assert header == list(imp.TEMPLATE_COLUMNS)
    for needed in ("name", "unit", "stock", "threshold", "cost_per_unit"):
        assert needed in header


# --------------------------- reading forgivingly ---------------------------
def test_header_case_spacing_and_synonyms_are_all_the_same_header():
    report = plan(
        "  Item Name , UOM ,Current Quantity, Reorder Level ,Unit Cost\n"
        "Bourbon 750ml,bottle,12,4,3500\n")
    assert report["file_errors"] == []
    row = row_at(report, 2)
    assert (row["name"], row["unit"], row["stock"]) == ("Bourbon 750ml", "bottle", 12.0)
    assert (row["threshold"], row["cost_per_unit"]) == (4.0, 3500.0)


def test_blank_rows_are_skipped_without_shifting_anybody_elses_row_number():
    report = plan(
        "name,unit,stock\n"
        "\n"
        "Bourbon 750ml,bottle,12\n"
        ",,\n"
        "Tonic Water,bottle,48\n")
    # Rows 3 and 5 in the owner's spreadsheet, not 2 and 3 — the number has to be the one
    # they can scroll to.
    assert [r["row"] for r in report["rows"]] == [3, 5]


def test_a_rupee_sign_thousands_separators_and_a_trailing_zero_are_all_numbers():
    report = plan(
        "name,unit,stock,cost_per_unit\n"
        "Bourbon 750ml,bottle,12.0,\"₹ 3,500.50\"\n"
        "Tonic Water,bottle,\"1,200\",Rs 60/-\n")
    assert report["file_errors"] == []
    assert row_at(report, 2)["cost_per_unit"] == 3500.50
    assert row_at(report, 2)["stock"] == 12.0
    assert row_at(report, 3)["stock"] == 1200.0
    assert row_at(report, 3)["cost_per_unit"] == 60.0


def test_the_optional_columns_fall_back_to_what_a_hand_typed_item_would_get():
    # Same defaults as InventoryItemIn — a supplier's sheet has no notion of a low-stock
    # threshold, and refusing the file over it would be refusing the ordinary case.
    report = plan("name,unit,stock\nBourbon 750ml,bottle,12\n")
    row = row_at(report, 2)
    assert row["threshold"] == 5.0 and row["cost_per_unit"] == 0.0
    assert row["category"] == "spirits"
    assert row["errors"] == []


def test_a_byte_order_mark_from_excel_does_not_swallow_the_first_column():
    raw = "﻿name,unit,stock\nBourbon 750ml,bottle,12\n".encode("utf-8")
    report = imp.plan_upload(raw, "stock.csv", [])
    assert report["file_errors"] == []
    assert row_at(report, 2)["name"] == "Bourbon 750ml"


# ------------------------- reporting precisely -------------------------
def test_a_missing_required_column_is_named_and_no_row_is_guessed_at():
    report = plan("name,unit,threshold\nBourbon 750ml,bottle,4\n")
    assert report["rows"] == []
    assert len(report["file_errors"]) == 1
    message = report["file_errors"][0]
    assert "stock" in message
    # Named, not "invalid file" — and the owner is told where to get the right shape.
    assert "template" in message.lower()


def test_an_empty_file_says_so_rather_than_reporting_nothing_to_do():
    assert "empty" in plan("")["file_errors"][0].lower()
    assert "no rows" in plan("name,unit,stock\n")["file_errors"][0].lower()


def test_an_unreadable_quantity_names_the_row_and_the_cell():
    report = plan(
        "name,unit,stock\n"
        "Bourbon 750ml,bottle,12\n"
        "Tonic Water,bottle,about a dozen\n")
    good, bad = row_at(report, 2), row_at(report, 3)
    assert good["errors"] == []
    assert len(bad["errors"]) == 1
    error = bad["errors"][0]
    assert error["column"] == "stock"
    assert error["value"] == "about a dozen"
    assert "row 3" in error["message"]
    assert "about a dozen" in error["message"]
    # And the row cannot be applied as it stands.
    assert bad["action"] == "skip"
    assert report["summary"]["blocked"] == 1


def test_a_negative_quantity_is_refused_by_the_cell_that_holds_it():
    report = plan("name,unit,stock\nBourbon 750ml,bottle,-3\n")
    error = row_at(report, 2)["errors"][0]
    assert error["column"] == "stock" and "row 2" in error["message"]


def test_a_row_with_no_name_is_refused_by_name_not_by_the_whole_file():
    report = plan("name,unit,stock\n ,bottle,12\nTonic Water,bottle,48\n")
    assert row_at(report, 2)["errors"][0]["column"] == "name"
    assert row_at(report, 3)["errors"] == []


def test_a_blank_unit_is_refused_because_a_case_is_not_a_bottle():
    report = plan("name,unit,stock\nBourbon 750ml, ,12\n")
    assert row_at(report, 2)["errors"][0]["column"] == "unit"


def test_a_row_carrying_more_than_one_bad_cell_reports_both():
    report = plan("name,unit,stock,cost_per_unit\nBourbon 750ml,bottle,nope,also nope\n")
    assert [e["column"] for e in row_at(report, 2)["errors"]] == ["stock", "cost_per_unit"]


# ------------------------- matching existing stock -------------------------
def test_a_name_matching_existing_stock_in_a_different_case_is_an_update():
    report = plan(
        "name,unit,stock,threshold,cost_per_unit\n"
        "  gin   750ML ,bottle,20,6,2600\n", existing_stock())
    row = row_at(report, 2)
    assert row["kind"] == "update" and row["action"] == "update"
    assert row["item_id"] == "inv-gin"
    # Before and after, both carried, because "update" without the before is a number
    # nobody can check.
    assert row["existing"]["stock"] == 8.0
    assert row["stock"] == 20.0
    assert row["existing"]["name"] == "Gin 750ml"
    assert report["summary"]["update"] == 1 and report["summary"]["new"] == 0


def test_matching_is_on_the_name_alone_and_the_stored_name_is_kept():
    # The supplier's capitalisation does not get to rename the property's own item.
    report = plan("name,unit,stock\nGIN 750ML,bottle,20\n", existing_stock())
    assert row_at(report, 2)["name"] == "Gin 750ml"


def test_a_duplicate_within_the_file_is_called_a_duplicate_and_points_at_the_first():
    report = plan(
        "name,unit,stock\n"
        "Bourbon 750ml,bottle,12\n"
        "Tonic Water,bottle,48\n"
        "bourbon  750ml,bottle,30\n", existing_stock())
    assert row_at(report, 2)["kind"] == "new"
    later = row_at(report, 4)
    assert later["kind"] == "duplicate"
    assert later["duplicate_of"] == 2
    # Blocking, not silently resolved: two rows disagreeing about one item is exactly the
    # number nobody should end up trusting. The admin drops one or edits it.
    assert later["errors"] and "row 2" in later["errors"][0]["message"]
    assert later["action"] == "skip"
    assert report["summary"]["duplicate"] == 1


def test_the_match_rule_is_case_and_whitespace_insensitive_and_nothing_more():
    assert imp.match_key("  Gin   750ML ") == imp.match_key("gin 750ml")
    assert imp.match_key("Gin 750 ml") != imp.match_key("Gin 750ml")


# ------------------------------ the size bound ------------------------------
def test_a_file_over_the_size_bound_is_refused_before_it_is_parsed():
    header = b"name,unit,stock\n"
    body = b"Bourbon 750ml,bottle,12\n"
    raw = header + body * ((imp.MAX_UPLOAD_BYTES // len(body)) + 1)
    assert len(raw) > imp.MAX_UPLOAD_BYTES
    with pytest.raises(imp.ImportRefused) as exc:
        imp.plan_upload(raw, "stock.csv", [])
    assert exc.value.status_code == 413
    assert "1 MB" in exc.value.detail


def test_a_file_at_the_bound_is_still_read():
    raw = b"name,unit,stock\nBourbon 750ml,bottle,12\n"
    raw += b"\n" * (imp.MAX_UPLOAD_BYTES - len(raw))
    assert len(raw) == imp.MAX_UPLOAD_BYTES
    assert imp.plan_upload(raw, "stock.csv", [])["file_errors"] == []


def test_too_many_rows_is_refused_by_count_as_well_as_by_bytes():
    text = "name,unit,stock\n" + "Item,bottle,1\n" * (imp.MAX_ROWS + 1)
    report = plan(text)
    assert report["rows"] == []
    assert str(imp.MAX_ROWS) in report["file_errors"][0]


def test_a_file_type_this_reader_cannot_open_says_what_to_export_instead():
    with pytest.raises(imp.ImportRefused) as exc:
        imp.plan_upload(b"%PDF-1.4", "stock.pdf", [])
    assert exc.value.status_code == 400
    assert ".csv" in exc.value.detail


# --------------------------------- Excel ---------------------------------
def test_an_xlsx_sheet_reads_exactly_as_the_same_csv_would():
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Item Name", "Unit", "Current Quantity", "Reorder Level", "Unit Cost"])
    sheet.append(["Bourbon 750ml", "bottle", 12, 4, 3500])
    # A spreadsheet holds numbers as numbers, so 12 arrives as an int and 12.0 as a float
    # — neither of which is a string the CSV path would ever see.
    sheet.append(["gin 750ml", "bottle", 20.0, 6, 2600])
    buffer = io.BytesIO()
    book.save(buffer)

    report = imp.plan_upload(buffer.getvalue(), "stock.xlsx", existing_stock())
    assert report["file_errors"] == []
    assert [r["row"] for r in report["rows"]] == [2, 3]
    assert row_at(report, 2)["stock"] == 12.0
    assert row_at(report, 3)["kind"] == "update"
    assert row_at(report, 3)["stock"] == 20.0


# ------------------------------ planning a write ------------------------------
def reviewed(**overrides):
    row = {"row": 2, "name": "Bourbon 750ml", "unit": "bottle", "stock": 12.0,
           "threshold": 4.0, "cost_per_unit": 3500.0, "category": "spirits",
           "action": "create", "item_id": None}
    row.update(overrides)
    return row


def test_reviewed_rows_become_the_writes_they_say_they_are():
    ops, refusals = imp.plan_apply(
        [reviewed(),
         reviewed(row=3, name="Gin 750ml", stock=20.0, action="update",
                  item_id="inv-gin"),
         reviewed(row=4, name="Tonic", action="skip")],
        existing_stock())
    assert refusals == []
    assert [(o["row"], o["action"]) for o in ops] == [(2, "create"), (3, "update")]
    assert ops[1]["item_id"] == "inv-gin"
    assert ops[0]["item"]["name"] == "Bourbon 750ml"
    assert ops[0]["item"]["stock"] == 12.0


def test_a_row_whose_numbers_are_still_unreadable_cannot_be_applied():
    ops, refusals = imp.plan_apply([reviewed(stock="about a dozen")], existing_stock())
    assert ops == []
    assert refusals[0]["row"] == 2 and "stock" in refusals[0]["message"]


def test_an_update_naming_an_item_this_property_does_not_have_is_refused():
    # The row could name another hotel's item id, or one deleted while the review screen
    # was open. Either way the write does not happen and the row says why.
    ops, refusals = imp.plan_apply(
        [reviewed(action="update", item_id="inv-somebody-elses")], existing_stock())
    assert ops == []
    assert "no longer" in refusals[0]["message"] or "not" in refusals[0]["message"]


def test_creating_something_the_property_already_holds_is_refused_not_duplicated():
    ops, refusals = imp.plan_apply(
        [reviewed(name="GIN 750ML", action="create")], existing_stock())
    assert ops == []
    assert "already" in refusals[0]["message"]


def test_two_rows_creating_the_same_item_are_refused_on_the_second():
    ops, refusals = imp.plan_apply(
        [reviewed(), reviewed(row=5, name="bourbon 750ml")], existing_stock())
    assert ops == []
    assert refusals[0]["row"] == 5 and "row 2" in refusals[0]["message"]


def test_two_rows_updating_one_item_are_refused_rather_than_last_one_wins():
    ops, refusals = imp.plan_apply(
        [reviewed(row=2, name="Gin 750ml", stock=20.0, action="update", item_id="inv-gin"),
         reviewed(row=3, name="Gin 750ml", stock=30.0, action="update", item_id="inv-gin")],
        existing_stock())
    assert ops == []
    assert refusals[0]["row"] == 3


def test_an_admin_may_correct_a_match_by_hand():
    # The review screen lets a row be re-pointed. A create turned into an update against
    # a real item is honoured, because that correction is the whole point of the screen.
    ops, refusals = imp.plan_apply(
        [reviewed(name="Gin 750 ml", action="update", item_id="inv-gin", stock=20.0)],
        existing_stock())
    assert refusals == []
    assert ops[0]["action"] == "update" and ops[0]["item"]["stock"] == 20.0


def test_applying_nothing_at_all_is_refused_rather_than_reported_as_success():
    ops, refusals = imp.plan_apply([reviewed(action="skip")], existing_stock())
    assert ops == [] and refusals == []
