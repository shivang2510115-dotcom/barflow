"""Issuing the operator's GST invoice: the routes, against a real (mock) database.

The pure rules — the financial year, the number, the split, the words — are proved in
test_invoicing.py without any of this. What is proved here is what the routes do with
them, and it is the part where a mistake is permanent:

* an invoice per recorded payment, **once**, whatever the operator's mouse does;
* a same-state hotel gets CGST and SGST, a different-state one gets IGST, and the
  operator can override the rule;
* **numbers are consecutive**, and two issued back to back never share one;
* **nothing edits or deletes an issued invoice.** That is asserted against the running
  application's own route table, not against this file's memory of it.

No server: the endpoints are ordinary coroutines, the same style as test_isolation.py.
"""
import asyncio
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.invoices as invoices
import routers.platform as platform
from mock_db import MockDatabase
from models.property import Property, SubscriptionPayment
from services.access import PLATFORM_ADMIN
from services.invoicing import INTER_STATE, INTRA_STATE

TODAY = "2026-08-27"          # in the 2026-27 financial year
NEXT_FY = "2027-04-02"        # and this one is not


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


_UNSCOPED_HOLDERS = (db_module, platform, invoices, security)

PLATFORM_STATE = "Maharashtra"


@dataclass
class World:
    operator: dict
    same_state: str     # a hotel in the platform's own state
    other_state: str    # and one that is not


@pytest.fixture
def world(tmp_path, monkeypatch):
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)
    # The day, frozen, so the financial year under test is the one written here rather
    # than whichever one the machine is in.
    monkeypatch.setattr(invoices, "today", lambda: TODAY)

    for tag, name, state in (("a", "The Grand", PLATFORM_STATE),
                             ("b", "The Regent", "Karnataka")):
        record = Property(id=f"{tag}-property", name=name, state=state,
                          legal_name=f"{name} Hospitality Pvt Ltd",
                          gstin="27AAPFU0939F1ZV" if tag == "a" else "29AAPFU0939F1ZV",
                          address_line1="12 MG Road", city="Pune",
                          pincode="411001").model_dump()
        record["id"] = f"{tag}-property"
        run(handle.properties.insert_one(record))

    operator = {"id": "op-1", "email": "ops@barflow.io", "role": PLATFORM_ADMIN,
                "active": True}
    run(handle.users.insert_one(operator))
    return World(operator=operator, same_state="a-property",
                 other_state="b-property")


def set_platform(operator, **overrides):
    body = dict(legal_name="BarFlow Technologies Pvt Ltd", gstin="27AAPFU0939F1ZV",
                address_line1="4 Church Street", city="Mumbai", state=PLATFORM_STATE,
                pincode="400001", email="ops@barflow.io", phone="9990000000",
                prices_include_gst=True)
    body.update(overrides)
    return call(invoices.update_settings,
                payload=invoices.PlatformSettingsFields(**body), user=operator)


def record_payment(property_id: str, amount: float = 12000.0,
                   payment_id: str = "pay-1") -> dict:
    line = SubscriptionPayment(
        id=payment_id, property_id=property_id, amount=amount,
        received_on=TODAY, covers_from="2026-08-27", covers_to="2026-09-27",
        method="bank_transfer", reference="NEFT/HDFC/0921331",
        recorded_by="op-1").model_dump()
    line["id"] = payment_id
    run(db_module.unscoped_db.subscription_payments.insert_one(line))
    return line


def issue(operator, property_id="a-property", payment_id="pay-1", override=None):
    return call(invoices.issue_invoice, property_id=property_id,
                payment_id=payment_id,
                payload=invoices.IssueIn(place_of_supply=override), user=operator)


# ------------------------------- the platform itself -------------------------------
def test_the_platforms_own_details_are_settings_not_a_deployment_variable(world):
    out = set_platform(world.operator)
    assert out["legal_name"] == "BarFlow Technologies Pvt Ltd"
    assert out["state"] == PLATFORM_STATE
    assert call(invoices.get_settings, user=world.operator)["gstin"] == "27AAPFU0939F1ZV"


def test_a_deployment_that_has_not_been_set_up_reads_blank_rather_than_404ing(world):
    out = call(invoices.get_settings, user=world.operator)
    assert out["legal_name"] == "" and out["state"] == ""


def test_a_malformed_platform_gstin_is_refused_by_name(world):
    refusal = refused(invoices.update_settings,
                      payload=invoices.PlatformSettingsFields(gstin="27AAPFU0939F1Z"),
                      user=world.operator)
    assert refusal.status_code == 400 and "gstin" in refusal.detail


def test_only_the_operator_reaches_any_of_this(world):
    """A hotel admin is not the platform. `platform_admin` refuses on the role, and it is
    the same gate routers/platform.py already uses rather than a second copy of it."""
    hotel_admin = {"id": "u1", "role": "admin", "property_id": "a-property"}
    for fn, kwargs in (
        (invoices.get_settings, {}),
        (invoices.list_invoices, {"property_id": "a-property"}),
    ):
        with pytest.raises(HTTPException) as exc:
            run(fn(user=run(platform.platform_admin(hotel_admin)), **kwargs))
        assert exc.value.status_code == 403


# --------------------------------- issuing one ---------------------------------
def test_a_same_state_hotel_gets_cgst_and_sgst(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    inv = issue(world.operator)
    assert inv["place_of_supply"] == INTRA_STATE
    assert inv["taxable_value"] == 10169.49
    assert inv["cgst"] == 915.26 and inv["sgst"] == 915.25
    assert inv["igst"] == 0.0
    assert inv["tax_total"] == 1830.51
    assert inv["total"] == 12000.00


def test_a_different_state_hotel_gets_igst(world):
    set_platform(world.operator)
    record_payment(world.other_state, payment_id="pay-b")
    inv = issue(world.operator, property_id=world.other_state, payment_id="pay-b")
    assert inv["place_of_supply"] == INTER_STATE
    assert inv["igst"] == 1830.51
    assert inv["cgst"] == 0.0 and inv["sgst"] == 0.0
    assert inv["total"] == 12000.00


def test_the_two_come_to_the_same_money(world):
    """Same 18% either way. What differs is the head it sits under, and that is the whole
    reason this rule exists rather than one flat line."""
    set_platform(world.operator)
    record_payment(world.same_state)
    record_payment(world.other_state, payment_id="pay-b")
    intra = issue(world.operator)
    inter = issue(world.operator, property_id=world.other_state, payment_id="pay-b")
    assert intra["total"] == inter["total"]
    assert intra["tax_total"] == inter["tax_total"]


def test_the_operator_can_override_the_split(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    inv = issue(world.operator, override=INTER_STATE)
    assert inv["place_of_supply"] == INTER_STATE
    assert inv["igst"] == 1830.51


def test_an_invoice_carries_both_gstins_both_addresses_and_the_period(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    inv = issue(world.operator)
    assert inv["supplier"]["gstin"] == "27AAPFU0939F1ZV"
    assert inv["customer"]["gstin"] == "27AAPFU0939F1ZV"
    assert "Church Street" in inv["supplier"]["address"]
    assert "MG Road" in inv["customer"]["address"]
    assert inv["period_from"] == "2026-08-27" and inv["period_to"] == "2026-09-27"


def test_an_invoice_carries_the_total_in_words(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    assert issue(world.operator)["total_in_words"] == "Rupees Twelve Thousand Only"


def test_the_parties_are_snapshotted_not_re_rendered(world):
    """A hotel that moves office must not silently restate an invoice filed last year."""
    set_platform(world.operator)
    record_payment(world.same_state)
    inv = issue(world.operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": world.same_state}, {"$set": {"address_line1": "Somewhere else"}}))
    again = call(invoices.get_invoice, invoice_id=inv["id"], user=world.operator)
    assert "MG Road" in again["customer"]["address"]


def test_the_platform_can_agree_a_price_before_tax_instead(world):
    """A real arrangement, and one the operator has to choose rather than have assumed:
    the invoice then totals more than the transfer did."""
    set_platform(world.operator, prices_include_gst=False)
    record_payment(world.same_state)
    inv = issue(world.operator)
    assert inv["taxable_value"] == 12000.0
    assert inv["tax_total"] == 2160.0
    assert inv["total"] == 14160.0


# ------------------------------- what is refused -------------------------------
def test_an_invoice_cannot_be_issued_without_the_platforms_own_state(world):
    record_payment(world.same_state)
    refusal = refused(invoices.issue_invoice, property_id=world.same_state,
                      payment_id="pay-1", payload=invoices.IssueIn(),
                      user=world.operator)
    assert refusal.status_code == 400
    assert "platform" in refusal.detail


def test_an_invoice_cannot_be_issued_without_the_hotels_state(world):
    set_platform(world.operator)
    run(db_module.unscoped_db.properties.update_one(
        {"id": world.same_state}, {"$set": {"state": ""}}))
    record_payment(world.same_state)
    refusal = refused(invoices.issue_invoice, property_id=world.same_state,
                      payment_id="pay-1", payload=invoices.IssueIn(),
                      user=world.operator)
    assert refusal.status_code == 400
    # …but naming the split explicitly still works, which is what the override is for.
    assert issue(world.operator, override=INTER_STATE)["place_of_supply"] == INTER_STATE


def test_a_payment_that_is_not_this_propertys_is_not_found(world):
    set_platform(world.operator)
    record_payment(world.other_state, payment_id="pay-b")
    assert refused(invoices.issue_invoice, property_id=world.same_state,
                   payment_id="pay-b", payload=invoices.IssueIn(),
                   user=world.operator).status_code == 404


def test_an_unknown_property_is_not_found(world):
    set_platform(world.operator)
    assert refused(invoices.issue_invoice, property_id="nobody", payment_id="pay-1",
                   payload=invoices.IssueIn(), user=world.operator).status_code == 404


# --------------------------------- the numbering ---------------------------------
def test_two_invoices_in_sequence_get_consecutive_numbers(world):
    set_platform(world.operator)
    record_payment(world.same_state, payment_id="pay-1")
    record_payment(world.same_state, payment_id="pay-2")
    first = issue(world.operator, payment_id="pay-1")
    second = issue(world.operator, payment_id="pay-2")
    assert first["number"] == "BF/2026-27/0001"
    assert second["number"] == "BF/2026-27/0002"


def test_issuing_twice_for_one_payment_returns_the_same_document(world):
    """The operator double-clicks. A second document for money that arrived once is a
    second tax invoice, and neither of them can be deleted afterwards."""
    set_platform(world.operator)
    record_payment(world.same_state)
    first = issue(world.operator)
    again = issue(world.operator)
    assert again["id"] == first["id"] and again["number"] == first["number"]
    rows = run(db_module.unscoped_db.platform_invoices.find({}, {"_id": 0}).to_list(100))
    assert len(rows) == 1


def test_two_invoices_issued_at_the_same_instant_do_not_share_a_number(world):
    """Concurrency, as far as one process can be made to have it: both coroutines are
    started before either finishes, so the read of the highest number and the insert that
    follows it are given every chance to interleave."""
    set_platform(world.operator)
    for n in range(1, 6):
        record_payment(world.same_state, payment_id=f"pay-{n}")

    async def issue_all():
        return await asyncio.gather(*[
            invoices.issue_invoice(
                property_id=world.same_state, payment_id=f"pay-{n}",
                payload=invoices.IssueIn(), user=world.operator)
            for n in range(1, 6)])

    issued = run(issue_all())
    numbers = sorted(i["number"] for i in issued)
    assert numbers == [f"BF/2026-27/{n:04d}" for n in range(1, 6)]
    assert len(set(numbers)) == 5


def test_the_series_restarts_in_the_next_financial_year(world, monkeypatch):
    set_platform(world.operator)
    record_payment(world.same_state, payment_id="pay-1")
    assert issue(world.operator, payment_id="pay-1")["number"] == "BF/2026-27/0001"

    monkeypatch.setattr(invoices, "today", lambda: NEXT_FY)
    record_payment(world.same_state, payment_id="pay-2")
    later = issue(world.operator, payment_id="pay-2")
    assert later["number"] == "BF/2027-28/0001"
    assert later["financial_year"] == "2027-28"


# --------------------------------- immutability ---------------------------------
def test_there_is_no_route_that_edits_or_deletes_an_invoice():
    """Asserted against the application's own route table rather than this file's memory
    of it, so a PUT added tomorrow fails here rather than being found by an auditor."""
    from fastapi.routing import APIRoute
    from server import app

    offending = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "/platform/invoices" not in route.path:
            continue
        for method in route.methods:
            if method in ("PUT", "PATCH", "DELETE"):
                offending.append(f"{method} {route.path}")
    assert not offending, (
        "an issued invoice is a tax document and cannot be rewritten; a correction is a "
        f"credit note. These routes would rewrite one: {sorted(offending)}")


def test_a_correction_is_a_credit_note_that_names_the_original(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    original = issue(world.operator)
    note = call(invoices.issue_credit_note, invoice_id=original["id"],
                payload=invoices.CreditNoteIn(reason="billed the wrong term"),
                user=world.operator)
    assert note["kind"] == "credit_note"
    assert note["number"] == "BF/CN/2026-27/0001"
    assert note["corrects"] == original["number"]
    assert note["total"] == -original["total"]
    assert note["total_in_words"].startswith("Rupees Minus")
    assert note["reason"] == "billed the wrong term"


def test_a_credit_note_does_not_consume_an_invoice_number(world):
    """Sharing one series would leave the invoice run with holes in it where the
    corrections sat, which is exactly the thing an auditor asks about."""
    set_platform(world.operator)
    record_payment(world.same_state, payment_id="pay-1")
    record_payment(world.same_state, payment_id="pay-2")
    first = issue(world.operator, payment_id="pay-1")
    call(invoices.issue_credit_note, invoice_id=first["id"],
         payload=invoices.CreditNoteIn(), user=world.operator)
    second = issue(world.operator, payment_id="pay-2")
    assert second["number"] == "BF/2026-27/0002"


def test_the_original_is_untouched_by_its_credit_note(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    original = issue(world.operator)
    call(invoices.issue_credit_note, invoice_id=original["id"],
         payload=invoices.CreditNoteIn(), user=world.operator)
    again = call(invoices.get_invoice, invoice_id=original["id"], user=world.operator)
    assert again["total"] == original["total"]
    assert again["number"] == original["number"]
    assert again["kind"] == "invoice"


def test_an_invoice_is_credited_once(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    original = issue(world.operator)
    call(invoices.issue_credit_note, invoice_id=original["id"],
         payload=invoices.CreditNoteIn(), user=world.operator)
    refusal = refused(invoices.issue_credit_note, invoice_id=original["id"],
                      payload=invoices.CreditNoteIn(), user=world.operator)
    assert refusal.status_code == 409
    assert "BF/CN/2026-27/0001" in refusal.detail


def test_a_credit_note_cannot_be_credited(world):
    set_platform(world.operator)
    record_payment(world.same_state)
    original = issue(world.operator)
    note = call(invoices.issue_credit_note, invoice_id=original["id"],
                payload=invoices.CreditNoteIn(), user=world.operator)
    assert refused(invoices.issue_credit_note, invoice_id=note["id"],
                   payload=invoices.CreditNoteIn(),
                   user=world.operator).status_code == 404


# ------------------------------------ reading ------------------------------------
def test_the_list_is_newest_first_and_only_this_businesss(world):
    set_platform(world.operator)
    record_payment(world.same_state, payment_id="pay-1")
    record_payment(world.other_state, payment_id="pay-b")
    issue(world.operator, payment_id="pay-1")
    issue(world.operator, property_id=world.other_state, payment_id="pay-b")

    mine = call(invoices.list_invoices, property_id=world.same_state,
                user=world.operator)
    assert len(mine) == 1
    assert mine[0]["number"] == "BF/2026-27/0001"
    assert mine[0]["place_of_supply_label"].startswith("Within state")


def test_a_scoped_handle_cannot_reach_the_platforms_invoices(world):
    """`platform_invoices` and `platform_settings` stand outside tenancy, and asking a
    hotel's handle for one raises rather than handing back an unfiltered collection."""
    from scoped_db import PropertyScopedDatabase, UnscopedCollectionError
    handle = PropertyScopedDatabase("a-property")
    for name in ("platform_invoices", "platform_settings"):
        with pytest.raises(UnscopedCollectionError):
            getattr(handle, name)
