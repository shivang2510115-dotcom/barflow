"""Fixed-window rate limiting, per key, in this process.

Extracted from `routers/signup.py`, which had the only copy. It is now shared by the
three unauthenticated doors — signing a hotel up, logging in, and placing a QR order —
because a second implementation of this is how two of them end up with different
behaviour and only one of them gets fixed.

**In-process, so it does not survive more than one worker.** Each worker keeps its own
counters and the effective limit multiplies by the worker count. That is a real
limitation rather than a rounding error, and it is stated here once rather than in each
caller: a shared store (Redis, or a collection with a TTL index) is what this needs
before the platform runs more than one process. It still does the job it is here for —
an attacker on one connection is throttled by whichever worker answers them, and the
numbers below are chosen with the multiplier in mind.

Fixed window rather than a token bucket or a sliding log with decay: the window is short,
the limits are far above real use, and the failure mode of a fixed window (a burst
straddling two windows can reach 2x the limit) does not matter at these magnitudes.
"""
import time
from typing import Optional

# Above this many tracked keys, expired entries are swept before the next admission.
# Without it a stream of requests from a botnet grows the dict forever — every address
# ever seen, kept for as long as the process lives.
_SWEEP_ABOVE_KEYS = 10_000


class RateLimiter:
    """How many times one key may do something inside one window.

    A key is whatever the caller decides identifies the source: an IP, an IP and the
    table it is ordering against, an email address. The class does not care, which is
    why the same one serves all three doors.
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}

    def limited(self, key: str, now: Optional[float] = None) -> bool:
        """True when this key has already had its allowance, and record it when it has not.

        Recording only on admission is deliberate: a caller that is already being refused
        does not extend its own penalty by keeping on trying, so the block always lifts
        one window after the last *allowed* attempt rather than after the last attempt.
        """
        now = time.time() if now is None else now
        if len(self._hits) > _SWEEP_ABOVE_KEYS:
            self._sweep(now)
        recent = [t for t in self._hits.get(key, []) if now - t < self.window_seconds]
        self._hits[key] = recent
        if len(recent) >= self.limit:
            return True
        recent.append(now)
        return False

    def forget(self, key: str) -> None:
        """Clear one key's history.

        Used by the login limiter: a correct password means this address is not the
        attacker the counter was accumulating against, so their next typo starts from
        zero rather than from wherever a shared office IP had got to.
        """
        self._hits.pop(key, None)

    def reset(self) -> None:
        """Forget everything. For tests, and for nothing else."""
        self._hits.clear()

    def _sweep(self, now: float) -> None:
        self._hits = {
            key: hits for key, hits in self._hits.items()
            if any(now - t < self.window_seconds for t in hits)
        }


def client_ip(request) -> str:
    """The address a request came from, or "unknown".

    "unknown" is one bucket that every unattributable request shares, which is the safe
    way round: they throttle each other rather than each getting a fresh allowance.

    `X-Forwarded-For`'s leftmost entry is preferred over the socket, and the trade-off is
    worth stating because it goes the way that looks wrong. This app is deployed behind
    Render's proxy (see render.yaml), so the socket address is the *proxy's* and is the
    same for every client on earth. Bucketing on it would not make the limits strict, it
    would make them a platform-wide outage switch: one stranger tripping the login limit
    would lock every hotel on the platform out for the length of the window.

    The header is attacker-settable, so an attacker can give themselves a fresh
    allowance by changing one string. That is why the login door does not rely on this
    alone — it also counts failures per email address, which no header can move. Read
    them together: the IP limit is what stops one client spraying many accounts, and the
    email limit is what stops many clients guessing at one account.
    """
    if request is None:
        return "unknown"
    forwarded = (getattr(request, "headers", None) or {}).get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return (getattr(client, "host", "") or "") or "unknown"
