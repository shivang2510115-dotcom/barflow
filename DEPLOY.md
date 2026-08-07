# Deploying BarFlow

Two services from one `render.yaml` blueprint: a Dockerised FastAPI API and a static
React site. Data lives in MongoDB Atlas. All three tiers are free.

The result is a permanent URL — `https://barflow-web.onrender.com` — that works on any
laptop whether or not your Mac is on, and does not change between deploys.

---

## 1. Database — MongoDB Atlas

1. Create a free **M0** cluster at <https://cloud.mongodb.com>.
2. **Database Access** → add a user, save the password.
3. **Network Access** → allow `0.0.0.0/0`. Render's outbound IPs aren't fixed on the
   free plan, so an IP allowlist can't work here. The database is still protected by
   its username and password.
4. Copy the connection string. It looks like:
   `mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`

Skip this and the API silently falls back to a JSON file that is **wiped on every
restart and every redeploy**. It will look like it works, then lose a day of bills.

## 2. Push the code

The working tree isn't a git repo yet:

```bash
cd ~/dev/bar-management-system
git init && git add . && git commit -m "feat: deploy config for Render + Atlas"
git remote add origin https://github.com/<you>/bar-management-system.git
git push -u origin main
```

`.gitignore` already keeps `node_modules`, `build/`, `.env` and `backend/db.json` out.
Confirm before pushing: `git status --short` should list no `.env` and no `db.json`.

## 3. Deploy both services

1. <https://dashboard.render.com> → **New** → **Blueprint** → pick the repo.
2. Render reads `render.yaml` and proposes **barflow-api** and **barflow-web**.
3. It will prompt for every `sync: false` variable. Set at minimum:

   | Variable | Value |
   |---|---|
   | `MONGO_URL` | the Atlas string from step 1 |
   | `ADMIN_EMAIL` | your real email |
   | `ADMIN_PASSWORD` | a real password — blank seeds the published default `admin123` |
   | `CORS_ORIGINS` | leave blank for now |
   | `REACT_APP_BACKEND_URL` | leave blank for now |

4. Apply. The API builds first; note its URL, e.g. `https://barflow-api.onrender.com`.

## 4. Point the two services at each other

Chicken-and-egg: neither URL exists until the first deploy, so set them after.

1. **barflow-web** → Environment → `REACT_APP_BACKEND_URL` = the API URL, **with**
   `https://` and **no** trailing slash. Save, then **Manual Deploy → Clear build cache
   & deploy** — this value is compiled into the bundle, so a restart alone does nothing.
2. **barflow-api** → Environment → `CORS_ORIGINS` = the site URL, with `https://`.
   Save; the API restarts on its own.

Leave `CORS_ORIGINS` unset and it defaults to `*`. Combined with `allow_credentials`,
that lets any website on the internet call your API with a logged-in user's token.

## 5. Check it

```bash
curl https://barflow-api.onrender.com/api/          # {"service":"BarFlow API","status":"ok"}
```

Then open the site, log in as `ADMIN_EMAIL`, add a table and a menu item, and reload.
If the data survives, Atlas is wired up correctly.

---

## Known trade-offs of the free tier

- **Cold starts.** A free API sleeps after ~15 minutes idle; the next request takes
  40–60s. Before a client demo, load the site once to wake it. A paid instance
  (~$7/mo) removes this and is the single upgrade worth making.
- **Build minutes and bandwidth** are capped monthly. Fine for demos, not for a
  venue running real service.

## Before real money goes through it

- `STRIPE_API_KEY` currently defaults to `sk_test_emergent`. Live payments need a real
  key **and** the webhook endpoint `/api/webhook/stripe` registered in Stripe.
- `DAILY_BRIEF_ENABLED` is `false` in the blueprint. Turning it on needs
  `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` and `OWNER_PHONE`; without them the scheduler
  only writes the message to the log.
- `DEMO_LOGINS` must stay `false`. It is `true` by default so a fresh local clone is
  usable immediately, and that seeds manager/waiter/kitchen accounts whose passwords
  are committed to this repo.
