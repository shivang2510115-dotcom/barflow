"""Menu items."""
import uuid
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from scoped_db import PropertyScopedDatabase, public_db, tenant_db
from security import require_configuration
from services.access import OUTLET

# The menu itself is configuration — prices on it become money on a bill — so only the
# admin edits it. Reading it is deliberately unauthenticated below: the QR code on the
# table is a guest's menu, not a staff screen.
# Setup-time: typing the menu in is setting the outlet up. Selling from it is not — the
# POS and the KOT board stay locked until the property is approved.
CONFIG = require_configuration(OUTLET, setup_time=True)

router = APIRouter()


class MenuVariant(BaseModel):
    """One portion of a dish, at its own price.

    `label` is the hotel's own word and nothing here knows what it should say. Half/Full
    on a north Indian card, Small/Large on a coffee list, 30ml/60ml over a bar — the pair
    is not hardcoded anywhere, and a menu that has never heard of portions carries none
    of these at all.
    """
    label: str
    price: float = Field(ge=0)

    @field_validator("label")
    @classmethod
    def _named(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a portion needs a name")
        return v


class MenuItemIn(BaseModel):
    name: str
    category: str
    # What one of this dish costs. When `variants` is empty this is the only price there
    # is, exactly as it always was.
    #
    # When variants exist it **mirrors the first of them**, and the two writes below make
    # that true rather than trusting the client to. The alternative — a base price that
    # nothing charges — leaves a real number on the record that every reader not taught
    # about variants goes on printing, which is how a dish shows ₹379 on the POS and
    # ₹689 on the bill. Mirroring cannot produce that: the scalar is always a price some
    # real portion is actually sold at, so an untaught reader is behind, never wrong.
    price: float
    description: str = ""
    image: str = ""
    station: Literal["bar", "kitchen"] = "bar"
    available: bool = True
    # Absent and empty are the same answer, which is why no migration was needed for the
    # menus that predate this field: see `variants_of`.
    variants: List[MenuVariant] = []

    @field_validator("variants")
    @classmethod
    def _distinct(cls, v: List[MenuVariant]) -> List[MenuVariant]:
        """Two portions may not share a name.

        Not tidiness. A waiter cannot tell two "Half" buttons apart, and resolving an
        ordered label would have to pick one of two prices — silently, on money. Compared
        case-blind because "Half" and "half" are the same word to everyone but a
        computer.
        """
        seen = {}
        for variant in v:
            key = variant.label.casefold()
            if key in seen:
                raise ValueError(f"'{seen[key]}' is listed twice — "
                                 f"each portion needs its own name")
            seen[key] = variant.label
        return v


class MenuItem(MenuItemIn):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))


def variants_of(item: dict) -> list[dict]:
    """This dish's portions, as a list, whatever the record looks like.

    The one place the shape is decided, so that "no variants" is a single answer rather
    than three: the field absent (every menu item written before this feature), the field
    null (hand-edited, or a backend that stores an empty list as nothing), and the field
    an empty list all come back the same. That is why there is no startup migration —
    there is nothing an existing record needs to be told.
    """
    return list(item.get("variants") or [])


def _normalised(payload: MenuItemIn) -> dict:
    """The document to store: the payload, with `price` made to mean what it says.

    Both writes go through here, because a rule enforced on create and forgotten on
    update is a rule that holds until the first edit.
    """
    doc = payload.model_dump()
    if doc["variants"]:
        doc["price"] = doc["variants"][0]["price"]
    return doc


# ----------------- Menu -----------------
@router.get("/menu")
async def list_menu(db: PropertyScopedDatabase = Depends(public_db)):
    """The card. Read by the QR page with no account, and by the POS and the Menu screen
    with one — so which hotel's card this is comes from `public_db`: the scanned table
    when the caller passes `table_id`, otherwise the caller's own token. See there for
    the third case, and why it can only ever answer for the founding property."""
    return await db.menu.find({}, {"_id": 0}).sort("category", 1).to_list(1000)


@router.post("/menu")
async def create_menu_item(payload: MenuItemIn, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    m = MenuItem(**_normalised(payload)).model_dump()
    await db.menu.insert_one(m)
    m.pop("_id", None)
    return m


@router.put("/menu/{item_id}")
async def update_menu_item(item_id: str, payload: MenuItemIn, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    await db.menu.update_one({"id": item_id}, {"$set": _normalised(payload)})
    doc = await db.menu.find_one({"id": item_id}, {"_id": 0})
    return doc


@router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str, user: dict = Depends(CONFIG),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    await db.menu.delete_one({"id": item_id})
    return {"ok": True}
