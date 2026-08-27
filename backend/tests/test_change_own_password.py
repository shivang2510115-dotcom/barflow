"""Changing your own password: the one password route that is not somebody else's.

`POST /api/staff/{id}/password` is an admin resetting another account, and it is the
only thing this application had. So an ordinary waiter could not change the password
their manager typed in front of them at the counter, and the admin who wanted to change
their *own* had to find it on the staff screen, next to everybody else's.

`POST /api/auth/password` is the other half, and three things about it are the whole
test file:

* it asks for the **current** password. Without that, a laptop left unlocked for two
  minutes is a permanent account takeover rather than a borrowed session that expires —
  and the only defence a wrong guess gets is a 401 that changes nothing;
* it takes **no account id at all**. There is no request that names somebody else, so
  there is no filter to get wrong and no 404 to write. That is asserted here as a fact
  about the signature, not only as a behaviour, because a future `target_id` field would
  pass every behavioural test in this file;
* it hangs off `get_current_user`, not `require_access`. `require_access` resolves the
  caller's property and refuses anybody who has none, which is the platform operator's
  normal state by design — so the obvious dependency is the one that locks the operator
  out of their own password. `/auth/me` had to solve this exact problem and solved it
  the same way.

No server. The handler is an ordinary coroutine and is called as one, the same style as
test_password_strength.py; the two tests that are about *which dependency was declared*
read it off the real app instead, because that is not a question the handler can answer.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import db as db_module
import security
from mock_db import MockDatabase
from routers import auth
from services.access import PLATFORM_ADMIN

GOOD = "harbour-lamp-4127"
BETTER = "quiet-kettle-in-room-12"


def run(coro):
    return asyncio.run(coro)


def refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One live property, three logins, and every module's handle pointed at it.

    The three logins are the three shapes this route has to answer: the owner, somebody
    who is not an admin at all, and the platform operator who belongs to no property.
    """
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.setattr(security, "unscoped_db", handle)
    monkeypatch.setattr(auth, "unscoped_db", handle)

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))

    def add(id, email, role, password, **extra):
        doc = {"id": id, "email": email, "name": id, "role": role,
               "domains": ["hotel", "restaurant", "bar"], "permissions": [],
               "active": True, "property_id": "p1",
               "password_hash": security.hash_password(password),
               "created_at": now}
        doc.update(extra)
        run(handle.users.insert_one(dict(doc)))
        return doc

    return {
        "db": handle,
        "admin": add("a1", "owner@grand.example.com", "admin", GOOD),
        "waiter": add("w1", "riley@grand.example.com", "waiter", GOOD,
                      domains=["bar"], permissions=["outlet.pos"]),
        # No property_id at all — the operator's normal state, and the reason
        # `require_access` would be the wrong dependency here.
        "operator": add("op", "ops@barflow.io", PLATFORM_ADMIN, GOOD,
                        property_id=None, domains=[]),
    }


def change(user, current, new):
    return run(auth.change_own_password(
        auth.ChangePasswordIn(current_password=current, new_password=new), user))


def stored_hash(world, user_id):
    return run(world["db"].users.find_one({"id": user_id}, {"_id": 0}))["password_hash"]


# ------------------------- the current password is required -------------------------

def test_the_wrong_current_password_is_refused(world):
    before = stored_hash(world, "a1")

    refusal = refused(lambda: change(world["admin"], "not-the-password", BETTER))

    # 401 and not 403: what failed is the proof of who you are, which is exactly what a
    # 401 means. A 403 would read as "your role cannot do this", which is not true of
    # anybody here — everyone may change their own password, this one just did not.
    assert refusal.status_code == 401
    assert stored_hash(world, "a1") == before
    assert security.verify_password(GOOD, stored_hash(world, "a1"))


def test_an_empty_current_password_is_refused(world):
    """The shape a client with a blank field sends. Refused like any other wrong one —
    an unlocked laptop must not be able to skip the check by sending nothing."""
    assert refused(lambda: change(world["admin"], "", BETTER)).status_code == 401
    assert security.verify_password(GOOD, stored_hash(world, "a1"))


def test_the_right_current_password_changes_it_and_the_old_one_stops_working(world):
    assert change(world["admin"], GOOD, BETTER) == {"ok": True}

    row = stored_hash(world, "a1")
    assert security.verify_password(BETTER, row)
    # The half that matters: a change that leaves the old password working is not one.
    assert not security.verify_password(GOOD, row)
    # And the new one is now the current one, so changing again asks for it.
    assert change(world["admin"], BETTER, "third-one-goes-here-9") == {"ok": True}
    assert refused(lambda: change(world["admin"], BETTER, GOOD)).status_code == 401


# ------------------------- the strength rule is the same one -------------------------

@pytest.mark.parametrize("weak", ["password", "12345678", "short", "admin123", "aaaaaaaa"])
def test_a_weak_new_password_is_refused(world, weak):
    """services/password.py, reused — not a second rule that drifts from it. Each of
    these is refused by `password_problem`, and the point of the test is that this door
    consults it at all."""
    refusal = refused(lambda: change(world["admin"], GOOD, weak))
    assert refusal.status_code == 400
    assert security.verify_password(GOOD, stored_hash(world, "a1"))


def test_your_own_email_is_refused_here_too(world):
    """`password_problem` takes the account's address, and the account is the caller's
    own — so the check has to be handed the caller's email, not left off."""
    assert refused(lambda: change(world["admin"], GOOD, "owner")).status_code == 400
    assert refused(
        lambda: change(world["admin"], GOOD, "owner@grand.example.com")).status_code == 400


def test_the_strength_rule_runs_after_the_current_password_check(world):
    """A wrong current password with a weak new one is a 401, not a 400.

    Otherwise the error code tells somebody holding a stolen session whether their guess
    at the current password was right — they would send a deliberately weak new password
    and read 400 as "that current password was correct"."""
    assert refused(
        lambda: change(world["admin"], "not-the-password", "password")).status_code == 401


def test_the_password_you_already_have_is_refused(world):
    """Not a security rule — a plain one. A form that accepts the same password twice
    and says "changed" has told the person something untrue about their own account."""
    refusal = refused(lambda: change(world["admin"], GOOD, GOOD))
    assert refusal.status_code == 400
    assert security.verify_password(GOOD, stored_hash(world, "a1"))


# ------------------------- who may do it: everybody, for themselves -------------------

def test_a_non_admin_can_change_their_own(world):
    """The whole reason this route exists. A waiter is refused every admin route, and
    `POST /staff/{id}/password` is one of them, so before this they had no way at all."""
    assert change(world["waiter"], GOOD, BETTER) == {"ok": True}
    assert security.verify_password(BETTER, stored_hash(world, "w1"))


def test_the_platform_operator_can_change_their_own(world):
    """The case the wrong dependency breaks silently.

    The operator holds no `property_id`, so `require_access` refuses them before it looks
    at anything else (services/access.py::_property_usable) — and every hotel endpoint is
    refused them deliberately. Their own password is not a hotel endpoint.
    """
    assert change(world["operator"], GOOD, BETTER) == {"ok": True}
    assert security.verify_password(BETTER, stored_hash(world, "op"))


def test_one_persons_change_leaves_everybody_else_alone(world):
    before_waiter = stored_hash(world, "w1")
    before_operator = stored_hash(world, "op")

    change(world["admin"], GOOD, BETTER)

    assert stored_hash(world, "w1") == before_waiter
    assert stored_hash(world, "op") == before_operator


def test_there_is_no_way_to_name_anybody_else(world):
    """The strongest form of "nobody can change anyone else's": there is nothing to name.

    A behavioural test can only try the ways of naming somebody that exist today. This
    reads the signature instead, so a `target_id` added to the payload or a `{staff_id}`
    added to the path fails here rather than passing every other test in this file.
    """
    import inspect

    assert set(auth.ChangePasswordIn.model_fields) == {"current_password", "new_password"}

    params = inspect.signature(auth.change_own_password).parameters
    # The payload, and the caller resolved from their own token. Nothing else.
    assert list(params) == ["payload", "user"]

    routes = [r for r in auth.router.routes if getattr(r, "path", None) == "/auth/password"]
    assert len(routes) == 1
    assert routes[0].methods == {"POST"}
    # No path parameter at all, so there is no id to substitute somebody else's into.
    assert not routes[0].param_convertors


def test_the_id_in_the_token_is_what_is_written_not_anything_in_the_payload(world):
    """Belt and braces on the same rule, from the other side: the update is filtered by
    the caller's own id, so a handler handed a caller writes to that caller and no one
    else, whatever else is in flight."""
    forged = dict(world["waiter"])
    change(forged, GOOD, BETTER)

    assert security.verify_password(BETTER, stored_hash(world, "w1"))
    assert security.verify_password(GOOD, stored_hash(world, "a1"))


# ------------------------- the dependency it actually declares -------------------------

def test_the_route_hangs_off_get_current_user_and_not_require_access():
    """Read off the assembled application, because the handler cannot tell you this.

    `require_access` resolves a property and refuses a caller who has none, which is the
    operator, permanently. `get_current_user` still refuses a deactivated account (see
    security.py), so this is not a way around that — it is the same trade `/auth/me`
    makes, and for the same reason.
    """
    import inspect
    from fastapi.params import Depends as DependsMarker
    from fastapi.routing import APIRoute
    from server import app

    route = next(r for r in app.routes
                 if isinstance(r, APIRoute) and r.path == "/api/auth/password")
    depends = [p.default for p in inspect.signature(route.endpoint).parameters.values()
               if isinstance(p.default, DependsMarker)]
    assert [d.dependency for d in depends] == [security.get_current_user]


def test_the_admin_reset_route_is_untouched_and_still_admin_only():
    """This work adds a door; it does not widen the one that was there. `ADMIN` is
    `require_access(SHARED, "admin", ...)`, and the reset route still takes it."""
    import inspect
    from fastapi.params import Depends as DependsMarker

    from routers import staff

    depends = [p.default for p in inspect.signature(staff.reset_password).parameters.values()
               if isinstance(p.default, DependsMarker)]
    assert [d.dependency for d in depends] == [staff.ADMIN]
    # And it still takes the id of somebody else, which is what makes it the other route.
    assert "staff_id" in inspect.signature(staff.reset_password).parameters
