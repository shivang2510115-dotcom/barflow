# Deploying BarFlow: Firebase, end to end

**Frontend** on Firebase Hosting. **API** as a Python Cloud Function (2nd gen). **Nightly
brief** as a scheduled function. **Database** on Firestore, in the same project.

One CLI, one project, one console, one bill. `firebase deploy` ships the whole product.

```
yourhotel.web.app
  ├─ /          → Firebase Hosting (the React build)
  └─ /api/**    → Function: api  (functions/main.py serves backend/server.py::app)
                    └─ Firestore (same project, service-account auth)

  23:00 IST     → Cloud Scheduler → Function: daily_brief → WhatsApp
```

Firebase Hosting serves static files only, so it cannot run FastAPI itself. What it *can*
do is rewrite `/api/**` to a function, which is why this pairing is worth the second
service: the browser sees **one origin**, so there is no cross-origin request, no
preflight, and CORS stops being load-bearing.

**The database is Firestore.** An earlier version of this page said it was Atlas and
would stay Atlas, on the grounds that moving meant rewriting every query. That turned out
to be wrong about this codebase, and the reason is worth keeping: there are ~244 database
call sites but they all go through **one handle**. `backend/db.py` picks a backend,
`backend/scoped_db.py` binds it to a tenant, and `backend/mock_db.py` was already a
second implementation of that interface over a JSON file. Firestore is a third —
`backend/firestore_db.py` — and no router changed. The proof is the existing suite: all
445 pure tests and all 132 API tests run unmodified against the Firestore emulator, and
those already cover tenant isolation, the money paths and the access boundary.

**Atlas still works and has not been removed.** `DB_BACKEND=mongo` with a `MONGO_URL` is
the same path it always was.

---

## How the API gets there

`backend/` is the only copy of the application. Nothing is forked for Functions.

The Firebase CLI uploads the *functions source directory* and nothing above it, so
`backend/` — which sits beside `functions/`, not inside it — would not be in the bundle.
A predeploy hook in `firebase.json` runs `functions/vendor-backend.sh`, which copies
`backend/` to `functions/backend/` (minus `.env`, `db.json`, `tests/` and caches, exactly
as `.dockerignore` does for the image). `functions/main.py` puts that directory on
`sys.path` and imports `server.app` — the same object `uvicorn server:app` serves.

`functions/backend/` is generated, gitignored and rewritten from scratch on every deploy.
**Never edit it.** Anything typed there is deleted by the next deploy without warning.

`functions/requirements.txt` does not restate the application's dependencies either; it
reads `backend/requirements.txt` out of that same copy, so the container and the function
install identical versions.

---

## Before you start

You need the `firebase` CLI and a Firebase project **on the Blaze plan** (Functions, Cloud
Scheduler and Secret Manager all require a billing account; the free tier still applies).

```bash
npm install -g firebase-tools
firebase login
```

You also need a **Firestore database** in that project, created once by hand:

> Console → Build → Firestore Database → **Create database** → production mode →
> the region nearest your hotels (`asia-south1` for India).

`deploy.sh` does not do this for you, on purpose: a Firestore database's location is
permanent and cannot be changed afterwards. Put it in the same region as the function.

There is no `gcloud` step, no second console to visit, and no database account to open.

---

## The short way

```bash
./deploy.sh
```

It asks three questions the first time, generates `JWT_SECRET` and
`GUEST_ID_ENCRYPTION_KEY`, keeps them in `.deploy-secrets` (gitignored, owner-readable
only), pushes the secrets to Secret Manager, writes `functions/.env`, deploys both
functions and the site, and curls `/api/` at the end. Re-running it reuses the same
secrets — see the warning under step 1.

The rest of this page is what it does, for when it does not work.

---

## 1. Generate the secrets before you need them

Two of these cannot be recovered later, so make them now and put them wherever your
organisation keeps things it cannot regenerate.

```bash
# Signs every login token. If this leaks, anyone can forge an admin session for any hotel.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Encrypts guest identity-document numbers at rest. LOSE THIS AND EVERY NUMBER WRITTEN
# UNDER IT IS UNREADABLE — it is encryption, not hashing, and there is no recovery.
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 2. Firestore: indexes and rules

There is nothing to configure to *connect*. The function authenticates as its own service
account, the project comes from the metadata server, and there is no connection string, no
password and no IP allowlist. What there is instead is two files, both deployed by
`firebase deploy --only firestore` (which `./deploy.sh` runs as part of `api` and `all`,
and on its own as `./deploy.sh data`).

**`firestore.indexes.json` — the composite indexes.** Firestore requires a declared index
for any query that combines an equality filter with a range on a different field, and it
fails such a query **at runtime**, with a 500 and a create-this-index link in the log. So
the indexes must exist before the traffic does. There are six and they are derived, not
guessed:

| collection | fields | the query |
|---|---|---|
| `bookings` | `property_id`, `check_out` | `GET /bookings?start=` |
| `bookings` | `property_id`, `status`, `check_out` | `GET /bookings?start=&status=` |
| `bookings` | `property_id`, `check_in` | `GET /bookings?end=` |
| `bookings` | `property_id`, `status`, `check_in` | `GET /bookings?end=&status=` |
| `orders` | `property_id`, `status`, `settled_at` | the revenue report's date window |
| `rate_limit_hits` | `key`, `at` | the fixed-window counter in `services/ratelimit.py` |

How they were found: `FIRESTORE_INDEX_TRACE=<path>` makes the adapter record the shape of
every query it runs — collection, equality fields, range field — which is exactly the
shape of the index that query needs. Running the whole suite against the emulator, then
reading the trace against a grep for every `$lt`/`$gte`/`$gt`/`$lte` site in `routers/`
and `services/`, then driving by hand the one endpoint the suite never exercises with
dates. Use the same trace if you add a query: the emulator does **not** enforce indexes,
so a passing test is not evidence that production will serve it.

Every other query the application makes filters on equality only, and Firestore serves
those by merging its automatic single-field indexes. Declaring composites for them anyway
would cost an index write per document write for nothing. If that turns out to be wrong
somewhere, the log says so by name: `firestore_db._query` catches `FAILED_PRECONDITION`
and reports the collection and fields involved.

**`firestore.rules` — deny everything.** This does not restrict the application at all,
which is the point worth understanding: it runs as a service account, and admin
credentials bypass security rules entirely. The rules close the *other* door — the
Firestore Web SDK, which any visitor to the site could otherwise point at the project's
public config and read every hotel's bookings with. Tenancy stays in
`backend/scoped_db.py` rather than being written a second time in the rules language,
where the two copies could disagree.

### Uniqueness is enforced by the application, and only by the application

**Firestore has no unique indexes.** `seed_data()` still declares four, and against
Firestore `create_index` is a documented no-op:

| constraint | what actually enforces it |
|---|---|
| `users.email` | a lookup before insert in `routers/staff.py` and `routers/signup.py` |
| `guests(property_id, phone)` | `routers/guests.py`, which returns 409 with the existing guest |
| `bookings(property_id, reference)` | generate-and-check in `routers/bookings.py` |
| `folio_entries(property_id, folio_id, charge_date)` for room nights | `services/folio.py::unposted_nights` |

Each is a read followed by a write, so two simultaneous requests can both read "no
duplicate" and both insert. **This was already true under the JSON mock**, whose
`create_index` is also a no-op, and the pre-checks were written knowing it — but MongoDB
was the thing that would have closed the race, and choosing Firestore is choosing not to.
The one to watch is the folio room-night entry, because a duplicate there is a guest
charged twice for one night; `unposted_nights` re-reads what is already posted
immediately before writing, which narrows it to the width of that one call. If it ever
matters, the fix is a Firestore transaction in `services/folio.py`, not an index.

The rate limiter's TTL is the other half of this. Firestore's TTL is a field policy, not
something a client can request, so it is declared in `firestore.indexes.json` under
`fieldOverrides` on `rate_limit_hits.expires_at`. It sweeps within 24 hours and is
best-effort, so `services/ratelimit.py` keeps pruning by hand — as it already does for
the mock.

## 3. Configuration, in two places on purpose

Functions take their environment from two mechanisms, and which one a value belongs in is
decided by whether reading it is a breach.

**`functions/.env`** — plain configuration. The CLI loads it both when it asks
`main.py` what functions exist (which is where `OWNER_BRIEF_TIME` becomes the Cloud
Scheduler cron and `PROPERTY_TZ` its timezone) and into the deployed function's
environment. It is gitignored and regenerated by `deploy.sh`; write it by hand only if you
are not using that script.

```
DB_BACKEND=firestore
ADMIN_EMAIL=you@yourhotel.in
PLATFORM_ADMIN_EMAIL=ops@yourcompany.in
CORS_ORIGINS=https://barflow-prod.web.app
CURRENCY_SYMBOL=₹
PROPERTY_TZ=Asia/Kolkata
TRUSTED_PROXY_HOPS=1
DEMO_LOGINS=false
SEED_DEMO_CONTENT=false
DAILY_BRIEF_ENABLED=true
OWNER_BRIEF_TIME=23:00
API_MAX_INSTANCES=10
BARFLOW_SECRETS=JWT_SECRET,ADMIN_PASSWORD,PLATFORM_ADMIN_PASSWORD,GUEST_ID_ENCRYPTION_KEY
```

**`DB_BACKEND=firestore` is the load-bearing line in that file.** Without it `backend/db.py`
falls back to the JSON-file mock, and the function starts, serves, and writes real
bookings to a container disk that the next cold start throws away. It is not a secret, so
it belongs here rather than in Secret Manager — but it is the one plain setting whose
absence is silent.

**Secret Manager** — the four values that must never sit in a file on a laptop or in a
function's plain configuration:

```bash
firebase functions:secrets:set JWT_SECRET
firebase functions:secrets:set ADMIN_PASSWORD
firebase functions:secrets:set PLATFORM_ADMIN_PASSWORD
firebase functions:secrets:set GUEST_ID_ENCRYPTION_KEY
```

`BARFLOW_SECRETS` is the list both functions declare, so a name in it that does not exist
in Secret Manager makes the deploy fail rather than the function start without it. To add
WhatsApp or Stripe later, set the secret and append its name — no code change:

```bash
firebase functions:secrets:set WHATSAPP_TOKEN
# then, in functions/.env:  BARFLOW_SECRETS=…,WHATSAPP_TOKEN
```

**`PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD` are the pair people forget.**
Without both, no operator account is created, so nobody can approve a hotel and every
signup sits pending for ever. There is deliberately no default: this account can approve
every business on the platform.

**The app refuses to start** against a real database if `JWT_SECRET` or `ADMIN_PASSWORD`
is still the value published in this repository. That is intentional — read the error, do
not work around it. Firestore counts as a real database for this: `db.py` reports
`using_mock = False` for it exactly as it does for Atlas, which is the switch those two
guards, and the `CORS_ORIGINS` one, all read.

**Upgrading a deployment that used Atlas:** `MONGO_URL` is no longer in `BARFLOW_SECRETS`,
so the function stops being given it, and the old secret can be destroyed in Secret
Manager at your leisure. `.deploy-secrets` may still contain a `MONGO_URL=` line; it is
ignored and can be deleted. There is no data migration path here and none is offered —
this change was made before anything was deployed.

## 4. Deploy the functions

```bash
firebase deploy --only functions
```

The predeploy hook vendors `backend/` first. The CLI enables the APIs it needs — Cloud
Functions, Cloud Build, Artifact Registry, Cloud Run, Cloud Scheduler, Secret Manager —
by itself.

Two functions are created:

| function | trigger | why |
|---|---|---|
| `api` | HTTPS, public invoker | the FastAPI app. Public is right: this *is* the public API and does its own authentication. |
| `daily_brief` | Cloud Scheduler, `OWNER_BRIEF_TIME` in `PROPERTY_TZ` | the nightly owner brief. |

### The brief, and why it moved

It used to be a `while True` loop inside the API process, and `max-instances` was pinned
to 1 so that two processes could not send it twice. Under scale-to-zero that loop stops
being a schedule at all: an instance with no traffic is shut down within minutes, so a
23:00 tick in a loop started at 09:00 only ever arrives if the hotel happened to be busy
all day. It failed silently — no error, nothing in the logs, just no message.

`backend/routers/reports.py::daily_brief_scheduler` still exists and is still what the
Docker image and `render.yaml` use, because those run a process that really does live all
night. Exactly one of the two ever runs:
`routers/reports.py::in_process_brief_enabled()` returns False whenever `FUNCTION_TARGET`
is set, which only the Functions runtime sets. `DAILY_BRIEF_ENABLED=false` still silences
both.

With the loop gone from the API, `API_MAX_INSTANCES` is no longer pinned to 1. Rate limits
live in the database and were always safe across instances.

## 5. Point Firebase Hosting at it

`firebase.json` already carries the rewrite:

```json
{ "source": "/api/**", "function": { "functionId": "api", "region": "asia-south1" } }
```

**The region appears in three files and they must agree**: this rewrite,
`functions/main.py`'s `REGION`, and `.deploy-secrets`. A rewrite naming a region the
function is not in answers 404 and explains nothing. `deploy.sh` refuses to run if the
last two disagree.

## 6. Build and deploy the frontend

```bash
cd frontend
npm ci
CI=false REACT_APP_BACKEND_URL= npm run build
cd .. && firebase deploy --only hosting
```

**Leave `REACT_APP_BACKEND_URL` empty.** Empty means same-origin, which is the whole point
of the rewrite. Setting it to the function's own URL would work but reintroduces
cross-origin requests and makes `CORS_ORIGINS` load-bearing again.

It is compiled in, not read at runtime: changing it needs a rebuild, not a restart.

## 7. CORS

With the rewrite there is no cross-origin request, but the server still requires
`CORS_ORIGINS` to be set — it refuses to start against a real database rather than
defaulting to a wildcard. Put your Hosting origin in `functions/.env` and add the custom
domain to the list, comma-separated, when you attach one.

## 8. Check it

```bash
curl -s https://barflow-prod.web.app/api/ ; echo
```

Expect `{"service":"BarFlow API","status":"ok"}`. The first request after a quiet period
is a cold start and takes a second or two longer; that is the startup work in the next
section.

Then, in the logs, confirm:

```bash
firebase functions:log --only api
```

```
Cold start: running application startup
Property backfill: …
Screen backfill: …
Platform operator seeded (ops@yourcompany.in).
Cold start complete.
```

If the operator line is missing, the two variables are not set and nothing can be
approved.

If the API answers 500 instead, the log names the cause — `db.py` fails fast with a
checklist rather than waiting on a 30-second driver timeout.

---

## What happens on a cold start

A container is one process that starts once. A function instance starts, serves, and is
shut down when it goes quiet, so "startup" happens repeatedly.

`functions/main.py` registers the work with `@core.init`, the Functions runtime's own
once-per-instance hook: it runs before the first invocation that instance serves and
never again, and never at import — which matters, because the CLI imports `main.py` on
your own machine just to find out what functions exist, and startup at import would mean
a deploy tried to reach the database to answer that question.

What runs is the app's real ASGI lifespan, not a copy of it, so `@app.on_event("startup")`
in `server.py` stays the single definition: the connection ping, the seed (the admin
account, the GST bands, the meal plans) and the five migrations. Measured against the
mock, a cold start on an already-seeded database is **40 database round trips** and no
writes; the wall-clock cost is those 40 round trips plus the Python import, so expect a
few hundred milliseconds on top of the container start. Create the Firestore database in
the same region as the function — that decision is permanent, so it is worth getting
right on the day.

If startup fails, the runtime's flag is left unset and the next invocation tries again,
rather than the instance serving 500s for ever out of a half-seeded state.

`concurrency` is 40 on the `api` function, deliberately: one instance serves a whole
service period's front desk, so that startup is paid once for all of them rather than
once per request.

---

## Afterwards

**Sign in as the operator** at `/platform` and approve your own hotel, which registers
through `/signup` like any other.

**Custom domain:** `firebase hosting:sites` → add a domain in the Firebase console, then
add it to `CORS_ORIGINS` in `functions/.env` and redeploy the functions.

**Redeploying:** `./deploy.sh`, or `./deploy.sh api` / `./deploy.sh web` / `./deploy.sh
data` for one part. Secrets persist; you only set them again when they change.

**Running it locally:** unchanged — `cd backend && uvicorn server:app --reload` with
`MONGO_URL` and `DB_BACKEND` unset uses the JSON mock, exactly as before. Nothing about
this change touches local development.

To run against Firestore locally instead, use the emulator rather than the real database:

```bash
firebase emulators:start --only firestore --project demo-barflow   # no login needed

cd backend
DB_BACKEND=firestore FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  JWT_SECRET=… ADMIN_PASSWORD=… CORS_ORIGINS=http://localhost:3000 \
  uvicorn server:app --reload
```

`JWT_SECRET`, `ADMIN_PASSWORD` and `CORS_ORIGINS` are needed here and not under the mock,
because Firestore is a real database as far as those three guards are concerned.

The whole test suite runs this way too, which is how the adapter is verified:

```bash
# 445 pure tests — tests/conftest.py points MockDatabase at the emulator
DB_BACKEND=firestore FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  JWT_SECRET=… ADMIN_PASSWORD=… CORS_ORIGINS=http://localhost:3000 \
  python3 -m pytest tests/ --ignore=tests/backend_test.py --ignore=tests/hotel_api_test.py

# 132 API tests — against a server started as above
REACT_APP_BACKEND_URL=http://127.0.0.1:8000 ADMIN_PASSWORD=… \
  python3 -m pytest tests/hotel_api_test.py

# 26 adapter tests — every operator, diffed against the mock's answer
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python3 -m pytest tests_firestore/
```

`tests/backend_test.py` is the exception and stays on the mock: it pins
`ADMIN_PASSWORD = "admin123"` in its source, which the app refuses on any real backend by
design. That was equally true of Atlas.

To exercise the function path instead:

```bash
./functions/vendor-backend.sh
python3.12 -m venv functions/venv
. functions/venv/bin/activate && pip install -r functions/requirements.txt
firebase emulators:start --only functions --project demo-barflow
```

The venv must be built with the same Python version as `runtime` in `firebase.json`; the
CLI looks for that exact interpreter by name. `--project demo-barflow` needs no login.
The emulator will still try to read the four secrets from Secret Manager and fail with a
403 if you are not authenticated — put local stand-ins in `functions/.secret.local`
(gitignored, same `KEY=value` format as `.env`) or leave them unset, in which case the
app falls back to the JSON mock as it does anywhere else.

Scheduled functions are not triggered by the emulator; invoke `daily_brief` directly with
`functions-framework --target daily_brief --source functions/main.py` and `POST /`.

**The container is still supported.** `backend/Dockerfile` and `render.yaml` are
unchanged and still valid; that path keeps the in-process brief. Nothing here forces a
choice.

**What still needs your credentials:** WhatsApp. Admin → Notifications names exactly which
variables are missing and sends a real test message. It needs a Meta WhatsApp Business
account with a verified business; nothing in this repository can obtain that for you. Add
`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` and `OWNER_PHONE` as described in step 3 — until
then `daily_brief` runs on schedule, builds every property's brief, and logs that it
could not send it.
