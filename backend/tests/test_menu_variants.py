"""Portion variants: one dish, several sizes, one line on the card.

A real Indian menu prices half of what it sells by portion — *Butter Chicken: Half ₹379,
Full ₹689*. With one price per menu item the only way to say that was two separate
entries, which doubles the card, splits one dish's sales figures across two rows in the
report, and makes a waiter hunt for "Butter Chicken (Full)" instead of tapping the dish
and choosing a size.

So a menu item carries an optional list of `{label, price}`. The labels are the hotel's
own words — Half/Full here, Small/Large or 30ml/60ml elsewhere — and an item with none
behaves exactly as it always did, which is most of them.

**What `price` means when variants exist.** It mirrors the first variant, and the server
is what makes that true: every write normalises it. The alternative — "the base price,
unused when variants exist" — leaves a real number on the record that nothing charges,
and every reader that has not been taught about variants (the POS card, the QR card, the
Menu screen, the next one somebody writes) prints it. That is the ₹379-on-one-screen,
₹689-on-another failure, and it cannot be fixed by remembering: it has to be impossible.
Mirroring makes the scalar always a price somebody is actually charged for a real
portion, so an untaught reader is behind rather than wrong.

Direct coroutine calls rather than a server, the same style as test_anonymous_orders.py:
what is under test is the routes' own reasoning about money.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import Headers

import db as db_module
import security
from mock_db import MockDatabase
from routers import menu as menu_router
from routers import orders
from scoped_db import PropertyScopedDatabase
from security import create_access_token


def run(coro):
    return asyncio.run(coro)


class Client:
    def __init__(self, host):
        self.host = host


class FakeRequest:
    def __init__(self, ip: str = "203.0.113.9", token: str | None = None):
        self.client = Client(ip)
        self.cookies = {}
        self.headers = Headers({"Authorization": f"Bearer {token}"} if token else {})


ADMIN = {"id": "u-admin", "role": "admin"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One live hotel, one table, a plain dish and a dish sold by portion."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.setattr(security, "unscoped_db", handle)
    run(orders.ANON_ORDERS_PER_TABLE.reset())
    run(orders.ANON_ORDERS_PER_ADDRESS.reset())

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "Anand Castle", "status": "live", "created_at": now}))
    run(handle.tables.insert_one(
        {"id": "t1", "label": "T01", "capacity": 4, "zone": "Restaurant",
         "status": "free", "current_order_id": None, "property_id": "p1"}))
    # A dish with no portions. Stored without a `variants` key at all, the way every
    # menu item written before this feature is stored — the absent field is the case
    # that has to keep working without a migration.
    run(handle.menu.insert_one(
        {"id": "m-dal", "name": "Dal Tadka", "category": "Mains", "price": 249.0,
         "station": "kitchen", "available": True, "property_id": "p1"}))
    run(handle.users.insert_one(
        {"id": "u1", "email": "waiter@anand.example.com", "name": "Riley",
         "role": "waiter", "domains": ["bar", "restaurant", "hotel"],
         "permissions": ["outlet.pos"], "active": True, "property_id": "p1",
         "password_hash": "x", "created_at": now}))
    return handle


def db_of() -> PropertyScopedDatabase:
    return PropertyScopedDatabase("p1")


def till(ip: str = "192.0.2.11") -> FakeRequest:
    return FakeRequest(ip, token=create_access_token("u1", "waiter@anand.example.com",
                                                     "waiter"))


def guest(ip: str = "203.0.113.9") -> FakeRequest:
    return FakeRequest(ip)


def add(request, items, table_id="t1"):
    return run(orders.add_items(table_id, orders.AddItemsIn(items=items), request))


def line(menu_item_id, quantity=1, variant_label=None):
    return orders.OrderItemIn(menu_item_id=menu_item_id, quantity=quantity,
                              variant_label=variant_label)


def refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


def butter_chicken(world) -> dict:
    """Half ₹379, Full ₹689 — the dish this whole feature exists for."""
    return run(menu_router.create_menu_item(
        menu_router.MenuItemIn(
            name="Butter Chicken", category="Mains", price=0, station="kitchen",
            variants=[menu_router.MenuVariant(label="Half", price=379.0),
                      menu_router.MenuVariant(label="Full", price=689.0)]),
        ADMIN, db_of()))


# ------------------------- the item with no portions -------------------------
def test_an_item_with_no_variants_prices_exactly_as_before(world):
    """The whole feature is additive or it is not shippable: most dishes have no
    portions, and their line has to come out byte for byte what it was."""
    made = add(till(), [line("m-dal", quantity=2)])
    (it,) = made["items"]
    assert it["name"] == "Dal Tadka"
    assert it["price"] == 249.0
    assert it["quantity"] == 2
    assert made["subtotal"] == 498.0


def test_a_line_with_no_variant_records_none_rather_than_an_empty_word(world):
    """`None`, not `""`. The bill and the KOT ticket both test this field to decide
    whether to print a portion at all, and an empty string that renders as a blank
    bracket after the dish name is the bug that reaches the kitchen."""
    made = add(till(), [line("m-dal")])
    assert made["items"][0]["variant_label"] is None


def test_an_item_stored_before_this_feature_reads_as_having_no_variants(world):
    """Dal Tadka was written into the fixture with no `variants` key at all, which is
    how every menu item in every existing database is stored. No migration: absent and
    empty have to be the same answer, and this is the test that says so."""
    stored = run(world.menu.find_one({"id": "m-dal"}, {"_id": 0}))
    assert "variants" not in stored
    assert menu_router.variants_of(stored) == []
    assert add(till(), [line("m-dal")])["items"][0]["price"] == 249.0


def test_naming_a_portion_of_a_dish_that_has_none_is_refused(world):
    """Not ignored. A client that sends "Full" for Dal Tadka believes it is ordering
    something this kitchen does not make, and dropping the word quietly serves the guest
    a portion nobody chose."""
    refusal = refused(lambda: add(till(), [line("m-dal", variant_label="Full")]))
    assert refusal.status_code == 400
    assert "Dal Tadka" in refusal.detail


# --------------------------- the item with portions ---------------------------
def test_the_chosen_portion_is_what_is_charged(world):
    bc = butter_chicken(world)
    made = add(till(), [line(bc["id"], variant_label="Full")])
    (it,) = made["items"]
    assert it["name"] == "Butter Chicken"
    assert it["variant_label"] == "Full"
    assert it["price"] == 689.0
    assert made["subtotal"] == 689.0


def test_the_other_portion_is_a_different_price_on_the_same_dish(world):
    bc = butter_chicken(world)
    made = add(till(), [line(bc["id"], variant_label="Half")])
    assert made["items"][0]["price"] == 379.0
    assert made["subtotal"] == 379.0


def test_ordering_a_portioned_dish_without_naming_a_portion_is_refused(world):
    """The failure this refusal prevents is silent: without it the line would take
    whatever `price` happens to hold and charge ₹379 for a full plate, or ₹689 for a
    half one, with nothing on the bill to show which was served."""
    bc = butter_chicken(world)
    refusal = refused(lambda: add(till(), [line(bc["id"])]))
    assert refusal.status_code == 400
    assert "Butter Chicken" in refusal.detail
    assert "Half" in refusal.detail and "Full" in refusal.detail


def test_a_portion_this_dish_does_not_come_in_is_refused(world):
    bc = butter_chicken(world)
    refusal = refused(lambda: add(till(), [line(bc["id"], variant_label="Quarter")]))
    assert refusal.status_code == 400
    assert "Quarter" in refusal.detail


def test_a_portion_is_matched_however_it_is_capitalised_and_stored_the_hotel_s_way(world):
    """A phone keyboard capitalises; the printed ticket must still say the hotel's own
    word. The label the kitchen reads is the one off the menu, never the one typed."""
    bc = butter_chicken(world)
    made = add(till(), [line(bc["id"], variant_label="  full ")])
    assert made["items"][0]["variant_label"] == "Full"
    assert made["items"][0]["price"] == 689.0


def test_a_refusal_leaves_no_empty_bill_and_no_table_marked_occupied(world):
    """The refusal happens before anything is written. A guest who is turned away must
    not leave a phantom order behind and a table a waiter has to go and free by hand."""
    bc = butter_chicken(world)
    assert refused(lambda: add(guest(), [line(bc["id"])])).status_code == 400
    assert run(world.orders.count_documents({})) == 0
    assert run(world.tables.find_one({"id": "t1"}, {"_id": 0}))["status"] == "free"


def test_one_dish_at_two_portions_bills_two_amounts_on_one_ticket(world):
    """The end-to-end shape of the whole feature: one dish, two sizes, one bill, and a
    subtotal that is the sum of what was actually served."""
    bc = butter_chicken(world)
    made = add(till(), [line(bc["id"], variant_label="Half"),
                        line(bc["id"], variant_label="Full")])
    half, full = made["items"]
    assert (half["variant_label"], half["price"]) == ("Half", 379.0)
    assert (full["variant_label"], full["price"]) == ("Full", 689.0)
    assert made["subtotal"] == 1068.0
    # Two lines, not one merged line: they are different money and the kitchen cooks
    # them differently.
    assert len({half["id"], full["id"]}) == 2


def test_both_portions_reach_the_kitchen_named(world):
    """"Butter Chicken" alone tells the kitchen nothing. The ticket has to say which."""
    bc = butter_chicken(world)
    add(till(), [line(bc["id"], variant_label="Half"),
                 line(bc["id"], variant_label="Full")])
    (ticket,) = run(orders.list_kot(ADMIN, db_of()))
    assert [(i["name"], i["variant_label"]) for i in ticket["items"]] == [
        ("Butter Chicken", "Half"), ("Butter Chicken", "Full")]


def test_a_guest_ordering_from_their_phone_gets_the_same_answer(world):
    """The QR page carries no token, so its lines go through exactly the same resolution
    — the price a guest is shown is the price the server charges, or neither is true."""
    bc = butter_chicken(world)
    made = add(guest(), [line(bc["id"], variant_label="Half")])
    assert made["source"] == "qr"
    assert made["items"][0]["price"] == 379.0


# --------------------- what `price` means when variants exist ---------------------
def test_price_mirrors_the_first_variant_on_create(world):
    """Written by the server, whatever the client sent. `price: 0` above becomes ₹379."""
    assert butter_chicken(world)["price"] == 379.0


def test_price_mirrors_the_first_variant_on_update_too(world):
    """The half of the rule that decides whether it holds. A record normalised once and
    then edited is a record that drifts."""
    bc = butter_chicken(world)
    edited = run(menu_router.update_menu_item(
        bc["id"],
        menu_router.MenuItemIn(
            name="Butter Chicken", category="Mains", price=9999.0, station="kitchen",
            variants=[menu_router.MenuVariant(label="Half", price=399.0),
                      menu_router.MenuVariant(label="Full", price=719.0)]),
        ADMIN, db_of()))
    assert edited["price"] == 399.0


def test_removing_every_variant_leaves_the_dish_at_the_price_that_was_asked_for(world):
    """A dish that stops being sold by portion is a plain dish again, and the admin's
    typed price is the one that stands — there is no first variant left to mirror."""
    bc = butter_chicken(world)
    plain = run(menu_router.update_menu_item(
        bc["id"],
        menu_router.MenuItemIn(name="Butter Chicken", category="Mains", price=629.0,
                               station="kitchen", variants=[]),
        ADMIN, db_of()))
    assert plain["price"] == 629.0
    assert plain["variants"] == []
    assert add(till(), [line(bc["id"])])["items"][0]["price"] == 629.0


# ------------------------------ what a variant is ------------------------------
def test_a_portion_needs_a_name(world):
    with pytest.raises(ValidationError):
        menu_router.MenuVariant(label="   ", price=100.0)


def test_a_portion_cannot_cost_a_negative_amount(world):
    with pytest.raises(ValidationError):
        menu_router.MenuVariant(label="Half", price=-1.0)


def test_two_portions_cannot_share_a_name(world):
    """Not a tidiness rule. A waiter cannot tell two "Half" buttons apart, and the line
    that resolves a label would have to pick one of two prices."""
    with pytest.raises(ValidationError) as exc:
        menu_router.MenuItemIn(
            name="Paneer Tikka", category="Starters", price=0, station="kitchen",
            variants=[menu_router.MenuVariant(label="Half", price=200.0),
                      menu_router.MenuVariant(label="half", price=300.0)])
    assert "Half" in str(exc.value)


# ------------------------- a bill that has been settled -------------------------
def test_a_settled_line_keeps_its_price_when_the_menu_moves(world):
    """The constraint the whole feature is subordinate to. The guest paid what the
    printed bill said; a menu edit afterwards is tomorrow's price, not theirs."""
    bc = butter_chicken(world)
    add(till(), [line(bc["id"], variant_label="Half"),
                 line(bc["id"], variant_label="Full")])
    order_id = run(world.orders.find_one({}, {"_id": 0}))["id"]
    settled = run(orders.settle_order(order_id, orders.SettleIn(payment_method="cash"),
                                      {"id": "u1", "role": "waiter"}, db_of()))
    assert settled["subtotal"] == 1068.0

    run(menu_router.update_menu_item(
        bc["id"],
        menu_router.MenuItemIn(
            name="Butter Chicken", category="Mains", price=0, station="kitchen",
            variants=[menu_router.MenuVariant(label="Half", price=499.0),
                      menu_router.MenuVariant(label="Full", price=899.0)]),
        ADMIN, db_of()))

    after = run(orders.get_order(order_id, {"id": "u1", "role": "waiter"}, db_of()))
    assert [(i["variant_label"], i["price"]) for i in after["items"]] == [
        ("Half", 379.0), ("Full", 689.0)]
    assert after["subtotal"] == 1068.0
    assert after["total"] == round(1068.0 * 1.05, 2)


def test_renaming_a_portion_does_not_rename_it_on_a_settled_bill(world):
    """The label is stored on the line for the same reason the price is: it is what the
    guest was served, not what the card says today."""
    bc = butter_chicken(world)
    add(till(), [line(bc["id"], variant_label="Half")])
    order_id = run(world.orders.find_one({}, {"_id": 0}))["id"]
    run(orders.settle_order(order_id, orders.SettleIn(payment_method="cash"),
                            {"id": "u1", "role": "waiter"}, db_of()))
    run(menu_router.update_menu_item(
        bc["id"],
        menu_router.MenuItemIn(
            name="Butter Chicken", category="Mains", price=0, station="kitchen",
            variants=[menu_router.MenuVariant(label="Chhoti", price=379.0),
                      menu_router.MenuVariant(label="Poori", price=689.0)]),
        ADMIN, db_of()))
    after = run(orders.get_order(order_id, {"id": "u1", "role": "waiter"}, db_of()))
    assert after["items"][0]["variant_label"] == "Half"
