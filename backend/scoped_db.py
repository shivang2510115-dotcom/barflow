"""The property-scoped database handle: the tenancy a router cannot forget.

There are ~244 database call sites in `routers/`. Adding `{"property_id": pid}` to each
of them works exactly until somebody forgets once, and that failure is silent — nothing
errors, a list simply contains another hotel's rows, and it is found by a customer rather
than by a test. So the scope is **not passed, it is bound**: a router asks for its handle
from a dependency that has already resolved the caller's property, every read it issues is
filtered to that property and every insert is stamped with it, by construction.

Reading a router, `db` is now always the scoped handle — it is a parameter of the
endpoint, not a module import — and `unscoped_db` is the only way to reach the whole
database. That name is the point: it is greppable, it reads wrong at a call site that
should not have it, and there are few enough uses to check each one in review.

Three collections stand outside tenancy, named here rather than left as exceptions to
notice later:

* `users` — a login must be findable by email before we know which hotel it belongs to;
* `properties` — the tenant record itself, the thing every scope is resolved from;
* `subscription_payments` — what each business pays the platform, and `rate_limit_hits`,
  which counts against an address rather than a tenant;
* `payment_transactions` — the Stripe session ledger, keyed by a session id the payment
  provider hands back to an anonymous caller.

Asking the scoped handle for one of them raises rather than returning an unfiltered
collection, so a router cannot believe it is scoped when it is not. Everything else is
scoped, including a collection nobody has invented yet: an unknown name is scoped rather
than passed through, because the safe default for tenancy is to fail closed.
"""
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request

import db as _db_module
from security import get_current_user
from services.access import LIVE
from services.crypto import decrypt_secret, encrypt_secret

# Fields that are stored encrypted, by collection. Bound to the handle for the same
# reason the tenant filter is: there are eight places that read a guest record and one
# of them forgetting to decrypt puts `enc:v1:gAAAA…` in front of a receptionist, while
# one place forgetting to encrypt writes an identity document to disk in the clear and
# nothing at all goes wrong until the day the database leaks. Neither is a mistake worth
# leaving available.
#
# Only fields nothing queries or sorts by belong here — ciphertext is opaque to the
# database, so a filter on one of these would match nothing. That is enforced below
# rather than trusted.
ENCRYPTED_FIELDS: dict[str, tuple[str, ...]] = {
    "guests": ("id_proof_number",),
}

# The field every scoped document carries. One constant, so the migration, the indexes
# and this module cannot disagree about the name.
PROPERTY_FIELD = "property_id"

# Outside tenancy, deliberately. See the module docstring for why each one is here.
UNSCOPED_COLLECTIONS = frozenset({
    "users", "properties", "payment_transactions",
    # The platform's own money: what each business pays us. Nothing a tenant should be
    # able to reach through their own handle, by accident or otherwise.
    "subscription_payments",
    # And the tax documents issued against it, plus the platform's own registration that
    # they are issued under. Same reasoning: an invoice from the platform to a hotel is
    # the platform's record of its own supply, the operator who writes it belongs to no
    # property, and there is exactly one settings row for a company that is not a tenant.
    "platform_invoices", "platform_settings",
    # Rate-limit counters, keyed by an address that belongs to no hotel — and the whole
    # point is that a caller cannot earn a fresh allowance by arriving at a different one.
    "rate_limit_hits",
})

# Everything a hotel owns. Listed rather than derived, because the startup migration has
# to know which collections to stamp, and "every collection that is not in the unscoped
# set" would sweep in whatever a future feature happens to create.
SCOPED_COLLECTIONS = (
    "bookings", "rooms", "room_types", "rates", "rate_periods", "meal_plans",
    "tax_slabs", "folios", "folio_entries", "orders", "tables", "reservations",
    "menu", "inventory", "guests",
)


class UnscopedCollectionError(RuntimeError):
    """Raised when a scoped handle is asked for a collection that stands outside tenancy.

    A programming error, not a request error: the router wanted `users`, `properties` or
    `payment_transactions`, all of which have to be reached through `unscoped_db` and
    filtered — or deliberately not filtered — in the open.
    """


class DecryptingCursor:
    """The driver's own cursor, with the encrypted fields decoded on the way out.

    Only ever wraps a cursor over a collection that has encrypted fields — one, today —
    so no other query in the application changes shape. `sort` and `limit` are forwarded
    and return this wrapper, which is what the two drivers' cursors do, and `__aiter__`
    is forwarded for a caller that streams instead of collecting.
    """

    def __init__(self, cursor: Any, decrypt):
        self._cursor = cursor
        self._decrypt = decrypt

    def sort(self, *args, **kwargs):
        self._cursor = self._cursor.sort(*args, **kwargs)
        return self

    def limit(self, count: int):
        self._cursor = self._cursor.limit(count)
        return self

    def skip(self, count: int):
        self._cursor = self._cursor.skip(count)
        return self

    async def to_list(self, length=None):
        return [self._decrypt(doc) for doc in await self._cursor.to_list(length)]

    async def __aiter__(self):
        async for doc in self._cursor:
            yield self._decrypt(doc)


class ScopedCollection:
    """One collection, bound to one property.

    Reads are filtered and inserts are stamped. The property id is forced rather than
    defaulted: a caller that passes its own `property_id` — from a request body, or a
    document read elsewhere — cannot widen or redirect the scope by doing so.
    """

    def __init__(self, collection: Any, property_id: str, encrypted: tuple[str, ...] = ()):
        self._collection = collection
        self._property_id = property_id
        self._encrypted = encrypted

    # ---------------------------- encryption -----------------------------
    # See ENCRYPTED_FIELDS at the top of this module for why this lives on the handle
    # rather than in each router. All three methods are no-ops for a collection with no
    # encrypted fields, which is every collection but one.
    def _guard(self, keys) -> None:
        """Refuse to look a document up by a field that is stored encrypted.

        Ciphertext is opaque to the database and carries a fresh IV each time, so such a
        filter matches nothing — silently. A raise is the only honest answer: whoever
        wrote it wanted a search that this storage cannot do, and needs to know now
        rather than through a bug report saying the guest is not in the system.
        """
        for field in self._encrypted:
            if field in keys:
                raise UnscopedCollectionError(
                    f"'{field}' is stored encrypted, so it cannot be queried or matched "
                    f"on — ciphertext differs every time the same value is written")

    def _encrypt(self, doc: dict) -> dict:
        for field in self._encrypted:
            if field in doc:
                doc[field] = encrypt_secret(doc[field])
        return doc

    def _decrypt(self, doc):
        if not doc or not self._encrypted:
            return doc
        for field in self._encrypted:
            if field in doc:
                doc[field] = decrypt_secret(doc[field])
        return doc

    # ----------------------------- filtering -----------------------------
    def _query(self, filter_query: Optional[dict] = None) -> dict:
        """This filter, restricted to the bound property.

        A top-level `$or` is left alone and the property is added beside it, which is an
        AND in both MongoDB and mock_db — so `{"$or": [...]}` narrows within the hotel
        rather than escaping it.
        """
        merged = dict(filter_query or {})
        self._guard(merged)
        merged[PROPERTY_FIELD] = self._property_id
        return merged

    def _stamp(self, doc: dict) -> dict:
        """Mark a document as this property's. Mutates, so that the dict a router
        inserts and the dict it returns to the client say the same thing."""
        doc[PROPERTY_FIELD] = self._property_id
        return doc

    # ------------------------------- reads -------------------------------
    def find(self, filter_query: Optional[dict] = None, projection: Optional[dict] = None):
        # Returns the driver's own cursor, so .sort(), .limit() and .to_list() behave
        # exactly as they do today; only the filter has been narrowed. A collection with
        # encrypted fields gets the wrapper below instead, which is the same cursor with
        # one step added on the way out.
        cursor = self._collection.find(self._query(filter_query), projection)
        if not self._encrypted:
            return cursor
        return DecryptingCursor(cursor, self._decrypt)

    async def find_one(self, filter_query: Optional[dict] = None,
                       projection: Optional[dict] = None):
        return self._decrypt(
            await self._collection.find_one(self._query(filter_query), projection))

    async def count_documents(self, filter_query: Optional[dict] = None) -> int:
        return await self._collection.count_documents(self._query(filter_query))

    def aggregate(self, pipeline: list, *args, **kwargs):
        """A pipeline that can only ever see this property's documents.

        The `$match` is prepended, never appended: a later stage can group, unwind or
        project, and a filter placed after one of those has already read the other
        hotel's rows.
        """
        return self._collection.aggregate(
            [{"$match": {PROPERTY_FIELD: self._property_id}}, *pipeline], *args, **kwargs)

    # ------------------------------ writes -------------------------------
    # `_encrypt` mutates the dict, like `_stamp` and for the same reason: the document a
    # router inserts and the one it returns to the client must not disagree about what is
    # in the database. The router hands back its own dict, so a guest created at the desk
    # shows the number the desk typed — it is re-read through `_decrypt` on the next read
    # either way, and the two paths agree.
    async def insert_one(self, doc: dict):
        result = await self._collection.insert_one(self._encrypt(self._stamp(doc)))
        self._decrypt(doc)
        return result

    async def insert_many(self, docs: list):
        result = await self._collection.insert_many(
            [self._encrypt(self._stamp(d)) for d in docs])
        for d in docs:
            self._decrypt(d)
        return result

    async def update_one(self, filter_query: dict, update_query: dict, **kwargs):
        return await self._collection.update_one(
            self._query(filter_query), self._encrypt_update(update_query), **kwargs)

    async def update_many(self, filter_query: dict, update_query: dict, **kwargs):
        return await self._collection.update_many(
            self._query(filter_query), self._encrypt_update(update_query), **kwargs)

    def _encrypt_update(self, update_query: dict) -> dict:
        """An update, with any encrypted field inside its `$set` encrypted.

        `$set` only. An encrypted field arriving under any other operator — `$push`,
        `$inc`, `$unset` with a value, a replacement document — is refused rather than
        guessed at, because each of those would need its own rule and the wrong guess
        writes an identity document to disk in the clear.
        """
        if not self._encrypted:
            return update_query
        patched = dict(update_query)
        for operator, fields in update_query.items():
            if not isinstance(fields, dict):
                continue
            if operator == "$set":
                patched["$set"] = self._encrypt(dict(fields))
            else:
                self._guard(fields)
        return patched

    async def delete_one(self, filter_query: dict):
        return await self._collection.delete_one(self._query(filter_query))

    async def delete_many(self, filter_query: Optional[dict] = None):
        return await self._collection.delete_many(self._query(filter_query))


class PropertyScopedDatabase:
    """A database-shaped object bound to one hotel.

    Used exactly as `db` was: `db.bookings.find_one({"id": booking_id})`. The difference
    is that the filter now also says which hotel, and there is no way to write the call
    that omits it.
    """

    def __init__(self, property_id: str):
        if not property_id:
            # Never reachable through the dependencies below, which refuse first — but an
            # unbound "scoped" handle would be an unfiltered one, which is the whole
            # failure this module exists to make impossible.
            raise ValueError("a scoped database handle needs a property id")
        self.property_id = property_id

    def _collection(self, name: str) -> ScopedCollection:
        if name in UNSCOPED_COLLECTIONS:
            raise UnscopedCollectionError(
                f"'{name}' stands outside tenancy — reach it through unscoped_db and "
                f"say in the open how it is filtered")
        # Resolved at access time rather than at construction, so a test that swaps the
        # underlying database swaps it for every router at once.
        return ScopedCollection(_db_module.unscoped_db[name], self.property_id,
                                ENCRYPTED_FIELDS.get(name, ()))

    def __getattr__(self, name: str) -> ScopedCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collection(name)

    def __getitem__(self, name: str) -> ScopedCollection:
        return self._collection(name)


# A caller that has not been looked at yet, told apart from one that was looked at and
# turned out to be nobody. The two must not collapse: "anonymous" is a decision this
# module has already taken, and re-taking it costs a token decode and a user read.
_UNRESOLVED = object()


async def optional_user(request: Request | None) -> dict | None:
    """Who is calling, when the route does not require anyone to be.

    `None` means a guest — no token, an expired or forged one, or a deactivated account.
    All four are the same answer here on purpose: these routes are open, so the question
    is never "may you in", only "are you staff". Anything short of a live staff session
    is treated as the person with the phone, which is the safe way round — the worst it
    costs a real waiter with a stale token is that their order is labelled `qr`.
    """
    if request is None:
        return None
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


# --------------------------- the dependencies ---------------------------
async def tenant_db(user: dict = Depends(get_current_user)) -> PropertyScopedDatabase:
    """The caller's hotel, as a database handle.

    Declared beside `require_access` on every endpoint that touches hotel data.
    `require_access` decides whether the caller may pass and whether their property is in
    a state that permits it; this decides what they can see once they have. Both resolve
    from the same `get_current_user`, which FastAPI caches per request, so the token is
    decoded once.

    A user naming no property is refused here as well as there. It is the same refusal
    for the same reason, kept in both places because an endpoint that took this handle
    without an authorization dependency would otherwise be scoped to nothing at all.
    """
    property_id = user.get(PROPERTY_FIELD)
    if not property_id:
        raise HTTPException(403, "Not permitted")
    return PropertyScopedDatabase(property_id)


async def _live_property(property_id: str | None) -> dict | None:
    if not property_id:
        return None
    record = await _db_module.unscoped_db.properties.find_one(
        {"id": property_id}, {"_id": 0})
    if not record or record.get("status") != LIVE:
        return None
    return record


async def db_for_table(table_id: str, request: Request | None = None,
                       caller: dict | None = _UNRESOLVED  # type: ignore[assignment]
                       ) -> tuple[PropertyScopedDatabase, dict]:
    """The tenant a QR link identifies, resolved from the table it points at.

    The self-ordering routes carry no token — a guest at the table scans and orders
    without an account — so there is no caller to resolve a property from. A table
    belongs to exactly one hotel, which makes the QR link itself the tenant identifier:
    the property comes from the record rather than from the request, and nothing a guest
    can type names another hotel.

    A pending or suspended hotel's link stops working here. 404 rather than 403, and the
    same 404 an unknown table gets: a guest holding a printed QR code is not owed the
    information that this hotel exists but has been switched off, and the desk is not
    helped by a different code.

    `request` is optional because the guest has none. When it is supplied and carries a
    token, the caller's own hotel must be the table's — see below. `caller` is for a
    route that has already resolved that caller for its own reasons (orders.add_items
    decides a lot from it) and would otherwise pay for a second token decode and user
    read on every keypress at a busy till.
    """
    table = await _db_module.unscoped_db.tables.find_one({"id": table_id}, {"_id": 0})
    if not table or not await _live_property(table.get(PROPERTY_FIELD)):
        raise HTTPException(404, "Table not found")

    # These routes are also how the POS opens a bill, and a POS caller does have a token.
    # When there is one it has to agree with the table, or a waiter in one hotel could
    # append items to a table in another by pasting its id — the QR route's openness is
    # for guests at that table, not a way around the caller's own scope. Same 404 as an
    # unknown table: a table that is not this hotel's does not exist as far as it is
    # concerned.
    if caller is _UNRESOLVED:
        caller = await optional_user(request)
    if caller and caller.get(PROPERTY_FIELD) != table[PROPERTY_FIELD]:
        raise HTTPException(404, "Table not found")

    return PropertyScopedDatabase(table[PROPERTY_FIELD]), table


async def db_for_order(order_id: str) -> tuple[PropertyScopedDatabase, dict]:
    """The same, for the payment routes, which are handed an order id by Stripe's
    redirect rather than a table id. Refused identically when the hotel is not live."""
    order = await _db_module.unscoped_db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order or not await _live_property(order.get(PROPERTY_FIELD)):
        raise HTTPException(404, "Order not found")
    return PropertyScopedDatabase(order[PROPERTY_FIELD]), order


async def _founding_property_id() -> str | None:
    """The oldest property. Only ever used as the last resort in `public_db` — see there.
    """
    rows = await _db_module.unscoped_db.properties.find({}, {"_id": 0}).to_list(1000)
    if not rows:
        return None
    return sorted(rows, key=lambda p: p.get("created_at") or "")[0]["id"]


async def public_db(request: Request, table_id: str | None = None) -> PropertyScopedDatabase:
    """A handle for a route that is read both by a signed-in staff member and by a guest
    holding a QR code — today, only `GET /menu`.

    Three sources, in order of how much they are trusted to name the tenant:

    1. the table the QR code points at, when the caller passes one;
    2. the caller's own token, when there is one — this is what stops a second hotel's
       admin opening their Menu screen and reading the first hotel's dishes;
    3. the oldest property, when there is neither.

    The third is a documented compromise, not a scope: a menu is public — it is printed
    on a card and reachable by anyone who scans a table — and the alternative to
    answering was to refuse an anonymous, table-less request that predates tenancy. It
    can only ever return the founding hotel's menu, never a newer tenant's, and it
    disappears the moment the QR link carries its table id.
    """
    if table_id:
        scoped, _table = await db_for_table(table_id)
        return scoped

    try:
        user = await get_current_user(request)
    except HTTPException:
        user = None
    if user and user.get(PROPERTY_FIELD):
        return PropertyScopedDatabase(user[PROPERTY_FIELD])

    property_id = await _founding_property_id()
    if not property_id:
        raise HTTPException(404, "No property is set up yet")
    return PropertyScopedDatabase(property_id)
