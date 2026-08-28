#!/usr/bin/env python3
"""Set up Anand Castle: 3 room types, 23 rooms across 3 floors, 20 tables.

Asks for the login rather than taking it on the command line, so the password never
lands in shell history. Idempotent: a room type or a room or a table that already
exists is left alone, so a half-finished run can simply be run again.

    python3 setup-anand-castle.py
"""
import getpass
import json
import shutil
import subprocess
import sys

SITE = "https://barflow-33e80.web.app"
API = f"{SITE}/api"

# Capacity 2 everywhere for now, and no rates — both are yours to set afterwards.
ROOM_TYPES = [
    ("Deluxe", "DLX"),
    ("Superior", "SUP"),
    ("Suite", "STE"),
]

# Every room, its type and its floor. Written out in full rather than derived from a
# rule: the numbering has no rule — 103 is Deluxe while 102 and 104 are Superior — so a
# clever loop would be a guess. This list is checkable against what you sent, line by line.
ROOMS = [
    # 1st floor
    ("101", "Superior", "1"), ("102", "Superior", "1"), ("103", "Deluxe", "1"),
    ("104", "Superior", "1"), ("105", "Suite", "1"), ("106", "Superior", "1"),
    ("107", "Superior", "1"),
    # 2nd floor
    ("201", "Suite", "2"), ("202", "Superior", "2"), ("203", "Superior", "2"),
    ("204", "Deluxe", "2"), ("205", "Superior", "2"), ("206", "Suite", "2"),
    ("207", "Superior", "2"), ("208", "Superior", "2"),
    # 3rd floor
    ("301", "Suite", "3"), ("302", "Superior", "3"), ("303", "Superior", "3"),
    ("304", "Deluxe", "3"), ("305", "Superior", "3"), ("306", "Suite", "3"),
    ("307", "Superior", "3"), ("308", "Superior", "3"),
]

TABLE_COUNT = 20

token = None


def call(path, body=None, method=None):
    """One request, through curl.

    Not urllib: the python.org build of Python on macOS ships without root certificates
    unless you separately run its "Install Certificates.command", and without them every
    HTTPS call dies with CERTIFICATE_VERIFY_FAILED. curl uses the system trust store, is
    already on every Mac, and sidesteps the whole thing.
    """
    cmd = ["curl", "-s", "--max-time", "90", "-w", "\n%{http_code}", API + path]
    cmd += ["-X", method or ("POST" if body is not None else "GET")]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    payload, _, code = out.rpartition("\n")
    try:
        return int(code or 0), json.loads(payload or "{}")
    except (ValueError, json.JSONDecodeError):
        return int(code or 0), payload


def main():
    global token

    # Sanity-check the data against the totals you gave, before touching anything.
    assert len(ROOMS) == 23, f"expected 23 rooms, the list has {len(ROOMS)}"
    by_type = {}
    for _, t, _f in ROOMS:
        by_type[t] = by_type.get(t, 0) + 1
    assert by_type == {"Deluxe": 3, "Superior": 15, "Suite": 5}, by_type

    if not shutil.which("curl"):
        print("curl is not on PATH — this script needs it.")
        sys.exit(1)

    print("Anand Castle setup — 3 room types, 23 rooms, 20 tables.")
    print("Sign in as the Anand Castle admin (not the platform operator).\n")
    email = input("  email or phone: ").strip()
    password = getpass.getpass("  password: ")

    status, body = call("/auth/login", {"email": email, "password": password})
    if status != 200:
        print(f"\nSign-in failed ({status}). {body}")
        print("That is the hotel's own admin login, the one you set when registering it.")
        sys.exit(1)
    token = body["token"]
    print(f"\nSigned in as {body['user']['name']} ({body['user']['role']}).")

    prop = call("/property")[1]
    print(f"Property: {prop.get('name')}  status={prop.get('status')}\n")

    # ---- room types
    existing = {t["name"]: t for t in call("/room-types")[1]}
    type_ids = {}
    for name, code in ROOM_TYPES:
        if name in existing:
            type_ids[name] = existing[name]["id"]
            print(f"  room type {name:9} already there")
            continue
        s, b = call("/room-types", {
            "name": name, "code": code,
            "base_occupancy": 2, "max_occupancy": 2, "max_extra_beds": 0,
        })
        if s != 200:
            print(f"  room type {name:9} FAILED {s}: {b}")
            sys.exit(1)
        type_ids[name] = b["id"]
        print(f"  room type {name:9} created")

    # ---- rooms
    have = {r["number"] for r in call("/rooms")[1]}
    made = skipped = 0
    for number, type_name, floor in ROOMS:
        if number in have:
            skipped += 1
            continue
        s, b = call("/rooms", {
            "number": number, "room_type_id": type_ids[type_name], "floor": floor,
        })
        if s != 200:
            print(f"  room {number} FAILED {s}: {b}")
            sys.exit(1)
        made += 1
    print(f"\n  rooms: {made} created, {skipped} already there")

    # ---- tables
    have_tables = {t["label"] for t in call("/tables")[1]}
    tmade = tskipped = 0
    for n in range(1, TABLE_COUNT + 1):
        label = f"T{n:02d}"
        if label in have_tables:
            tskipped += 1
            continue
        s, b = call("/tables", {"label": label, "capacity": 4, "zone": "Restaurant"})
        if s != 200:
            print(f"  table {label} FAILED {s}: {b}")
            sys.exit(1)
        tmade += 1
    print(f"  tables: {tmade} created, {tskipped} already there")

    # ---- what it looks like now
    rooms = call("/rooms")[1]
    print(f"\nAnand Castle now has {len(rooms)} rooms and "
          f"{len(call('/tables')[1])} tables.\n")
    for name, _ in ROOM_TYPES:
        nums = sorted(r["number"] for r in rooms if r["room_type_id"] == type_ids[name])
        print(f"  {name:9} {len(nums):>2}  {', '.join(nums)}")
    print("\nRates are yours to set: Hotel -> Rates. Nothing can be booked until one exists.")


if __name__ == "__main__":
    main()
