#!/usr/bin/env bash
#
# Deploy BarFlow: the API to Cloud Run, the site to Firebase Hosting.
#
# Safe to re-run. Secrets are generated once and kept in .deploy-secrets (gitignored);
# every later run reuses them, because regenerating GUEST_ID_ENCRYPTION_KEY would make
# every guest identity number already stored unreadable, permanently.
#
#   ./deploy.sh            everything
#   ./deploy.sh api        the Cloud Run service only
#   ./deploy.sh web        the Firebase site only
#
set -euo pipefail
cd "$(dirname "$0")"

SECRETS_FILE=".deploy-secrets"
TARGET="${1:-all}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 — $2"; exit 1; }; }
need gcloud "curl https://sdk.cloud.google.com | bash"
need firebase "npm install -g firebase-tools"

# ---------------------------------------------------------------- settings
if [ ! -f "$SECRETS_FILE" ]; then
  echo "First run. Four answers, then it is automatic from here."
  echo
  read -rp "Google Cloud project id (e.g. barflow-prod): " PROJECT
  read -rp "Region [asia-south1]: " REGION; REGION="${REGION:-asia-south1}"
  echo
  echo "MongoDB Atlas connection string."
  echo "  Replace <db_password> with the real password, and percent-encode any @ : / ? # in it."
  echo "  Atlas -> Network Access must allow 0.0.0.0/0: Cloud Run's outbound IP is not fixed."
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
REGION=$REGION
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
SITE="https://${PROJECT}.web.app"

# ---------------------------------------------------------------- api
if [ "$TARGET" = "all" ] || [ "$TARGET" = "api" ]; then
  echo "==> Cloud Run: barflow-api  ($REGION)"
  gcloud config set project "$PROJECT" --quiet
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com --quiet

  gcloud run deploy barflow-api \
    --source ./backend \
    --region "$REGION" \
    --allow-unauthenticated \
    --min-instances 0 \
    --max-instances 1 \
    --quiet \
    --set-env-vars "^@^MONGO_URL=${MONGO_URL}@DB_NAME=barflow@JWT_SECRET=${JWT_SECRET}@ADMIN_EMAIL=${ADMIN_EMAIL}@ADMIN_PASSWORD=${ADMIN_PASSWORD}@PLATFORM_ADMIN_EMAIL=${PLATFORM_ADMIN_EMAIL}@PLATFORM_ADMIN_PASSWORD=${PLATFORM_ADMIN_PASSWORD}@GUEST_ID_ENCRYPTION_KEY=${GUEST_ID_ENCRYPTION_KEY}@CORS_ORIGINS=${SITE}@CURRENCY_SYMBOL=₹@PROPERTY_TZ=Asia/Kolkata@TRUSTED_PROXY_HOPS=1@DEMO_LOGINS=false@SEED_DEMO_CONTENT=false@DAILY_BRIEF_ENABLED=true@OWNER_BRIEF_TIME=23:00"

  # max-instances 1 on purpose: the nightly WhatsApp brief is an in-process loop, so two
  # instances would send it twice. Rate limits are in the database and are safe across
  # instances; the brief is not. Lift this when the brief moves to Cloud Scheduler.
  echo "==> API deployed"
fi

# ---------------------------------------------------------------- web
if [ "$TARGET" = "all" ] || [ "$TARGET" = "web" ]; then
  echo "==> Building the site"
  # REACT_APP_BACKEND_URL empty = same origin. firebase.json rewrites /api/** to Cloud
  # Run, so the browser makes no cross-origin request at all. It is compiled in, not read
  # at runtime, which is why this rebuilds rather than restarts.
  ( cd frontend && npm ci --silent && CI=false REACT_APP_BACKEND_URL= npm run build )

  echo "==> Firebase Hosting"
  firebase use "$PROJECT" --non-interactive 2>/dev/null || firebase use --add
  firebase deploy --only hosting --project "$PROJECT" --non-interactive
fi

# ---------------------------------------------------------------- check
echo
echo "==> Checking $SITE/api/"
if curl -fsS --max-time 30 "$SITE/api/" ; then
  echo
  echo "Live."
  echo "  Site:     $SITE"
  echo "  Operator: $SITE/platform   ($PLATFORM_ADMIN_EMAIL)"
  echo "  Sign up:  $SITE/signup"
else
  echo
  echo "The API did not answer. Read the logs — it names its own cause:"
  echo "  gcloud run services logs read barflow-api --region $REGION --limit 50"
  echo
  echo "Most often one of:"
  echo "  1. Atlas Network Access does not allow 0.0.0.0/0"
  echo "  2. MONGO_URL still contains the literal <db_password>, or an unencoded @ or #"
  echo "  3. the cluster is paused"
  exit 1
fi
