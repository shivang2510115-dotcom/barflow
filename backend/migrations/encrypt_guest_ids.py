"""One-shot: encrypt the identity-document number on every existing guest.

Idempotent — a value that already carries the `enc:v1:` label is left alone, so
re-running is safe and so is running it against a collection that is half done because
the last run was interrupted.

Does nothing at all when GUEST_ID_ENCRYPTION_KEY is unset. That is not a failure: an
unset key means this deployment has chosen plain text (see services/crypto.py), and a
migration that encrypted anyway would produce rows nothing could ever read.

    cd backend && MONGO_URL=... GUEST_ID_ENCRYPTION_KEY=... \
        python3 -m migrations.encrypt_guest_ids

Also imported by server.py and run at startup, like the other migrations here: this app
deploys as a container with no manual shell step, so a migration nobody runs is a
migration that never runs. It reads the whole guests collection, which is the same thing
the front-desk board does on every load.

Reads and writes `unscoped_db` on purpose — every property's guests at once, because
this is maintenance on the storage format rather than a hotel looking at its own data,
and it does its own encrypting rather than going through the scoped handle that would
otherwise do it invisibly.
"""
import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import db as _db_module  # noqa: E402  the module, not the handle: a test that swaps
#                          the database swaps it here too. See routers/orders.py.
from scoped_db import ENCRYPTED_FIELDS  # noqa: E402
from services.crypto import (  # noqa: E402
    ENV_VAR, encrypt_secret, encryption_configured, looks_encrypted)

COLLECTION = "guests"
FIELDS = ENCRYPTED_FIELDS[COLLECTION]


async def backfill() -> tuple[int, int, int]:
    """Encrypt what is not yet encrypted. Returns (encrypted, already, skipped).

    * encrypted — rows this run rewrote;
    * already — rows whose value was already encrypted, by an earlier run or by the
      application having written them after the key was set;
    * skipped — rows with nothing to encrypt: no number recorded, or no key configured
      (in which case every row is skipped and nothing is written).
    """
    if not encryption_configured():
        total = await _db_module.unscoped_db[COLLECTION].count_documents({})
        return 0, 0, total

    guests = await _db_module.unscoped_db[COLLECTION].find({}, {"_id": 0}).to_list(100000)
    encrypted = already = skipped = 0
    for guest in guests:
        patch = {}
        for field in FIELDS:
            value = guest.get(field)
            if not value:
                continue
            if looks_encrypted(value):
                already += 1
                continue
            patch[field] = encrypt_secret(value)
        if not patch:
            if not any(guest.get(f) for f in FIELDS):
                skipped += 1
            continue
        await _db_module.unscoped_db[COLLECTION].update_one({"id": guest["id"]}, {"$set": patch})
        encrypted += 1
    return encrypted, already, skipped


async def main() -> None:
    if not encryption_configured():
        print(f"{ENV_VAR} is not set — nothing to do; these fields stay in plain text.")
        return
    encrypted, already, skipped = await backfill()
    print(f"guests encrypted: {encrypted}, already encrypted: {already}, "
          f"nothing recorded: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
