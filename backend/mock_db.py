import json
import operator
import os
import re
import asyncio

_COMPARISON_OPS = {
    "$gt": operator.gt,
    "$gte": operator.ge,
    "$lt": operator.lt,
    "$lte": operator.le,
}

class MockCursor:
    def __init__(self, items):
        self.items = items

    def sort(self, key, direction=1):
        reverse = (direction == -1)
        self.items.sort(key=lambda x: x.get(key) if x.get(key) is not None else "", reverse=reverse)
        return self

    def limit(self, count):
        self.items = self.items[:count]
        return self

    async def to_list(self, length=None):
        if length is not None:
            return self.items[:length]
        return self.items

class MockCollection:
    def __init__(self, name, db_instance):
        self.name = name
        self.db = db_instance

    def _get_items(self):
        if self.name not in self.db.data:
            self.db.data[self.name] = []
        return self.db.data[self.name]

    def _save(self):
        self.db.save()

    def _field_matches(self, field_val, condition):
        """Handle a Mongo-style operator dict for a single field, e.g. {"$ne": x}."""
        for op, opval in condition.items():
            if op == "$ne":
                if field_val == opval:
                    return False
            elif op == "$in":
                if field_val not in opval:
                    return False
            elif op == "$nin":
                if field_val in opval:
                    return False
            elif op == "$exists":
                if (field_val is not None) != bool(opval):
                    return False
            elif op == "$regex":
                flags = re.IGNORECASE if condition.get("$options") == "i" else 0
                if not re.search(opval, field_val if isinstance(field_val, str) else "", flags):
                    return False
            elif op == "$options":
                continue  # handled alongside $regex
            elif op in _COMPARISON_OPS:
                # Values here are usually YYYY-MM-DD strings, but keep this generic —
                # Python's ordering operators work for strings and numbers alike. A
                # document missing the field (None) or holding an incomparable type
                # must simply not match rather than raise.
                try:
                    if not _COMPARISON_OPS[op](field_val, opval):
                        return False
                except TypeError:
                    return False
            else:
                raise ValueError(f"mock_db: unsupported operator {op}")
        return True

    def _match(self, doc, filter_query):
        if not filter_query:
            return True
        for k, v in filter_query.items():
            if k == "$or":
                if not any(self._match(doc, sub) for sub in v):
                    return False
            elif k == "$expr":
                if "$lte" in v:
                    left, right = v["$lte"]
                    left_val = doc.get(left.lstrip("$")) if isinstance(left, str) and left.startswith("$") else left
                    right_val = doc.get(right.lstrip("$")) if isinstance(right, str) and right.startswith("$") else right
                    try:
                        if not (left_val <= right_val):
                            return False
                    except Exception:
                        return False
            elif isinstance(v, dict) and v and all(str(op).startswith("$") for op in v):
                if not self._field_matches(doc.get(k), v):
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    async def create_index(self, keys, unique=False, sparse=False,
                           partialFilterExpression=None, expireAfterSeconds=None):
        """A no-op, deliberately — and `expireAfterSeconds` is the one worth naming.

        Every caller has to work without the index it just asked for, because this does
        nothing. Uniqueness is enforced by the routers' own duplicate checks; expiry is
        the interesting one, since a TTL index is the only thing that removes a document
        nobody will ever read again. Anything relying on it must also prune by hand — see
        services/ratelimit.py, which does.
        """
        pass

    async def find_one(self, filter_query, projection=None):
        items = self._get_items()
        for item in items:
            if self._match(item, filter_query):
                res = dict(item)
                if projection:
                    for pk in list(res.keys()):
                        if pk in projection and projection[pk] == 0:
                            res.pop(pk, None)
                return res
        return None

    def find(self, filter_query=None, projection=None):
        items = self._get_items()
        matched = []
        for item in items:
            if self._match(item, filter_query):
                res = dict(item)
                if projection:
                    for pk in list(res.keys()):
                        if pk in projection and projection[pk] == 0:
                            res.pop(pk, None)
                matched.append(res)
        return MockCursor(matched)

    async def insert_one(self, doc):
        items = self._get_items()
        item_copy = dict(doc)
        items.append(item_copy)
        self._save()
        class InsertResult:
            def __init__(self, inserted_id):
                self.inserted_id = inserted_id
        return InsertResult(item_copy.get("id"))

    async def insert_many(self, docs):
        items = self._get_items()
        docs_copy = [dict(d) for d in docs]
        items.extend(docs_copy)
        self._save()
        class InsertManyResult:
            def __init__(self, inserted_ids):
                self.inserted_ids = inserted_ids
        return InsertManyResult([d.get("id") for d in docs_copy])

    def _apply_update(self, item, update_query):
        """Apply $set/$push/$inc to one document. Returns whether anything changed.

        Unknown operators raise, for the reason `_field_matches` gives about query
        operators: silently ignoring `$inc` would make a counter that never counts, and
        the code depending on it would pass its tests locally and be wrong against real
        MongoDB. Loud beats plausible.
        """
        modified = False
        for op, fields in update_query.items():
            if op == "$set":
                changed = any(item.get(uk) != uv for uk, uv in fields.items())
                for uk, uv in fields.items():
                    item[uk] = uv
                modified = modified or changed
            elif op == "$push":
                for uk, uv in fields.items():
                    item.setdefault(uk, [])
                    item[uk].append(uv)
                modified = True
            elif op == "$inc":
                # Mongo treats a missing field as zero and creates it, which is what
                # makes `$inc` with an upsert a whole counter in one round trip.
                for uk, uv in fields.items():
                    item[uk] = (item.get(uk) or 0) + uv
                modified = True
            else:
                raise ValueError(f"mock_db: unsupported update operator {op}")
        return modified

    async def update_one(self, filter_query, update_query, upsert=False):
        """`upsert` included because a counter needs it: increment-or-create is one
        atomic call against real MongoDB, and splitting it into find-then-insert here
        would test a different algorithm from the one that ships.

        The created document is Mongo's: the filter's plain equality terms, then the
        update applied on top. Operator terms in the filter (`{"$lt": ...}`) contribute
        nothing to it, exactly as Mongo does.
        """
        items = self._get_items()
        matched_count = 0
        modified_count = 0
        upserted_id = None
        for item in items:
            if self._match(item, filter_query):
                matched_count = 1
                if self._apply_update(item, update_query):
                    modified_count = 1
                break
        if matched_count == 0 and upsert:
            created = {k: v for k, v in (filter_query or {}).items()
                       if not (isinstance(v, dict) and any(str(o).startswith("$")
                                                           for o in v))}
            self._apply_update(created, update_query)
            items.append(created)
            upserted_id = created.get("id")
            self._save()
        elif modified_count > 0:
            self._save()
        class UpdateResult:
            def __init__(self, matched, modified, upserted):
                self.matched_count = matched
                self.modified_count = modified
                self.upserted_id = upserted
        return UpdateResult(matched_count, modified_count, upserted_id)

    async def update_many(self, filter_query, update_query):
        """Every match, not just the first. Same update grammar as update_one — which is
        why the two share `_apply_update` rather than each growing their own copy of it.
        """
        items = self._get_items()
        matched_count = modified_count = 0
        for item in items:
            if self._match(item, filter_query):
                matched_count += 1
                if self._apply_update(item, update_query):
                    modified_count += 1
        if modified_count > 0:
            self._save()
        class UpdateResult:
            def __init__(self, matched, modified):
                self.matched_count = matched
                self.modified_count = modified
        return UpdateResult(matched_count, modified_count)

    def aggregate(self, pipeline, *args, **kwargs):
        """Only `$match`, and loudly nothing else.

        The scoped database handle prepends a `$match` on property_id to every pipeline,
        so this exists to keep that path working against the mock. A `$group` or
        `$lookup` silently ignored here would make a report add up locally and differently
        against real MongoDB, which is exactly the class of bug the operator check in
        `_field_matches` refuses to introduce. Returns a cursor, like Motor does.
        """
        items = [dict(item) for item in self._get_items()]
        for stage in pipeline or []:
            for op, spec in stage.items():
                if op != "$match":
                    raise ValueError(f"mock_db: unsupported aggregation stage {op}")
                items = [item for item in items if self._match(item, spec)]
        return MockCursor(items)

    async def delete_one(self, filter_query):
        items = self._get_items()
        deleted_count = 0
        for idx, item in enumerate(items):
            if self._match(item, filter_query):
                items.pop(idx)
                deleted_count = 1
                break
        if deleted_count > 0:
            self._save()
        class DeleteResult:
            def __init__(self, count):
                self.deleted_count = count
        return DeleteResult(deleted_count)

    async def delete_many(self, filter_query):
        items = self._get_items()
        kept = [item for item in items if not self._match(item, filter_query)]
        deleted_count = len(items) - len(kept)
        if deleted_count > 0:
            items[:] = kept
            self._save()
        class DeleteResult:
            def __init__(self, count):
                self.deleted_count = count
        return DeleteResult(deleted_count)

    async def count_documents(self, filter_query):
        items = self._get_items()
        count = 0
        for item in items:
            if self._match(item, filter_query):
                count += 1
        return count

class MockDatabase:
    def __init__(self, filepath):
        self.filepath = filepath
        self.data = {}
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        else:
            self.data = {}

    def save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.data, f, indent=2)

    def __getattr__(self, name):
        return MockCollection(name, self)

    def __getitem__(self, name):
        return MockCollection(name, self)

class MockMongoClient:
    def __init__(self, uri):
        self.db = MockDatabase(os.path.join(os.path.dirname(__file__), "db.json"))

    def __getitem__(self, name):
        return self.db

    def close(self):
        pass
