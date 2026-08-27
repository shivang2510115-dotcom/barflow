"""Collect nothing at all unless the Firestore emulator is up.

A `pytest.mark.skipif` would be the usual way, and it is the wrong one here. The
product's suite has published baselines — `445 passed` for the pure tests — and a bare
`pytest` run from `backend/` sweeps this directory in too. Skipped tests would turn that
into `445 passed, 26 skipped`, which is not the baseline any more and takes a moment to
re-derive every time somebody checks. Not collecting leaves the number alone.

The suite is still asked for by name and still runs in full:

    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python3 -m pytest tests_firestore/ -q
"""
import os

if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
    collect_ignore_glob = ["*.py"]
