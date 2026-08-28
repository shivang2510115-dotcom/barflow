"""BarFlow on Firebase Functions (2nd gen): the same FastAPI app, and the scheduled work.

Three functions are defined here and nothing else:

* `api` serves `backend/server.py::app` behind Firebase Hosting's `/api/**` rewrite, so
  the browser still sees one origin and CORS is still not load-bearing;
* `daily_brief` is woken by Cloud Scheduler at OWNER_BRIEF_TIME and sends the owner
  brief, which under Functions the in-process loop cannot — see `_brief_cron` below;
* `customer_follow_ups` is woken at CUSTOMER_FOLLOW_UP_TIME and messages the customers a
  property has not seen inside its own window. Same mechanism, deliberately: a second one
  would be a second place for "did it run last night" to be answered differently.

The app itself is not forked. `backend/` is the only copy of the routers there has ever
been, and it is placed on `sys.path` here rather than reimplemented; `_backend_dir()`
says how it gets into the deploy bundle.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("barflow.functions")


def _backend_dir() -> Path:
    """Where `server.py` lives, for this process.

    The Firebase CLI uploads the *functions source directory* and nothing above it, so
    `backend/` — which sits beside it, not inside it — is not in the bundle by itself.
    Three ways to fix that were available and only one keeps a single copy of the code:

    * a package install (`pip install ../backend`) cannot work: requirements are resolved
      remotely, from the uploaded bundle, where `../backend` does not exist. Publishing
      the backend to an index instead would make every API change a release;
    * a `sys.path` entry alone cannot work either, for the same reason — a path can only
      point at files that were uploaded;
    * so: a predeploy step copies `backend/` to `functions/backend/` (see
      `vendor-backend.sh`), which *is* inside the source directory and is uploaded with
      it. That copy is generated, gitignored and rewritten from scratch on every deploy,
      so it cannot drift: `backend/` remains the only version under review, and the only
      one anybody edits.

    Locally — the emulator, a test, or the CLI's own manifest discovery, which imports
    this file on the developer's machine — the vendored copy usually does not exist yet,
    so the repository's own `backend/` is used directly. Same files either way.
    """
    vendored = _HERE / "backend"
    if (vendored / "server.py").is_file():
        return vendored
    repo = _HERE.parent / "backend"
    if (repo / "server.py").is_file():
        return repo
    raise RuntimeError(
        f"Neither {vendored}/server.py nor {repo}/server.py exists. The deploy bundle is "
        f"built by functions/vendor-backend.sh, which firebase.json runs as a predeploy "
        f"hook; run it by hand if you are invoking the runtime some other way."
    )


BACKEND_DIR = _backend_dir()
# Ahead of everything else on the path, and as a string because that is what the app's own
# flat imports (`from db import ...`, `from routers import ...`) expect — the same shape
# `uvicorn server:app` gives them when it is started from inside backend/.
sys.path.insert(0, str(BACKEND_DIR))

from a2wsgi import ASGIMiddleware  # noqa: E402
from firebase_functions import core, https_fn, options, scheduler_fn  # noqa: E402

import db  # noqa: E402  (backend/db.py)
import server  # noqa: E402  (backend/server.py — importing it builds the FastAPI app)
from routers import messaging, reports  # noqa: E402

# ---------------------------------------------------------------- deployment shape
# The region is in two files and they must agree: here, which is where the function is
# created, and firebase.json, which is where Hosting is told to look for it. A rewrite
# naming a region the function is not in answers 404, so changing one means changing the
# other. It is not read from the environment because firebase.json cannot read one.
REGION = "asia-south1"

# The API is public by design and does its own authentication — this *is* the login
# endpoint. `invoker="public"` is the Functions spelling of Cloud Run's
# --allow-unauthenticated and means exactly as much as it did there.
#
# max_instances is no longer 1. It was pinned there only because the nightly brief was an
# in-process loop and a second instance would have sent it twice; that is now a scheduled
# function that runs once wherever it runs. Rate limits live in the database and are
# already safe across instances.
#
# concurrency > 1 is deliberate and is what keeps the cold-start cost below rare: one
# instance serves a whole service period's front desk, so `_cold_start` runs once for all
# of them rather than once per request.
MAX_INSTANCES = int(os.environ.get("API_MAX_INSTANCES", "10"))


def _secrets() -> list[str]:
    """The environment variables that come from Secret Manager rather than functions/.env.

    Read from the environment (`functions/.env`, which the CLI loads before it asks this
    file what functions exist) rather than hard-coded, so that adding the WhatsApp or
    Stripe credentials later is `firebase functions:secrets:set WHATSAPP_TOKEN` plus one
    name in a config file, not an edit to this module.

    Every name listed here must already exist in Secret Manager or the deploy is refused,
    which is why the default is only the four the application genuinely cannot run
    without. deploy.sh creates exactly those.

    MONGO_URL was the fifth and is not one any more: the database is Firestore, in this
    same Firebase project, reached with the function's own service account. There is no
    connection string to keep. Leaving it in this default would refuse every deploy of a
    project that has correctly never created that secret.
    """
    default = "JWT_SECRET,ADMIN_PASSWORD,PLATFORM_ADMIN_PASSWORD,GUEST_ID_ENCRYPTION_KEY"
    return [name.strip() for name in os.environ.get("BARFLOW_SECRETS", default).split(",")
            if name.strip()]


# ---------------------------------------------------------------- the ASGI bridge
# Hop-by-hop headers describe one connection and must not be forwarded onto another;
# Content-Length is dropped because Werkzeug recomputes it from the body we hand back and
# a stale one would truncate the response.
_DROP_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "transfer-encoding", "upgrade", "content-length",
})

_bridge_lock = threading.Lock()
_bridge_state: tuple[int, ASGIMiddleware] | None = None


def _bridge() -> ASGIMiddleware:
    """The ASGI<->WSGI adapter, built lazily and rebuilt if this process was forked.

    `https_fn.on_request` hands over a WSGI-flavoured flask.Request; FastAPI speaks ASGI.
    a2wsgi's ASGIMiddleware is the adapter, and what makes it the right one is what it
    owns: a single event loop, running in a daemon thread, shared by every request and by
    the startup work below. motor requires that — a loop per request would hand the
    driver a closed loop to keep its connections in and fail the second request with
    "attached to a different loop".

    The PID check is not defensive habit; without it nothing here works in production.
    The Functions runtime serves Python through functions-framework, which runs the app
    under gunicorn: the framework imports this module in the arbiter and gunicorn then
    *forks* the worker that actually handles requests. Threads do not survive fork. A
    middleware built at import would leave every worker holding a loop whose thread only
    ever existed in its parent, and `run_coroutine_threadsafe` against it does not raise
    — it blocks for ever, so the symptom is a request that hangs until the platform times
    it out, with nothing in the log after "Cold start". Building on first use puts the
    loop in the process that will use it, and comparing PIDs is what makes "first use"
    mean first use *in this process*.
    """
    global _bridge_state
    pid = os.getpid()
    state = _bridge_state
    if state is not None and state[0] == pid:
        return state[1]
    with _bridge_lock:
        state = _bridge_state
        if state is None or state[0] != pid:
            state = (pid, ASGIMiddleware(server.app))
            _bridge_state = state
        return state[1]


def _run(coro):
    """Run a coroutine on the bridge's loop, from the request's thread, and wait."""
    return asyncio.run_coroutine_threadsafe(coro, _bridge().loop).result()


async def _lifespan_startup() -> None:
    """Drive the ASGI lifespan protocol far enough to fire the app's startup handlers.

    a2wsgi only ever sends `http` scopes, so `@app.on_event("startup")` would otherwise
    never run — and it is not optional work: it pings the database, seeds the admin
    account, the GST bands and the meal plans, and applies five migrations without which
    every pre-existing login reaches nothing.

    Driving the real protocol rather than calling `server.on_startup()` directly is the
    point. There is then still exactly one definition of what starting up means, and a
    handler added to the app later runs under Functions without anyone remembering to
    come back here.

    The task is kept referenced for the life of the instance: it is parked inside the
    app's lifespan, awaiting a shutdown message that a function instance is never
    given, and dropping the reference would let it be garbage-collected mid-flight.
    """
    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
    inbox: asyncio.Queue = asyncio.Queue()
    await inbox.put({"type": "lifespan.startup"})
    started = asyncio.get_running_loop().create_future()

    async def receive():
        return await inbox.get()

    async def send(message):
        kind = message.get("type")
        if started.done():
            return
        if kind == "lifespan.startup.complete":
            started.set_result(None)
        elif kind == "lifespan.startup.failed":
            started.set_exception(RuntimeError(
                message.get("message") or "the app refused to start"))

    task = asyncio.ensure_future(server.app(scope, receive, send))
    global _lifespan_task
    _lifespan_task = task
    # Whichever happens first. Starlette reports a failure both ways — the message and
    # then the exception — but an app that returned without saying anything at all would
    # leave `started` pending for ever, and a hung cold start is the worst of the three.
    await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
    if task.done() and not started.done():
        task.result()  # re-raises whatever the app raised
        raise RuntimeError("the app's lifespan ended without starting")
    await started


_lifespan_task: asyncio.Task | None = None


@core.init
def _cold_start() -> None:
    """Once per instance, before the first invocation it serves. Never per request.

    `core.init` is the Functions runtime's own answer to this, and it is a better one
    than doing the work at import: the Firebase CLI imports this module on the
    developer's machine to discover what functions exist, and startup at import would
    mean a deploy tried to reach Firestore — or, with no DB_BACKEND set, quietly seeded
    the local JSON mock — just to find out the name of an endpoint.

    A failure here leaves the runtime's `_did_init` flag unset, so the next invocation
    tries again rather than the instance serving 500s for ever from a half-seeded state.
    That is the same shape as a container that fails its health check and is restarted.
    """
    logger.info("Cold start: running application startup (%s).", BACKEND_DIR)
    _run(_lifespan_startup())
    logger.info("Cold start complete.")


# ---------------------------------------------------------------- the API
@https_fn.on_request(
    region=REGION,
    memory=options.MemoryOption.MB_512,
    timeout_sec=120,
    min_instances=0,
    max_instances=MAX_INSTANCES,
    cpu=1,
    concurrency=40,
    invoker="public",
    secrets=_secrets(),
)
def api(request: https_fn.Request) -> https_fn.Response:
    """Serve one HTTP request through the FastAPI app.

    Hosting rewrites `/api/**` here with the path intact, which is why nothing rewrites
    it again: the app's own router is mounted at `/api`, so the URL the browser asked for
    is the URL FastAPI matches.
    """
    captured: dict = {}

    def start_response(status: str, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    # `request.environ` is the untouched WSGI environment, so the request body, the query
    # string and every header the caller sent — Authorization included — reach the ASGI
    # scope as they arrived. a2wsgi reads the body from `wsgi.input` itself, and nothing
    # in the Functions wrapper has consumed it first.
    body = b"".join(_bridge()(request.environ, start_response))

    status = captured.get("status") or "500 Internal Server Error"
    headers = [(name, value) for name, value in captured.get("headers", ())
               if name.lower() not in _DROP_RESPONSE_HEADERS]
    # The numeric code, not the string: the app's 401s, 404s, 422s and 429s are the whole
    # contract of half its endpoints and must survive the trip unchanged.
    return https_fn.Response(body, status=int(status.split(" ", 1)[0]), headers=headers)


# ---------------------------------------------------------------- the nightly brief
def _brief_cron() -> str:
    """OWNER_BRIEF_TIME, as the daily cron expression Cloud Scheduler wants.

    Resolved when the CLI reads this file for the deploy manifest, so the value comes
    from `functions/.env` and changing it takes a redeploy — a schedule is part of the
    deployed shape of a function, not something an instance can decide at 23:00.
    """
    raw = (os.environ.get("OWNER_BRIEF_TIME") or "23:00").strip()
    try:
        hour, _, minute = raw.partition(":")
        return f"{int(minute or 0)} {int(hour)} * * *"
    except ValueError:
        logger.warning("OWNER_BRIEF_TIME=%r is not HH:MM; falling back to 23:00.", raw)
        return "0 23 * * *"


@scheduler_fn.on_schedule(
    schedule=_brief_cron(),
    # The property's clock, not UTC's — an 11pm brief must go out at 11pm where the bar
    # is, and cover that same local day. This is the same PROPERTY_TZ the reports read.
    timezone=scheduler_fn.Timezone(os.environ.get("PROPERTY_TZ", "Asia/Kolkata")),
    region=REGION,
    memory=options.MemoryOption.MB_512,
    timeout_sec=540,
    # One instance and no retries. A brief is a WhatsApp message to a human: sending it
    # twice is worse than sending it late, and a retry storm would be worse than both.
    max_instances=1,
    retry_count=0,
    secrets=_secrets(),
)
def daily_brief(event: scheduler_fn.ScheduledEvent) -> None:
    """The owner brief, once a night, for every live property.

    This is the sender under Functions, and the *only* one: `backend/server.py` starts
    the in-process loop only when `routers.reports.in_process_brief_enabled()` says so,
    and that returns False whenever FUNCTION_TARGET is set, which the runtime sets here.
    The container path is unaffected and still uses the loop.

    Why it had to move at all: the loop makes progress only while a process is alive.
    Cloud Run with scale-to-zero, and every function, shut an idle instance down within
    minutes of the last request, so a 23:00 tick in a loop started at 09:00 arrives only
    if the hotel happened to be busy all day. It stops silently — no error, no message,
    nothing in the logs — which is the failure this function exists to remove.
    """
    if os.environ.get("DAILY_BRIEF_ENABLED", "true").lower() != "true":
        logger.info("[daily-brief] DAILY_BRIEF_ENABLED is not true; nothing sent.")
        return

    async def send() -> int:
        # Named error rather than a driver timeout in the middle of the property loop,
        # exactly as the app's own startup does it.
        await db.check_connection()
        # No date argument: the scheduler fires at OWNER_BRIEF_TIME in PROPERTY_TZ, so
        # the property-local today this resolves to is the day that just traded.
        return await reports.send_daily_brief()

    count = _run(send())
    logger.info("[daily-brief] scheduled run sent %d brief(s) (job %s).",
                count, getattr(event, "job_name", "?"))


# ---------------------------------------------------------------- the visit follow-up
def _follow_up_cron() -> str:
    """CUSTOMER_FOLLOW_UP_TIME, as a daily cron expression. Same shape as `_brief_cron`.

    Defaults to 11:00 rather than the brief's 23:00, and the hour is the point: this is a
    message to a *customer*, not to the owner, and a restaurant telling somebody it misses
    them at eleven at night is a different message from the one it meant to send.
    """
    raw = (os.environ.get("CUSTOMER_FOLLOW_UP_TIME") or "11:00").strip()
    try:
        hour, _, minute = raw.partition(":")
        return f"{int(minute or 0)} {int(hour)} * * *"
    except ValueError:
        logger.warning("CUSTOMER_FOLLOW_UP_TIME=%r is not HH:MM; falling back to 11:00.",
                       raw)
        return "0 11 * * *"


@scheduler_fn.on_schedule(
    schedule=_follow_up_cron(),
    # The property's own clock, like the brief: "eleven in the morning" means eleven where
    # the restaurant is, and the ten-day window is counted in that property's days.
    timezone=scheduler_fn.Timezone(os.environ.get("PROPERTY_TZ", "Asia/Kolkata")),
    region=REGION,
    memory=options.MemoryOption.MB_512,
    timeout_sec=540,
    # One instance and no retries, for the reason the brief gives and more so: these are
    # messages to customers. A second instance would race the claim rather than duplicate
    # a message — the claim holds — but a retry after a partial run would re-enter a job
    # that has already written its log rows, and "sent late" beats every alternative to
    # "sent twice".
    max_instances=1,
    retry_count=0,
    secrets=_secrets(),
)
def customer_follow_ups(event: scheduler_fn.ScheduledEvent) -> None:
    """One follow-up to each customer who has not been back inside the property's window.

    A scheduled function rather than an in-process loop, for exactly the reason the daily
    brief became one: a loop only makes progress while a process is alive, and both the
    Functions runtime and Cloud Run with scale-to-zero shut an idle instance down within
    minutes. A follow-up that goes out only on the days the hotel happened to be busy at
    eleven in the morning is worse than none, because nobody would ever notice.

    There is no in-process equivalent for the container deployment. That is a real gap and
    not an oversight: the container is the development shape, the deployed shape is
    Functions, and adding a second mechanism would give the two deployments different
    behaviour for the one feature where "did it send" has to have a single answer.

    Safe when it cannot send. `run_follow_ups` checks the switch, the template and the
    WhatsApp credentials once per property before it reads a single guest, and a property
    that is not ready is one log line rather than a failure row per customer per night.
    """
    async def send() -> list:
        # Named error rather than a driver timeout in the middle of the property loop,
        # exactly as the app's own startup and the brief both do it.
        await db.check_connection()
        # No date argument: the scheduler fires in PROPERTY_TZ, so the property-local
        # today this resolves to is the day the window should be measured against.
        return await messaging.send_follow_ups()

    results = _run(send())
    sent = sum(r.get("sent", 0) for r in results)
    logger.info("[follow-ups] scheduled run: %d propert(ies), %d message(s) (job %s).",
                len(results), sent, getattr(event, "job_name", "?"))
