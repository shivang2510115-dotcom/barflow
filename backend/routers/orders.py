"""Orders: cart building, KOT, item status, settlement."""
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import db as _db_module
from scoped_db import PropertyScopedDatabase, db_for_table, optional_user, tenant_db
from security import require_access
from models.folio import FolioEntry
from services.access import OUTLET
from services.folio import direction_for, folio_balance
from services.ratelimit import RateLimiter, client_ip
from services.tax import outlet_gst_settings, outlet_totals

router = APIRouter()

# ----------------- What a guest at the table may do -----------------
# Everything in this block exists because `POST /orders/table/{id}/items` takes no token.
# The numbers are all far above what a real table does in an evening and far below what
# an automated caller does in a second, which is the only property they need: none of
# them is a security boundary on its own, they bound the damage a stranger with a table
# id can do before somebody notices.
#
# Two of them apply to staff as well, because they are not about trust: a request
# carrying two hundred lines, or a line ordering minus five lagers, is a bug or an
# attack whoever sends it. `quantity` had no lower bound at all, so `-5` on a ₹100 dish
# took ₹500 off a bill that was about to be settled.
MAX_ITEMS_PER_REQUEST = 20
MAX_QUANTITY_PER_LINE = 20

# And these apply only to a caller with no token. A large party's real bill goes well
# past them; a waiter is a person the hotel employs and can be asked afterwards what
# they typed.
ANON_MAX_LINES = 40
ANON_MAX_UNITS = 100

# Keyed per address *and table*, not per address alone: a whole restaurant on the venue's
# guest wifi shares one address, so a flat per-IP budget would let the first four tables
# to order silence the rest of the room. A table id is a uuid4 nobody can guess, so the
# key is only shared by people who can actually see the same QR code.
ANON_ORDERS_PER_TABLE = RateLimiter(limit=20, window_seconds=600, name="qr_table")

# And a looser one per address across every table, which is what catches the caller who
# has scraped or been given many table ids. Twenty rounds a minute from one address is
# not a restaurant.
ANON_ORDERS_PER_ADDRESS = RateLimiter(limit=120, window_seconds=600, name="qr_ip")

# What a guest is told when one of these stops them. Deliberately the same tone in each
# case and never an explanation of which limit they hit: the person reading it is
# overwhelmingly a real guest whose phone retried, and the useful next step is the same
# for all of them.
ASK_STAFF = "Please ask a member of staff to add this to your bill."


class OrderItemIn(BaseModel):
    menu_item_id: str
    quantity: int = Field(1, ge=1, le=MAX_QUANTITY_PER_LINE)
    notes: str = ""


class OrderItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    menu_item_id: str
    name: str
    price: float
    quantity: int
    station: str
    notes: str = ""
    status: Literal["pending", "preparing", "ready", "served"] = "pending"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_id: str
    table_label: str
    items: List[OrderItem] = []
    status: Literal["open", "settled", "cancelled", "voided"] = "open"
    # When a waiter last showed this bill to the guest. While it is set, the total the
    # guest is looking at is the total on record, and self-ordering is refused so it
    # stays that way. Cleared whenever staff change the bill, because that is a new
    # total nobody has been shown yet.
    presented_at: Optional[str] = None
    subtotal: float = 0.0
    # The figure the tax was worked out on, and the rate it was worked out at, both
    # written onto the bill rather than looked up from the property when it is read
    # back. A bill settled at 5% has to still say 5% after the hotel moves to 18%: the
    # guest paid what the printed bill said, and a settled bill that re-prices itself
    # from today's settings is a bill that cannot be reconciled against the till.
    taxable_value: float = 0.0
    tax: float = 0.0
    gst_rate: float = 0.0
    gst_inclusive: bool = False
    discount: float = 0.0
    total: float = 0.0
    payment_method: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    source: Literal["pos", "qr"] = "pos"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    settled_at: Optional[str] = None


class AddItemsIn(BaseModel):
    items: List[OrderItemIn]
    # `source` used to be a field here, defaulting to "pos". It is the server's word now
    # and the request has no say — see add_items. Removed rather than ignored so that
    # nothing can read it back by accident; a client that still sends it (the QR page
    # does) has the field dropped, which is what pydantic does with anything it does not
    # declare.


class SettleIn(BaseModel):
    payment_method: Literal["cash", "card", "online", "room"] = "cash"
    discount: float = 0.0
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    # Required when payment_method is "room".
    folio_id: Optional[str] = None


# ----------------- Helpers -----------------
# Opening, adding to and settling a bill is the POS; the kitchen's board is its own
# screen. `GET /orders/{id}` is read by both, so it names both.
#
# The two routes below with no dependency at all are the QR menu: a guest at the table
# scans and orders without an account, and gating them on a staff screen would break the
# product's front door.
#
# They resolve their hotel from the *table* instead of from the caller, through
# `db_for_table`: a table belongs to exactly one property, so the QR link is itself the
# tenant identifier and nothing a guest can type names another hotel. That also closes
# what this comment used to describe as open — a pending or suspended hotel's QR link now
# refuses, because `db_for_table` checks the owning property's status.
POS = require_access(OUTLET, permission="outlet.pos")
POS_SETTLE = require_access(OUTLET, "admin", "manager", "waiter", permission="outlet.pos")
KOT = require_access(OUTLET, permission="outlet.kot")
ORDER_READ = require_access(OUTLET, permission=("outlet.pos", "outlet.kot"))


def compute_totals(order: dict, rate_percent: float, inclusive: bool) -> dict:
    """Price this bill at the rate the hotel has set.

    This used to read `tax = round(subtotal * 0.10, 2)`. Ten percent is not an Indian
    GST rate — restaurant service is 5% without input tax credit — so every bill this
    POS printed carried a tax figure that matched nothing a guest could lawfully be
    charged. The arithmetic itself now lives in `services/tax.py`, where both branches
    are tested without a server; this only decides which order fields it lands in.

    **A settled bill is never recomputed.** It is returned exactly as it stands. The
    guest paid what the printed bill said, and re-pricing it afterwards — because the
    hotel has since moved from 5% to 18%, or turned inclusive pricing on — would change
    a figure that has already been reconciled and put the books out. The refusal is
    here, on the arithmetic itself, rather than only on the three routes that call it,
    so a fourth caller written later inherits it.
    """
    if order.get("status") not in (None, "open"):
        return order
    items_total = sum(i["price"] * i["quantity"] for i in order.get("items", []))
    figures = outlet_totals(items_total, rate_percent, inclusive,
                            discount=order.get("discount", 0) or 0)
    order["subtotal"] = figures["subtotal"]
    # What the tax was actually worked out on. Equal to the subtotal when the rate is
    # exclusive; the price with the tax taken back out of it when it is inclusive, which
    # is the figure that belongs on a GST bill.
    order["taxable_value"] = figures["taxable_value"]
    order["tax"] = figures["tax"]
    order["total"] = figures["total"]
    # Written onto the bill, not just read from the property. The property's rate is what
    # it is *today*; this is what this bill was charged at, and the two stop agreeing the
    # moment the owner opens the settings screen.
    order["gst_rate"] = figures["gst_rate"]
    order["gst_inclusive"] = figures["gst_inclusive"]
    return order


async def compute_totals_for(db, order: dict) -> dict:
    """The same, with the rate read from the property this bill belongs to.

    `properties` stands outside tenancy — it is the record every scope is resolved from
    — so it is reached through `unscoped_db` and filtered by the id the scoped handle is
    already bound to. There is no id here from a request that could name another hotel.

    A property that predates the field, or one somebody hand-edited into nonsense, bills
    at the statutory default rather than raising: see `services.tax.outlet_gst_settings`.
    A till that will not open is worse than one that bills 5%.
    """
    record = await _db_module.unscoped_db.properties.find_one(
        {"id": db.property_id}, {"_id": 0})
    rate, inclusive = outlet_gst_settings(record)
    return compute_totals(order, rate, inclusive)


# ----------------- Orders -----------------
async def _open_order(db, table: dict) -> dict | None:
    """This table's bill, if one is open. Read-only: a refusal must not leave an empty
    order behind and a table marked occupied by somebody who was turned away."""
    if not table.get("current_order_id"):
        return None
    order = await db.orders.find_one({"id": table["current_order_id"]}, {"_id": 0})
    if order and order["status"] == "open":
        return order
    return None


async def _open_new_order(db, table: dict, source: str) -> dict:
    order = Order(table_id=table["id"], table_label=table["label"],
                  source=source).model_dump()
    await db.orders.insert_one(order)
    order.pop("_id", None)
    await db.tables.update_one({"id": table["id"]}, {"$set": {
        "status": "occupied", "current_order_id": order["id"]}})
    return order


async def _bill_locked(order: dict) -> bool:
    """Whether this bill has been shown to the guest and must not change under them.

    Two ways that happens. A waiter presses Present on the settle screen, which stamps
    `presented_at` — that is the cash path, where the guest is holding a printed total.
    Or a Stripe checkout session is open, which is the same situation with the total
    locked in on Stripe's page.

    A session is the bill presented and its total locked in — the guest is on Stripe's
    page looking at a number. Anything appended after that is a line they never agreed
    to and the hotel cannot collect, which is the closest thing this application has to
    "an order already being settled". The cash path has no equivalent signal and is
    still open to the same race; that is stated in the hardening report rather than
    papered over here.

    `payment_transactions` stands outside tenancy — it is keyed by the session id Stripe
    hands an anonymous caller — so it is reached through `unscoped_db` and filtered by
    order id in the open, exactly as routers/payments.py does. A session that has
    already been paid does not lock anything: its order is settled, and the next sitting
    at that table opens a new one.

    An abandoned session leaves the bill locked to self-ordering until staff touch it.
    That is the deliberate direction to be wrong in: the guest is told to ask a waiter,
    and a waiter can always add the drink.
    """
    # The module, not the handle: `from db import unscoped_db` binds whatever existed at
    # import time, and a test that swaps the database would still be answered by the
    # real one. scoped_db.py reaches it the same way and for the same reason.
    if order.get("presented_at"):
        return True
    sessions = await _db_module.unscoped_db.payment_transactions.find(
        {"order_id": order["id"]}, {"_id": 0}).to_list(50)
    return any(s.get("payment_status") not in ("paid", "expired", "failed")
               for s in sessions)


def _within_guest_limits(order: dict | None, items: List[OrderItemIn]) -> bool:
    existing = (order or {}).get("items", [])
    lines = len(existing) + len(items)
    units = (sum(i.get("quantity", 0) for i in existing)
             + sum(i.quantity for i in items))
    return lines <= ANON_MAX_LINES and units <= ANON_MAX_UNITS


@router.post("/orders/table/{table_id}/items")
async def add_items(table_id: str, payload: AddItemsIn, request: Request = None):
    """Add items to this table's bill. Open to a guest with a phone and no account.

    `source` is decided here and never read from the body. A caller holding a live staff
    session is the till; everybody else is the QR code — including a waiter whose token
    has expired, which costs them a label and nothing else. The point is that "this line
    was typed by somebody the hotel employs" stays a fact the server knows rather than a
    string the client chose, so a bill with an injected line can be told from one
    without.
    """
    # Everyone, staff included: a request this size is a bug or an attack whoever sends
    # it, and it is refused before any of the work below.
    if len(payload.items) > MAX_ITEMS_PER_REQUEST:
        raise HTTPException(
            400, f"An order can carry at most {MAX_ITEMS_PER_REQUEST} lines at a time")

    staff = await optional_user(request)

    if staff is None:
        ip = client_ip(request)
        # Both, and in this order: the table budget is the one a real guest could
        # conceivably reach, so it is checked first and its refusal is the one they see.
        if (await ANON_ORDERS_PER_TABLE.limited(f"{ip}|{table_id}")
                or await ANON_ORDERS_PER_ADDRESS.limited(ip)):
            raise HTTPException(429, f"Too many orders from this device. {ASK_STAFF}")

    # The scope comes from the table, and so does the menu: an item id from another
    # hotel's card simply is not found, and is skipped like any unknown id.
    db, table = await db_for_table(table_id, request, caller=staff)
    order = await _open_order(db, table)

    if staff is None:
        # Checked against the bill as it stands, before anything is created: a guest who
        # is refused must not leave an empty order behind and the table marked occupied.
        if order is not None and await _bill_locked(order):
            raise HTTPException(
                409, f"This bill has been totalled and cannot be added to. {ASK_STAFF}")
        if not _within_guest_limits(order, payload.items):
            raise HTTPException(
                400, f"This bill has reached the limit for self-ordering. {ASK_STAFF}")

    if order is None:
        order = await _open_new_order(db, table, "pos" if staff else "qr")

    new_items = []
    for it in payload.items:
        m = await db.menu.find_one({"id": it.menu_item_id}, {"_id": 0})
        if not m:
            continue
        oi = OrderItem(
            menu_item_id=m["id"], name=m["name"], price=m["price"],
            quantity=it.quantity, station=m.get("station", "bar"), notes=it.notes,
        ).model_dump()
        new_items.append(oi)
    order["items"].extend(new_items)
    order = await compute_totals_for(db, order)
    await db.orders.update_one({"id": order["id"]}, {"$set": {
        "items": order["items"], "subtotal": order["subtotal"], "tax": order["tax"],
        "total": order["total"], "taxable_value": order["taxable_value"],
        "gst_rate": order["gst_rate"], "gst_inclusive": order["gst_inclusive"],
        # The bill just changed, so whatever was presented is stale. Staff re-present at
        # the new total; a guest looking at a printed slip is never quietly overcharged.
        "presented_at": None,
    }})
    order["presented_at"] = None
    return order


@router.post("/orders/{order_id}/present")
async def present_bill(order_id: str, user: dict = Depends(POS_SETTLE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    """Mark this bill as shown to the guest, freezing it against self-ordering.

    Called when a waiter opens the settle screen or prints the bill. From here until the
    bill is settled or changed by staff, an anonymous QR caller on that table is refused
    with a message telling them to ask a waiter — the guest is looking at a total, and a
    line arriving after that is one nobody agreed to.

    Idempotent: presenting twice is what happens when a waiter walks back to the table.
    """
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    if order["status"] != "open":
        raise HTTPException(409, f"This order is already {order['status']}")
    stamp = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one({"id": order_id}, {"$set": {"presented_at": stamp}})
    return {**order, "presented_at": stamp}


@router.get("/orders/table/{table_id}/current")
async def current_order(table_id: str, request: Request = None):
    db, table = await db_for_table(table_id, request)
    if not table.get("current_order_id"):
        return None
    return await db.orders.find_one({"id": table["current_order_id"]}, {"_id": 0})


@router.get("/orders/kot")
async def list_kot(user: dict = Depends(KOT),
                   db: PropertyScopedDatabase = Depends(tenant_db)):
    """All pending/preparing items across open orders."""
    open_orders = await db.orders.find({"status": "open"}, {"_id": 0}).to_list(500)
    tickets = []
    for o in open_orders:
        pending_items = [i for i in o["items"] if i["status"] in ("pending", "preparing")]
        if pending_items:
            tickets.append({
                "order_id": o["id"],
                "table_label": o["table_label"],
                "created_at": o["created_at"],
                "items": pending_items,
            })
    return tickets


class UpdateItemStatusIn(BaseModel):
    status: Literal["pending", "preparing", "ready", "served"]


@router.put("/orders/{order_id}/items/{item_id}/status")
async def update_item_status(order_id: str, item_id: str, payload: UpdateItemStatusIn, user: dict = Depends(KOT),
                             db: PropertyScopedDatabase = Depends(tenant_db)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    updated = False
    for it in order["items"]:
        if it["id"] == item_id:
            it["status"] = payload.status
            updated = True
            break
    if updated:
        await db.orders.update_one({"id": order_id}, {"$set": {"items": order["items"]}})
    return order


@router.get("/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(ORDER_READ),
                    db: PropertyScopedDatabase = Depends(tenant_db)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    return order


@router.post("/orders/{order_id}/settle")
async def settle_order(order_id: str, payload: SettleIn, user: dict = Depends(POS_SETTLE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    if order["status"] != "open":
        raise HTTPException(409, f"This order is already {order['status']} and cannot be settled again")
    order["discount"] = payload.discount
    order = await compute_totals_for(db, order)

    # --- validation, before anything is written ---
    folio = None
    if payload.payment_method == "room":
        if not payload.folio_id:
            raise HTTPException(400, "folio_id is required when charging to a room")
        folio = await db.folios.find_one({"id": payload.folio_id}, {"_id": 0})
        if not folio:
            raise HTTPException(404, "Folio not found")
        if folio["status"] != "open":
            raise HTTPException(409, f"That folio is {folio['status']} and cannot be charged")

    order["status"] = "settled"
    order["payment_method"] = payload.payment_method
    order["customer_name"] = (payload.customer_name or "").strip() or None
    order["customer_phone"] = (payload.customer_phone or "").strip() or None
    order["settled_at"] = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one({"id": order_id}, {"$set": order})
    await db.tables.update_one({"id": order["table_id"]}, {"$set": {"status": "free", "current_order_id": None}})

    # --- after the table is freed: post the receivable ---
    if folio is not None:
        entry = FolioEntry(
            folio_id=folio["id"], kind="outlet", direction=direction_for("outlet"),
            amount=round(order["total"], 2),
            description=f"{order['table_label']} · bill {order['id'][:8]}",
            ref_order_id=order["id"], posted_by=user.get("id")).model_dump()
        await db.folio_entries.insert_one(entry)
        entries = await db.folio_entries.find(
            {"folio_id": folio["id"]}, {"_id": 0}).to_list(5000)
        await db.folios.update_one({"id": folio["id"]}, {"$set": {
            "balance": folio_balance(entries)}})

    return order


@router.delete("/orders/{order_id}/items/{item_id}")
async def remove_item(order_id: str, item_id: str, user: dict = Depends(POS_SETTLE),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    # The one route that could re-price a bill after the guest had paid it. Settling
    # refuses a second settlement and presenting refuses a closed bill; this had no
    # check at all, so taking a line off a settled order rewrote its total at whatever
    # rate the property holds today. Refused in the same words, for the same reason.
    if order["status"] != "open":
        raise HTTPException(
            409, f"This order is already {order['status']} and cannot be changed")
    order["items"] = [i for i in order["items"] if i["id"] != item_id]
    order = await compute_totals_for(db, order)
    await db.orders.update_one({"id": order_id}, {"$set": order})
    return order
