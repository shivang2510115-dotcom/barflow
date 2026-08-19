"""Guest identity documents, and what is on disk if this database is ever copied.

`id_proof_number` is an Aadhaar, a passport or a driving licence number, collected at
check-in because Indian law requires it. It was written to the database in plain text —
demonstrated below against a handle with no key configured, which is what every
deployment was — and one database now holds many hotels' guests, so a single leaked
connection string is an identity-document dump for all of them at once.

What is asserted here, in order: that the number does not appear in the stored row; that
the desk still reads it back; that an unset key keeps the application working rather than
losing a hotel its check-in; and what happens when a key is lost or changed, which is the
failure mode that actually matters.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

import db as db_module
import services.crypto as crypto
from mock_db import MockDatabase
from scoped_db import PropertyScopedDatabase
from migrations.encrypt_guest_ids import backfill as encrypt_guest_ids

AADHAAR = "9090-8080-7070"
KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One hotel's scoped handle over a fresh mock database, with no key set."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.delenv(crypto.ENV_VAR, raising=False)
    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))
    return handle, PropertyScopedDatabase("p1")


def check_in(db, guest_id="g1", number=AADHAAR, name="Nina Patel"):
    """A guest record as the front desk creates one."""
    run(db.guests.insert_one({
        "id": guest_id, "name": name, "phone": f"99{guest_id}", "email": None,
        "address": None, "nationality": "Indian", "id_proof_type": "Aadhaar",
        "id_proof_number": number, "notes": None,
        "created_at": datetime.now(timezone.utc).isoformat()}))


def stored(handle, guest_id="g1") -> dict:
    """The row as it sits on disk — read around the scoped handle, deliberately, because
    what is on disk is the whole question."""
    return run(handle.guests.find_one({"id": guest_id}, {"_id": 0}))


# ------------------------------- the hole itself -------------------------------
def test_with_no_key_the_document_number_is_on_disk_in_plain_text(world):
    """The state every deployment was in, asserted rather than remembered — and the
    behaviour that is deliberately kept when no key is configured."""
    handle, db = world
    check_in(db)
    assert stored(handle)["id_proof_number"] == AADHAAR


def test_with_a_key_it_is_not(world, monkeypatch):
    handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db)

    on_disk = stored(handle)["id_proof_number"]
    assert AADHAAR not in on_disk
    assert on_disk.startswith(crypto.PREFIX)
    # Nor anywhere else in the row: not copied into a second field, not left in a log of
    # the document that was written.
    assert AADHAAR not in str(stored(handle))


def test_the_desk_still_reads_it_back(world, monkeypatch):
    """Encryption at rest, not hashing. A guest disputing a bill, a police verification
    or a Form C all need the number itself."""
    _handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db)
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == AADHAAR
    assert run(db.guests.find({}).to_list(10))[0]["id_proof_number"] == AADHAAR


def test_the_same_number_twice_does_not_produce_the_same_ciphertext(world, monkeypatch):
    """Otherwise the storage leaks which guests share a document, and a stolen database
    can be searched for a known number by encrypting it."""
    handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db, "g1")
    check_in(db, "g2")
    assert stored(handle, "g1")["id_proof_number"] != stored(handle, "g2")["id_proof_number"]


def test_an_update_at_check_in_is_encrypted_too(world, monkeypatch):
    """The front desk writes this field through `$set` on an existing guest, not through
    an insert — that is the path check-in actually takes."""
    handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db, number=None)
    run(db.guests.update_one({"id": "g1"}, {"$set": {
        "id_proof_type": "Passport", "id_proof_number": "Z1234567"}}))

    assert "Z1234567" not in str(stored(handle))
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == "Z1234567"


def test_the_other_fields_are_left_alone(world, monkeypatch):
    """Only the identity document. The name and phone are queried and sorted on, and
    encrypting them would break the guest search this hotel runs all day."""
    handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db)
    row = stored(handle)
    assert row["name"] == "Nina Patel"
    assert row["nationality"] == "Indian"
    assert row["id_proof_type"] == "Aadhaar"


def test_searching_by_the_encrypted_field_is_refused_rather_than_silent(world, monkeypatch):
    """It would match nothing, every time, and look like a missing guest."""
    from scoped_db import UnscopedCollectionError
    _handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    with pytest.raises(UnscopedCollectionError):
        run(db.guests.find_one({"id_proof_number": AADHAAR}))


# ------------------------------- the migration -------------------------------
def test_the_migration_encrypts_what_is_already_there(world, monkeypatch):
    handle, db = world
    check_in(db, "g1")                      # written before the key existed
    check_in(db, "g2", number=None)         # no document recorded
    assert stored(handle, "g1")["id_proof_number"] == AADHAAR

    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    assert run(encrypt_guest_ids()) == (1, 0, 1)
    assert AADHAAR not in str(stored(handle, "g1"))
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == AADHAAR


def test_the_migration_is_idempotent(world, monkeypatch):
    handle, db = world
    check_in(db)
    monkeypatch.setenv(crypto.ENV_VAR, KEY)

    run(encrypt_guest_ids())
    once = stored(handle)["id_proof_number"]
    assert run(encrypt_guest_ids()) == (0, 1, 0)
    assert stored(handle)["id_proof_number"] == once
    # Not encrypted twice, which would read back as `enc:v1:…` in front of a receptionist.
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == AADHAAR


def test_the_migration_does_nothing_without_a_key(world):
    handle, db = world
    check_in(db)
    assert run(encrypt_guest_ids()) == (0, 0, 1)
    assert stored(handle)["id_proof_number"] == AADHAAR


def test_a_half_migrated_collection_reads_correctly(world, monkeypatch):
    """The state during a rollout, and after an interrupted migration: some rows
    encrypted, some not. Both have to come back readable."""
    handle, db = world
    check_in(db, "g1", number="OLD-PLAIN-1")
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db, "g2", number="NEW-ENC-2")

    assert stored(handle, "g1")["id_proof_number"] == "OLD-PLAIN-1"
    assert stored(handle, "g2")["id_proof_number"].startswith(crypto.PREFIX)
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == "OLD-PLAIN-1"
    assert run(db.guests.find_one({"id": "g2"}))["id_proof_number"] == "NEW-ENC-2"


# --------------------------- when the key goes wrong ---------------------------
def test_a_lost_key_blanks_the_field_rather_than_crashing_check_in(world, monkeypatch):
    """The failure mode that matters. The number is gone — nothing can recover it — but
    the desk gets an empty field and asks for the document again, rather than a 500 in
    the middle of a check-in."""
    _handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db)

    monkeypatch.delenv(crypto.ENV_VAR)
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] is None
    # And the rest of the record is untouched, so the guest is still findable.
    assert run(db.guests.find_one({"id": "g1"}))["name"] == "Nina Patel"


def test_a_changed_key_does_the_same(world, monkeypatch):
    _handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db)
    monkeypatch.setenv(crypto.ENV_VAR, OTHER_KEY)
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] is None


def test_rotation_works_when_the_old_key_is_kept_in_the_list(world, monkeypatch):
    """Which is the whole reason the variable takes a list: new key first, old key
    second, re-run the migration, then drop the old one."""
    handle, db = world
    monkeypatch.setenv(crypto.ENV_VAR, KEY)
    check_in(db)

    monkeypatch.setenv(crypto.ENV_VAR, f"{OTHER_KEY},{KEY}")
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == AADHAAR

    # Re-encrypting under the new key is the migration's job, and afterwards the old key
    # can be dropped without losing anything.
    run(db.guests.update_one({"id": "g1"}, {"$set": {"id_proof_number": AADHAAR}}))
    monkeypatch.setenv(crypto.ENV_VAR, OTHER_KEY)
    assert run(db.guests.find_one({"id": "g1"}))["id_proof_number"] == AADHAAR
    assert AADHAAR not in str(stored(handle))


def test_a_malformed_key_is_refused_rather_than_silently_ignored(monkeypatch):
    """Somebody meant to encrypt and mistyped. Falling back to plain text under a
    variable whose name promises otherwise is how this gets discovered in a breach
    report instead of at startup."""
    monkeypatch.setenv(crypto.ENV_VAR, "not-a-real-fernet-key")
    with pytest.raises(RuntimeError, match=crypto.ENV_VAR):
        crypto.encryption_configured()


def test_a_blank_key_counts_as_unset(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, "   ")
    assert crypto.encryption_configured() is False
