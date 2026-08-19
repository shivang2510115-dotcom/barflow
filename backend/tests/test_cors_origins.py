"""Which websites may call this API with a logged-in user's cookie.

`allow_credentials=True` and `allow_origins=["*"]` are each defensible alone and are a
hole together: the browser sends the session, the origin check passes for everybody, and
any page a signed-in manager happens to open can read their hotel's guests and post to
their folios. The default used to be exactly that pair, because `CORS_ORIGINS` was read
with `'*'` as its fallback.

The first test below is the hole itself, asserted against the app that is actually
assembled rather than against a helper — the middleware stack is what a browser meets.
"""
import pytest

import server
from server import CORS_DEV_ORIGINS, app, cors_origins


def cors_options() -> dict:
    """The options the CORS middleware was actually installed with."""
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return middleware.kwargs
    raise AssertionError("the app has no CORS middleware")


# ------------------------------- the hole itself -------------------------------
def test_credentials_are_never_allowed_from_every_origin():
    options = cors_options()
    if options.get("allow_credentials"):
        assert "*" not in options["allow_origins"], (
            "any website a signed-in user visits can call this API with their session")


def test_the_assembled_app_allows_only_named_origins():
    """The same statement without the conditional: this app does allow credentials, and
    every origin it names is a real one."""
    options = cors_options()
    assert options["allow_credentials"] is True
    assert options["allow_origins"]
    for origin in options["allow_origins"]:
        assert origin.startswith("http://") or origin.startswith("https://"), origin


# ------------------------------- the rules -------------------------------
@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)


def test_a_configured_list_is_what_is_used(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://barflow-web.onrender.com")
    assert cors_origins() == ["https://barflow-web.onrender.com"]


def test_several_origins_are_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example , https://b.example ,")
    assert cors_origins() == ["https://a.example", "https://b.example"]


def test_a_wildcard_is_refused_outright(monkeypatch):
    """Not narrowed, not warned about — refused. There is no deployment of this app for
    which `*` is correct while credentials are allowed."""
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        cors_origins()


def test_a_wildcard_hidden_in_a_list_is_refused_too(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://barflow-web.onrender.com,*")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        cors_origins()


def test_unset_against_the_mock_database_is_local_development(monkeypatch):
    """A fresh clone runs `npm start` and hits a local API. Refusing to start there would
    make every new contributor's first act be setting a variable to a value this file
    already knows."""
    monkeypatch.setattr(server, "using_mock", True)
    assert cors_origins() == list(CORS_DEV_ORIGINS)
    assert "*" not in cors_origins()


def test_unset_against_a_real_database_refuses_to_start(monkeypatch):
    """The same shape of guard as JWT_SECRET and ADMIN_PASSWORD, and for the same reason:
    a wrong value here is invisible until somebody else's guest list has been read."""
    monkeypatch.setattr(server, "using_mock", False)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        cors_origins()


def test_a_blank_value_counts_as_unset(monkeypatch):
    """`CORS_ORIGINS=""` in a dashboard is somebody who meant to fill it in."""
    monkeypatch.setattr(server, "using_mock", False)
    monkeypatch.setenv("CORS_ORIGINS", "  , ")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        cors_origins()
