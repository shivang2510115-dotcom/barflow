"""Encryption at rest for the few fields that are somebody's identity document.

`id_proof_number` is an Aadhaar, a passport or a driving licence number. It sat in the
database in plain text, and now that one database holds many hotels' guests, a single
compromise — a leaked connection string, a snapshot copied to a laptop, an Atlas
allowlist opened wider than intended — is an identity-document dump for every hotel on
the platform at once.

Encryption at rest, not hashing: the front desk has to read the number back. A guest
disputing a bill, a police verification request, a Form C — all of them need the number
itself, so the requirement is "unreadable in the database" and not "unreadable at all".

Fernet (AES-128-CBC with an HMAC, from `cryptography`, already a dependency). Authenticated,
so a tampered ciphertext is a detected failure rather than silent garbage, and each token
carries its own random IV so two guests with the same passport number do not produce the
same stored value.

**No key means plain text, deliberately.** A hotel mid-check-in must not lose the ability
to register a guest because a variable is missing; the whole point of this application is
that the desk keeps working. So an unset key leaves the field exactly as it is today,
with a warning at startup that says so in as many words. That choice is the opposite of
the one the Stripe webhook makes, and for a reason: an unverified webhook lets a stranger
take money, while an unencrypted ID number is only as exposed as it was yesterday.

A key that is *set but unreadable* does raise. It means somebody intended encryption and
mistyped it, and quietly writing plain text under a variable whose name promises
otherwise is the failure mode that gets discovered in a breach report.
"""
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)

ENV_VAR = "GUEST_ID_ENCRYPTION_KEY"

# Stored values carry their own label, which is what makes every read able to cope with
# a database that is half migrated and what makes the migration idempotent: a value that
# already starts with this is not encrypted a second time. Versioned so that a future
# change of scheme can be told from this one at rest rather than guessed at by length.
PREFIX = "enc:v1:"

# What a read returns when a value is encrypted and this deployment cannot decrypt it —
# the key was lost, rotated away, or the row came from another deployment. `None`, the
# same as "no number recorded", so the desk sees an empty field and asks the guest for
# the document again rather than meeting a 500 in the middle of a check-in.
UNREADABLE = None

_cache: tuple[str, Optional[MultiFernet]] = ("\0unset", None)


def _cipher() -> Optional[MultiFernet]:
    """The configured cipher, or None when no key is set.

    More than one key may be listed, comma-separated, newest first: everything is
    written with the first and read with any of them, which is what makes rotating a key
    possible without rewriting every row on the same day. Drop the old key from the list
    once the migration has been re-run under the new one.
    """
    global _cache
    raw = (os.environ.get(ENV_VAR) or "").strip()
    if _cache[0] == raw:
        return _cache[1]

    if not raw:
        _cache = (raw, None)
        return None

    keys = [part.strip() for part in raw.split(",") if part.strip()]
    try:
        cipher = MultiFernet([Fernet(k.encode("utf-8")) for k in keys])
    except Exception as exc:  # noqa: BLE001 — any malformed key lands here
        raise RuntimeError(
            f"{ENV_VAR} is set but is not a valid key: {exc}. It must be a urlsafe "
            f"base64-encoded 32-byte key — generate one with:\n"
            f"  python3 -c \"from cryptography.fernet import Fernet; "
            f"print(Fernet.generate_key().decode())\"\n"
            f"Several may be listed comma-separated, newest first, while rotating. "
            f"Unset the variable entirely to store these fields in plain text."
        ) from exc

    _cache = (raw, cipher)
    return cipher


def encryption_configured() -> bool:
    """Whether anything written from now on will be encrypted. Raises if the key is
    set and malformed, which is why startup calls it."""
    return _cipher() is not None


def looks_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    """The value as it should be stored.

    Unchanged when there is no key, when there is nothing to encrypt, or when it is
    already encrypted — so this is safe to apply to a value that has been round-tripped
    through a read.
    """
    if value is None or value == "" or looks_encrypted(value):
        return value
    cipher = _cipher()
    if cipher is None:
        return value
    return PREFIX + cipher.encrypt(str(value).encode("utf-8")).decode("utf-8")


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """The value as a person should read it.

    A value with no prefix is returned as it is: rows written before this existed, and
    rows written while the key was unset, are plain text and stay readable.
    """
    if not looks_encrypted(value):
        return value

    cipher = _cipher()
    if cipher is None:
        # Encrypted rows and no key. Logged every time rather than once: this is a
        # deployment that has lost the ability to read its own guests' documents, and it
        # should be noisy until somebody puts the key back.
        logger.warning(
            "A guest identity document is encrypted but %s is not set, so it cannot be "
            "read. Set the key that wrote it, or the field stays blank.", ENV_VAR)
        return UNREADABLE

    try:
        return cipher.decrypt(value[len(PREFIX):].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning(
            "A guest identity document cannot be decrypted with any key in %s. It was "
            "written with a key that is no longer listed — add it back, newest first.",
            ENV_VAR)
        return UNREADABLE
