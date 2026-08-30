"""Messaging a customer: the occasions somebody presses send on, and the follow-up nobody does.

Two senders, deliberately different, because the two messages are different things.

* **An occasion is pressed.** A birthday greeting is a decision — the desk looks at
  today's list, sees who it is, and chooses. There is no scheduler here and there should
  not be one: a hotel that automatically wishes a happy birthday to a guest who died last
  month has done something worse than nothing.
* **A follow-up is automatic.** "We have not seen you in ten days" is not a decision
  anybody makes per customer, and a screen of them is a queue nobody works. It goes out
  from a scheduled function, once per customer per visit, and the only human involvement
  is the switch and the number of days.

**Everything either of them sends leaves through `_deliver`, and nothing else calls the
transport.** That is the single most important sentence in this module. Consent is
honoured there, first, ahead of every other reason a message might not go — so a customer
who has asked not to be messaged is refused by construction rather than by each caller
remembering. `tests/test_customer_messaging.py::test_consent_has_exactly_one_door`
asserts the shape as well as the behaviour, because the behaviour only covers the two
senders that exist today and the third one somebody writes next year is the one that
would forget.

**Nothing here writes a sentence.** WhatsApp permits free-form text only within 24 hours
of the customer messaging the business, which neither of these is ever inside, so both
are approved templates: a name Meta has reviewed, plus positional variables. The names
are the property's — obtained under its own WhatsApp Business account — which is why they
are configuration and not constants, and why a property that has obtained none can send
nothing and is told exactly that. See services/messaging.py.
"""
import asyncio
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import unscoped_db
from models.hotel import Guest
from models.messaging import MessageLogEntry, MessagingSettings, MessagingSettingsIn
from routers.guests import find_by_phone, record_occasion
from routers.reports import (
    send_whatsapp_template, whatsapp_config_problem, whatsapp_credentials,
    whatsapp_for)
import db as _db_module
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access, resolve_property
from services.access import LIVE, SHARED
from services.clock import local_date, today as local_today
from services.identity import PHONE_SHAPE, normalise_phone
from services.messaging import (
    FAILED, FOLLOW_UP, FOLLOW_UP_OFF, OCCASION, REFUSED, SENT, blocking_problem,
    consent_problem, dedupe_key, due_for_follow_up, days_since, follow_up_days,
    follow_up_enabled, follow_up_variables, label_key, occasion_variables, recipient,
    send_definitely_did_not_happen, template_for, template_language, template_problem,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Sending a greeting is operational work. The front desk knows the guest arriving today
# and the waiter took the booking for the anniversary dinner; making this admin-only would
# mean the one person who is not in the building decides whether a message goes out.
#
# `admin` is named explicitly, as every role tuple in this codebase names it, because the
# role check in services/access.py::can_access runs *ahead* of the admin domain bypass —
# a tuple that left it out would lock the owner out of their own screen.
#
# SHARED, like guests.py: a bar regular and a hotel guest are the same person, so this is
# not the restaurant's feature or the hotel's.
OPERATIONAL_ROLES = ("admin", "manager", "front_desk", "waiter")
OPERATIONAL = require_access(SHARED, *OPERATIONAL_ROLES)

# Which Meta template carries which occasion, and how long a gap counts as lapsed, are
# configuration in the sense routers/property.py means it: decisions about how the
# business runs, made once by the person who holds the WhatsApp Business account. A waiter
# who can send a birthday message still cannot change what it says or start the property
# messaging every customer it has not seen this week.
CONFIGURE_ROLES = ("admin",)
CONFIGURE = require_access(SHARED, *CONFIGURE_ROLES)

# The settings row is a singleton per property, found by a fixed id — safe because the
# handle it is read through is already bound to one hotel.
_SETTINGS_ID = "messaging"


# ------------------------------- settings ---------------------------------
async def _settings(db: PropertyScopedDatabase) -> dict:
    """This property's messaging configuration, as a plain dict.

    A property that has never opened the screen has no row, and gets the model's defaults
    rather than an empty dict: `MessagingSettingsIn()` is the documented starting state —
    no templates, so nothing sendable, and the follow-up on at ten days — and building it
    here means the reader of a stored row and the reader of an absent one cannot disagree
    about what "not configured" means.
    """
    stored = await db.messaging_settings.find_one({"id": _SETTINGS_ID}, {"_id": 0})
    return stored or MessagingSettings(id=_SETTINGS_ID).model_dump()


@router.get("/messaging/settings")
async def get_settings(user: dict = Depends(CONFIGURE),
                       db: PropertyScopedDatabase = Depends(tenant_db)):
    return await _settings(db)


@router.put("/messaging/settings")
async def update_settings(payload: MessagingSettingsIn, user: dict = Depends(CONFIGURE),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """Replace this property's messaging configuration.

    Wholesale, unlike `PUT /api/property`, and that is safe here for the reason it is not
    there: this record was born with every field it has, so there is no client in the
    world that predates one of them and could drop it by omission. The moment a field is
    added to it, the same "None means not mentioned" arrangement `no_messages` uses will
    be needed.

    Template names are stored under a lowercased label key so that "Birthday" and
    "birthday" find the same template — the same normalisation the lookup does, applied
    once on the way in rather than hoped for.
    """
    if payload.follow_up_days < 1:
        raise HTTPException(400, "The follow-up window has to be at least one day — "
                                 "messaging somebody the morning after their dinner is "
                                 "not a follow-up.")
    body = payload.model_dump()
    body["occasion_templates"] = {
        label_key(k): (v or "").strip()
        for k, v in (payload.occasion_templates or {}).items()
        if label_key(k) and (v or "").strip()
    }
    record = MessagingSettings(**body, updated_by=user.get("id")).model_dump()
    record["id"] = _SETTINGS_ID
    await db.messaging_settings.update_one(
        {"id": _SETTINGS_ID}, {"$set": record}, upsert=True)
    return await _settings(db)


# --------------------------- capture at the till ---------------------------
class OccasionCaptureIn(BaseModel):
    """"It's her birthday tomorrow" — typed at the till while the card is going through.

    Phone-first, because at the till there is no guest id: the waiter has a number written
    on the bill and nothing else. The guest record is found by it, or made, so recording
    an occasion never becomes "go to the Guests screen first" — which, mid-service, means
    it is never recorded at all.
    """
    phone: str
    name: Optional[str] = None
    label: str
    date: str


@router.post("/messaging/occasions")
async def capture_occasion(payload: OccasionCaptureIn, user: dict = Depends(OPERATIONAL),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    """Record an occasion against whoever this number belongs to, creating them if new.

    Reachable by a waiter, which is why it is here and not on `routers/guests.py`: the
    guest routes sit behind the `hotel.guests` screen key, and a waiter holds neither that
    nor the front-desk role those routes name. This is the same write with the till's
    authorization, and it is deliberately the *only* thing a waiter can do to a guest
    record — it creates a name and a number, and adds a date. It cannot read the guest
    list, edit an address or see anybody's identity document.
    """
    canonical = normalise_phone(payload.phone)
    if not canonical:
        raise HTTPException(400, f"That is not a number a message could go to — expected "
                                 f"{PHONE_SHAPE}")

    guest = await find_by_phone(db, payload.phone)
    created = False
    if not guest:
        guest = Guest(name=(payload.name or "").strip() or "Guest",
                      phone=canonical).model_dump()
        await db.guests.insert_one(guest)
        guest.pop("_id", None)
        created = True
    elif payload.name and not (guest.get("name") or "").strip():
        # A name arriving for a record that has none is worth keeping; a *different* name
        # is not this endpoint's to impose. The desk owns the guest record.
        await db.guests.update_one({"id": guest["id"]},
                                   {"$set": {"name": payload.name.strip()}})
        guest["name"] = payload.name.strip()

    occasion = await record_occasion(db, guest["id"], payload.label, payload.date,
                                     user.get("id"))
    return {"guest_id": guest["id"], "guest_created": created, "occasion": occasion}


# ---------------------------- today's occasions ----------------------------
@router.get("/messaging/occasions/today")
async def occasions_today(user: dict = Depends(OPERATIONAL),
                          db: PropertyScopedDatabase = Depends(tenant_db)):
    """Everybody whose occasion falls today, and whether a message has already gone.

    One indexed equality filter on the stored `month_day`, which is the whole reason
    occasions are their own collection rather than a list on the guest — see
    models/hotel.py.

    `already_sent` reads the log, because that is the honest answer to "has a message
    gone out". `sendable` reads the *claim*, because that is the structural one: a claim
    exists exactly when this greeting has been sent or may have been, and it is what the
    send endpoint will consult. Two fields rather than one, so the screen can say "sent at
    10:14" and "cannot send" for different reasons without pretending they are the same.
    """
    _wa_record, _, _ = await whatsapp_for(user)
    _wa_problem = whatsapp_config_problem(_wa_record, need_owner_phone=False)
    day = local_today()
    settings = await _settings(db)
    rows = await db.occasions.find({"month_day": day[5:]}, {"_id": 0}).to_list(2000)
    sent = {r.get("subject") for r in await db.message_log.find(
        {"kind": OCCASION, "status": SENT, "subject_day": day}, {"_id": 0}).to_list(5000)}
    claimed = {r.get("key") for r in await db.message_claims.find(
        {"kind": OCCASION}, {"_id": 0}).to_list(5000)}

    out = []
    for occasion in sorted(rows, key=lambda r: (r.get("label") or "")):
        guest = await db.guests.find_one({"id": occasion["guest_id"]}, {"_id": 0})
        if not guest:
            # An occasion whose guest was removed. Skipped rather than shown with a blank
            # name: there is nobody to message, and a row that cannot be acted on is
            # noise on a screen used during service.
            continue
        problem = blocking_problem(settings, guest, OCCASION, occasion.get("label"))
        key = dedupe_key(OCCASION, guest["id"], occasion["id"], day)
        out.append({
            "occasion_id": occasion["id"],
            "label": occasion.get("label"),
            "date": occasion.get("date"),
            "guest_id": guest["id"],
            "name": guest.get("name"),
            "phone": guest.get("phone"),
            "already_sent": occasion["id"] in sent,
            # Claimed but not sent is a real and separate state: an attempt whose outcome
            # we could not read. The screen has to be able to say "we are not sending this
            # again and here is why" rather than showing a button that does nothing.
            "claimed": key in claimed,
            "sendable": not problem and key not in claimed,
            "problem": problem,
            "template": template_for(settings, OCCASION, occasion.get("label")),
        })

    return {
        "date": day,
        "occasions": out,
        # The deployment-level state, reported beside the list rather than folded into
        # each row's `problem`: it is the same answer for every row and it is fixed by a
        # different person in a different place. Same shape GET /whatsapp/status answers
        # in, so the screen can say the same thing the Notifications screen does.
        "whatsapp": {"configured": not _wa_problem,
                     "problem": _wa_problem},
    }


@router.post("/messaging/occasions/{occasion_id}/send")
async def send_occasion(occasion_id: str, user: dict = Depends(OPERATIONAL),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    """Send this occasion's greeting, once.

    The subject day is the occasion's date *in the current year*, not literally today, so
    the desk that catches a birthday a day late still sends that year's one greeting and
    a second press the following morning is refused. That is what makes the guarantee
    "one greeting per occasion per year" rather than "one per calendar day", which a loop
    could satisfy by sending 365.
    """
    occasion = await db.occasions.find_one({"id": occasion_id}, {"_id": 0})
    if not occasion:
        raise HTTPException(404, "Occasion not found")
    guest = await db.guests.find_one({"id": occasion["guest_id"]}, {"_id": 0})
    if not guest:
        raise HTTPException(404, "Guest not found")

    settings = await _settings(db)
    property_record = await resolve_property(user) or {}
    label = occasion.get("label") or ""
    subject_day = f"{local_today()[:4]}-{occasion['month_day']}"

    result = await _deliver(
        db, kind=OCCASION, guest=guest, settings=settings,
        label=label,
        variables=occasion_variables(guest.get("name"), label,
                                     property_record.get("name")),
        subject=occasion_id, subject_day=subject_day,
        sent_by=user.get("id"),
    )
    if result.get("duplicate"):
        # 409 rather than a 200 saying "already sent". A second press is a conflict with
        # what is already true, and the screen has to be able to tell it apart from a
        # send that failed — the first needs no action and the second does.
        #
        # Two wordings, because the claim covers two situations and only one of them is
        # "it went". An attempt whose outcome we could not read holds its claim precisely
        # because the message may have arrived, and telling staff it definitely did would
        # be the same dishonesty this whole feature is built to avoid.
        confirmed = await db.message_log.find_one(
            {"kind": OCCASION, "subject": occasion_id, "subject_day": subject_day,
             "status": SENT}, {"_id": 0})
        raise HTTPException(409, (
            "That greeting has already gone out to this customer for this occasion. "
            "It is not sent twice."
            if confirmed else
            "An earlier attempt at this greeting could not be confirmed — it may have "
            "reached them. It is not sent again; the message log says what happened."))
    return result


# ------------------------------- follow-ups --------------------------------
async def _last_visits(db: PropertyScopedDatabase) -> Dict[str, str]:
    """The property-local date each customer last settled a bill, keyed by E.164 number.

    Read from settled orders rather than from a field on the guest, because a stored
    `last_visit` is wrong the moment anything writes a bill without updating it — and
    wrong in the direction that messages somebody who came in yesterday.

    Keyed on the normalised number, which is what makes `09876500001` on a bill and
    `+91 98765 00001` on a guest record one customer rather than two. That normaliser is
    the staff phone-login work's; see services/identity.py.

    `settled_at` is a UTC instant and the window is counted in the property's own days, so
    it goes through services/clock.py — a bill settled at 1am is that evening's trade.
    """
    visits: Dict[str, str] = {}
    orders = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(50000)
    for order in orders:
        phone = normalise_phone((order.get("customer_phone") or "").strip())
        day = local_date(order.get("settled_at"))
        if not phone or not day:
            continue
        if day > visits.get(phone, ""):
            visits[phone] = day
    return visits


async def _due_follow_ups(db: PropertyScopedDatabase, settings: dict, day: str) -> list:
    """Who the follow-up would go to right now, and why each one qualifies.

    Shared by the screen and by the nightly job so the two cannot disagree — a screen
    that lists somebody the job then skips is how staff learn to stop believing it.

    Three filters, and each one is a different kind of "no":

    * **consent** — dropped entirely, not shown as unsendable. Somebody who asked not to
      be messaged is not a pending task with a problem attached;
    * **already attempted** — filtered on the *claim*, not on a sent log row. The job
      keeps its claim whatever happened, so a follow-up that failed at Meta is one this
      customer has had their attempt at; re-listing it would promise a retry that is not
      going to come;
    * **not lapsed yet** — services/messaging.py::due_for_follow_up, which is `>=`, so a
      ten-day window fires on the tenth day.
    """
    days = follow_up_days(settings)
    visits = await _last_visits(db)
    claimed = {r.get("key") for r in await db.message_claims.find(
        {"kind": FOLLOW_UP}, {"_id": 0}).to_list(20000)}

    out = []
    for guest in await db.guests.find({}, {"_id": 0}).to_list(20000):
        number = recipient(guest)
        last = visits.get(number) if number else None
        if not last or not due_for_follow_up(last, day, days):
            continue
        if consent_problem(guest):
            continue
        if dedupe_key(FOLLOW_UP, guest["id"], last, last) in claimed:
            continue
        out.append({"guest_id": guest["id"], "name": guest.get("name"),
                    "phone": guest.get("phone"), "last_visit": last,
                    "days_since": days_since(last, day),
                    "problem": blocking_problem(settings, guest, FOLLOW_UP)})
    # Longest away first: if a run is cut short, the people who have been gone longest
    # are the ones who got their message.
    return sorted(out, key=lambda r: r["last_visit"])


@router.get("/messaging/follow-ups")
async def follow_ups(user: dict = Depends(OPERATIONAL),
                     db: PropertyScopedDatabase = Depends(tenant_db)):
    """Who the nightly job will message, and under what settings.

    A view, not a work queue — there is no send button behind it and there is deliberately
    no endpoint one could call. The owner asked for the follow-up to happen without
    anybody pressing anything, and a screen with a button on it is how "automatic" quietly
    becomes "somebody was supposed to".

    It exists because an automatic sender that cannot be inspected is one nobody trusts:
    this is the answer to "what is it going to do tonight", and GET /messaging/log is the
    answer to "what did it do".
    """
    _wa_record, _, _ = await whatsapp_for(user)
    _wa_problem = whatsapp_config_problem(_wa_record, need_owner_phone=False)
    settings = await _settings(db)
    if not follow_up_enabled(settings):
        return {"enabled": False, "days": follow_up_days(settings), "customers": [],
                "problem": FOLLOW_UP_OFF}
    return {
        "enabled": True,
        "days": follow_up_days(settings),
        "customers": await _due_follow_ups(db, settings, local_today()),
        "problem": template_problem(settings, FOLLOW_UP) or _wa_problem,
    }


async def run_follow_ups(db: PropertyScopedDatabase, property_record: dict,
                         day: Optional[str] = None) -> dict:
    """Send one property's due follow-ups. Returns what it did, and never raises.

    **Bounded before it starts.** The switch, the template and the WhatsApp credentials
    are all checked once, up front, and a run that cannot send stops there having written
    nothing per customer. That is the difference between an automatic sender and the
    button beside an occasion: a person who presses send is owed a log row saying exactly
    why it did not go, and gets one; a job that cannot send is owed one line in the log
    for the whole run, because the alternative is a failure row per customer per night
    for as long as the credentials are missing, which buries the day somebody fixes them.

    **One attempt per customer per visit, and the claim is kept whatever happened.** The
    job runs every night and the customer is still lapsed tomorrow, so "have we already
    done this one" cannot be a matter of timing — it is the claim taken in `_deliver`,
    keyed on the guest and the date of the visit being followed up. That is also why a
    customer who comes back and lapses again gets a second follow-up: it is a different
    visit and therefore a different key. Unlike the occasion path, a refusal from Meta
    does not release the claim: nobody is watching, so a nightly retry of a number that
    is not on WhatsApp would run until the property closed.
    """
    day = day or local_today()
    settings = await _settings(db)
    counts = {"property": property_record.get("name"), "due": 0, "sent": 0, "failed": 0}

    if not follow_up_enabled(settings):
        return {**counts, "skipped": FOLLOW_UP_OFF}
    _wa_problem = whatsapp_config_problem(property_record, need_owner_phone=False)
    blocked = template_problem(settings, FOLLOW_UP) or _wa_problem
    if blocked:
        logger.warning("[follow-ups] %s: nothing sent — %s",
                       property_record.get("name"), blocked)
        return {**counts, "skipped": blocked}

    due = await _due_follow_ups(db, settings, day)
    counts["due"] = len(due)
    for row in due:
        guest = await db.guests.find_one({"id": row["guest_id"]}, {"_id": 0})
        if not guest:
            continue
        result = await _deliver(
            db, kind=FOLLOW_UP, guest=guest, settings=settings, label="",
            variables=follow_up_variables(guest.get("name"),
                                          property_record.get("name")),
            subject=row["last_visit"], subject_day=row["last_visit"],
            sent_by=None, release_on_definite_failure=False,
        )
        counts["sent" if result.get("sent") else "failed"] += 1
    logger.info("[follow-ups] %s: %d due, %d sent, %d failed.",
                property_record.get("name"), counts["due"], counts["sent"],
                counts["failed"])
    return counts


async def send_follow_ups(day: Optional[str] = None) -> list:
    """Every live property's follow-ups, once. What the scheduled function calls.

    Nothing here belongs to a request, so there is no caller to scope from — the same
    situation `routers/reports.py::send_daily_brief` is in, and the same answer: the
    tenant list comes from `unscoped_db` and each property's work is done through a handle
    bound to that one hotel, so no property's customers can be messaged on another's
    behalf. Pending and suspended hotels are skipped; a hotel that cannot trade has no
    customers to miss.

    A property that raises does not stop the others. One tenant with a broken settings row
    must not silence the whole platform's follow-ups for the night, and the exception is
    logged with the property named so it can actually be found.
    """
    day = day or local_today()
    properties = await unscoped_db.properties.find(
        {"status": LIVE}, {"_id": 0}).to_list(1000)
    results = []
    for record in properties:
        try:
            results.append(await run_follow_ups(
                PropertyScopedDatabase(record["id"]), record, day))
        except Exception as exc:  # noqa: BLE001 — one tenant must not stop the rest
            logger.exception("[follow-ups] %s (%s) failed: %s",
                             record.get("name"), record.get("id"), exc)
            results.append({"property": record.get("name"), "error": str(exc)})
    return results


# ------------------------------- the log -----------------------------------
@router.get("/messaging/log")
async def message_log(limit: int = 200, user: dict = Depends(OPERATIONAL),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    """Every message this property attempted, newest first.

    Append-only — there is no write endpoint here and no update anywhere in this module.
    This is what answers "did the birthday message go out" and "why did it not", and both
    answers are worthless against a row somebody could have tidied up afterwards. See
    models/messaging.py.

    Readable by the same people who can send, and not restricted to admin: the person who
    pressed send an hour ago is the one who needs to know whether it arrived.
    """
    return await db.message_log.find({}, {"_id": 0}).sort(
        "sent_at", -1).to_list(min(max(limit, 1), 1000))


# --------------------------- the one way out -------------------------------
async def _claim(db: PropertyScopedDatabase, key: str, kind: str, guest_id: str) -> bool:
    """Take the exclusive right to send this message, or find somebody already has.

    An atomic upsert, not a read followed by an insert. Two members of staff pressing send
    on two terminals in the same second both pass a read-then-insert check, and the guest
    gets two birthday messages — the exact failure "make it structural, not a disabled
    button" is about. `$inc` on an upsert is one round trip against MongoDB, and the
    unique index on (property_id, key) in server.py is what makes it atomic there.

    Returns True when *this* call created the claim. `matched_count == 0` is the test:
    Mongo reports a match only when the row already existed, so the winner is the caller
    that matched nothing.

    The counter is not decoration. It is how "somebody pressed it four times" survives to
    be read later, and it is the reason a refused second press does not need a log row of
    its own — nothing was sent, so nothing belongs in a log of messages.
    """
    result = await db.message_claims.update_one(
        {"key": key},
        {"$inc": {"attempts": 1},
         "$set": {"kind": kind, "guest_id": guest_id, "last_attempt_at": local_today()}},
        upsert=True)
    return result.matched_count == 0


async def _log(db: PropertyScopedDatabase, **fields) -> dict:
    row = MessageLogEntry(**fields).model_dump()
    await db.message_log.insert_one(row)
    row.pop("_id", None)
    return row


async def _deliver(db: PropertyScopedDatabase, *, kind: str, guest: dict, settings: dict,
                   label: str, variables: List[str], subject: str, subject_day: str,
                   sent_by: Optional[str],
                   release_on_definite_failure: bool = True) -> dict:
    """Send one message to one customer, once, and write down what happened.

    **The only place in this application that hands a customer's number to WhatsApp.**
    Every rule that has to hold for every message holds here and nowhere else: consent,
    a usable number, an approved template, and the claim that makes a second send
    impossible. A new kind of message added later gets all four by calling this, and a new
    kind that does not call this is caught by the test that counts the call sites.

    Never raises. A duplicate comes back as `{"duplicate": True}` for the endpoint to turn
    into a 409, because the nightly job's answer to the same situation is to move on to
    the next customer rather than to abandon the run.
    """
    # The number this message goes out from, resolved from the property it belongs to.
    # There is no fallback: a property with no credentials of its own sends nothing,
    # which is the decision stated in services/whatsapp.py.
    _sender = await _db_module.unscoped_db.properties.find_one(
        {"id": db.property_id}, {"_id": 0})
    phone_id, token = whatsapp_credentials(_sender)

    # Consent, first and by name. `blocking_problem` checks it too — the same question
    # asked twice, on purpose, so that the one refusal carrying legal weight is visible in
    # this function rather than three call frames away. Unsolicited commercial messaging
    # is regulated in India and it is the property that carries the risk.
    denied = consent_problem(guest)
    # Missing credentials join the same refusal path rather than returning early, so an
    # unsendable message is logged with its reason exactly as every other refusal is. A
    # staff member who pressed a button is owed the reason nothing went; returning
    # silently was a regression this test caught.
    #
    # The nightly job does not reach here at all when credentials are absent — it stops
    # at `run_follow_ups`, which is what keeps one missing setting from writing a failure
    # row per customer per night.
    no_credentials = (None if (phone_id and token)
                      else whatsapp_config_problem(_sender, need_owner_phone=False))
    refusal = denied or no_credentials or blocking_problem(settings, guest, kind, label)
    if refusal:
        row = await _log(db, kind=kind, status=REFUSED if denied else FAILED,
                         guest_id=guest["id"], guest_name=guest.get("name") or "",
                         to=recipient(guest) or "", occasion_label=label,
                         template=template_for(settings, kind, label),
                         language=template_language(settings), variables=variables,
                         subject=subject, subject_day=subject_day, error=refusal,
                         sent_by=sent_by)
        return {"sent": False, "error": refusal, "log_id": row["id"],
                "status": row["status"]}

    key = dedupe_key(kind, guest["id"], subject, subject_day)
    if not await _claim(db, key, kind, guest["id"]):
        return {"sent": False, "duplicate": True, "error":
                "This message has already gone out to this customer.", "log_id": None}

    to = recipient(guest)
    template = template_for(settings, kind, label)
    language = template_language(settings)
    # Off the event loop: it is a blocking urllib call, and the till is on the same
    # process. The lambda is what keeps the module-level name resolved at call time.
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: send_whatsapp_template(phone_id, token, to, template, language,
                                             variables))

    sent = bool(result.get("sent"))
    row = await _log(db, kind=kind, status=SENT if sent else FAILED,
                     guest_id=guest["id"], guest_name=guest.get("name") or "", to=to,
                     occasion_label=label, template=template, language=language,
                     variables=variables, subject=subject, subject_day=subject_day,
                     message_id=result.get("message_id"),
                     error="" if sent else (result.get("error") or "")[:1000],
                     provider_status=result.get("status"),
                     provider_error_code=result.get("error_code"),
                     sent_by=sent_by)

    if not sent and release_on_definite_failure and send_definitely_did_not_happen(result):
        # Nothing reached the customer and we know it, so the day is not burnt: staff can
        # fix the configuration and press send again. A failure we cannot account for —
        # a socket that died mid-request — keeps its claim, because the message may well
        # have been delivered and a retry would be the second one.
        await db.message_claims.delete_one({"key": key})

    return {"sent": sent, "message_id": result.get("message_id"),
            "error": "" if sent else row["error"], "log_id": row["id"],
            "status": row["status"], "template": template, "to": to}
