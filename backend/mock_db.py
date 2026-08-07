import json
import os
import re
import asyncio

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
            else:
                return False
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

    async def create_index(self, keys, unique=False):
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

    async def update_one(self, filter_query, update_query):
        items = self._get_items()
        matched_count = 0
        modified_count = 0
        for item in items:
            if self._match(item, filter_query):
                matched_count = 1
                if "$set" in update_query:
                    changed = any(item.get(uk) != uv for uk, uv in update_query["$set"].items())
                    for uk, uv in update_query["$set"].items():
                        item[uk] = uv
                    if changed:
                        modified_count = 1
                break
        if modified_count > 0:
            self._save()
        class UpdateResult:
            def __init__(self, matched, modified):
                self.matched_count = matched
                self.modified_count = modified
        return UpdateResult(matched_count, modified_count)

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
