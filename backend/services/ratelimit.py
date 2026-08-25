"""Fixed-window rate limiting, per key, shared by every worker.

Used by the three unauthenticated doors — signing a hotel up, logging in, and placing a
QR order — because a second implementation of this is how two of them end up with
different behaviour and only one of them gets fixed.

**The counters live in the database, not in this process.** They used to be a
module-level dict, which is per *process*: the platform runs more than one uvicorn
worker, so the effective limit was `limit x workers`. Two workers alternating on one key
admitted twenty requests against a limit of ten, and the login door's fifty-failures-per-
address was really a hundred and fifty behind three workers. Nothing about that code was
wrong for a single process; there simply was never only one.

They are reached through `unscoped_db` and deliberately *not* through the property-scoped
handle. A rate limit is not a tenant's data — the address being throttled has no hotel,
and the whole point is that one caller cannot earn a fresh allowance by arriving at a
different one.

Fixed window rather than a token bucket or a sliding log with decay: the window is short,
the limits are far above real use, and the failure mode of a fixed window (a burst
straddling two windows can reach 2x the limit) does not matter at these magnitudes.
"""
import logging
import time
from typing import Optional

# The module, not the handle: `from db import unscoped_db` binds whatever existed at
# import time, so a test that swaps the database would still be answered by the real one
# — and a rate limiter that quietly counts against the wrong store is one that looks
# tested and is not. orders.py and scoped_db.py reach it the same way, for the same reason.
import db as _db_module
from db import using_mock

logger = logging.getLogger(__name__)

COLLECTION = "rate_limit_hits"

# One prune in roughly this many admissions. Against real MongoDB the TTL index below
# does the work; against the JSON mock `create_index` is a no-op, so something has to
# sweep or the file grows forever. Doing it on a fraction of calls keeps the cost off
# the hot path without needing a background task.
_PRUNE_EVERY = 200
_since_prune = 0

_index_ready = False


async def _ensure_index() -> None:
    """Ask for a TTL index once. Harmless to repeat; a no-op against the mock."""
    global _index_ready
    if _index_ready:
        return
    _index_ready = True
    try:
        await _db_module.unscoped_db[COLLECTION].create_index("key")
        # Expiry is what stops this collection growing without bound on real MongoDB.
        # The longest window in use is an hour; a day of slack costs nothing and means
        # changing a window never silently outlives its own index.
        await _db_module.unscoped_db[COLLECTION].create_index("expires_at", expireAfterSeconds=86_400)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rate-limit index not created: %s", exc)


class RateLimiter:
    """How many times one key may do something inside one window.

    A key is whatever the caller decides identifies the source: an IP, an IP and the
    table it is ordering against, an email address. The class does not care, which is
    why the same one serves all three doors.

    `name` separates one limiter's counters from another's in the shared collection, so
    the login door and the signup door do not consume each other's allowance.
    """

    def __init__(self, limit: int, window_seconds: int, name: str = ""):
        self.limit = limit
        self.window_seconds = window_seconds
        self.name = name or f"limit{limit}w{window_seconds}"

    def _scoped(self, key: str) -> str:
        return f"{self.name}|{key}"

    async def limited(self, key: str, now: Optional[float] = None) -> bool:
        """True when this key has already had its allowance, and record it when it has not.

        The everyday form: one call, asked before doing the thing. Recording only on
        admission is deliberate — a caller that is already being refused does not extend
        its own penalty by keeping on trying, so the block always lifts one window after
        the last *allowed* attempt rather than after the last attempt.
        """
        now = time.time() if now is None else now
        if await self.blocked(key, now):
            return True
        await self.record(key, now)
        return False

    async def blocked(self, key: str, now: Optional[float] = None) -> bool:
        """Whether this key is over its limit, counting nothing.

        Separate from `limited` for the login door, which counts *failures* rather than
        attempts: it has to ask before checking the password and count afterwards, and
        only when the answer was no.

        **A database failure opens the gate rather than closing it.** Every caller of
        this needs the database for the work it is guarding — login reads the user,
        signup writes a property, the QR route reads the table — so a failure here means
        the request was going to fail anyway a line later, with its own honest error.
        Refusing instead would convert a transient database blip into a platform-wide
        lockout of every hotel, which is a far worse outage than briefly unmetered
        attempts against a database that is not answering.
        """
        now = time.time() if now is None else now
        await _ensure_index()
        try:
            return await _db_module.unscoped_db[COLLECTION].count_documents({
                "key": self._scoped(key),
                "at": {"$gte": now - self.window_seconds},
            }) >= self.limit
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rate-limit read failed, allowing: %s", exc)
            return False

    async def record(self, key: str, now: Optional[float] = None) -> None:
        """Count one against this key."""
        global _since_prune
        now = time.time() if now is None else now
        try:
            hit = {"key": self._scoped(key), "at": now}
            if not using_mock:
                # A real datetime, and only against real MongoDB: the TTL index expires
                # on this field's value and would ignore the float above. The JSON mock
                # has no TTL index and cannot serialise a datetime, so adding it there
                # would write a row that fails silently down the fail-open path — which
                # is how this was nearly shipped counting nothing at all.
                hit["expires_at"] = _as_datetime(now + self.window_seconds)
            await _db_module.unscoped_db[COLLECTION].insert_one(hit)
            _since_prune += 1
            if _since_prune >= _PRUNE_EVERY:
                _since_prune = 0
                await _db_module.unscoped_db[COLLECTION].delete_many(
                    {"at": {"$lt": now - self.window_seconds}})
        except Exception as exc:  # noqa: BLE001
            # Same reasoning as `blocked`: a counter that cannot be written is not worth
            # refusing a request over.
            logger.warning("Rate-limit write failed, not counted: %s", exc)

    async def forget(self, key: str) -> None:
        """Clear one key's history.

        Used by the login limiter: a correct password means this address is not the
        attacker the counter was accumulating against, so their next typo starts from
        zero rather than from wherever a shared office IP had got to.
        """
        try:
            await _db_module.unscoped_db[COLLECTION].delete_many({"key": self._scoped(key)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rate-limit clear failed: %s", exc)

    async def reset(self) -> None:
        """Forget everything under this limiter's name. For tests, and nothing else."""
        rows = await _db_module.unscoped_db[COLLECTION].find({}, {"_id": 0}).to_list(100_000)
        prefix = f"{self.name}|"
        for row in rows:
            if str(row.get("key", "")).startswith(prefix):
                await _db_module.unscoped_db[COLLECTION].delete_many({"key": row["key"]})


def _as_datetime(stamp: float):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


# How many proxies sit in front of this app. `X-Forwarded-For` is a list the client
# starts and each proxy appends to, so the entries a client can forge are on the LEFT and
# the ones a proxy wrote are on the RIGHT. Counting from the right by the number of hops
# we actually have is what makes the address unforgeable.
#
#   1  direct to Render (the default, and correct for render.yaml as it stands)
#   2  Cloudflare or another CDN in front of Render
#   0  no proxy at all — ignore the header entirely and use the socket
#
# Too high and every client looks like the same forged address; too low and the client
# picks its own. One is right for the deployment this repo describes.
import os  # noqa: E402  — read at import, beside the constant it configures

TRUSTED_PROXY_HOPS = max(0, int(os.environ.get("TRUSTED_PROXY_HOPS", "1") or 1))


def client_ip(request, hops: Optional[int] = None) -> str:
    """The address a request came from, or "unknown".

    "unknown" is one bucket that every unattributable request shares, which is the safe
    way round: they throttle each other rather than each getting a fresh allowance.

    The socket address is not used when a proxy is configured, and the reason is worth
    stating because it looks backwards. This app deploys behind Render's proxy, so the
    socket is the *proxy's* address and identical for every client on earth. Bucketing on
    it would not make the limits strict, it would make them a platform-wide outage
    switch: one stranger tripping the login limit would lock out every hotel.

    So the header is used — but only the part of it a proxy wrote. With one trusted hop
    the rightmost entry is the address Render observed, which the client cannot set; the
    entries to its left are whatever the client sent and are ignored.
    """
    if request is None:
        return "unknown"
    hops = TRUSTED_PROXY_HOPS if hops is None else hops
    if hops > 0:
        forwarded = (getattr(request, "headers", None) or {}).get("x-forwarded-for", "")
        parts = [p.strip() for p in str(forwarded).split(",") if p.strip()]
        if parts:
            # Count from the right. If the header is shorter than the configured hops,
            # the leftmost entry is the furthest back we can see — still better than the
            # socket, which is the proxy for everyone.
            index = max(0, len(parts) - hops)
            return parts[index]
    client = getattr(request, "client", None)
    return (getattr(client, "host", "") or "") or "unknown"
