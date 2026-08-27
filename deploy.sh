#!/usr/bin/env bash
#
# Deploy BarFlow. One CLI, one project, one console: everything is Firebase.
#
#   the API   -> a Python Cloud Function (2nd gen), `api`, defined in functions/main.py
#   the brief -> a scheduled function, `daily_brief`, woken by Cloud Scheduler
#   the site  -> Firebase Hosting, which rewrites /api/** to the function
#   the data  -> MongoDB Atlas, unchanged
#
# Safe to re-run. Secrets are generated once and kept in .deploy-secrets (gitignored);
# every later run reuses them, because regenerating GUEST_ID_ENCRYPTION_KEY would make
# every guest identity number already stored unreadable, permanently.
#
#   ./deploy.sh            everything
#   ./deploy.sh api        the functions only
#   ./deploy.sh web        the Firebase site only
#
set -euo pipefail
cd "$(dirname "$0")"

SECRETS_FILE=".deploy-secrets"
FUNCTIONS_ENV="functions/.env"
TARGET="${1:-all}"

# The five the application cannot run without, and the only ones deploy.sh creates. They
# go to Secret Manager rather than into functions/.env: functions/.env is read from disk
# on the machine that deploys and its contents end up in the function's plain
# configuration, while a secret is versioned, access-controlled and mounted at runtime.
# Anything else — WHATSAPP_TOKEN, STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET — is added later
# with `firebase functions:secrets:set NAME` plus its name in BARFLOW_SECRETS below; the
# deploy refuses any name listed there that does not yet exist, which is the right way
# round.
SECRET_VARS=(MONGO_URL JWT_SECRET ADMIN_PASSWORD PLATFORM_ADMIN_PASSWORD GUEST_ID_ENCRYPTION_KEY)

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
if [ ! -f "$SECRETS_FILE" ]; then
  echo "First run. Four answers, then it is automatic from here."
  echo
  echo "The Firebase project must be on the Blaze plan. Functions, Cloud Scheduler and"
  echo "Secret Manager all need a billing account attached; the free tier still applies."
  echo
  read -rp "Firebase project id (e.g. barflow-prod): " PROJECT
  echo
  echo "MongoDB Atlas connection string."
  echo "  Replace <db_password> with the real password, and percent-encode any @ : / ? # in it."
  echo "  Atlas -> Network Access must allow 0.0.0.0/0: a function's outbound IP is not fixed."
  read -rp "  MONGO_URL: " MONGO_URL
  echo
  echo "Your own login — the hotel admin of the first property."
  read -rp "  ADMIN_EMAIL: " ADMIN_EMAIL
  read -rsp "  ADMIN_PASSWORD: " ADMIN_PASSWORD; echo
  echo
  echo "The platform operator — the account that approves and suspends hotels."
  echo "  This is not a hotel login. Without it nobody can approve anything."
  read -rp "  PLATFORM_ADMIN_EMAIL: " PLATFORM_ADMIN_EMAIL
  read -rsp "  PLATFORM_ADMIN_PASSWORD: " PLATFORM_ADMIN_PASSWORD; echo

  JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  GUEST_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

  umask 077
  cat > "$SECRETS_FILE" <<EOF
PROJECT=$PROJECT
REGION=$REGION_FROM_JSON
MONGO_URL=$MONGO_URL
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
DB_NAME=barflow
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
  # and a deploy that minted five of them per run would bury the one version anybody
  # ever needs to look at — the current MONGO_URL — under a hundred identical ones.
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
  echo "  1. Atlas Network Access does not allow 0.0.0.0/0"
  echo "  2. MONGO_URL still contains the literal <db_password>, or an unencoded @ or #"
  echo "  3. the cluster is paused"
  echo "  4. the first request timed out on a cold start — try the curl once more"
  exit 1
fi
