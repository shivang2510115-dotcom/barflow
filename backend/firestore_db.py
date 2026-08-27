"""Firestore, wearing the interface the rest of this application already speaks.

`mock_db.py` is the specification. There are ~244 database call sites in `routers/` and
none of them are touched by this file: they go through `PropertyScopedDatabase`, which
goes through `db.unscoped_db`, which is whichever of the three backends the environment
selected. So this is a third implementation of one small interface — `find`, `find_one`,
`insert_one`, `insert_many`, `update_one`, `update_many`, `delete_one`, `delete_many`,
`count_documents`, `aggregate`, `create_index`, and a cursor with `.sort()`, `.limit()`
and `.to_list()` — and it is correct exactly when the existing suite cannot tell it from
the mock.

Three things Firestore cannot do, and what happens instead
----------------------------------------------------------
Firestore is not a document database with a general query language; it is an index
lookup with a query language shaped to fit. Three parts of the grammar this application
uses have no server-side equivalent at all:

* **`$regex`** — no substring search of any kind. Two sites, both in `routers/guests.py`,
  searching guest name and phone.
* **`$expr`** — no comparing two fields of the same document. Two sites, both in
  `routers/reports.py`: `{"$lte": ["$stock", "$threshold"]}`, the low-stock list.
* **unique indexes** — Firestore has none, at all. See `create_index` below.

and three more where Firestore *has* an operator but it means something different from
Mongo's, which is worse than not having one:

* `!=` and `not-in` **exclude** documents that do not have the field; `$ne` and `$nin`
  include them.
* `== null` matches only an explicit null; `{"field": None}` in Mongo also matches a
  document with no such field.
* `order_by` **drops** documents that do not have the sort key; `mock_db` sorts them to
  the front (it substitutes `""`).

Each of those is a silent wrong answer — a shorter list, never an error. So the strategy
throughout is: **push down only what is provably equivalent, then re-apply the entire
filter in Python** with `mock_db.matches`, the same function the mock itself uses. The
pushdown exists to keep the fetched set small and to make Firestore's indexes do real
work; it is never trusted for correctness. Narrowing too little costs bandwidth,
narrowing too much would cost a wrong answer, and only one of those is recoverable.

What that costs, stated plainly
-------------------------------
Every read fetches all documents matching the pushed-down part of the filter and filters
the rest in this process. For the two operators above, and for `$or`, `$ne`, `$nin` and
`$exists`, "the pushed-down part" is whatever equality terms sit beside them — in
practice `property_id`, because `scoped_db.py` puts it in every query. So the bound on a
fetch-and-filter read is **one property's rows in one collection**.

That is the right trade at this size and it is not the right trade forever:

* `guests` — the search in `routers/guests.py`. Hundreds to a few thousand per property
  is fine. A property with tens of thousands of guests will feel it, and it is the first
  thing to revisit: the fix is a search index (a `name_lower` prefix field for
  `>=`/`<` range matching handles "starts with", and anything better wants Algolia or
  Typesense).
* `inventory` — the low-stock `$expr` in `routers/reports.py`. A bar's stock list is
  tens of items; this will never be the problem. If it ever is, the fix is to maintain a
  boolean `low_stock` field on write, which Firestore can then filter on directly.
* `orders` — not fetch-and-filter by operator, but by cursor: `find(...).sort(...)
  .limit(50)` sorts in Python, so the limit cannot be pushed to the server and a
  property's whole settled-order history is read to answer it. This is the largest
  collection in the product and the one that grows without bound. It is the first place
  a real performance problem will appear.

Ordering
--------
`find()` with no `.sort()` returns documents in **insertion order**, because that is
what `mock_db` and Mongo's natural order both do and several call sites depend on it —
`find_one` returns the first match, and with a uuid primary key Firestore's own document
order is arbitrary. Every document written through this adapter carries a hidden,
strictly increasing `_ins` stamp, and every read sorts by it and strips it. That also
means sorting is always done here rather than by Firestore, which is what lets a missing
sort key behave the way the mock behaves.

The event loop
--------------
`AsyncClient` is grpc-backed and a grpc.aio channel binds to the event loop that created
it. The deployed application has one long-lived loop (a2wsgi owns it, see
`functions/main.py`), but the test suite calls `asyncio.run()` per assertion — a new loop
each time — and a channel used from a loop that did not create it raises. So the client
lives on one dedicated background loop owned by this module and every call is handed to
it; callers await an ordinary future on their own loop. One client per process, whatever
the caller's loop situation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Iterable, Optional

from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1.async_client import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter

from mock_db import apply_update, matches, upsert_seed

logger = logging.getLogger(__name__)

# The insertion stamp. Underscore-prefixed so it cannot collide with an application
# field, stripped from every document on the way out, and never visible to a router.
INSERTION_FIELD = "_ins"

# Firestore's `in` takes at most this many values. Beyond it the term is not pushed down
# and the Python filter does the work — correct either way, just less selective.
MAX_IN_VALUES = 10

_RANGE_OPS = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}


# --------------------------------------------------------------------------
# One event loop, one client
# --------------------------------------------------------------------------
_loop_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_client: Optional[AsyncClient] = None


def _io_loop() -> asyncio.AbstractEventLoop:
    """The dedicated loop the Firestore client lives on. Started once, on demand."""
    global _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever,
                                      name="firestore-io", daemon=True)
            thread.start()
            _loop = loop
        return _loop


def _project_id() -> Optional[str]:
    """The Google Cloud project the database belongs to.

    Deployed, this is unset and the client reads it from the metadata server, which is
    the only source that cannot be wrong. Against the emulator there is no metadata
    server and no credentials, so a project id has to be supplied; `demo-` prefixed ids
    are the emulator's own convention for "not a real project".
    """
    for var in ("FIRESTORE_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT",
                "GCP_PROJECT"):
        value = os.environ.get(var)
        if value:
            return value
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        return "demo-barflow"
    return None


async def _get_client() -> AsyncClient:
    """Created on the io loop, so its grpc channel belongs to the loop that uses it."""
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {}
        project = _project_id()
        if project:
            kwargs["project"] = project
        database = os.environ.get("FIRESTORE_DATABASE")
        if database:
            kwargs["database"] = database
        _client = AsyncClient(**kwargs)
    return _client


async def _io(coro_fn):
    """Run one client operation on the io loop and await it from the caller's.

    `coro_fn` is an async callable taking the client. It is not a coroutine object,
    because one must not be created on a loop other than the one that will run it.
    """
    loop = _io_loop()

    async def run():
        return await coro_fn(await _get_client())

    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(run(), loop))


# --------------------------------------------------------------------------
# Insertion order
# --------------------------------------------------------------------------
_stamp_lock = threading.Lock()
_last_stamp = 0


def _next_stamp() -> int:
    """A strictly increasing integer, small enough to be a Firestore integer.

    Wall-clock nanoseconds, forced upward on a tie so two documents written in the same
    microsecond still have an order. Across processes this is only as good as the
    clocks are — which is also true of Mongo's natural order, and nothing in the
    application depends on the relative order of two documents written concurrently by
    two instances.
    """
    global _last_stamp
    with _stamp_lock:
        value = max(time.time_ns(), _last_stamp + 1)
        _last_stamp = value
        return value


# --------------------------------------------------------------------------
# Which parts of a filter Firestore may be trusted with
# --------------------------------------------------------------------------
def _pushable_value(value: Any) -> bool:
    """Whether a value can be compared server-side with the same meaning as in Python.

    Excluded, each for a reason that would otherwise be a silently short answer:

    * `None` — Firestore's `== null` wants an explicit null; Mongo also matches a
      document that has no such field.
    * `bool` — Firestore types booleans and numbers separately and will not match `1`
      against `True`; Python will. Booleans are low-selectivity filters anyway, so
      declining to push them down costs essentially nothing.
    * lists and dicts — array and map equality have their own rules in both systems and
      no query in this application needs them pushed down.
    """
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _plan(filter_query: Optional[dict]) -> tuple[list[FieldFilter], list[str], Optional[str]]:
    """Split a filter into the part Firestore will run and a description for the index.

    Returns the server-side filters, the equality field names, and the one field carrying
    a range — which is exactly the shape of the composite index the query needs.

    One range field, not several. Firestore does now allow inequalities on more than one
    field, but each extra one multiplies the composite indexes the deployment has to
    declare, and the second range narrows a set that the first has already made small.
    The rest is filtered here.
    """
    if not filter_query:
        return [], [], None

    filters: list[FieldFilter] = []
    equality: list[str] = []
    range_field: Optional[str] = None
    have_in = False

    for field, condition in filter_query.items():
        if field.startswith("$"):
            continue  # $or, $expr — no server-side equivalent, handled in Python

        if not isinstance(condition, dict):
            if _pushable_value(condition):
                filters.append(FieldFilter(field, "==", condition))
                equality.append(field)
            continue

        if not condition or not all(str(op).startswith("$") for op in condition):
            continue  # a nested document compared whole; left to Python

        # `$in` behaves as equality for indexing purposes, and one per query is all
        # Firestore has ever reliably allowed.
        values = condition.get("$in")
        if (not have_in and isinstance(values, (list, tuple)) and values
                and len(values) <= MAX_IN_VALUES
                and all(_pushable_value(v) for v in values)):
            filters.append(FieldFilter(field, "in", list(values)))
            equality.append(field)
            have_in = True

        if range_field is not None and field != range_field:
            continue
        pushed = [(op, bound) for op, bound in condition.items()
                  if op in _RANGE_OPS and _pushable_value(bound)]
        if pushed:
            for op, bound in pushed:
                filters.append(FieldFilter(field, _RANGE_OPS[op], bound))
            range_field = field

    return filters, equality, range_field


# --------------------------------------------------------------------------
# The index trace
# --------------------------------------------------------------------------
# Firestore fails a query that has no index at *runtime*, with a link to create one. In
# production that is a 500 on a page nobody opened during testing. `firestore.indexes.json`
# is meant to make that impossible, and it has to list the indexes the application
# actually needs — which is a question about what the code does, not about what anybody
# remembers it doing. Setting FIRESTORE_INDEX_TRACE=<path> records the shape of every
# query the process runs, and the suite is then the thing that enumerates them. The
# emulator does not enforce indexes, so this is the only way the test run can answer the
# question at all.
_trace_path = os.environ.get("FIRESTORE_INDEX_TRACE")
_trace_seen: set[str] = set()
_trace_lock = threading.Lock()


def _trace(collection: str, equality: list[str], range_field: Optional[str]) -> None:
    if not _trace_path:
        return
    line = json.dumps({"collection": collection, "equality": sorted(equality),
                       "range": range_field}, sort_keys=True)
    with _trace_lock:
        if line in _trace_seen:
            return
        _trace_seen.add(line)
        try:
            with open(_trace_path, "a") as handle:
                handle.write(line + "\n")
        except OSError:  # tracing must never be the reason a request fails
            pass


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------
def _valid_document_id(value: Any) -> Optional[str]:
    """This value used as a Firestore document id, or None if it cannot be.

    The application's own `id` field is the document id wherever possible, which is what
    turns `find_one({"id": x})` — the single most common read in the product — into a
    direct get instead of a query. Firestore's rules on ids are few but absolute, and a
    value breaking one of them gets an auto-id instead; such a document is still found by
    every query, just not by the fast path.
    """
    if not isinstance(value, str) or not value:
        return None
    if "/" in value or value in (".", "..") or len(value.encode()) > 1500:
        return None
    if value.startswith("__") and value.endswith("__"):
        return None
    return value


def _encode(doc: dict) -> dict:
    """A document as Firestore will store it. Tuples become lists; nothing else changes."""
    def convert(value):
        if isinstance(value, tuple):
            return [convert(v) for v in value]
        if isinstance(value, list):
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        return value

    return {k: convert(v) for k, v in doc.items()}


def _project(doc: dict, projection: Optional[dict]) -> dict:
    """A copy of the document with the internal stamp gone and the projection applied.

    Exclusion-only, like `mock_db`: `{"_id": 0}` — which every call site in the
    application passes and neither of these backends has ever had a use for — drops a
    key that is not there, harmlessly.
    """
    result = {k: v for k, v in doc.items() if k != INSERTION_FIELD}
    if projection:
        for key, keep in projection.items():
            if keep == 0:
                result.pop(key, None)
    return result


class _Row:
    """One fetched document: its reference, so a write can find it again, and its data
    including the internal stamp, so ordering survives an update."""

    __slots__ = ("ref", "data")

    def __init__(self, ref, data: dict):
        self.ref = ref
        self.data = data


# --------------------------------------------------------------------------
# The cursor
# --------------------------------------------------------------------------
class FirestoreCursor:
    """What `find()` returns: the query, plus the operations asked of it, in order.

    Order matters and is preserved rather than normalised, because `mock_db`'s cursor is
    eager — `.limit(3).sort("x")` truncates and *then* sorts there, which is a different
    answer from `.sort("x").limit(3)`. Nothing in the application relies on that, and an
    adapter that quietly disagreed with the mock about it would be a difference waiting
    to matter.

    Everything happens in this process on `to_list()`. See the module docstring for why
    sorting is not pushed to Firestore, and what it costs.
    """

    def __init__(self, collection: "FirestoreCollection",
                 filter_query: Optional[dict], projection: Optional[dict]):
        self._collection = collection
        self._filter = filter_query
        self._projection = projection
        self._ops: list[tuple[str, tuple]] = []

    def sort(self, key, direction=1):
        self._ops.append(("sort", (key, direction)))
        return self

    def limit(self, count):
        self._ops.append(("limit", (count,)))
        return self

    def skip(self, count):
        self._ops.append(("skip", (count,)))
        return self

    async def to_list(self, length=None):
        rows = await self._collection._fetch(self._filter)
        items = [_project(row.data, self._projection) for row in rows]
        for op, args in self._ops:
            if op == "sort":
                key, direction = args
                items.sort(key=lambda x: x.get(key) if x.get(key) is not None else "",
                           reverse=(direction == -1))
            elif op == "limit":
                items = items[:args[0]]
            elif op == "skip":
                items = items[args[0]:]
        if length is not None:
            return items[:length]
        return items

    async def __aiter__(self):
        for item in await self.to_list():
            yield item


# --------------------------------------------------------------------------
# The results the drivers return
# --------------------------------------------------------------------------
class InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class UpdateResult:
    def __init__(self, matched, modified, upserted=None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted


class DeleteResult:
    def __init__(self, count):
        self.deleted_count = count


# --------------------------------------------------------------------------
# The collection
# --------------------------------------------------------------------------
class FirestoreCollection:
    def __init__(self, name: str, database: "FirestoreDatabase"):
        self.name = name
        self.db = database

    @property
    def _path(self) -> str:
        return f"{self.db.namespace}{self.name}"

    # ------------------------------- reading -------------------------------
    async def _fetch(self, filter_query: Optional[dict]) -> list[_Row]:
        """Every document matching this filter, in insertion order.

        Two routes in. When the filter pins the application's `id` — which it does at
        most read sites, because that is how a router looks up the thing it was asked
        about — the document id is known and this is a single get. Otherwise it is a
        query with whatever `_plan` could push down, followed by the full filter applied
        here.
        """
        filter_query = filter_query or {}
        document_id = _valid_document_id(filter_query.get("id")) \
            if not isinstance(filter_query.get("id"), dict) else None

        if document_id is not None:
            rows = await self._get(document_id)
        else:
            rows = await self._query(filter_query)

        kept = [row for row in rows if matches(row.data, filter_query)]
        kept.sort(key=lambda row: row.data.get(INSERTION_FIELD) or 0)
        return kept

    async def _get(self, document_id: str) -> list[_Row]:
        path, name = self._path, self.name

        async def op(client):
            ref = client.collection(path).document(document_id)
            snapshot = await ref.get()
            if not snapshot.exists:
                return []
            return [_Row(ref, snapshot.to_dict() or {})]

        _trace(name, ["id"], None)
        return await _io(op)

    async def _query(self, filter_query: dict) -> list[_Row]:
        filters, equality, range_field = _plan(filter_query)
        path, name = self._path, self.name
        _trace(name, equality, range_field)

        async def op(client):
            query = client.collection(path)
            for field_filter in filters:
                query = query.where(filter=field_filter)
            return [_Row(snapshot.reference, snapshot.to_dict() or {})
                    async for snapshot in query.stream()]

        try:
            return await _io(op)
        except FailedPrecondition as exc:
            # The one Firestore failure that is a deploy bug rather than a runtime one:
            # a query whose composite index does not exist. It fails at the moment a user
            # opens the page, which is why `firestore.indexes.json` exists — so say which
            # query it was and where the answer lives, rather than leaving a raw gRPC
            # message and Google's create-this-index link as the only clue.
            logger.error(
                "Firestore has no index for a query this application makes: "
                "collection %r, equality on %s, range on %r. firestore.indexes.json is "
                "meant to declare every one of these — add it there and "
                "`firebase deploy --only firestore:indexes`, rather than only clicking "
                "the link below, or the next deployment is missing it again.\n  %s",
                name, sorted(equality) or "nothing", range_field, exc)
            raise

    def find(self, filter_query=None, projection=None) -> FirestoreCursor:
        return FirestoreCursor(self, filter_query, projection)

    async def find_one(self, filter_query=None, projection=None):
        rows = await self._fetch(filter_query)
        if not rows:
            return None
        return _project(rows[0].data, projection)

    async def count_documents(self, filter_query=None) -> int:
        # Firestore's own `count()` aggregation cannot be used: it counts what the
        # server-side query matched, and the server-side query is deliberately wider
        # than the filter. Counting the filtered rows is the only correct answer.
        return len(await self._fetch(filter_query))

    def aggregate(self, pipeline, *args, **kwargs) -> FirestoreCursor:
        """`$match` and nothing else, exactly as `mock_db` allows.

        `scoped_db.py` prepends a `$match` on property_id to every pipeline, which is the
        only reason this method exists. A `$group` or `$lookup` quietly ignored here
        would make a report add up locally and differently in production, so an unknown
        stage raises — the same refusal, for the same reason, as an unknown query
        operator.
        """
        merged: dict = {}
        extra: list[dict] = []
        for stage in pipeline or []:
            for op, spec in stage.items():
                if op != "$match":
                    raise ValueError(f"firestore_db: unsupported aggregation stage {op}")
                # Merged into one filter where the keys do not collide, so the pushdown
                # sees the whole thing; a repeated key keeps its own stage and is applied
                # in Python.
                for key, value in (spec or {}).items():
                    if key in merged and merged[key] != value:
                        extra.append({key: value})
                    else:
                        merged[key] = value
        return _StagedCursor(self, merged, extra)

    # ------------------------------- writing -------------------------------
    async def insert_one(self, doc: dict):
        payload = _encode(dict(doc))
        payload[INSERTION_FIELD] = _next_stamp()
        document_id = _valid_document_id(doc.get("id"))
        path = self._path

        async def op(client):
            collection = client.collection(path)
            ref = collection.document(document_id) if document_id \
                else collection.document()
            await ref.set(payload)

        await _io(op)
        return InsertOneResult(doc.get("id"))

    async def insert_many(self, docs: Iterable[dict]):
        docs = list(docs)
        payloads = []
        for doc in docs:
            payload = _encode(dict(doc))
            payload[INSERTION_FIELD] = _next_stamp()
            payloads.append((_valid_document_id(doc.get("id")), payload))
        path = self._path

        async def op(client):
            collection = client.collection(path)
            # Firestore batches are capped at 500 writes. Seeding a demo property is the
            # only caller anywhere near it, but a silent truncation at 501 is not a thing
            # to leave available.
            for start in range(0, len(payloads), 400):
                batch = client.batch()
                for document_id, payload in payloads[start:start + 400]:
                    ref = collection.document(document_id) if document_id \
                        else collection.document()
                    batch.set(ref, payload)
                await batch.commit()

        await _io(op)
        return InsertManyResult([doc.get("id") for doc in docs])

    async def _write(self, row: _Row, data: dict) -> None:
        """Store a document that has been updated in place.

        An update that changes the `id` field has to move the document, or the fast path
        in `_fetch` — which looks a document up *by* that field — would stop finding it.
        Nothing in the application does this today; the alternative to handling it is an
        invariant held only by nobody having tried.
        """
        path = self._path
        new_id = _valid_document_id(data.get("id"))
        old_ref = row.ref

        async def op(client):
            if new_id is not None and old_ref.id != new_id:
                await client.collection(path).document(new_id).set(data)
                await old_ref.delete()
            else:
                await old_ref.set(data)

        await _io(op)

    async def update_one(self, filter_query: dict, update_query: dict, upsert=False):
        # An upsert keyed only on `id` is a write to a document we can already name, so
        # it goes straight there and skips the read. That is not just faster — it is the
        # difference between correct and not. The general path below reads, decides, then
        # writes, so two callers racing on the same key both see "nothing there" and both
        # create a row. Firestore has no unique constraints to catch the second one.
        #
        # The one place it matters today is posting a room night: routers/folios.py
        # derives a deterministic id per (folio, night) precisely so this path is taken,
        # and a guest cannot be charged twice for one night. A single-document set() is
        # atomic in Firestore, so the second writer overwrites with an identical row.
        if upsert and set(filter_query) == {"id"} and isinstance(filter_query["id"], str):
            document_id = _valid_document_id(filter_query["id"])
            if document_id:
                seed = upsert_seed(filter_query)
                apply_update(seed, update_query)
                payload = _encode(seed)
                payload[INSERTION_FIELD] = _next_stamp()
                path = self._path

                async def op(client):
                    await client.collection(path).document(document_id).set(payload)

                await _io(op)
                return UpdateResult(0, 0, seed.get("id"))

        rows = await self._fetch(filter_query)
        if rows:
            row = rows[0]
            data = dict(row.data)
            modified = apply_update(data, update_query)
            if modified:
                await self._write(row, _encode(data))
            return UpdateResult(1, 1 if modified else 0)

        if not upsert:
            return UpdateResult(0, 0)

        created = upsert_seed(filter_query)
        apply_update(created, update_query)
        await self.insert_one(created)
        return UpdateResult(0, 0, created.get("id"))

    async def update_many(self, filter_query: dict, update_query: dict, **kwargs):
        rows = await self._fetch(filter_query)
        matched = modified = 0
        for row in rows:
            matched += 1
            data = dict(row.data)
            if apply_update(data, update_query):
                modified += 1
                await self._write(row, _encode(data))
        return UpdateResult(matched, modified)

    async def delete_one(self, filter_query: dict):
        rows = await self._fetch(filter_query)
        if not rows:
            return DeleteResult(0)
        ref = rows[0].ref

        async def op(client):
            await ref.delete()

        await _io(op)
        return DeleteResult(1)

    async def delete_many(self, filter_query: Optional[dict] = None):
        rows = await self._fetch(filter_query)
        if not rows:
            return DeleteResult(0)
        refs = [row.ref for row in rows]

        async def op(client):
            for start in range(0, len(refs), 400):
                batch = client.batch()
                for ref in refs[start:start + 400]:
                    batch.delete(ref)
                await batch.commit()

        await _io(op)
        return DeleteResult(len(refs))

    # ------------------------------- indexes -------------------------------
    async def create_index(self, keys, unique=False, sparse=False,
                           partialFilterExpression=None, expireAfterSeconds=None):
        """A no-op, and the `unique=True` callers are the ones worth naming.

        **Firestore has no unique indexes.** Not a limitation of this adapter — the
        product does not have the feature. So the four uniqueness constraints
        `server.py::seed_data` declares are enforced by application code and nothing
        else:

        * `users.email` — `routers/staff.py` and `routers/signup.py` look for an existing
          address before inserting;
        * `guests(property_id, phone)` — `routers/guests.py` returns 409 with the
          existing guest;
        * `bookings(property_id, reference)` — `routers/bookings.py` generates and checks;
        * `folio_entries(property_id, folio_id, charge_date)` for `kind: "room_night"` —
          `services/folio.py::unposted_nights` is the real idempotency, the index was
          the belt.

        Every one of those is a read followed by a write, so two concurrent requests can
        both read "no duplicate" and both insert. That race is **already** the situation
        under `mock_db`, whose `create_index` is a no-op too, and the pre-checks were
        written knowing it. It is not new here — but Mongo was the thing that would have
        closed it, and choosing Firestore is choosing not to. The report accompanying
        this change says which of the four can actually hurt.

        `expireAfterSeconds` is the other one. Firestore does have TTL, but it is a
        field-level policy on the database rather than something a client can ask for, so
        it is declared in `firestore.indexes.json` under `fieldOverrides` — where the
        rate limiter's `expires_at` now has one. `services/ratelimit.py` also prunes by
        hand and must keep doing so; the policy is best-effort and sweeps within 24h.
        """
        return None


class _StagedCursor(FirestoreCursor):
    """`aggregate`'s cursor: one merged `$match` plus any stages that could not merge."""

    def __init__(self, collection, merged: dict, extra: list[dict]):
        super().__init__(collection, merged, None)
        self._extra = extra

    async def to_list(self, length=None):
        items = await super().to_list(None)
        for spec in self._extra:
            items = [item for item in items if matches(item, spec)]
        if length is not None:
            return items[:length]
        return items


# --------------------------------------------------------------------------
# The database and the client handle
# --------------------------------------------------------------------------
class FirestoreDatabase:
    """A database-shaped object, used exactly as the mock and Motor handles are.

    `namespace` prefixes every collection name. It is empty in the application and set
    to something unique per test by `tests/conftest.py`, which is what lets 445 tests
    share one emulator without seeing each other's rows — the same isolation each test
    gets from its own `tmp_path` under the mock.
    """

    def __init__(self, namespace: str = ""):
        self.namespace = f"{namespace}__" if namespace else ""

    def __getattr__(self, name: str) -> FirestoreCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return FirestoreCollection(name, self)

    def __getitem__(self, name: str) -> FirestoreCollection:
        return FirestoreCollection(name, self)


class FirestoreClient:
    """Stands where `AsyncIOMotorClient` and `MockMongoClient` stand in `db.py`.

    Firestore has no connection string, no database name to select and no connection to
    open, so this exists only so that `db.py` reads the same for all three backends and
    `server.py`'s shutdown hook has a `close()` to call.
    """

    def __init__(self, namespace: str = ""):
        self._database = FirestoreDatabase(namespace)

    def __getitem__(self, _name) -> FirestoreDatabase:
        return self._database

    def close(self) -> None:
        global _client
        client, _client = _client, None
        if client is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(_shutdown(client), _io_loop()).result(10)
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise over a channel
            logger.warning("Firestore client did not close cleanly: %s", exc)


async def _shutdown(client: AsyncClient) -> None:
    client.close()


async def check_connection() -> None:
    """Prove the database answers, so a misconfiguration is a named error at startup.

    The same job `db.check_connection` does for Atlas, and the failures are different
    ones worth naming: Firestore in a Firebase project needs no connection string and no
    IP allowlist, so what goes wrong is a database that was never created in the console,
    or a service account without `roles/datastore.user`.
    """
    async def op(client):
        # Any read proves credentials, project and database at once. This collection is
        # not expected to exist; a query over an absent collection is legal and empty.
        return [snapshot async for snapshot in
                client.collection("_startup_check").limit(1).stream()]

    try:
        await _io(op)
        logger.info("Firestore connection OK.")
    except Exception as exc:  # noqa: BLE001 — surface anything the client raises
        logger.error(
            "Firestore unreachable: %s\n"
            "  Check, in this order:\n"
            "   1. the Firebase project has a Firestore database created "
            "(console -> Build -> Firestore Database -> Create database)\n"
            "   2. the function's service account has roles/datastore.user\n"
            "   3. FIRESTORE_DATABASE, if set, names a database that exists — "
            "the default one is called '(default)'",
            exc,
        )
        raise
