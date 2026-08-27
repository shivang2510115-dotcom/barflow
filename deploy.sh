#!/usr/bin/env bash
#
# Deploy BarFlow. One CLI, one project, one console: everything is Firebase.
#
#   the API   -> a Python Cloud Function (2nd gen), `api`, defined in functions/main.py
#   the brief -> a scheduled function, `daily_brief`, woken by Cloud Scheduler
#   the site  -> Firebase Hosting, which rewrites /api/** to the function
#   the data  -> Firestore, in this same project
#
# Everything the product runs on is now inside one Firebase project, which is what makes
# the data line above short: Firestore needs no connection string, no IP allowlist and no
# password, because the function authenticates as its own service account.
#
# Safe to re-run. Secrets are generated once and kept in .deploy-secrets (gitignored);
# every later run reuses them, because regenerating GUEST_ID_ENCRYPTION_KEY would make
# every guest identity number already stored unreadable, permanently.
#
#   ./deploy.sh            everything
#   ./deploy.sh api        the functions only
#   ./deploy.sh web        the Firebase site only
#   ./deploy.sh data       the Firestore indexes and rules only
#
set -euo pipefail
cd "$(dirname "$0")"

SECRETS_FILE=".deploy-secrets"
FUNCTIONS_ENV="functions/.env"
TARGET="${1:-all}"

# The four the application cannot run without, and the only ones deploy.sh creates. They
# go to Secret Manager rather than into functions/.env: functions/.env is read from disk
# on the machine that deploys and its contents end up in the function's plain
# configuration, while a secret is versioned, access-controlled and mounted at runtime.
# Anything else — WHATSAPP_TOKEN, STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET — is added later
# with `firebase functions:secrets:set NAME` plus its name in BARFLOW_SECRETS below; the
# deploy refuses any name listed there that does not yet exist, which is the right way
# round.
#
# MONGO_URL was the first of these and is gone. Firestore is in this same project, so the
# credential is the function's own service account and there is no connection string to
# store, rotate, percent-encode or leak. The four failure modes it used to bring — a
# closed IP allowlist, a literal <db_password>, an unencoded @ in the password, a paused
# cluster — went with it.
SECRET_VARS=(JWT_SECRET ADMIN_PASSWORD PLATFORM_ADMIN_PASSWORD GUEST_ID_ENCRYPTION_KEY)

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 — $2"; exit 1; }; }
need firebase "npm install -g firebase-tools"
need python3 "https://www.python.org/downloads/"

# The region lives in firebase.json (the Hosting rewrite) and in functions/main.py (where
# the function is created), and a rewrite pointing at a region the function is not in
# answers 404 rather than saying anything useful. Read it from firebase.json here so this
# script cannot be the third place that disagrees.
REGION_FROM_JSON="$(python3 -c '
import json, sys
cfg = json.load(open("firebase.json"))
for r in cfg["hosting"]["rewrites"]:
    fn = r.get("function")
    if isinstance(fn, dict):
        print(fn.get("region", "us-central1")); sys.exit(0)
sys.exit("firebase.json has no /api/** function rewrite")
')"

# ---------------------------------------------------------------- settings
# An existing file is not the same as a complete one. A run that was interrupted, or one
# where somebody pressed Enter through the prompts, leaves a file with the keys present
# and the values blank — and skipping the interview on "the file exists" then failed much
# later with an empty password, having already created a project alias. Ask again for
# whatever is actually missing.
INCOMPLETE=""
if [ -f "$SECRETS_FILE" ]; then
  set -a; . "./$SECRETS_FILE"; set +a
  for _v in PROJECT ADMIN_EMAIL ADMIN_PASSWORD PLATFORM_ADMIN_EMAIL PLATFORM_ADMIN_PASSWORD; do
    [ -n "${!_v:-}" ] || INCOMPLETE="yes"
  done
  if [ -n "$INCOMPLETE" ]; then
    echo "$SECRETS_FILE exists but is missing some answers. Asking for those again."
    echo "Anything already filled in is kept — in particular the two generated keys,"
    echo "because regenerating GUEST_ID_ENCRYPTION_KEY would orphan any guest ID"
    echo "numbers already written under it."
    echo
  fi
fi

if [ ! -f "$SECRETS_FILE" ] || [ -n "$INCOMPLETE" ]; then
  echo "First run. Three answers, then it is automatic from here."
  echo
  echo "The Firebase project must be on the Blaze plan. Functions, Cloud Scheduler and"
  echo "Secret Manager all need a billing account attached; the free tier still applies."
  echo
  echo "It also needs a Firestore database, and this script deliberately does not create"
  echo "one: choosing its location is permanent and cannot be changed afterwards, so it"
  echo "is not a decision to take on your behalf from a prompt."
  echo "  Console -> Build -> Firestore Database -> Create database"
  echo "  Production mode, and the region nearest your hotels (asia-south1 for India)."
  echo
  # Each prompt keeps what is already there, so a re-run after an interrupted one asks
  # only for the gaps. Empty answer = keep the existing value.
  ask()  { local cur="${!2:-}" a; read -rp "$1${cur:+ [$cur]}: " a; printf '%s' "${a:-$cur}"; }
  asks() { local cur="${!2:-}" a; read -rsp "$1${cur:+ [unchanged]}: " a; echo >&2;
           printf '%s' "${a:-$cur}"; }

  PROJECT="$(ask 'Firebase project id (e.g. barflow-prod)' PROJECT)"
  echo
  echo "Your own login — the hotel admin of the first property."
  ADMIN_EMAIL="$(ask '  ADMIN_EMAIL' ADMIN_EMAIL)"
  ADMIN_PASSWORD="$(asks '  ADMIN_PASSWORD' ADMIN_PASSWORD)"
  echo
  echo "The platform operator — the account that approves and suspends hotels."
  echo "  This is not a hotel login. Without it nobody can approve anything."
  PLATFORM_ADMIN_EMAIL="$(ask '  PLATFORM_ADMIN_EMAIL' PLATFORM_ADMIN_EMAIL)"
  PLATFORM_ADMIN_PASSWORD="$(asks '  PLATFORM_ADMIN_PASSWORD' PLATFORM_ADMIN_PASSWORD)"

  # Generated once and never again. A new GUEST_ID_ENCRYPTION_KEY would orphan every
  # guest identity number already written under the old one — it is encryption, not
  # hashing, so there is no way back — and a new JWT_SECRET signs every existing session
  # out. Both are kept if the file already has them.
  JWT_SECRET="${JWT_SECRET:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')}"
  GUEST_KEY="${GUEST_ID_ENCRYPTION_KEY:-$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"

  umask 077
  cat > "$SECRETS_FILE" <<EOF
PROJECT=$PROJECT
REGION=$REGION_FROM_JSON
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_PASSWORD=$ADMIN_PASSWORD
PLATFORM_ADMIN_EMAIL=$PLATFORM_ADMIN_EMAIL
PLATFORM_ADMIN_PASSWORD=$PLATFORM_ADMIN_PASSWORD
JWT_SECRET=$JWT_SECRET
GUEST_ID_ENCRYPTION_KEY=$GUEST_KEY
EOF
  echo
  echo "Written to $SECRETS_FILE (owner-readable only, gitignored)."
  echo
  echo "  BACK THIS FILE UP SOMEWHERE SAFE."
  echo "  GUEST_ID_ENCRYPTION_KEY decrypts your guests' Aadhaar and passport numbers."
  echo "  It is encryption, not hashing: lose it and every number stored under it is"
  echo "  gone for good, and the front desk sees an empty field."
  echo
fi

set -a; . "./$SECRETS_FILE"; set +a
REGION="${REGION:-$REGION_FROM_JSON}"
SITE="https://${PROJECT}.web.app"

if [ "$REGION" != "$REGION_FROM_JSON" ]; then
  echo "Region mismatch: $SECRETS_FILE says '$REGION', firebase.json says '$REGION_FROM_JSON'."
  echo "They must be the same, and functions/main.py's REGION must match too — a Hosting"
  echo "rewrite that names the wrong region answers 404 and explains nothing."
  exit 1
fi

firebase use "$PROJECT" --non-interactive >/dev/null 2>&1 || firebase use --add

# ---------------------------------------------------------------- api
if [ "$TARGET" = "all" ] || [ "$TARGET" = "api" ]; then
  echo "==> Configuration: $FUNCTIONS_ENV"
  # The non-secret half of the environment. The Firebase CLI loads functions/.env both
  # when it asks main.py what functions exist — which is where OWNER_BRIEF_TIME becomes
  # the Cloud Scheduler cron and PROPERTY_TZ its timezone — and into the deployed
  # function's own environment. It is regenerated from $SECRETS_FILE on every run rather
  # than edited, so there is one place to change a setting. Gitignored; no secret is
  # written here.
  umask 077
  cat > "$FUNCTIONS_ENV" <<EOF
# Generated by deploy.sh from $SECRETS_FILE. Edit that, not this — this is overwritten.
# The one line that decides where the hotel's data lives. Without it backend/db.py falls
# back to the JSON-file mock, and the function would start, serve, and write real
# bookings to a file on a container disk that the next cold start throws away.
DB_BACKEND=firestore
ADMIN_EMAIL=$ADMIN_EMAIL
PLATFORM_ADMIN_EMAIL=$PLATFORM_ADMIN_EMAIL
CORS_ORIGINS=$SITE
CURRENCY_SYMBOL=₹
PROPERTY_TZ=Asia/Kolkata
TRUSTED_PROXY_HOPS=1
DEMO_LOGINS=false
SEED_DEMO_CONTENT=false
DAILY_BRIEF_ENABLED=true
OWNER_BRIEF_TIME=23:00
API_MAX_INSTANCES=10
BARFLOW_SECRETS=$(IFS=,; echo "${SECRET_VARS[*]}")
EOF

  echo "==> Secret Manager"
  # Written only when the stored value differs. Every `secrets:set` mints a new version,
  # and a deploy that minted four of them per run would bury the one version anybody ever
  # needs to look at — GUEST_ID_ENCRYPTION_KEY, the one whose loss is permanent — under a
  # hundred identical ones.
  for name in "${SECRET_VARS[@]}"; do
    want="${!name}"
    if [ -z "$want" ]; then
      echo "    $name is empty in $SECRETS_FILE. Fill it in and re-run."
      exit 1
    fi
    have="$(firebase functions:secrets:access "${name}@latest" --project "$PROJECT" 2>/dev/null || true)"
    if [ "$have" = "$want" ]; then
      echo "    $name unchanged"
    else
      printf '%s' "$want" | firebase functions:secrets:set "$name" \
        --project "$PROJECT" --data-file - --force >/dev/null
      echo "    $name updated"
    fi
  done

  echo "==> Functions: api + daily_brief  ($REGION)"
  # firebase.json's predeploy hook copies backend/ into functions/backend/ first, which
  # is how the application source reaches the bundle at all. The CLI enables the APIs it
  # needs — Cloud Functions, Cloud Build, Artifact Registry, Cloud Run, Cloud Scheduler,
  # Secret Manager — by itself. There is no gcloud in this file and none is needed.
  firebase deploy --only functions --project "$PROJECT" --non-interactive
fi

# ---------------------------------------------------------------- data
if [ "$TARGET" = "all" ] || [ "$TARGET" = "data" ] || [ "$TARGET" = "api" ]; then
  echo "==> Firestore: indexes + rules"
  # Before the API is called and with `api`, not only with `data`, because the failure
  # this prevents is a *runtime* one. Firestore refuses a query it has no composite index
  # for at the moment somebody opens the page, with a 500 and a create-this-index link in
  # the log — so an API deployed ahead of its indexes is an API that works until a
  # receptionist filters the booking list by date. firestore.indexes.json is derived from
  # the queries the application actually makes; see docs/deploy-firebase.md.
  #
  # Index builds are asynchronous and can take minutes on a database with data in it.
  # This returns as soon as they are accepted, which is correct for a fresh project and
  # worth knowing on one that is not: `firebase firestore:indexes --project $PROJECT`
  # shows the state.
  #
  # The rules deny everything, and that does not restrict this application at all — it
  # runs as a service account and admin credentials bypass rules. It closes the browser's
  # direct path to the database. See firestore.rules.
  firebase deploy --only firestore --project "$PROJECT" --non-interactive
fi

# ---------------------------------------------------------------- web
if [ "$TARGET" = "all" ] || [ "$TARGET" = "web" ]; then
  echo "==> Building the site"
  # REACT_APP_BACKEND_URL empty = same origin. firebase.json rewrites /api/** to the
  # function, so the browser makes no cross-origin request at all. It is compiled in, not
  # read at runtime, which is why this rebuilds rather than restarts.
  ( cd frontend && npm ci --silent && CI=false REACT_APP_BACKEND_URL= npm run build )

  echo "==> Firebase Hosting"
  firebase deploy --only hosting --project "$PROJECT" --non-interactive
fi

# ---------------------------------------------------------------- check
echo
echo "==> Checking $SITE/api/"
if curl -fsS --max-time 60 "$SITE/api/" ; then
  echo
  echo "Live."
  echo "  Site:     $SITE"
  echo "  Operator: $SITE/platform   ($PLATFORM_ADMIN_EMAIL)"
  echo "  Sign up:  $SITE/signup"
  echo
  echo "  The nightly brief is a scheduled function now, not a loop inside the API, so"
  echo "  it fires whether or not anyone used the app that day:"
  echo "    firebase functions:log --only daily_brief --project $PROJECT"
else
  echo
  echo "The API did not answer. Read the logs — the app names its own cause:"
  echo "  firebase functions:log --only api --project $PROJECT"
  echo
  echo "Most often one of:"
  echo "  1. the project has no Firestore database yet — the console will not let the"
  echo "     function create one. Build -> Firestore Database -> Create database."
  echo "  2. the function's service account is missing roles/datastore.user. It has it by"
  echo "     default; a project with hardened IAM may not."
  echo "  3. the first request timed out on a cold start — try the curl once more"
  echo
  echo "The app names its own cause in the log for all three: db.py pings Firestore at"
  echo "startup precisely so the error says which of these it is."
  exit 1
fi
