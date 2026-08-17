"""What a fresh start creates, and what it must not.

The default is the whole point of this file. Demo content — a showcase bar's tables,
menu, stock, room types and rates — is furniture a real hotel would have to delete by
hand, item by item, before a single number on its dashboard meant anything. So the flag
that plants it is off unless someone asks, and this test is what stops the default from
drifting back the way defaults do.
"""
import os

import pytest

from server import demo_content_enabled


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("SEED_DEMO_CONTENT", raising=False)


def test_demo_content_is_off_unless_asked_for():
    assert demo_content_enabled() is False


def test_demo_content_is_on_when_the_flag_says_so(monkeypatch):
    # A clone that should be usable in one command says so explicitly, the same way
    # DEMO_LOGINS is turned off explicitly on a public deployment.
    monkeypatch.setenv("SEED_DEMO_CONTENT", "true")
    assert demo_content_enabled() is True
    monkeypatch.setenv("SEED_DEMO_CONTENT", "TRUE")
    assert demo_content_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "yes-please"])
def test_anything_that_is_not_true_leaves_it_off(value, monkeypatch):
    """Fails closed: an unrecognised value plants nothing rather than everything."""
    monkeypatch.setenv("SEED_DEMO_CONTENT", value)
    assert demo_content_enabled() is False
