"""Every operator the application uses, run against both backends and diffed.

This suite lives outside `tests/` on purpose. `tests/` is the product's own suite and its
counts are a published baseline (445 pure, 132 API); adding files to it would move those
numbers and hide a regression behind arithmetic. This is a suite about one adapter, it
needs the Firestore emulator to run at all, and it is asked for by name:

    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python3 -m pytest tests_firestore/ -q

**Why differential rather than expected-value.** The failure this is written against is
not a crash. It is a filter that Firestore translates into something narrower than the
caller meant, matches nothing, and returns `[]` — which is indistinguishable from an
empty collection at every call site in the application. Asserting `result == []` would
pass. So every case here runs the *same* scenario against `MockDatabase` — the reference
implementation, the one 445 passing tests already agree with — and against
`FirestoreDatabase`, and requires the two to return the same thing. A silently narrowed
filter shows up as a diff, not as a plausible empty list.

The operator inventory below is measured from the application, not imagined: `$set` (55
sites), `$in` (9), `$ne` (5), `$lt` (4), `$gte` (4), `$or` (3), `$lte` (3),
`$regex`+`$options` (2), `$expr` (2), `$push` (1), `$nin` (1), `$gt` (1), `$exists` (1),
`$inc` (1, via the rate limiter's upsert), and `$match` as the only aggregation stage.
Every one of them appears here at least once, and each with at least one document that
*should not* match — a filter that matches everything is as wrong as one that matches
nothing, and only a negative case tells them apart.
"""
import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator: "
           "firebase emulators:start --only firestore --project demo-barflow",
)

from mock_db import MockDatabase  # noqa: E402


def _handles(tmp_path):
    """One handle onto each backend, both empty."""
    from firestore_db import FirestoreDatabase

    return [
        MockDatabase(str(tmp_path / "db.json")),
        FirestoreDatabase(namespace=f"t{uuid.uuid4().hex}"),
    ]


def diff(tmp_path, scenario):
    """Run one scenario against both backends and require identical answers.

    `scenario` is an async function taking a database handle and returning anything
    comparable. It is called once per backend, on a handle nothing else has touched.
    """
    results = [asyncio.run(scenario(handle)) for handle in _handles(tmp_path)]
    mock_result, fs_result = results
    assert fs_result == mock_result, (
        f"\n  mock_db  : {mock_result!r}"
        f"\n  firestore: {fs_result!r}")
    return mock_result


# The population every read case runs against. Deliberately mixed: two properties (so a
# missing tenant filter shows up), documents missing fields other documents have (so
# `$exists` and `$ne` have something to disagree about), and stock/threshold pairs on
# both sides of each other (so `$expr` has a negative case).
STOCK = [
    {"id": "a1", "property_id": "p1", "name": "Old Monk", "category": "rum",
     "stock": 2.0, "threshold": 5.0, "phone": "9812345678", "tags": ["bar"]},
    {"id": "a2", "property_id": "p1", "name": "Kingfisher", "category": "beer",
     "stock": 40.0, "threshold": 10.0, "phone": "9800000001", "tags": ["bar", "cold"]},
    {"id": "a3", "property_id": "p1", "name": "gin & TONIC", "category": "gin",
     "stock": 5.0, "threshold": 5.0, "phone": "9998887776"},
    {"id": "a4", "property_id": "p1", "name": "Rooh Afza", "category": None,
     "stock": 1.0, "threshold": 0.0},
    {"id": "b1", "property_id": "p2", "name": "Old Monk", "category": "rum",
     "stock": 0.0, "threshold": 99.0, "phone": "9812345678"},
]

DATED = [
    {"id": "d1", "property_id": "p1", "check_in": "2026-01-05", "check_out": "2026-01-08",
     "status": "confirmed", "total": 4200},
    {"id": "d2", "property_id": "p1", "check_in": "2026-01-08", "check_out": "2026-01-11",
     "status": "cancelled", "total": 3100},
    {"id": "d3", "property_id": "p1", "check_in": "2026-02-01", "check_out": "2026-02-03",
     "status": "confirmed", "total": 900},
    {"id": "d4", "property_id": "p2", "check_in": "2026-01-05", "check_out": "2026-01-08",
     "status": "confirmed", "total": 7000},
]


async def _seed(db, rows=None):
    await db.inventory.insert_many([dict(r) for r in (rows or STOCK)])
    return db


async def _seed_dated(db):
    await db.bookings.insert_many([dict(r) for r in DATED])
    return db


async def _ids(cursor):
    return [row["id"] for row in await cursor.to_list(1000)]


# ------------------------------- plain equality -------------------------------
def test_equality_and_the_tenant_filter(tmp_path):
    async def scenario(db):
        await _seed(db)
        return {
            "p1": await _ids(db.inventory.find({"property_id": "p1"})),
            "rum_in_p1": await _ids(db.inventory.find({"property_id": "p1",
                                                       "category": "rum"})),
            "count": await db.inventory.count_documents({"property_id": "p1"}),
        }

    assert diff(tmp_path, scenario)["rum_in_p1"] == ["a1"]


def test_equality_against_a_null_valued_field_also_matches_a_missing_one(tmp_path):
    """`{"category": None}` matches a document whose category is null *and* one that has
    no category at all. Mongo and mock_db agree on that; Firestore's `== null` does not
    match a missing field, so this is a case the adapter must not push down."""
    async def scenario(db):
        await _seed(db)
        # a4 has category: None. d-shaped row below has no category key at all.
        await db.inventory.insert_one(
            {"id": "a5", "property_id": "p1", "name": "Water", "stock": 9.0,
             "threshold": 1.0})
        return sorted(await _ids(db.inventory.find({"property_id": "p1",
                                                    "category": None})))

    assert diff(tmp_path, scenario) == ["a4", "a5"]


def test_find_one_is_by_the_documents_own_id(tmp_path):
    async def scenario(db):
        await _seed(db)
        return [
            await db.inventory.find_one({"id": "a2"}),
            await db.inventory.find_one({"id": "a2", "property_id": "p2"}),
            await db.inventory.find_one({"id": "nope"}),
        ]

    got = diff(tmp_path, scenario)
    assert got[0]["name"] == "Kingfisher"
    assert got[1] is None and got[2] is None


# ------------------------------ query operators -------------------------------
def test_in(tmp_path):
    async def scenario(db):
        await _seed(db)
        return sorted(await _ids(db.inventory.find(
            {"property_id": "p1", "category": {"$in": ["rum", "gin"]}})))

    assert diff(tmp_path, scenario) == ["a1", "a3"]


def test_nin(tmp_path):
    async def scenario(db):
        await _seed(db)
        return sorted(await _ids(db.inventory.find(
            {"property_id": "p1", "category": {"$nin": ["rum", "beer"]}})))

    # a4's category is null and a null is not in the list, so it stays — the same answer
    # Mongo gives and the opposite of Firestore's own `not-in`, which drops it.
    assert diff(tmp_path, scenario) == ["a3", "a4"]


def test_ne_keeps_documents_that_do_not_have_the_field(tmp_path):
    async def scenario(db):
        await _seed(db)
        await db.inventory.insert_one({"id": "a6", "property_id": "p1", "name": "Soda",
                                       "stock": 3.0, "threshold": 1.0})
        return sorted(await _ids(db.inventory.find(
            {"property_id": "p1", "category": {"$ne": "rum"}})))

    # a6 has no category at all. Firestore's `!=` would silently drop it.
    assert diff(tmp_path, scenario) == ["a2", "a3", "a4", "a6"]


def test_exists(tmp_path):
    async def scenario(db):
        await _seed(db)
        return {
            "has_phone": sorted(await _ids(db.inventory.find(
                {"property_id": "p1", "phone": {"$exists": True}}))),
            "no_phone": sorted(await _ids(db.inventory.find(
                {"property_id": "p1", "phone": {"$exists": False}}))),
        }

    got = diff(tmp_path, scenario)
    assert got["has_phone"] == ["a1", "a2", "a3"] and got["no_phone"] == ["a4"]


def test_ranges_gt_gte_lt_lte(tmp_path):
    async def scenario(db):
        await _seed_dated(db)
        return {
            "gte": sorted(await _ids(db.bookings.find(
                {"property_id": "p1", "check_in": {"$gte": "2026-01-08"}}))),
            "lt": sorted(await _ids(db.bookings.find(
                {"property_id": "p1", "check_in": {"$lt": "2026-01-08"}}))),
            "gt_lte": sorted(await _ids(db.bookings.find(
                {"property_id": "p1", "total": {"$gt": 900, "$lte": 3100}}))),
            # The overlap test from availability: two ranges on two different fields.
            "overlap": sorted(await _ids(db.bookings.find(
                {"property_id": "p1", "status": {"$ne": "cancelled"},
                 "check_in": {"$lt": "2026-01-09"},
                 "check_out": {"$gt": "2026-01-06"}}))),
        }

    got = diff(tmp_path, scenario)
    assert got["gte"] == ["d2", "d3"]
    assert got["lt"] == ["d1"]
    assert got["gt_lte"] == ["d2"]
    assert got["overlap"] == ["d1"]


def test_a_range_never_matches_a_document_missing_the_field(tmp_path):
    async def scenario(db):
        await _seed_dated(db)
        await db.bookings.insert_one({"id": "d5", "property_id": "p1",
                                      "status": "confirmed"})
        return sorted(await _ids(db.bookings.find(
            {"property_id": "p1", "check_in": {"$gte": ""}})))

    assert diff(tmp_path, scenario) == ["d1", "d2", "d3"]


def test_or_narrows_within_the_property_rather_than_escaping_it(tmp_path):
    async def scenario(db):
        await _seed(db)
        return sorted(await _ids(db.inventory.find({
            "property_id": "p1",
            "$or": [{"category": "beer"}, {"stock": {"$lt": 2.0}}],
        })))

    # b1 in p2 has stock 0.0 and must not appear.
    assert diff(tmp_path, scenario) == ["a2", "a4"]


def test_regex_with_options_i(tmp_path):
    """The guest search: case-insensitive substring over name and phone."""
    async def scenario(db):
        await _seed(db)
        return {
            "tonic": sorted(await _ids(db.inventory.find({
                "property_id": "p1",
                "$or": [{"phone": {"$regex": "tonic", "$options": "i"}},
                        {"name": {"$regex": "tonic", "$options": "i"}}],
            }))),
            "by_phone_fragment": sorted(await _ids(db.inventory.find({
                "property_id": "p1",
                "$or": [{"phone": {"$regex": "98123", "$options": "i"}},
                        {"name": {"$regex": "98123", "$options": "i"}}],
            }))),
        }

    got = diff(tmp_path, scenario)
    assert got["tonic"] == ["a3"]
    assert got["by_phone_fragment"] == ["a1"]


def test_expr_compares_two_fields_of_one_document(tmp_path):
    """The low-stock report: `stock <= threshold`, both on the same document."""
    async def scenario(db):
        await _seed(db)
        return {
            "low": sorted(await _ids(db.inventory.find(
                {"property_id": "p1", "$expr": {"$lte": ["$stock", "$threshold"]}}))),
            "count": await db.inventory.count_documents(
                {"property_id": "p1", "$expr": {"$lte": ["$stock", "$threshold"]}}),
        }

    got = diff(tmp_path, scenario)
    assert got["low"] == ["a1", "a3"]  # a3 is the boundary: 5.0 <= 5.0
    assert got["count"] == 2


# --------------------------------- the cursor ---------------------------------
def test_find_with_no_sort_is_insertion_order(tmp_path):
    """Nothing in the application asks for this and several things depend on it.

    `find_one` returns the *first* match and a Firestore query returns documents in
    document-id order, which for a uuid primary key is arbitrary. Insertion order is what
    mock_db and Mongo's natural order both give, so the adapter has to reproduce it.
    """
    async def scenario(db):
        for n in range(6):
            await db.tables.insert_one({"id": f"t{n}", "property_id": "p1",
                                        "label": f"Table {5 - n}"})
        return {
            "order": await _ids(db.tables.find({"property_id": "p1"})),
            "first": (await db.tables.find_one({"property_id": "p1"}))["id"],
        }

    got = diff(tmp_path, scenario)
    assert got["order"] == ["t0", "t1", "t2", "t3", "t4", "t5"]
    assert got["first"] == "t0"


def test_sort_then_limit_then_to_list(tmp_path):
    async def scenario(db):
        await _seed_dated(db)
        return {
            "asc": await _ids(db.bookings.find({"property_id": "p1"}).sort("total", 1)),
            "desc": await _ids(db.bookings.find({"property_id": "p1"}).sort("total", -1)),
            "top2": [r["id"] for r in await db.bookings.find({"property_id": "p1"})
                     .sort("total", -1).limit(2).to_list(2)],
            "to_list_truncates": [r["id"] for r in
                                  await db.bookings.find({"property_id": "p1"}).to_list(2)],
        }

    got = diff(tmp_path, scenario)
    assert got["asc"] == ["d3", "d2", "d1"]
    assert got["desc"] == ["d1", "d2", "d3"]
    assert got["top2"] == ["d1", "d2"]
    assert got["to_list_truncates"] == ["d1", "d2"]


def test_sort_puts_a_missing_field_first(tmp_path):
    """mock_db substitutes "" for a missing sort key, so those rows lead an ascending
    sort rather than being dropped. Firestore's own order-by drops them entirely."""
    async def scenario(db):
        await db.menu.insert_many([
            {"id": "m1", "property_id": "p1", "name": "Pav Bhaji", "category": "mains"},
            {"id": "m2", "property_id": "p1", "name": "Chai"},
            {"id": "m3", "property_id": "p1", "name": "Lassi", "category": "drinks"},
        ])
        return await _ids(db.menu.find({"property_id": "p1"}).sort("category", 1))

    assert diff(tmp_path, scenario) == ["m2", "m3", "m1"]


def test_projection_drops_the_named_fields(tmp_path):
    async def scenario(db):
        await _seed(db)
        return [
            await db.inventory.find_one({"id": "a1"}, {"_id": 0, "threshold": 0}),
            (await db.inventory.find({"id": "a1"}, {"_id": 0, "phone": 0})
             .to_list(1))[0],
        ]

    got = diff(tmp_path, scenario)
    assert "threshold" not in got[0] and "stock" in got[0]
    assert "phone" not in got[1]


# ---------------------------------- the writes --------------------------------
def test_insert_one_and_insert_many_report_their_ids(tmp_path):
    async def scenario(db):
        one = await db.rooms.insert_one({"id": "r1", "property_id": "p1", "number": "101"})
        many = await db.rooms.insert_many([
            {"id": "r2", "property_id": "p1", "number": "102"},
            {"id": "r3", "property_id": "p1", "number": "103"},
        ])
        return {"one": one.inserted_id, "many": list(many.inserted_ids),
                "all": await _ids(db.rooms.find({"property_id": "p1"}))}

    got = diff(tmp_path, scenario)
    assert got["one"] == "r1" and got["many"] == ["r2", "r3"]


def test_set(tmp_path):
    async def scenario(db):
        await _seed(db)
        first = await db.inventory.update_one({"id": "a1"}, {"$set": {"stock": 12.0}})
        again = await db.inventory.update_one({"id": "a1"}, {"$set": {"stock": 12.0}})
        missing = await db.inventory.update_one({"id": "zz"}, {"$set": {"stock": 1.0}})
        row = await db.inventory.find_one({"id": "a1"})
        return {
            "first": [first.matched_count, first.modified_count],
            # A $set that changes nothing matches but does not modify. The folio and
            # order routers both read modified_count to decide whether to 404.
            "again": [again.matched_count, again.modified_count],
            "missing": [missing.matched_count, missing.modified_count],
            "stock": row["stock"], "name": row["name"],
        }

    got = diff(tmp_path, scenario)
    assert got["first"] == [1, 1] and got["again"] == [1, 0] and got["missing"] == [0, 0]
    assert got["stock"] == 12.0 and got["name"] == "Old Monk"


def test_push(tmp_path):
    async def scenario(db):
        await db.orders.insert_one({"id": "o1", "property_id": "p1", "total": 100})
        await db.orders.update_one({"id": "o1"}, {"$push": {"items": {"n": "Chai"}}})
        await db.orders.update_one({"id": "o1"}, {"$push": {"items": {"n": "Samosa"}}})
        return (await db.orders.find_one({"id": "o1"}))["items"]

    assert diff(tmp_path, scenario) == [{"n": "Chai"}, {"n": "Samosa"}]


def test_inc_with_upsert_is_the_rate_limiters_counter(tmp_path):
    async def scenario(db):
        created = await db.rate_limit_hits.update_one(
            {"key": "login|203.0.113.7"}, {"$inc": {"hits": 1}}, upsert=True)
        for _ in range(3):
            await db.rate_limit_hits.update_one(
                {"key": "login|203.0.113.7"}, {"$inc": {"hits": 1}}, upsert=True)
        rows = await db.rate_limit_hits.find({}).to_list(100)
        return {"upserted": created.matched_count, "rows": len(rows),
                "hits": rows[0]["hits"], "key": rows[0]["key"]}

    got = diff(tmp_path, scenario)
    assert got["upserted"] == 0 and got["rows"] == 1 and got["hits"] == 4
    assert got["key"] == "login|203.0.113.7"


def test_update_many(tmp_path):
    async def scenario(db):
        await _seed_dated(db)
        res = await db.bookings.update_many(
            {"property_id": "p1", "status": {"$ne": "cancelled"}},
            {"$set": {"status": "confirmed", "audited": True}})
        return {"counts": [res.matched_count, res.modified_count],
                "audited": sorted(await _ids(db.bookings.find({"audited": True})))}

    got = diff(tmp_path, scenario)
    # d1 and d3 match; only d3... both already say "confirmed", so status is unchanged
    # and `audited` is what makes them modified.
    assert got["counts"] == [2, 2] and got["audited"] == ["d1", "d3"]


def test_delete_one_and_delete_many(tmp_path):
    async def scenario(db):
        await _seed(db)
        one = await db.inventory.delete_one({"property_id": "p1", "category": "rum"})
        gone = await db.inventory.delete_one({"id": "nothing-here"})
        many = await db.inventory.delete_many({"property_id": "p1"})
        return {"one": one.deleted_count, "gone": gone.deleted_count,
                "many": many.deleted_count,
                "left": await _ids(db.inventory.find({}))}

    got = diff(tmp_path, scenario)
    assert got["one"] == 1 and got["gone"] == 0 and got["many"] == 3
    assert got["left"] == ["b1"]  # the other property is untouched


# -------------------------------- the pipeline --------------------------------
def test_aggregate_match_is_the_only_stage(tmp_path):
    async def scenario(db):
        await _seed(db)
        rows = await db.inventory.aggregate([
            {"$match": {"property_id": "p1"}},
            {"$match": {"stock": {"$lte": 5.0}}},
        ]).to_list(100)
        return sorted(r["id"] for r in rows)

    assert diff(tmp_path, scenario) == ["a1", "a3", "a4"]


def test_aggregate_refuses_a_stage_it_cannot_run(tmp_path):
    """Silently ignoring `$group` would make a report add up locally and differently in
    production. mock_db raises; so must the adapter."""
    async def scenario(db):
        try:
            await db.inventory.aggregate([{"$group": {"_id": "$category"}}]).to_list(10)
            return "no error"
        except ValueError as exc:
            return "ValueError"

    assert diff(tmp_path, scenario) == "ValueError"


def test_an_unknown_operator_raises_rather_than_matching_everything(tmp_path):
    async def scenario(db):
        await _seed(db)
        try:
            await db.inventory.find({"stock": {"$mod": [2, 0]}}).to_list(10)
            return "no error"
        except ValueError:
            return "ValueError"

    assert diff(tmp_path, scenario) == "ValueError"


# --------------------------------- the indexes --------------------------------
def test_create_index_is_a_no_op_on_both(tmp_path):
    """Firestore has no unique indexes at all, so every uniqueness guarantee the app
    declares is enforced by the routers' own pre-checks — exactly as it already is under
    mock_db. This test is here so that stays a decision rather than a discovery."""
    async def scenario(db):
        await db.users.create_index("email", unique=True)
        await db.guests.create_index([("property_id", 1), ("phone", 1)], unique=True)
        await db.rate_limit_hits.create_index("expires_at", expireAfterSeconds=86_400)
        await db.folio_entries.create_index(
            [("property_id", 1), ("folio_id", 1), ("charge_date", 1)],
            unique=True, partialFilterExpression={"kind": "room_night"})
        await db.users.insert_many([
            {"id": "u1", "email": "same@barflow.io"},
            {"id": "u2", "email": "same@barflow.io"},
        ])
        return await db.users.count_documents({"email": "same@barflow.io"})

    assert diff(tmp_path, scenario) == 2
