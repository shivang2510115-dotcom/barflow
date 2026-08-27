#!/usr/bin/env bash
#
# Copy backend/ into functions/backend/ so it travels in the deploy bundle.
#
# The Firebase CLI uploads the functions source directory and nothing above it, so this
# is how `backend/` reaches the runtime at all. firebase.json runs it as a predeploy
# hook; run it by hand before `firebase emulators:start` or any local test of main.py.
#
# The copy is generated, gitignored and rebuilt from scratch every time. It is not a
# fork and must never be edited: `backend/` is the only version under review, and the
# only one `uvicorn server:app`, the Docker image and the tests ever see. Anything typed
# into functions/backend/ is deleted by the next deploy without saying so.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../backend"
DST="$HERE/backend"

if [ ! -f "$SRC/server.py" ]; then
  echo "vendor-backend.sh: $SRC/server.py does not exist — run this from a checkout." >&2
  exit 1
fi

# The same exclusions as backend/.dockerignore, for the same reasons, and they are worth
# repeating rather than pointing at:
#
#   .env        holds the JWT signing key, the Atlas password and the key that decrypts
#               guest identity documents. In the bundle it would both ship those secrets
#               and silently override the environment the deployment injects, so the
#               function would run on development keys without ever saying so.
#   db.json     is one property's real guests, folios and ID numbers. It is also what
#               the app falls back to when MONGO_URL is unset, so a copy present in the
#               bundle would be served as though it were the hotel's own data.
#   tests/      is not run in the deployed function and carries test-only fixtures.
#   caches      can shadow the source next to them with stale bytecode.
rsync -a --delete \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'db.json' \
  --exclude 'db_*.json' \
  --exclude '__pycache__/' \
  --exclude '*.py[cod]' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '*.egg-info/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'tests/' \
  --exclude '.DS_Store' \
  --exclude '*.swp' \
  "$SRC/" "$DST/"

echo "vendor-backend.sh: $SRC -> $DST"
