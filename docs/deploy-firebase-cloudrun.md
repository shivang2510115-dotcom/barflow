# Deploying BarFlow: Firebase Hosting + Cloud Run

**Frontend** on Firebase Hosting. **API** on Cloud Run. **Database** on MongoDB Atlas.

Firebase Hosting serves static files only, so it cannot run the FastAPI container. What it
*can* do is rewrite `/api/**` to a Cloud Run service, which is why this pairing is worth
the extra service: the browser sees **one origin**, so there is no cross-origin request,
no preflight, and CORS stops being load-bearing.

```
yourhotel.web.app
  ├─ /          → Firebase Hosting (the React build)
  └─ /api/**    → Cloud Run: barflow-api (the FastAPI container)
                    └─ MongoDB Atlas
```

---

## Before you start

You need the `gcloud` and `firebase` CLIs, a Google Cloud project with billing enabled
(Cloud Run has a free tier but still requires a billing account), and an Atlas cluster.

```bash
brew install --cask google-cloud-sdk
npm install -g firebase-tools
gcloud auth login && firebase login
```

Pick one project id and use it everywhere below:

```bash
export PROJECT=barflow-prod
export REGION=asia-south1          # Mumbai. Keep it near the property and near Atlas.
gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com
```

---

## 1. Generate the secrets before you need them

Two of these cannot be recovered later, so make them now and put them where you keep the
Atlas password.

```bash
# Signs every login token. If this leaks, anyone can forge an admin session for any hotel.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Encrypts guest identity-document numbers at rest. LOSE THIS AND EVERY NUMBER WRITTEN
# UNDER IT IS UNREADABLE — it is encryption, not hashing, and there is no recovery.
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 2. Atlas

Network Access must allow **`0.0.0.0/0`**. Cloud Run's outbound address is not fixed, so
an IP allowlist cannot name it. (If that is unacceptable, the alternative is a VPC
connector with Cloud NAT and a static egress IP — more moving parts, and worth it only if
a policy demands it.)

In the connection string, replace `<db_password>` with the real password and
percent-encode any `@ : / ? # [ ] %` in it. A literal `<db_password>` left in place is the
single most common cause of the API hanging at startup.

## 3. Deploy the API to Cloud Run

From the repository root:

```bash
gcloud run deploy barflow-api \
  --source ./backend \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --set-env-vars "$(paste -sd, - <<'ENV'
DB_NAME=barflow
CURRENCY_SYMBOL=₹
PROPERTY_TZ=Asia/Kolkata
TRUSTED_PROXY_HOPS=1
DEMO_LOGINS=false
SEED_DEMO_CONTENT=false
DAILY_BRIEF_ENABLED=true
OWNER_BRIEF_TIME=23:00
ENV
)"
```

`--allow-unauthenticated` is right here: this service *is* the public API and does its own
authentication. It does not mean unauthenticated access to your data.

Then set the secrets, which should never appear in a shell history or a source file:

```bash
gcloud run services update barflow-api --region "$REGION" --set-env-vars \
  MONGO_URL='mongodb+srv://…',\
JWT_SECRET='…',\
ADMIN_EMAIL='you@yourhotel.in',\
ADMIN_PASSWORD='…',\
GUEST_ID_ENCRYPTION_KEY='…',\
PLATFORM_ADMIN_EMAIL='ops@yourcompany.in',\
PLATFORM_ADMIN_PASSWORD='…'
```

**`PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD` are the pair people forget.**
Without both, no operator account is created, so nobody can approve a hotel and every
signup sits pending for ever. There is deliberately no default: this account can approve
every business on the platform.

**The app refuses to start** against a real database if `JWT_SECRET` or `ADMIN_PASSWORD`
is still the value published in this repository. That is intentional — read the error, do
not work around it.

### Only one instance, for now

```bash
gcloud run services update barflow-api --region "$REGION" --max-instances 1
```

The nightly WhatsApp brief is an in-process loop, so two instances would send it twice.
Rate limits are shared through the database and are safe across instances; the brief is
not. Lift this once the brief moves to Cloud Scheduler.

## 4. Point Firebase at it

```bash
firebase use --add            # choose $PROJECT, alias it "prod"
```

`firebase.json` in this repository already carries the rewrite. If you changed `$REGION`
or the service name above, change them there to match.

## 5. Build and deploy the frontend

```bash
cd frontend
npm ci
CI=false REACT_APP_BACKEND_URL= npm run build
cd .. && firebase deploy --only hosting
```

**Leave `REACT_APP_BACKEND_URL` empty.** Empty means same-origin, which is the whole point
of the rewrite. Setting it to the Cloud Run URL would work but reintroduces cross-origin
requests and makes `CORS_ORIGINS` load-bearing again.

It is compiled in, not read at runtime: changing it needs a rebuild, not a restart.

## 6. CORS

With the rewrite there is no cross-origin request, but the server still requires
`CORS_ORIGINS` to be set — it refuses to start against a real database rather than
defaulting to a wildcard. Give it your hosting origin:

```bash
gcloud run services update barflow-api --region "$REGION" \
  --set-env-vars CORS_ORIGINS='https://barflow-prod.web.app'
```

Add the custom domain to that list, comma-separated, when you attach one.

## 7. Check it

```bash
curl -s https://barflow-prod.web.app/api/ ; echo
```

Expect `{"service":"BarFlow API","status":"ok"}`. Then, in the Cloud Run logs, confirm:

```
Property backfill: …
Screen backfill: …
Platform operator seeded (ops@yourcompany.in).
```

If the operator line is missing, the two variables are not set and nothing can be
approved.

If the API hangs instead of answering, the log names the cause — `db.py` fails fast with a
checklist rather than waiting on a 30-second driver timeout.

---

## Afterwards

**Sign in as the operator** at `/platform` and approve your own hotel, which registers
through `/signup` like any other.

**Custom domain:** `firebase hosting:sites` → add a domain in the Firebase console, then
add it to `CORS_ORIGINS`.

**Redeploying:** the frontend is `npm run build && firebase deploy --only hosting`; the
API is the same `gcloud run deploy` command. Environment variables persist across
deploys — you only set them again when they change.

**What still needs your credentials:** WhatsApp. Admin → Notifications names exactly which
variables are missing and sends a real test message. It needs a Meta WhatsApp Business
account with a verified business; nothing in this repository can obtain that for you.
