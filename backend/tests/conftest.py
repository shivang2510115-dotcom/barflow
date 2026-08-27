"""Run this whole suite against Firestore without editing a line of it.

    firebase emulators:start --only firestore --project demo-barflow
    DB_BACKEND=firestore FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
      JWT_SECRET=... ADMIN_PASSWORD=... CORS_ORIGINS=http://localhost:3000 \
      python3 -m pytest tests/ --ignore=tests/backend_test.py \
                              --ignore=tests/hotel_api_test.py -q

Inert otherwise. With `DB_BACKEND` unset or `mock` this file does nothing at all, and
the mock run is the run it has always been — same 445 tests, same fixtures, same counts.

**Why this is the switch.** These tests build their world with
`MockDatabase(str(tmp_path / "db.json"))` — a real, file-backed database, deliberately,
because a hand-written stub of the database is a stub of the bug too. That makes the
constructor the one place the whole suite agrees on, so pointing it at Firestore points
all 445 tests at Firestore. The alternative was a parametrised fixture threaded through
every test module, which is 20 files edited to prove that an adapter behind an interface
still satisfies the interface.

`tmp_path` is what isolates one test's data from another's under the mock — a fresh
directory each time. The Firestore stand-in reproduces that with a collection-name
prefix derived from the same path, plus a per-process salt so that two runs against one
long-lived emulator do not inherit each other's rows. Same isolation, same granularity,
and a test asking for its handle twice still gets the same data both times.

The test suite is the proof for this adapter. It already covers tenant isolation, the
money paths and the access boundary against the mock; if it says the same things against
Firestore, the translation is right.
"""
import hashlib
import os
import uuid

# One salt per worker process. Without it, `tmp_path` for a given test is the same string
# on every run (pytest numbers directories per test, not per run), so a second run
# against a still-running emulator would read the first run's documents and fail in a way
# that looks like a bug in the adapter.
_SALT = uuid.uuid4().hex[:8]


def _namespace_for(path: str) -> str:
    return f"t{_SALT}{hashlib.sha1(str(path).encode()).hexdigest()[:12]}"


if os.environ.get("DB_BACKEND", "").strip().lower() in ("firestore", "firebase"):
    import mock_db
    from firestore_db import FirestoreDatabase

    def _firestore_stand_in(filepath):
        """`MockDatabase(path)`, answered by Firestore.

        The path is not opened; it is only hashed, to give this handle the same identity
        the file gave it. Two calls with one path are one database, two paths are two.
        """
        return FirestoreDatabase(namespace=_namespace_for(filepath))

    # Rebinding the name in `mock_db` rather than in each test module, because the tests
    # do `from mock_db import MockDatabase` at import time and conftest is imported
    # first. Nothing in the suite is edited and nothing in it can tell.
    mock_db.MockDatabase = _firestore_stand_in
