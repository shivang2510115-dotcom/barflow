"""The whole database, unscoped. One of three backends, chosen here and nowhere else.

The handle is called `unscoped_db` rather than `db` because it can see every hotel's
rows at once. Routers do not use it: they take a property-scoped handle from a
dependency (see scoped_db.py), so the tenant filter is not theirs to forget. What is
left here is the work that genuinely has no tenant — seeding, the startup migrations,
logging in, and looking a property up in order to scope something else — and the name is
what makes each of those visible on sight in review. `grep -rn unscoped_db backend/` is
the whole audit.

Three backends now, and one variable decides:

    DB_BACKEND=mock        the JSON file in backend/db.json         (the default)
    DB_BACKEND=firestore   Firestore, in the Firebase project        (what deploys)
    DB_BACKEND=mongo       MongoDB, at MONGO_URL

`MONGO_URL` still decides on its own when `DB_BACKEND` is unset, exactly as it did
before this file grew a third option: `mock`/`local`/unset gives the JSON mock and
anything else is an Atlas URL. A clone that has never heard of `DB_BACKEND` behaves
identically, which is the point — every existing .env, every existing test command and
every local checkout keeps working without being edited.
"""
import os
import logging

logger = logging.getLogger(__name__)

# Fail fast on an unreachable database rather than hanging.
#
# Startup runs seed_data(), which issues many queries. With the driver's default
# selection timeout each one waits 30s, so a bad connection string or a closed Atlas
# IP allowlist makes the app appear to hang forever: the health check never passes,
# the platform reports nothing useful, and the actual cause is invisible. Ten seconds
# is long enough for a cold Atlas cluster and short enough that the error reaches the
# logs before a deploy is marked failed.
SERVER_SELECTION_TIMEOUT_MS = 10_000

MOCK = "mock"
MONGO = "mongo"
FIRESTORE = "firestore"

mongo_url = os.environ.get('MONGO_URL', 'mock')


def _chosen_backend() -> str:
    """Which of the three, from the environment.

    `DB_BACKEND` is explicit and wins. Without it the answer comes from `MONGO_URL` the
    way it always has, so nothing that predates this function changes behaviour.

    An unrecognised `DB_BACKEND` raises rather than falling back to the mock. The mock
    writes to a JSON file beside the source and answers every query cheerfully, so a
    deployment that typo'd `firestoer` would start, serve, and take real bookings into a
    file on an ephemeral container disk. There is no safe default for this question.
    """
    declared = os.environ.get("DB_BACKEND", "").strip().lower()
    if not declared:
        if not mongo_url or mongo_url.startswith('mock') or mongo_url.startswith('local'):
            return MOCK
        return MONGO
    if declared in ("mock", "local", "json"):
        return MOCK
    if declared in ("firestore", "firebase"):
        return FIRESTORE
    if declared in ("mongo", "mongodb", "atlas", "motor"):
        return MONGO
    raise RuntimeError(
        f"DB_BACKEND is {declared!r}, which is not a database this application has. "
        f"Use one of: mock (a JSON file, for local development), firestore (the "
        f"Firebase project's database, which is what deploys), mongo (MongoDB at "
        f"MONGO_URL)."
    )


backend = _chosen_backend()

# The switch the rest of the application already reads. It asks one question — "is this
# a laptop or is this real?" — and the answer for Firestore is the same as for Atlas:
# real. So `JWT_SECRET` and `ADMIN_PASSWORD` still refuse to be the values published in
# this repository, `CORS_ORIGINS` is still required, and the rate limiter still writes
# the expiry timestamp that only a real database has a use for.
using_mock = (backend == MOCK)

if backend == MOCK:
    logger.info("Using local JSON file-based database mock...")
    from mock_db import MockMongoClient
    client = MockMongoClient(None)
    unscoped_db = client[None]
elif backend == FIRESTORE:
    logger.info("Using Firestore...")
    from firestore_db import FirestoreClient
    from firestore_db import check_connection as _check_firestore
    client = FirestoreClient()
    unscoped_db = client[None]
else:
    logger.info("Connecting to remote MongoDB client...")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(
        mongo_url,
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
        connectTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
    )
    unscoped_db = client[os.environ.get('DB_NAME', 'barflow')]


async def check_connection() -> None:
    """Ping the database so a misconfiguration surfaces as a named error.

    Raises with an actionable message, and the actionable part is different for each
    backend — see firestore_db.check_connection for Firestore's. The two causes that
    actually happen with Atlas are an IP allowlist that does not include the host, and a
    password left as the <db_password> placeholder or containing characters that need
    percent-encoding.
    """
    if using_mock:
        return
    if backend == FIRESTORE:
        await _check_firestore()
        return
    try:
        await client.admin.command("ping")
        logger.info("MongoDB connection OK.")
    except Exception as exc:  # noqa: BLE001 — surface anything the driver raises
        logger.error(
            "MongoDB unreachable: %s\n"
            "  Check, in this order:\n"
            "   1. Atlas -> Network Access allows 0.0.0.0/0 "
            "(a host's outbound IP is usually not fixed)\n"
            "   2. the password in MONGO_URL is the real one, not <db_password>, "
            "and any @ / : # ? in it are percent-encoded\n"
            "   3. the cluster is running and not paused",
            exc,
        )
        raise
