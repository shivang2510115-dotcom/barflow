#!/usr/bin/env python3
"""Clear one property's trial transactions, keeping everything it was set up with.

Written for the morning a hotel stops testing and starts trading. It deletes what was
recorded during the trial — bookings, guests, folios, bills, orders, housekeeping jobs,
attendance, payroll — and keeps what was configured: rooms, room types, rates, the menu,
tables, packages, outlets and staff logins.

The table QR codes matter here and are the reason `tables` is on the keep list: those
twenty printed cards encode table ids, and deleting the rows would turn every one of them
into a dead link.

    python3 reset-trial-data.py                 # count only, deletes nothing
    python3 reset-trial-data.py --delete        # actually delete

Counting is the default on purpose. A destructive script whose safe mode needs a flag is
one keystroke away from being the wrong script.
"""
import json
import subprocess
import sys
import urllib.parse

BASE = "https://barflow-33e80.web.app/api"

# Recorded during the trial. Gone.
CLEAR = [
    "bookings", "guests", "folios", "folio_entries", "bills",
    "orders", "housekeeping_jobs", "housekeeping_events",
    "attendance", "salary_runs", "payslips", "advances",
    "entitlement_uses", "message_log", "message_claims",
    "reservations", "expenses",
]

# Configured, and kept. Listed rather than implied, so the next person reading this can
# see what survives without inferring it from what does not appear above.
KEEP = [
    "rooms", "room_types", "rates", "rate_periods", "menu", "tables",
    "packages", "inclusions", "outlets", "tax_slabs", "meal_plans",
    "expense_categories", "calendar_categories", "users", "properties",
]


def curl(method, path, token=None, body=None):
    """The python.org macOS build ships without root certificates, so every HTTPS call
    in this repo's scripts goes through curl rather than urllib."""
    cmd = ["curl", "-s", "--max-time", "60", "-X", method, f"{BASE}{path}",
           "-H", "Content-Type: application/json"]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError:
        return None


def main():
    delete = "--delete" in sys.argv
    email = input("Anand Castle admin email: ").strip()
    password = input("Password: ").strip()

    auth = curl("POST", "/auth/login", body={"email": email, "password": password})
    token = (auth or {}).get("token")
    if not token:
        print("Could not sign in. Nothing was touched.")
        return 1

    me = curl("GET", "/auth/me", token)
    print(f"\nSigned in as {me.get('name')} · {me.get('role')}")
    if me.get("role") != "admin":
        print("Only an admin can do this. Nothing was touched.")
        return 1

    print(f"\n{'DELETING' if delete else 'COUNTING (nothing will be deleted)'}\n")
    result = curl("POST", "/property/reset-trial-data",
                  token, {"confirm": "DELETE" if delete else ""})
    if result is None:
        print("The server did not answer.")
        return 1
    if "detail" in result:
        print(f"Refused: {result['detail']}")
        return 1

    for name in CLEAR:
        n = result.get("counts", {}).get(name, 0)
        if n:
            print(f"  {name:22} {n:>6} {'deleted' if delete else 'would be deleted'}")
    print(f"\n  kept, untouched: {', '.join(KEEP)}")
    if not delete:
        print("\nNothing was deleted. Run again with --delete to do it for real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
