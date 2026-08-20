"""The whole database, unscoped. Falls back to a JSON-file mock when MONGO_URL is unset.

The handle is called `unscoped_db` rather than `db` because it can see every hotel's
rows at once. Routers do not use it: they take a property-scoped handle from a
dependency (see scoped_db.py), so the tenant filter is not theirs to forget. What is
left here is the work that genuinely has no tenant — seeding, the startup migrations,
logging in, and looking a property up in order to scope something else — and the name is
what makes each of those visible on sight in review. `grep -rn unscoped_db backend/` is
the whole audit.
"""
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient

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

mongo_url = os.environ.get('MONGO_URL', 'mock')
using_mock = (
    not mongo_url or mongo_url.startswith('mock') or mongo_url.startswith('local')
)

if using_mock:
    logger.info("Using local JSON file-based database mock...")
    from mock_db import MockMongoClient
    client = MockMongoClient(None)
    unscoped_db = client[None]
else:
    logger.info("Connecting to remote MongoDB client...")
    client = AsyncIOMotorClient(
        mongo_url,
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
        connectTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
    )
    unscoped_db = client[os.environ.get('DB_NAME', 'barflow')]


async def check_connection() -> None:
    """Ping the database so a misconfiguration surfaces as a named error.

    Raises with an actionable message. The two causes that actually happen in
    deployment are an Atlas IP allowlist that does not include the host, and a
    password that was left as the <db_password> placeholder or contains characters
    that need percent-encoding.
    """
    if using_mock:
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
