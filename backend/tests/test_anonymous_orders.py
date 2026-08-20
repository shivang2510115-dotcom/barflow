"""The QR self-order: the one write in this application that anybody may make.

`POST /orders/table/{table_id}/items` carries no token by design — a guest at the table
scans a printed code and orders, and gating it on a staff screen would close the
product's front door. What it used to also allow, to anyone who had ever seen a table id:

* label their own items `source: "pos"`, so an item injected from a laptop in the car
  park was indistinguishable in the database from one a waiter typed at the till;
* send `quantity: -5` and take money *off* a bill that was about to be settled;
* repeat either without limit, and keep appending to a bill while the guest was on
  Stripe's page paying the total it had at the time.

Direct coroutine calls rather than a server, the same style as test_isolation.py and
test_webhook_security.py: what is under test is the route's own reasoning, and the
transport adds nothing to it.
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
from routers import orders
from security import create_access_token


def run(coro):
    return asyncio.run(coro)


class Client:
    def __init__(self, host):
        self.host = host


class FakeRequest:
    """Everything the route and `get_current_user` read off a request."""

    def __init__(self, ip: str = "203.0.113.7", token: str | None = None):
        self.client = Client(ip)
        self.cookies = {}
        self.headers = Headers({"Authorization": f"Bearer {token}"} if token else {})


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One live hotel, one free table, a cheap dish and an expensive one."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.setattr(security, "unscoped_db", handle)
    orders.ANON_ORDERS_PER_TABLE.reset()
    orders.ANON_ORDERS_PER_ADDRESS.reset()

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))
    run(handle.tables.insert_one(
        {"id": "t1", "label": "T01", "capacity": 4, "zone": "Bar", "status": "free",
         "current_order_id": None, "property_id": "p1"}))
    run(handle.menu.insert_one(
        {"id": "m1", "name": "House Lager", "category": "Draft Beer", "price": 100.0,
         "station": "bar", "available": True, "property_id": "p1"}))
    run(handle.users.insert_one(
        {"id": "u1", "email": "waiter@grand.example.com", "name": "Riley",
         "role": "waiter", "domains": ["bar", "restaurant", "hotel"],
         "permissions": ["outlet.pos"], "active": True, "property_id": "p1",
         "password_hash": "x", "created_at": now}))
    return handle


def guest(ip: str = "203.0.113.7") -> FakeRequest:
    return FakeRequest(ip)


def till(ip: str = "192.0.2.10") -> FakeRequest:
    return FakeRequest(ip, token=create_access_token("u1", "waiter@grand.example.com",
                                                     "waiter"))


def order_of(handle) -> dict:
    return run(handle.orders.find_one({}, {"_id": 0}))


def add(request, items, table_id="t1"):
    return run(orders.add_items(
        table_id, orders.AddItemsIn(items=items), request))


def one(quantity: int = 1):
    return [orders.OrderItemIn(menu_item_id="m1", quantity=quantity)]


def refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


# --------------------------- the label on the item ---------------------------
def test_an_anonymous_order_is_marked_qr_however_the_caller_asks(world):
    """The hole this file was written for.

    `source` used to be a field on the request body, so an anonymous caller could write
    "pos" into it and their items sat on the bill wearing the till's own label. It is
    the server's word now, and the request has no say.
    """
    made = run(orders.add_items(
        "t1", orders.AddItemsIn(items=one(), source="pos"), guest()))
    assert made["source"] == "qr"
    assert order_of(world)["source"] == "qr"


def test_the_till_marks_its_own_orders_pos(world):
    made = add(till(), one())
    assert made["source"] == "pos"
    assert order_of(world)["source"] == "pos"


def test_a_stale_or_forged_token_is_treated_as_a_guest(world):
    """Not refused — this route is open — but not believed either."""
    made = add(FakeRequest("203.0.113.7", token="not-a-real-token"), one())
    assert made["source"] == "qr"


# --------------------------- what a guest may send ---------------------------
def test_a_negative_quantity_is_not_a_discount(world):
    """`quantity: -5` on a ₹100 dish used to take ₹500 off a bill about to be settled."""
    with pytest.raises(ValidationError):
        orders.AddItemsIn(items=[orders.OrderItemIn(menu_item_id="m1", quantity=-5)])
    with pytest.raises(ValidationError):
        orders.AddItemsIn(items=[orders.OrderItemIn(menu_item_id="m1", quantity=0)])


def test_a_single_line_cannot_order_a_thousand_of_anything(world):
    with pytest.raises(ValidationError):
        orders.AddItemsIn(items=[orders.OrderItemIn(menu_item_id="m1", quantity=1000)])


def test_one_request_cannot_carry_an_unbounded_basket(world):
    too_many = one() * (orders.MAX_ITEMS_PER_REQUEST + 1)
    assert refused(lambda: add(guest(), too_many)).status_code == 400


def test_an_anonymous_bill_stops_growing_at_the_cap(world):
    """A guest's own order is bounded, so the worst a stranger with a table id can do to
    a real table is add a fixed amount of nonsense a waiter can void, not an unbounded
    one that has to be reconciled line by line."""
    per_call = orders.MAX_QUANTITY_PER_LINE
    for _ in range(orders.ANON_MAX_UNITS // per_call):
        add(guest(), one(quantity=per_call))
    refusal = refused(lambda: add(guest(), one()))
    assert refusal.status_code == 400
    assert "staff" in refusal.detail.lower()


def test_the_till_is_not_bound_by_the_guest_cap(world):
    """A large party's real bill goes past it, and a waiter is a person the hotel employs."""
    for _ in range(orders.ANON_MAX_UNITS // orders.MAX_QUANTITY_PER_LINE + 2):
        add(till(), one(quantity=orders.MAX_QUANTITY_PER_LINE))
    assert sum(i["quantity"] for i in order_of(world)["items"]) > orders.ANON_MAX_UNITS


# --------------------------- how often ---------------------------
def test_an_anonymous_caller_cannot_hammer_one_table(world):
    for _ in range(orders.ANON_ORDERS_PER_TABLE.limit):
        add(guest(), one())
    assert refused(lambda: add(guest(), one())).status_code == 429


def test_one_address_cannot_walk_the_whole_floor(world):
    """The per-table budget is per table on purpose — a whole restaurant on one guest
    wifi shares an address — so a second limit bounds one address across all of them."""
    now = 1_000_000.0
    for n in range(orders.ANON_ORDERS_PER_ADDRESS.limit):
        assert orders.ANON_ORDERS_PER_ADDRESS.limited("198.51.100.4", now + n) is False
    assert orders.ANON_ORDERS_PER_ADDRESS.limited("198.51.100.4", now) is True


def test_the_till_is_not_rate_limited(world):
    """A busy service is one address and hundreds of rounds."""
    for _ in range(orders.ANON_ORDERS_PER_TABLE.limit * 3):
        add(till(), one())
    assert len(order_of(world)["items"]) == orders.ANON_ORDERS_PER_TABLE.limit * 3


def test_two_tables_have_their_own_allowances(world):
    run(world.tables.insert_one(
        {"id": "t2", "label": "T02", "capacity": 2, "zone": "Bar", "status": "free",
         "current_order_id": None, "property_id": "p1"}))
    for _ in range(orders.ANON_ORDERS_PER_TABLE.limit):
        orders.ANON_ORDERS_PER_TABLE.limited("203.0.113.7|t1")
    assert add(guest(), one(), table_id="t2")["items"]


# --------------------------- a bill being settled ---------------------------
def test_nothing_is_appended_while_the_guest_is_paying(world):
    """A Stripe session is the bill presented and a total locked in. Anything added after
    it is a line the guest never agreed to and the hotel cannot collect."""
    opened = add(guest(), one())
    run(world.payment_transactions.insert_one(
        {"id": "tx1", "session_id": "sess_1", "order_id": opened["id"], "table_id": "t1",
         "amount": opened["total"], "currency": "usd", "payment_status": "initiated",
         "status": "open", "metadata": {},
         "created_at": datetime.now(timezone.utc).isoformat()}))

    refusal = refused(lambda: add(guest(), one()))
    assert refusal.status_code == 409
    assert len(order_of(world)["items"]) == 1


def test_the_till_may_still_correct_a_bill_being_paid_for(world):
    """A waiter is who fixes it when the guest asks for one more before tapping pay."""
    opened = add(till(), one())
    run(world.payment_transactions.insert_one(
        {"id": "tx1", "session_id": "sess_1", "order_id": opened["id"], "table_id": "t1",
         "amount": opened["total"], "currency": "usd", "payment_status": "initiated",
         "status": "open", "metadata": {},
         "created_at": datetime.now(timezone.utc).isoformat()}))
    assert len(add(till(), one())["items"]) == 2


def test_a_paid_session_does_not_block_the_next_guest_at_that_table(world):
    """The order it belonged to is settled and the table is free; the sitting after it
    opens a new bill and must not inherit the last one's lock."""
    first = add(guest(), one())
    run(world.payment_transactions.insert_one(
        {"id": "tx1", "session_id": "sess_1", "order_id": first["id"], "table_id": "t1",
         "amount": first["total"], "currency": "usd", "payment_status": "paid",
         "status": "open", "metadata": {},
         "created_at": datetime.now(timezone.utc).isoformat()}))
    run(world.orders.update_one({"id": first["id"]}, {"$set": {"status": "settled"}}))
    run(world.tables.update_one(
        {"id": "t1"}, {"$set": {"status": "free", "current_order_id": None}}))

    second = add(guest(), one())
    assert second["id"] != first["id"]
