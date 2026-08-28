"""Occasions, follow-ups, and the one door every message has to go through.

WhatsApp permits free-form text only inside 24 hours of the customer messaging the
business. A birthday greeting is by definition outside that window, so it can only ever
be an **approved template** — a name Meta has reviewed, with its variables filled in at
send time. That single fact is what this feature is shaped around, and it is why nothing
here composes a sentence: a message is a template name plus a list of variables, and a
property that has not obtained a template has nothing to send.

Five claims, and they are the ones worth breaking the build over:

* an occasion recorded while a bill is settled appears on today's list on its date;
* pressing send twice sends once — enforced by a claim taken before the send, not by a
  button that greys out after the first click;
* a customer who has asked not to be messaged is never sendable, and the transport is
  never reached for them;
* the follow-up picks exactly the customers past the window and none inside it, sends
  itself from a scheduled job rather than from a screen, and messages each customer once
  per visit however many nights the job runs;
* a send with no template configured fails **naming what is missing**, and the message
  log carries that reason — the failure mode this whole design exists to prevent is a
  send that appears to have worked.

No server: the endpoints are ordinary coroutines and the scoped handle is an ordinary
dependency, the same style as test_isolation.py and test_meal_plans_setting.py.
"""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import db as db_module
import security
import routers.guests as guests_router
import routers.messaging as messaging
from mock_db import MockDatabase
from models.hotel import OccasionIn
from models.messaging import MessagingSettingsIn
from models.property import Property
from scoped_db import PropertyScopedDatabase
from services.access import DOMAINS, LIVE, SCREEN_KEYS
from services.clock import today as local_today
from services import messaging as messaging_service

# Every module that holds the unscoped handle itself and is reached from here.
_UNSCOPED_HOLDERS = (db_module, security)


def run(coro):
    return asyncio.run(coro)


def call(fn, **kwargs):
    return run(fn(**kwargs))


def refused(fn, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call(fn, **kwargs)
    return exc.value


def days_ago(n: int) -> str:
    return (date.fromisoformat(local_today()) - timedelta(days=n)).isoformat()


def month_day_today() -> str:
    return local_today()[5:]


@dataclass
class World:
    db: PropertyScopedDatabase
    admin: dict
    waiter: dict
    sends: list


def _staff(uid, role):
    return {"id": uid, "email": f"{uid}@grand.example.com", "name": role.title(),
            "role": role, "domains": list(DOMAINS), "permissions": list(SCREEN_KEYS),
            "active": True, "property_id": "p1"}


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    """One live property, an admin, a waiter, and a transport that records instead of
    sending. WhatsApp is deliberately left unconfigured at the environment level unless a
    test says otherwise — that is the state the owner is actually in."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    for module in _UNSCOPED_HOLDERS:
        monkeypatch.setattr(module, "unscoped_db", handle)
    for var in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "OWNER_PHONE"):
        monkeypatch.delenv(var, raising=False)

    record = Property(id="p1", name="The Grand", status=LIVE).model_dump()
    record["id"] = "p1"
    run(handle.properties.insert_one(record))

    admin, waiter = _staff("u-admin", "admin"), _staff("u-waiter", "waiter")
    run(handle.users.insert_one(admin))
    run(handle.users.insert_one(waiter))

    return World(db=PropertyScopedDatabase("p1"), admin=admin, waiter=waiter, sends=[])


def accepting_transport(world: World, message_id="wamid.TEST"):
    """Stand in for Meta, and record every call. Returns what the Cloud API returns."""
    def fake(to, template, language, variables):
        world.sends.append({"to": to, "template": template, "language": language,
                            "variables": list(variables)})
        return {"sent": True, "configured": True, "to": to, "status": 200,
                "message_id": message_id, "response": {}}
    return fake


def whatsapp_configured(monkeypatch):
    """The three environment variables `whatsapp_config_problem()` names. Set only where a
    test is about something other than the credentials being missing."""
    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "test-phone-id")
    monkeypatch.setenv("OWNER_PHONE", "919999999999")


def configure(world: World, **overrides):
    body = {"default_occasion_template": "guest_occasion_v1",
            "follow_up_template": "guest_follow_up_v1"}
    body.update(overrides)
    return call(messaging.update_settings, payload=MessagingSettingsIn(**body),
                user=world.admin, db=world.db)


def add_guest(world: World, gid, name, phone, **extra):
    doc = {"id": gid, "name": name, "phone": phone, "no_messages": False,
           "created_at": "2026-01-01T00:00:00+00:00"}
    doc.update(extra)
    run(world.db.guests.insert_one(doc))
    return doc


def settled_order(world: World, phone, day, oid="o1"):
    run(world.db.orders.insert_one({
        "id": oid, "status": "settled", "customer_phone": phone,
        "table_id": "t1", "table_label": "1", "items": [], "total": 100.0,
        # Stored UTC, read back through the property's own clock — 18:30 UTC is the
        # evening of the same local day in Asia/Kolkata, so the date does not drift.
        "settled_at": f"{day}T12:00:00+00:00"}))


def log_rows(world: World):
    return run(world.db.message_log.find({}, {"_id": 0}).to_list(1000))


def run_job(world: World, day=None) -> dict:
    """One night's follow-up run for this property, as the scheduled function does it."""
    counts = call(messaging.run_follow_ups, db=world.db,
                  property_record={"id": "p1", "name": "The Grand"}, day=day)
    return {k: v for k, v in counts.items() if k != "property"}


# --------------------------------------------------------------------------
# 1. An occasion recorded at billing shows up on today's list, on its date.
# --------------------------------------------------------------------------
def test_occasion_recorded_at_billing_appears_on_todays_list(world):
    """The waiter types a name, a phone and 'Birthday' on the settle screen. Nothing else
    happens that evening; the greeting is the property's to send on the day."""
    created = call(messaging.capture_occasion, payload=messaging.OccasionCaptureIn(
        phone="98765 43210", name="Asha Menon", label="Birthday",
        date=f"1994-{month_day_today()}"), user=world.waiter, db=world.db)

    # The guest did not exist before the bill, so the capture made one — the phone is the
    # identity key across bar, restaurant and rooms, so there is nothing else to key on.
    guest = run(world.db.guests.find_one({"id": created["guest_id"]}, {"_id": 0}))
    assert guest["name"] == "Asha Menon"
    assert guest["phone"] == "+919876543210", "stored E.164, via services.identity"

    listed = call(messaging.occasions_today, user=world.waiter, db=world.db)
    assert listed["date"] == local_today()
    assert [(o["name"], o["label"], o["phone"]) for o in listed["occasions"]] == [
        ("Asha Menon", "Birthday", "+919876543210")]
    assert listed["occasions"][0]["already_sent"] is False


def test_an_occasion_on_another_day_is_not_on_todays_list(world):
    """The test that would pass with no date filter at all is the one that does not count."""
    other = (date.fromisoformat(local_today()) + timedelta(days=3)).isoformat()
    call(messaging.capture_occasion, payload=messaging.OccasionCaptureIn(
        phone="9876543210", name="Asha", label="Birthday", date=other),
        user=world.waiter, db=world.db)
    assert call(messaging.occasions_today, user=world.waiter,
                db=world.db)["occasions"] == []


def test_the_year_does_not_have_to_match(world):
    """A birthday recurs; a birth year does not. Only the month and day are matched."""
    call(messaging.capture_occasion, payload=messaging.OccasionCaptureIn(
        phone="9876543210", name="Asha", label="Birthday",
        date=f"1962-{month_day_today()}"), user=world.waiter, db=world.db)
    assert len(call(messaging.occasions_today, user=world.waiter,
                    db=world.db)["occasions"]) == 1


def test_several_occasions_per_customer(world):
    """A birthday, a wedding anniversary and a child's birthday are three rows on one
    guest, not three guests and not a field that can hold one of them."""
    guest = add_guest(world, "g1", "Asha", "+919876543210")
    for label in ("Birthday", "Wedding anniversary", "Ananya's birthday"):
        call(guests_router.add_occasion, guest_id="g1",
             payload=OccasionIn(label=label, date=f"1994-{month_day_today()}"),
             user=world.admin, db=world.db)
    listed = call(guests_router.list_occasions, guest_id="g1", user=world.admin,
                  db=world.db)
    assert {o["label"] for o in listed} == {
        "Birthday", "Wedding anniversary", "Ananya's birthday"}
    assert guest["id"] == "g1"


# --------------------------------------------------------------------------
# 2. Pressing send twice sends once.
# --------------------------------------------------------------------------
def test_pressing_send_twice_sends_once(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)

    first = call(messaging.send_occasion, occasion_id=occasion["id"],
                 user=world.waiter, db=world.db)
    assert first["sent"] is True
    assert first["message_id"] == "wamid.TEST"

    second = refused(messaging.send_occasion, occasion_id=occasion["id"],
                     user=world.waiter, db=world.db)
    assert second.status_code == 409
    assert "already" in str(second.detail).lower()

    # The claim, not the button, is what held it.
    assert len(world.sends) == 1
    assert len([r for r in log_rows(world) if r["status"] == "sent"]) == 1
    claim = run(world.db.message_claims.find_one({}, {"_id": 0}))
    assert claim["attempts"] == 2, "the second press is counted, and refused"


def test_a_send_that_definitely_did_not_happen_can_be_retried(world, monkeypatch):
    """A refusal from Meta is not a message, so the claim is released. The alternative —
    holding it — would mean one misconfigured evening burns the greeting for good."""
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "pid")
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)

    def refusing(to, template, language, variables):
        world.sends.append(to)
        return {"sent": False, "configured": True, "to": to, "status": 400,
                "error_code": 132001, "error": "No approved template with that name."}

    monkeypatch.setattr(messaging, "send_whatsapp_template", refusing)
    first = call(messaging.send_occasion, occasion_id=occasion["id"],
                 user=world.waiter, db=world.db)
    assert first["sent"] is False
    assert run(world.db.message_claims.count_documents({})) == 0

    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    second = call(messaging.send_occasion, occasion_id=occasion["id"],
                  user=world.waiter, db=world.db)
    assert second["sent"] is True
    # Both attempts are in the log; only one of them was a message.
    assert [r["status"] for r in log_rows(world)] == ["failed", "sent"]


def test_a_send_we_cannot_account_for_keeps_the_claim(world, monkeypatch):
    """A socket that died mid-request may or may not have delivered. Retrying would risk
    a second greeting, so the claim is kept and the log says why."""
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "pid")
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)

    monkeypatch.setattr(messaging, "send_whatsapp_template", lambda *a: {
        "sent": False, "configured": True, "to": "x", "error": "timed out"})
    assert call(messaging.send_occasion, occasion_id=occasion["id"],
                user=world.waiter, db=world.db)["sent"] is False
    assert run(world.db.message_claims.count_documents({})) == 1

    # The row says it is not sendable, and does *not* claim it was sent.
    row = call(messaging.occasions_today, user=world.waiter, db=world.db)["occasions"][0]
    assert (row["already_sent"], row["claimed"], row["sendable"]) == (False, True, False)

    # And the refusal is worded for what actually happened, not for a send.
    detail = refused(messaging.send_occasion, occasion_id=occasion["id"],
                     user=world.waiter, db=world.db)
    assert detail.status_code == 409
    assert "could not be confirmed" in detail.detail
    assert "may have reached them" in detail.detail


# --------------------------------------------------------------------------
# 3. Consent, everywhere.
# --------------------------------------------------------------------------
def test_an_opted_out_customer_is_never_sendable(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210", no_messages=True)
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)

    row = call(messaging.occasions_today, user=world.waiter, db=world.db)["occasions"][0]
    assert row["sendable"] is False
    assert row["problem"] == messaging_service.NO_CONSENT

    result = call(messaging.send_occasion, occasion_id=occasion["id"],
                  user=world.waiter, db=world.db)
    assert result["sent"] is False
    assert result["error"] == messaging_service.NO_CONSENT
    assert world.sends == [], "the transport is never reached for an opted-out customer"
    # And no claim was taken: consent restored later must not find the day burnt.
    assert run(world.db.message_claims.count_documents({})) == 0
    assert [r["status"] for r in log_rows(world)] == ["refused"]


def test_an_opted_out_customer_is_never_a_follow_up(world, monkeypatch):
    """Not "listed but unsendable" — absent. Somebody who asked not to be messaged is not
    a pending task with a problem attached, and the automatic job never reaches them."""
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "pid")
    monkeypatch.setenv("OWNER_PHONE", "919999999999")
    configure(world, follow_up_days=30)
    add_guest(world, "g1", "Asha", "+919876543210", no_messages=True)
    settled_order(world, "+919876543210", days_ago(90))

    assert call(messaging.follow_ups, user=world.waiter, db=world.db)["customers"] == []
    assert run_job(world) == {"due": 0, "sent": 0, "failed": 0}
    assert world.sends == []


def test_consent_has_exactly_one_door(world):
    """Every message this feature sends leaves through `_deliver`, and `_deliver` refuses
    an opted-out guest before anything else. This asserts the shape rather than the
    behaviour, because the behaviour above only covers the endpoints that exist today —
    the sixth one somebody adds next year is the one that would bypass it."""
    import inspect
    source = inspect.getsource(messaging)
    assert source.count("send_whatsapp_template(") == 1, (
        "the transport is called in more than one place — every send has to go through "
        "_deliver, which is where consent is honoured")
    body = inspect.getsource(messaging._deliver)
    assert "consent_problem" in body


def test_opting_out_is_not_undone_by_an_ordinary_guest_edit(world):
    """`PUT /guests/{id}` replaces the editable half wholesale. A form written before this
    field existed omits it, and a default of False would silently re-consent somebody who
    had asked not to be messaged — see models/hotel.py."""
    from models.hotel import GuestIn
    add_guest(world, "g1", "Asha", "+919876543210", no_messages=True)
    call(guests_router.update_guest, guest_id="g1",
         payload=GuestIn(name="Asha M", phone="+919876543210"),
         user=world.admin, db=world.db)
    assert run(world.db.guests.find_one({"id": "g1"}))["no_messages"] is True

    call(guests_router.update_guest, guest_id="g1",
         payload=GuestIn(name="Asha M", phone="+919876543210", no_messages=False),
         user=world.admin, db=world.db)
    assert run(world.db.guests.find_one({"id": "g1"}))["no_messages"] is False


# --------------------------------------------------------------------------
# 4. The follow-up window.
# --------------------------------------------------------------------------
def test_follow_up_is_on_at_ten_days_and_can_be_switched_off(world, monkeypatch):
    """On by default at the owner's ten days, and one click away from off.

    What keeps the default safe is not the switch — it is that no message goes anywhere
    until the property has obtained a Meta template of its own, that an opted-out customer
    is never reached, and that each customer hears once per visit."""
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    whatsapp_configured(monkeypatch)
    configure(world)  # templates set, follow-up untouched
    add_guest(world, "g1", "Asha", "+919876543210")
    settled_order(world, "+919876543210", days_ago(11))

    listed = call(messaging.follow_ups, user=world.waiter, db=world.db)
    assert listed["enabled"] is True
    assert listed["days"] == 10
    assert [c["guest_id"] for c in listed["customers"]] == ["g1"]

    configure(world, follow_up_enabled=False)
    off = call(messaging.follow_ups, user=world.waiter, db=world.db)
    assert off["enabled"] is False and off["customers"] == []
    assert run_job(world) == {"due": 0, "sent": 0, "failed": 0,
                              "skipped": messaging_service.FOLLOW_UP_OFF}
    assert world.sends == [], "off means the job stops before it reads a guest"


def test_the_job_sends_the_follow_up_with_nobody_pressing_anything(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    whatsapp_configured(monkeypatch)
    configure(world)
    add_guest(world, "g1", "Asha Menon", "+919876543210")
    settled_order(world, "+919876543210", days_ago(11))

    assert run_job(world) == {"due": 1, "sent": 1, "failed": 0}
    assert world.sends[0]["template"] == "guest_follow_up_v1"
    assert world.sends[0]["variables"] == ["Asha Menon", "The Grand"]
    row = log_rows(world)[0]
    assert row["status"] == "sent" and row["kind"] == "follow_up"
    assert row["sent_by"] is None, "nobody pressed anything; that is the point"


def test_the_job_run_twice_sends_once(world, monkeypatch):
    """It runs every night and the customer is still lapsed tomorrow. "Have we done this
    one" cannot be a matter of timing — it is the claim, keyed on the visit."""
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    whatsapp_configured(monkeypatch)
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    settled_order(world, "+919876543210", days_ago(11))

    assert run_job(world) == {"due": 1, "sent": 1, "failed": 0}
    assert run_job(world) == {"due": 0, "sent": 0, "failed": 0}
    assert run_job(world) == {"due": 0, "sent": 0, "failed": 0}
    assert len(world.sends) == 1
    assert len(log_rows(world)) == 1


def test_the_job_is_safe_when_whatsapp_is_not_configured(world, monkeypatch):
    """It records the refusal and moves on. One line for the run, not a failure row per
    customer per night for as long as the credentials are missing — which would bury the
    day somebody finally sets them."""
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    configure(world)  # templates named, but no WhatsApp credentials in the environment
    for i in range(3):
        add_guest(world, f"g{i}", f"Guest {i}", f"+91987650000{i}")
        settled_order(world, f"+91987650000{i}", days_ago(40), oid=f"o{i}")

    counts = run_job(world)
    assert counts["sent"] == 0 and counts["failed"] == 0
    assert "WHATSAPP_TOKEN" in counts["skipped"]
    assert log_rows(world) == [], "no per-customer noise while the deployment is unready"
    assert world.sends == []
    assert run(world.db.message_claims.count_documents({})) == 0, (
        "nobody's one follow-up was spent on a run that could not send")

    # And the day the credentials arrive, everybody is still there to be messaged.
    whatsapp_configured(monkeypatch)
    assert run_job(world)["sent"] == 3


def test_the_job_is_safe_when_no_template_is_configured(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    whatsapp_configured(monkeypatch)
    configure(world, follow_up_template="")
    add_guest(world, "g1", "Asha", "+919876543210")
    settled_order(world, "+919876543210", days_ago(40))

    counts = run_job(world)
    assert counts["sent"] == 0 and counts["failed"] == 0
    assert counts["skipped"].startswith("Not configured:")
    assert "follow-up" in counts["skipped"]
    assert world.sends == []


def test_one_tenant_cannot_stop_another_tenants_follow_ups(world, monkeypatch):
    """`send_follow_ups` iterates live properties. A settings row that breaks one hotel's
    run must not silence the whole platform's for the night."""
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    monkeypatch.setattr(messaging, "unscoped_db", db_module.unscoped_db)
    whatsapp_configured(monkeypatch)
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    settled_order(world, "+919876543210", days_ago(11))

    results = run(messaging.send_follow_ups())
    assert [r.get("sent") for r in results] == [1]
    assert len(world.sends) == 1


def test_follow_up_picks_exactly_the_customers_past_the_window(world):
    configure(world, follow_up_days=60)
    add_guest(world, "g-lapsed", "Lapsed", "+919876500001")
    add_guest(world, "g-edge", "Edge", "+919876500002")
    add_guest(world, "g-recent", "Recent", "+919876500003")
    add_guest(world, "g-never", "Never came", "+919876500004")
    settled_order(world, "+919876500001", days_ago(61), oid="o-lapsed")
    settled_order(world, "+919876500002", days_ago(60), oid="o-edge")
    settled_order(world, "+919876500003", days_ago(59), oid="o-recent")

    picked = call(messaging.follow_ups, user=world.waiter, db=world.db)
    assert picked["enabled"] is True
    assert picked["days"] == 60
    assert [c["guest_id"] for c in picked["customers"]] == ["g-lapsed", "g-edge"], (
        "60 days since the visit is 'has not been back for 60 days' and qualifies; 59 "
        "does not, and a customer who has never been at all is not a lapsed one")
    assert picked["customers"][0]["last_visit"] == days_ago(61)


def test_the_window_is_the_propertys_own(world):
    """Configurable, not a constant — a fine-dining room a guest visits twice a year and
    a bar somebody drinks at weekly are not the same business."""
    configure(world, follow_up_days=14)
    add_guest(world, "g1", "Asha", "+919876500001")
    settled_order(world, "+919876500001", days_ago(20))
    assert [c["guest_id"] for c in call(messaging.follow_ups, user=world.waiter,
                                        db=world.db)["customers"]] == ["g1"]

    configure(world, follow_up_days=30)
    assert call(messaging.follow_ups, user=world.waiter, db=world.db)["customers"] == []


def test_one_follow_up_per_visit(world, monkeypatch):
    """"Gets one follow-up" — not one a night until they come back."""
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    whatsapp_configured(monkeypatch)
    configure(world, follow_up_days=30)
    add_guest(world, "g1", "Asha", "+919876500001")
    settled_order(world, "+919876500001", days_ago(90))

    assert run_job(world) == {"due": 1, "sent": 1, "failed": 0}
    assert run_job(world) == {"due": 0, "sent": 0, "failed": 0}
    assert call(messaging.follow_ups, user=world.waiter, db=world.db)["customers"] == []
    assert len(world.sends) == 1

    # They come back, and lapse again. That is a new visit, and a new follow-up.
    settled_order(world, "+919876500001", days_ago(40), oid="o2")
    assert [c["guest_id"] for c in call(messaging.follow_ups, user=world.waiter,
                                        db=world.db)["customers"]] == ["g1"]
    assert run_job(world) == {"due": 1, "sent": 1, "failed": 0}
    assert len(world.sends) == 2


def test_a_follow_up_that_meta_refused_is_not_retried_every_night(world, monkeypatch):
    """Unlike the occasion button, the job keeps its claim whatever happened. Nobody is
    watching, so a nightly retry of a number that is not on WhatsApp would run until the
    property closed — and the log row already says why it did not go."""
    whatsapp_configured(monkeypatch)
    configure(world, follow_up_days=30)
    add_guest(world, "g1", "Asha", "+919876500001")
    settled_order(world, "+919876500001", days_ago(90))
    attempts = []

    def refusing(to, template, language, variables):
        attempts.append(to)
        return {"sent": False, "configured": True, "to": to, "status": 400,
                "error_code": 131026, "error": "That number cannot receive WhatsApp."}

    monkeypatch.setattr(messaging, "send_whatsapp_template", refusing)
    assert run_job(world) == {"due": 1, "sent": 0, "failed": 1}
    assert run_job(world) == {"due": 0, "sent": 0, "failed": 0}
    assert len(attempts) == 1
    assert log_rows(world)[0]["error"] == "That number cannot receive WhatsApp."
    assert log_rows(world)[0]["provider_error_code"] == 131026


def test_a_phone_typed_four_ways_is_one_customer(world):
    """The bill says 09876500001, the guest record says +91 98765 00001. Reusing the
    staff phone-login normaliser rather than writing a second one is what makes those the
    same person — see services/identity.py."""
    configure(world, follow_up_days=30)
    add_guest(world, "g1", "Asha", "+919876500001")
    settled_order(world, "09876500001", days_ago(90))
    assert [c["guest_id"] for c in call(messaging.follow_ups, user=world.waiter,
                                        db=world.db)["customers"]] == ["g1"]


# --------------------------------------------------------------------------
# 5. Nothing may appear to work.
# --------------------------------------------------------------------------
def test_a_send_with_no_template_configured_names_what_is_missing(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    add_guest(world, "g1", "Asha", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)

    result = call(messaging.send_occasion, occasion_id=occasion["id"],
                  user=world.waiter, db=world.db)
    assert result["sent"] is False
    assert result["error"].startswith("Not configured:")
    assert "Birthday" in result["error"]
    assert "template" in result["error"]
    assert world.sends == [], "nothing is handed to Meta that Meta would reject"

    # And the log carries the same reason, which is what answers "why did it not".
    rows = log_rows(world)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == result["error"]
    assert rows[0]["guest_id"] == "g1"
    assert rows[0]["kind"] == "occasion"


def test_a_send_with_whatsapp_unconfigured_reports_metas_own_missing_pieces(world):
    """No transport patch: this is the real `_send_whatsapp_template`, with no credentials
    in the environment — the state the owner is actually in today."""
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)

    result = call(messaging.send_occasion, occasion_id=occasion["id"],
                  user=world.waiter, db=world.db)
    assert result["sent"] is False
    assert "WHATSAPP_TOKEN" in result["error"]
    assert "WHATSAPP_PHONE_ID" in result["error"]
    assert log_rows(world)[0]["error"] == result["error"]


def test_the_log_records_the_template_and_its_variables_not_a_sentence(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    configure(world, occasion_templates={"birthday": "birthday_wish_v3"})
    add_guest(world, "g1", "Asha Menon", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)
    call(messaging.send_occasion, occasion_id=occasion["id"], user=world.waiter,
         db=world.db)

    assert world.sends[0]["template"] == "birthday_wish_v3", (
        "a label with a template of its own uses it, not the default")
    assert world.sends[0]["variables"] == ["Asha Menon", "Birthday", "The Grand"]
    row = log_rows(world)[0]
    assert row["template"] == "birthday_wish_v3"
    assert row["variables"] == ["Asha Menon", "Birthday", "The Grand"]
    assert row["message_id"] == "wamid.TEST"
    assert row["to"] == "+919876543210"
    assert row["sent_by"] == "u-waiter"
    assert "message" not in row, "there is no free text to store; there never was one"


def test_the_log_is_append_only(world, monkeypatch):
    """Following the reasoning in services/folio.py: a record that can be rewritten is a
    record that cannot settle an argument about whether a message went out."""
    import inspect
    source = inspect.getsource(messaging)
    for forbidden in ("message_log.update_one", "message_log.update_many",
                      "message_log.delete_one", "message_log.delete_many"):
        assert forbidden not in source, f"{forbidden} would rewrite the ledger"

    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    configure(world)
    add_guest(world, "g1", "Asha", "+919876543210")
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)
    call(messaging.send_occasion, occasion_id=occasion["id"], user=world.waiter,
         db=world.db)
    refused(messaging.send_occasion, occasion_id=occasion["id"], user=world.waiter,
            db=world.db)
    # The refused second press is not a message and does not become a row; the claim
    # counts it instead.
    assert len(log_rows(world)) == 1
    listed = call(messaging.message_log, user=world.admin, db=world.db)
    assert [r["status"] for r in listed] == ["sent"]


def test_a_guest_with_an_unusable_phone_is_not_sendable(world, monkeypatch):
    monkeypatch.setattr(messaging, "send_whatsapp_template", accepting_transport(world))
    configure(world)
    add_guest(world, "g1", "Asha", "1234567890")  # not an Indian mobile
    occasion = call(guests_router.add_occasion, guest_id="g1",
                    payload=OccasionIn(label="Birthday", date=f"1994-{month_day_today()}"),
                    user=world.admin, db=world.db)
    row = call(messaging.occasions_today, user=world.waiter, db=world.db)["occasions"][0]
    assert row["sendable"] is False and "10-digit" in row["problem"]
    assert call(messaging.send_occasion, occasion_id=occasion["id"], user=world.waiter,
                db=world.db)["sent"] is False
    assert world.sends == []


# --------------------------------------------------------------------------
# Who may do what.
# --------------------------------------------------------------------------
def test_sending_is_operational_and_configuring_is_not():
    """Sending a greeting is front-desk and waiter work; choosing which Meta template
    carries it is the owner's. Both name admin, because the role check runs ahead of the
    domain bypass."""
    assert messaging.OPERATIONAL_ROLES == (
        "admin", "manager", "front_desk", "waiter")
    assert messaging.CONFIGURE_ROLES == ("admin",)
    for roles in (messaging.OPERATIONAL_ROLES, messaging.CONFIGURE_ROLES):
        assert "admin" in roles


def test_the_settings_a_property_has_never_touched(world):
    """Read before written: every property starts here. The follow-up is on at ten days
    and no template is named, so the switch is on and nothing can go anywhere — which is
    the state the owner is in today, and the one the honest refusal has to describe."""
    settings = call(messaging.get_settings, user=world.admin, db=world.db)
    assert settings["follow_up_enabled"] is True
    assert settings["follow_up_days"] == 10 == messaging_service.DEFAULT_FOLLOW_UP_DAYS
    assert settings["default_occasion_template"] == ""
    assert settings["follow_up_template"] == ""
    assert settings["template_language"] == messaging_service.DEFAULT_TEMPLATE_LANGUAGE
    assert messaging_service.template_problem(
        settings, messaging_service.FOLLOW_UP).startswith("Not configured:")


def test_the_follow_up_window_has_to_be_at_least_a_day(world):
    assert refused(messaging.update_settings,
                   payload=MessagingSettingsIn(follow_up_days=0),
                   user=world.admin, db=world.db).status_code == 400


# --------------------------------------------------------------------------
# The pure rules, on their own.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("2026-08-28", "08-28"),
    ("1962-01-01", "01-01"),
    ("", None),
    (None, None),
    ("not a date", None),
])
def test_month_day(value, expected):
    assert messaging_service.month_day(value) == expected


@pytest.mark.parametrize("last,days,expected", [
    ("2026-06-28", 60, True),    # exactly 61 days before 2026-08-28
    ("2026-06-29", 60, True),    # exactly 60
    ("2026-06-30", 60, False),   # 59
    (None, 60, False),
    ("2026-08-28", 0, True),     # a zero window is nonsense, and it is the caller's
])
def test_due_for_follow_up(last, days, expected):
    assert messaging_service.due_for_follow_up(last, "2026-08-28", days) is expected


@pytest.mark.parametrize("result,expected", [
    ({"sent": False, "configured": False}, True),
    ({"sent": False, "configured": True, "status": 400, "error_code": 132001}, True),
    ({"sent": False, "configured": True, "error": "timed out"}, False),
    ({"sent": True, "configured": True, "message_id": "x"}, False),
])
def test_send_definitely_did_not_happen(result, expected):
    assert messaging_service.send_definitely_did_not_happen(result) is expected


def test_a_label_is_free_text_and_not_an_enum():
    """"A birthday, an anniversary, a child's birthday, or something the hotel names
    itself" — so the vocabulary belongs to the property, not to this module."""
    assert messaging_service.normalise_label("  Ananya's  first  birthday ") == (
        "Ananya's first birthday")
    assert messaging_service.normalise_label("") == ""
    assert messaging_service.label_key("Wedding Anniversary") == "wedding anniversary"


def test_template_problem_names_the_label_it_could_not_find():
    settings = {"occasion_templates": {"birthday": "b1"}, "default_occasion_template": "",
                "follow_up_template": ""}
    assert messaging_service.template_problem(settings, "occasion", "Birthday") == ""
    problem = messaging_service.template_problem(settings, "occasion", "Anniversary")
    assert problem.startswith("Not configured:") and "Anniversary" in problem
    assert messaging_service.template_problem(
        {**settings, "default_occasion_template": "d1"}, "occasion", "Anniversary") == ""


def test_a_dedupe_key_is_one_greeting_to_one_person_on_one_day():
    a = messaging_service.dedupe_key("occasion", "g1", "occ1", "2026-08-28")
    assert a == messaging_service.dedupe_key("occasion", "g1", "occ1", "2026-08-28")
    for other in (
        messaging_service.dedupe_key("occasion", "g2", "occ1", "2026-08-28"),
        messaging_service.dedupe_key("occasion", "g1", "occ2", "2026-08-28"),
        messaging_service.dedupe_key("occasion", "g1", "occ1", "2026-08-29"),
        messaging_service.dedupe_key("follow_up", "g1", "occ1", "2026-08-28"),
    ):
        assert other != a
