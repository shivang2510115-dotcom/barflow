"""Hotel API integration tests. Requires a running server (see backend_test.py)."""
import os
import random
import uuid
from datetime import date, timedelta

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@barflow.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


def test_front_desk_role_exists(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Desk Tester",
        "email": f"desk-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "desk12345",
        "role": "front_desk",
        "domains": ["hotel"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "front_desk"


def test_create_and_find_guest(admin):
    phone = f"99{uuid.uuid4().int % 100000000:08d}"
    r = admin.post(f"{API}/guests", json={"name": "Test Guest", "phone": phone})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]

    r2 = admin.get(f"{API}/guests", params={"q": phone})
    assert r2.status_code == 200
    assert any(g["id"] == gid for g in r2.json())


def test_duplicate_phone_returns_409_with_existing_guest(admin):
    phone = f"98{uuid.uuid4().int % 100000000:08d}"
    first = admin.post(f"{API}/guests", json={"name": "First", "phone": phone})
    assert first.status_code == 200

    dup = admin.post(f"{API}/guests", json={"name": "Second", "phone": phone})
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["guest"]["id"] == first.json()["id"]


# ------------------------ room types & rooms ------------------------
@pytest.fixture(scope="module")
def room_type(admin):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    r = admin.post(f"{API}/room-types", json={
        "name": "Deluxe Test", "code": code,
        "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_create_room_and_list(admin, room_type):
    number = f"R{uuid.uuid4().hex[:5]}"
    r = admin.post(f"{API}/rooms", json={"number": number, "room_type_id": room_type["id"]})
    assert r.status_code == 200, r.text

    listing = admin.get(f"{API}/rooms")
    assert any(x["number"] == number for x in listing.json())


def test_delete_room_type_with_rooms_is_blocked(admin, room_type):
    # Create our own room here (rather than relying on test_create_room_and_list having
    # run first) so this test is self-contained and passes in isolation or any order.
    number = f"R{uuid.uuid4().hex[:5]}"
    made = admin.post(f"{API}/rooms", json={"number": number, "room_type_id": room_type["id"]})
    assert made.status_code == 200, made.text

    r = admin.delete(f"{API}/room-types/{room_type['id']}")
    assert r.status_code == 409, r.text


def test_room_type_max_occupancy_below_base_occupancy_rejected(admin):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    r = admin.post(f"{API}/room-types", json={
        "name": "Bad Occupancy Type", "code": code,
        "base_occupancy": 3, "max_occupancy": 2,
    })
    assert r.status_code == 400, r.text


def test_update_room_type_roundtrip(admin):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    created = admin.post(f"{API}/room-types", json={
        "name": "Original Name", "code": code,
        "base_occupancy": 2, "max_occupancy": 2,
    })
    assert created.status_code == 200, created.text
    type_id = created.json()["id"]

    updated = admin.put(f"{API}/room-types/{type_id}", json={
        "name": "Renamed Type", "code": code,
        "base_occupancy": 2, "max_occupancy": 4,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Renamed Type"
    assert updated.json()["max_occupancy"] == 4


def test_delete_room_type_without_rooms_or_bookings_succeeds(admin):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    created = admin.post(f"{API}/room-types", json={
        "name": "Deletable Type", "code": code,
        "base_occupancy": 2, "max_occupancy": 2,
    })
    assert created.status_code == 200, created.text
    type_id = created.json()["id"]

    r = admin.delete(f"{API}/room-types/{type_id}")
    assert r.status_code == 200, r.text


def _fresh_room_type_and_room(admin):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    rt = admin.post(f"{API}/room-types", json={
        "name": "OOO Test Type", "code": code,
        "base_occupancy": 2, "max_occupancy": 2,
    })
    assert rt.status_code == 200, rt.text
    rt = rt.json()

    number = f"R{uuid.uuid4().hex[:5]}"
    room = admin.post(f"{API}/rooms", json={"number": number, "room_type_id": rt["id"]})
    assert room.status_code == 200, room.text
    return rt, room.json()


def test_mark_room_out_of_order_rejects_non_positive_range(admin):
    rt, room = _fresh_room_type_and_room(admin)
    r = admin.post(f"{API}/rooms/{room['id']}/out-of-order", json={
        "from": "2026-09-05", "to": "2026-09-01", "reason": "bad range",
    })
    assert r.status_code == 400, r.text


def test_mark_room_out_of_order_no_warning_without_live_bookings(admin):
    rt, room = _fresh_room_type_and_room(admin)
    r = admin.post(f"{API}/rooms/{room['id']}/out-of-order", json={
        "from": "2026-09-01", "to": "2026-09-05", "reason": "maintenance",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # No live bookings exist for this brand-new room type, so blocking its only room
    # must not produce a warning even though it drops availability to zero.
    assert body["warning"] is None

    stored_block = body["room"]["out_of_order"][0]
    assert stored_block["from"] == "2026-09-01"
    assert stored_block["to"] == "2026-09-05"


def test_out_of_order_block_stored_as_half_open_range(admin):
    """The persisted block feeds services.availability, which treats ranges as
    half-open [from, to) — a stay arriving the day the block ends is bookable."""
    from services.availability import count_available

    rt, room = _fresh_room_type_and_room(admin)
    r = admin.post(f"{API}/rooms/{room['id']}/out-of-order", json={
        "from": "2026-09-01", "to": "2026-09-05", "reason": "maintenance",
    })
    assert r.status_code == 200, r.text

    listing = admin.get(f"{API}/rooms").json()
    stored_room = next(x for x in listing if x["id"] == room["id"])

    # Still blocked on the last blocked night.
    assert count_available(rt["id"], "2026-09-04", "2026-09-06", [stored_room], []) == 0
    # Free again on the day the block ends.
    assert count_available(rt["id"], "2026-09-05", "2026-09-06", [stored_room], []) == 1


def test_out_of_order_response_only_warns_never_cancels(admin):
    """Marking a room out of order returns a warning field, never a cancellation or a
    booking mutation — the endpoint has no power to touch the bookings collection."""
    rt, room = _fresh_room_type_and_room(admin)

    r = admin.post(f"{API}/rooms/{room['id']}/out-of-order", json={
        "from": "2026-10-01", "to": "2026-10-10", "reason": "renovation",
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert "bookings" not in body
    assert "cancelled" not in body
    after_room = body["room"]
    assert after_room["room_type_id"] == room["room_type_id"]
    assert after_room["out_of_order"] == [
        {"from": "2026-10-01", "to": "2026-10-10", "reason": "renovation"}
    ]


def test_delete_room_not_found_returns_404(admin):
    r = admin.delete(f"{API}/rooms/does-not-exist-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 404, r.text


def test_delete_room_succeeds_when_unassigned(admin):
    rt, room = _fresh_room_type_and_room(admin)
    r = admin.delete(f"{API}/rooms/{room['id']}")
    assert r.status_code == 200, r.text

    listing = admin.get(f"{API}/rooms").json()
    assert not any(x["id"] == room["id"] for x in listing)


# --------------------- meal plans, rates, tax slabs ---------------------
def test_seeded_meal_plans_and_tax_slabs(admin):
    plans = admin.get(f"{API}/meal-plans")
    assert plans.status_code == 200, plans.text
    assert {p["code"] for p in plans.json()} >= {"EP", "CP", "MAP"}

    slabs = admin.get(f"{API}/tax-slabs")
    assert slabs.status_code == 200
    assert len(slabs.json()) >= 2


def test_create_rate_for_room_type(admin, room_type):
    r = admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": None,
        "base_rate": 5000.0, "extra_adult_rate": 1000.0, "extra_child_rate": 500.0,
    })
    assert r.status_code == 200, r.text
    assert r.json()["base_rate"] == 5000.0


def test_create_and_update_meal_plan(admin):
    code = f"MP{uuid.uuid4().hex[:6].upper()}"
    created = admin.post(f"{API}/meal-plans", json={
        "code": code, "name": "All inclusive",
        "price_per_adult_per_night": 2000.0, "price_per_child_per_night": 1000.0,
    })
    assert created.status_code == 200, created.text
    plan_id = created.json()["id"]

    updated = admin.put(f"{API}/meal-plans/{plan_id}", json={
        "code": code, "name": "All inclusive (renamed)",
        "price_per_adult_per_night": 2200.0, "price_per_child_per_night": 1100.0,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "All inclusive (renamed)"
    assert updated.json()["price_per_adult_per_night"] == 2200.0


def test_update_meal_plan_not_found_returns_404(admin):
    r = admin.put(f"{API}/meal-plans/does-not-exist-{uuid.uuid4().hex[:8]}", json={
        "code": "ZZ", "name": "Ghost plan",
    })
    assert r.status_code == 404, r.text


def test_rate_period_end_date_must_be_after_start_date(admin):
    r = admin.post(f"{API}/rate-periods", json={
        "name": f"Bad Period {uuid.uuid4().hex[:6]}",
        "start_date": "2026-12-10", "end_date": "2026-12-10",
    })
    assert r.status_code == 400, r.text


def _random_window(span_days: int, offset_days: int = 0):
    """A start/end date pair inside a large pseudo-random future range — so repeated
    runs of the same test never land on the same window as a period left over in
    db.json from a prior run against this same mock database."""
    base = date(2030, 1, 1) + timedelta(days=random.randint(0, 36500))
    start = base + timedelta(days=offset_days)
    end = start + timedelta(days=span_days)
    return start.isoformat(), end.isoformat()


def _random_priority() -> int:
    """A priority far outside the small fixed values other tests use (0, 1, 2, 5, 9),
    and randomised so reruns don't collide with a prior run's leftover periods."""
    return random.randint(10_000, 999_999)


def test_create_rate_period_overlap_same_priority_warns(admin):
    tag = uuid.uuid4().hex[:6]
    priority = _random_priority()
    start1, end1 = _random_window(span_days=14)
    # Overlaps the first window by 5 nights, same relationship the original fixed
    # dates had.
    start2 = (date.fromisoformat(start1) + timedelta(days=9)).isoformat()
    end2 = (date.fromisoformat(start1) + timedelta(days=19)).isoformat()

    first = admin.post(f"{API}/rate-periods", json={
        "name": f"Diwali {tag}", "start_date": start1, "end_date": end1,
        "priority": priority,
    })
    assert first.status_code == 200, first.text
    assert first.json()["overlap_warning"] is None

    second = admin.post(f"{API}/rate-periods", json={
        "name": f"Xmas {tag}", "start_date": start2, "end_date": end2,
        "priority": priority,
    })
    assert second.status_code == 200, second.text
    assert second.json()["overlap_warning"] is not None
    assert first.json()["name"] in second.json()["overlap_warning"]


def test_create_rate_period_overlap_different_priority_no_warning(admin):
    tag = uuid.uuid4().hex[:6]
    start1, end1 = _random_window(span_days=19)
    start2 = (date.fromisoformat(start1) + timedelta(days=9)).isoformat()
    end2 = (date.fromisoformat(start1) + timedelta(days=15)).isoformat()

    priority1 = _random_priority()
    priority2 = priority1 + 1  # guaranteed distinct from priority1, still unique per run

    first = admin.post(f"{API}/rate-periods", json={
        "name": f"Base Season {tag}", "start_date": start1, "end_date": end1,
        "priority": priority1,
    })
    assert first.status_code == 200, first.text

    second = admin.post(f"{API}/rate-periods", json={
        "name": f"Valentine Special {tag}", "start_date": start2, "end_date": end2,
        "priority": priority2,
    })
    assert second.status_code == 200, second.text
    assert second.json()["overlap_warning"] is None


def test_post_rates_replaces_existing_rate_for_same_pair(admin, room_type):
    period = admin.post(f"{API}/rate-periods", json={
        "name": f"Replace Test {uuid.uuid4().hex[:6]}",
        "start_date": "2027-03-01", "end_date": "2027-03-10", "priority": 2,
    })
    assert period.status_code == 200, period.text
    period_id = period.json()["id"]

    first = admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": period_id,
        "base_rate": 4000.0, "extra_adult_rate": 500.0, "extra_child_rate": 250.0,
    })
    assert first.status_code == 200, first.text

    second = admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": period_id,
        "base_rate": 4500.0, "extra_adult_rate": 600.0, "extra_child_rate": 300.0,
    })
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["base_rate"] == 4500.0

    listing = admin.get(f"{API}/rates").json()
    matches = [
        r for r in listing
        if r["room_type_id"] == room_type["id"] and r["period_id"] == period_id
    ]
    assert len(matches) == 1
    assert matches[0]["base_rate"] == 4500.0


def test_post_rates_rejects_unknown_room_type_and_period(admin, room_type):
    bad_room_type = admin.post(f"{API}/rates", json={
        "room_type_id": f"missing-{uuid.uuid4().hex[:8]}", "period_id": None,
        "base_rate": 3000.0,
    })
    assert bad_room_type.status_code == 400, bad_room_type.text

    bad_period = admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": f"missing-{uuid.uuid4().hex[:8]}",
        "base_rate": 3000.0,
    })
    assert bad_period.status_code == 400, bad_period.text


def test_delete_rate_period_removes_orphaned_rates(admin, room_type):
    period = admin.post(f"{API}/rate-periods", json={
        "name": f"To Delete {uuid.uuid4().hex[:6]}",
        "start_date": "2027-04-01", "end_date": "2027-04-10", "priority": 1,
    })
    assert period.status_code == 200, period.text
    period_id = period.json()["id"]

    rate = admin.post(f"{API}/rates", json={
        "room_type_id": room_type["id"], "period_id": period_id, "base_rate": 3500.0,
    })
    assert rate.status_code == 200, rate.text

    deleted = admin.delete(f"{API}/rate-periods/{period_id}")
    assert deleted.status_code == 200, deleted.text

    periods_listing = admin.get(f"{API}/rate-periods").json()
    assert not any(p["id"] == period_id for p in periods_listing)

    rates_listing = admin.get(f"{API}/rates").json()
    assert not any(r["period_id"] == period_id for r in rates_listing)


def test_put_tax_slabs_replaces_whole_table(admin):
    new_slabs = [
        {"min_tariff": 0.0, "max_tariff": 6000.0, "rate_percent": 5.0, "active": True},
        {"min_tariff": 6000.0, "max_tariff": None, "rate_percent": 18.0, "active": True},
    ]
    r = admin.put(f"{API}/tax-slabs", json=new_slabs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    assert sorted(s["rate_percent"] for s in body) == [5.0, 18.0]


def test_put_tax_slabs_rejects_empty_list(admin):
    r = admin.put(f"{API}/tax-slabs", json=[])
    assert r.status_code == 400, r.text


# ------------------------ availability & bookings ------------------------
# Every test below builds its own room type, rooms, rate and guest, in its own
# date window — nothing here is shared, so each test must pass alone or in any
# order under `-n 2 --dist loadscope`.
def _new_room_type(admin, **overrides):
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "name": "Booking Test Type", "code": code,
        "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1,
    }
    payload.update(overrides)
    r = admin.post(f"{API}/room-types", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _add_rooms(admin, room_type_id, count=1):
    rooms = []
    for _ in range(count):
        r = admin.post(f"{API}/rooms", json={
            "number": f"R{uuid.uuid4().hex[:5]}", "room_type_id": room_type_id})
        assert r.status_code == 200, r.text
        rooms.append(r.json())
    return rooms


def _add_default_rate(admin, room_type_id, base_rate=5000.0,
                      extra_adult_rate=1000.0, extra_child_rate=500.0):
    r = admin.post(f"{API}/rates", json={
        "room_type_id": room_type_id, "period_id": None,
        "base_rate": base_rate, "extra_adult_rate": extra_adult_rate,
        "extra_child_rate": extra_child_rate,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _reset_default_tax_slabs(admin):
    """Tax slabs are a single global table (PUT replaces it wholesale), and
    `test_put_tax_slabs_replaces_whole_table` above overwrites it. Reset to the
    canonical 12%/18% @ 7500 bands here so a test asserting an exact GST total
    is correct regardless of what ran before it."""
    r = admin.put(f"{API}/tax-slabs", json=[
        {"min_tariff": 0.0, "max_tariff": 7500.0, "rate_percent": 12.0, "active": True},
        {"min_tariff": 7500.0, "max_tariff": None, "rate_percent": 18.0, "active": True},
    ])
    assert r.status_code == 200, r.text


def _new_guest(admin):
    phone = f"9{uuid.uuid4().int % 1000000000:09d}"
    r = admin.post(f"{API}/guests", json={"name": "Booking Guest", "phone": phone})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def ep_plan(admin):
    """Read-only lookup of the seeded EP meal plan — safe to share across tests
    since nothing here mutates it."""
    plans = admin.get(f"{API}/meal-plans").json()
    return next(p for p in plans if p["code"] == "EP")


def test_availability_returns_priced_quote(admin, ep_plan):
    _reset_default_tax_slabs(admin)
    rt = _new_room_type(admin)
    _add_rooms(admin, rt["id"], 2)
    _add_default_rate(admin, rt["id"])

    r = admin.get(f"{API}/availability", params={
        "check_in": "2027-03-01", "check_out": "2027-03-03", "adults": 2, "children": 0})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["room_type"]["id"] == rt["id"])
    assert row["available"] == 2
    # Whether meal plans are on is the property's own setting, and a fresh property
    # defaults to off — one all-inclusive rate. Either way the room itself is priced the
    # same, which is what this test is about: with plans on, EP is the room-only plan and
    # carries no supplement; with them off there is a single quote and no plan at all.
    quote = next(q for q in row["quotes"]
                 if (q["meal_plan"] or {}).get("code", "EP") == "EP")
    assert quote["room_subtotal"] == 10000.0
    assert quote["tax_total"] == 1200.0
    assert quote["total"] == 11200.0


def test_create_booking_consumes_inventory(admin, ep_plan):
    _reset_default_tax_slabs(admin)
    rt = _new_room_type(admin)
    _add_rooms(admin, rt["id"], 2)
    _add_default_rate(admin, rt["id"])
    guest = _new_guest(admin)

    body = {
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-04-01",
        "check_out": "2027-04-03", "adults": 2, "children": 0,
    }
    r = admin.post(f"{API}/bookings", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["quote"]["total"] == 11200.0
    assert r.json()["reference"].startswith("BF-")

    avail = admin.get(f"{API}/availability", params={
        "check_in": "2027-04-01", "check_out": "2027-04-03", "adults": 2, "children": 0})
    row = next(x for x in avail.json() if x["room_type"]["id"] == rt["id"])
    assert row["available"] == 1


def test_checkout_day_does_not_block_next_arrival(admin, ep_plan):
    rt = _new_room_type(admin)
    _add_rooms(admin, rt["id"], 2)
    _add_default_rate(admin, rt["id"])
    guest = _new_guest(admin)

    body = {
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2028-01-01",
        "check_out": "2028-01-03", "adults": 2, "children": 0,
    }
    created = admin.post(f"{API}/bookings", json=body)
    assert created.status_code == 200, created.text

    # Existing stay is 2028-01-01 → 2028-01-03, so the 3rd must be fully free.
    avail = admin.get(f"{API}/availability", params={
        "check_in": "2028-01-03", "check_out": "2028-01-04", "adults": 2, "children": 0})
    row = next(x for x in avail.json() if x["room_type"]["id"] == rt["id"])
    assert row["available"] == 2


def test_overbooking_is_refused(admin, ep_plan):
    rt = _new_room_type(admin)
    _add_rooms(admin, rt["id"], 2)
    _add_default_rate(admin, rt["id"])
    guest = _new_guest(admin)

    body = {
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-05-01",
        "check_out": "2027-05-02", "adults": 2, "children": 0,
    }
    assert admin.post(f"{API}/bookings", json=body).status_code == 200
    assert admin.post(f"{API}/bookings", json=body).status_code == 200
    third = admin.post(f"{API}/bookings", json=body)
    assert third.status_code == 409, third.text


def test_missing_rate_refuses_rather_than_pricing_zero(admin, ep_plan):
    bare = _new_room_type(admin, name="Unpriced")
    _add_rooms(admin, bare["id"], 1)
    guest = _new_guest(admin)

    r = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": bare["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-06-01",
        "check_out": "2027-06-02", "adults": 2, "children": 0})
    assert r.status_code == 422, r.text
    assert "2027-06-01" in str(r.json()["detail"])


def test_checkout_before_checkin_is_rejected(admin, ep_plan):
    rt = _new_room_type(admin)
    guest = _new_guest(admin)
    r = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-07-05",
        "check_out": "2027-07-05", "adults": 2, "children": 0})
    assert r.status_code == 400, r.text


def test_occupancy_above_ceiling_is_rejected(admin, ep_plan):
    # base 2, max_occupancy 3, max_extra_beds 1 → ceiling of 4
    rt = _new_room_type(admin)
    _add_rooms(admin, rt["id"], 1)
    _add_default_rate(admin, rt["id"])
    guest = _new_guest(admin)

    r = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-08-01",
        "check_out": "2027-08-02", "adults": 9, "children": 0})
    assert r.status_code == 400, r.text


def test_cancel_releases_inventory(admin, ep_plan):
    rt = _new_room_type(admin)
    _add_rooms(admin, rt["id"], 2)
    _add_default_rate(admin, rt["id"])
    guest = _new_guest(admin)

    body = {
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": "2027-09-01",
        "check_out": "2027-09-02", "adults": 2, "children": 0,
    }
    booking = admin.post(f"{API}/bookings", json=body).json()

    before = admin.get(f"{API}/availability", params={
        "check_in": "2027-09-01", "check_out": "2027-09-02", "adults": 2, "children": 0})
    assert next(x for x in before.json()
                if x["room_type"]["id"] == rt["id"])["available"] == 1

    assert admin.post(f"{API}/bookings/{booking['id']}/cancel",
                      json={"reason": "Guest called"}).status_code == 200

    after = admin.get(f"{API}/availability", params={
        "check_in": "2027-09-01", "check_out": "2027-09-02", "adults": 2, "children": 0})
    assert next(x for x in after.json()
                if x["room_type"]["id"] == rt["id"])["available"] == 2


# ------------------ 409 refusals that needed a bookings endpoint ------------------
def test_delete_room_type_with_live_booking_is_blocked(admin, ep_plan):
    """Task 7 built this refusal path but could not test it — no bookings endpoint
    existed yet. Now one does."""
    rt = _new_room_type(admin, name="Live Booking Type")
    _add_rooms(admin, rt["id"], 1)
    _add_default_rate(admin, rt["id"], base_rate=4000.0)
    guest = _new_guest(admin)

    booking = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": rt["id"], "meal_plan_id": ep_plan["id"],
        "check_in": "2027-10-01", "check_out": "2027-10-02", "adults": 2, "children": 0,
    })
    assert booking.status_code == 200, booking.text

    r = admin.delete(f"{API}/room-types/{rt['id']}")
    assert r.status_code == 409, r.text


# ------------------------- Task 3: check-in and folio -------------------------
def _stay(admin, ci, co, adults=2):
    """Create a bookable room type with 1 room, a rate, a guest and a confirmed booking."""
    code = f"F{uuid.uuid4().hex[:6].upper()}"
    rt = admin.post(f"{API}/room-types", json={
        "name": f"Folio {code}", "code": code,
        "base_occupancy": 2, "max_occupancy": 3, "max_extra_beds": 1}).json()
    room = admin.post(f"{API}/rooms", json={
        "number": f"F{uuid.uuid4().hex[:5]}", "room_type_id": rt["id"]}).json()
    admin.post(f"{API}/rates", json={
        "room_type_id": rt["id"], "period_id": None, "base_rate": 5000.0,
        "extra_adult_rate": 1000.0, "extra_child_rate": 500.0})
    ep = next(p for p in admin.get(f"{API}/meal-plans").json() if p["code"] == "EP")
    guest = admin.post(f"{API}/guests", json={
        "name": "Folio Guest", "phone": f"96{uuid.uuid4().int % 100000000:08d}"}).json()
    booking = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": rt["id"], "meal_plan_id": ep["id"],
        "check_in": ci, "check_out": co, "adults": adults, "children": 0}).json()
    return {"room_type": rt, "room": room, "guest": guest, "booking": booking}


def _checked_in(admin, ci, co):
    """A stay that has been checked in, with its folio id attached."""
    s = _stay(admin, ci, co)
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "9090-8080-7070"})
    assert r.status_code == 200, r.text
    s["folio_id"] = r.json()["folio"]["id"]
    return s


def test_check_in_assigns_room_and_opens_folio(admin):
    s = _stay(admin, "2029-01-05", "2029-01-08")
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1234-5678-9012"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booking"]["status"] == "checked_in"
    assert body["booking"]["assigned_room_id"] == s["room"]["id"]
    assert body["folio"]["status"] == "open"


def test_check_in_requires_id_proof(admin):
    s = _stay(admin, "2029-02-05", "2029-02-08")
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "", "id_proof_number": ""})
    assert r.status_code == 400, r.text


def test_double_check_in_is_refused(admin):
    s = _checked_in(admin, "2029-03-05", "2029-03-08")
    again = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1111-2222-3333"})
    assert again.status_code == 409, again.text


def test_check_in_refuses_a_room_of_the_wrong_type(admin):
    s = _stay(admin, "2029-04-05", "2029-04-08")
    other = _stay(admin, "2029-04-05", "2029-04-08")
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": other["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "4444-5555-6666"})
    assert r.status_code == 409, r.text


def test_in_house_lists_checked_in_guests_only(admin):
    s = _stay(admin, "2029-05-05", "2029-05-08")
    before = admin.get(f"{API}/in-house").json()
    assert all(x["booking"]["id"] != s["booking"]["id"] for x in before)

    admin.post(f"{API}/bookings/{s['booking']['id']}/check-in", json={
        "room_id": s["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "7777-8888-9999"})
    after = admin.get(f"{API}/in-house").json()
    assert any(x["booking"]["id"] == s["booking"]["id"] for x in after)


def test_front_desk_groups_arrivals_departures_and_in_house(admin):
    r = admin.get(f"{API}/front-desk")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("date", "arrivals", "departures", "in_house"):
        assert key in body, body


def test_room_nights_post_lazily_and_only_once(admin):
    # Stay already in the past, so every night is due the moment the folio is read.
    s = _checked_in(admin, "2024-01-05", "2024-01-08")
    first = admin.get(f"{API}/folios/{s['folio_id']}").json()
    nights = [e for e in first["entries"] if e["kind"] == "room_night"]
    assert len(nights) == 3, first
    assert first["balance"] == round(sum(n["amount"] for n in nights), 2)

    second = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert len([e for e in second["entries"] if e["kind"] == "room_night"]) == 3
    assert second["balance"] == first["balance"]


def test_room_night_amounts_come_from_the_booking_quote(admin):
    s = _checked_in(admin, "2024-02-05", "2024-02-08")
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()
    booked = admin.get(f"{API}/bookings/{s['booking']['id']}").json()
    quoted = {n["date"]: round(n["tariff"] + n["gst_amount"], 2)
              for n in booked["quote"]["nights"]}
    posted = [e for e in folio["entries"] if e["kind"] == "room_night"]
    assert posted, folio
    for entry in posted:
        assert entry["amount"] == quoted[entry["charge_date"]]


def test_future_stay_posts_no_nights_yet(admin):
    s = _checked_in(admin, "2031-01-05", "2031-01-08")
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert [e for e in folio["entries"] if e["kind"] == "room_night"] == []
    assert folio["balance"] == 0.0


# ------------------------- Task 4: charges, payments, check-out -------------------------
def test_charge_and_payment_move_the_balance(admin):
    s = _checked_in(admin, "2029-08-05", "2029-08-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1500.0, "description": "Spa"})
    after_charge = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after_charge["balance"] == 1500.0

    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 500.0, "method": "cash", "kind": "payment"})
    after_payment = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after_payment["balance"] == 1000.0


def test_refund_increases_the_balance(admin):
    s = _checked_in(admin, "2029-09-05", "2029-09-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1000.0, "description": "Minibar"})
    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 1000.0, "method": "cash", "kind": "payment"})
    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 400.0, "method": "cash", "kind": "refund"})
    assert admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"] == 400.0


def test_negative_or_zero_amounts_are_refused(admin):
    s = _checked_in(admin, "2029-10-05", "2029-10-08")
    bad = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                     json={"amount": 0, "description": "Nothing"})
    assert bad.status_code == 400, bad.text
    worse = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                       json={"amount": -50, "description": "Negative"})
    assert worse.status_code == 400, worse.text


def test_check_out_blocked_while_balance_outstanding(admin):
    s = _checked_in(admin, "2029-06-05", "2029-06-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 900.0, "description": "Laundry"})
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out", json={})
    assert r.status_code == 409, r.text


def test_force_check_out_requires_a_reason_and_closes_unpaid(admin):
    s = _checked_in(admin, "2029-07-05", "2029-07-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 700.0, "description": "Minibar"})

    no_reason = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
                           json={"force": True})
    assert no_reason.status_code == 400, no_reason.text

    forced = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
                        json={"force": True, "reason": "Company will settle"})
    assert forced.status_code == 200, forced.text
    assert forced.json()["folio"]["status"] == "closed_unpaid"


def test_check_out_settles_when_the_balance_is_paid(admin):
    s = _checked_in(admin, "2031-03-05", "2031-03-08")   # future stay: no nights due yet
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 600.0, "description": "Laundry"})
    admin.post(f"{API}/folios/{s['folio_id']}/payments",
               json={"amount": 600.0, "method": "card", "kind": "payment"})
    r = admin.post(f"{API}/bookings/{s['booking']['id']}/check-out", json={})
    assert r.status_code == 200, r.text
    assert r.json()["folio"]["status"] == "settled"
    assert r.json()["booking"]["status"] == "checked_out"


def test_charging_a_closed_folio_is_refused(admin):
    s = _checked_in(admin, "2029-11-05", "2029-11-08")
    admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
               json={"force": True, "reason": "test"})
    r = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                   json={"amount": 100.0, "description": "Too late"})
    assert r.status_code == 409, r.text


# ------------------------- Task 5: voids -------------------------
def test_void_credits_the_folio_and_zeroes_the_balance(admin):
    s = _checked_in(admin, "2029-12-05", "2029-12-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 800.0, "description": "Spa"}).json()
    before = admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"]

    r = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void",
        json={"reason": "Guest disputed"})
    assert r.status_code == 200, r.text

    after = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after["balance"] == round(before - 800.0, 2)
    voids = [e for e in after["entries"] if e["kind"] == "void"]
    assert len(voids) == 1
    assert voids[0]["direction"] == "credit"
    assert voids[0]["ref_entry_id"] == charge["entry"]["id"]


def test_voiding_a_payment_debits_the_folio(admin):
    s = _checked_in(admin, "2030-01-05", "2030-01-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1000.0, "description": "Spa"})
    pay = admin.post(f"{API}/folios/{s['folio_id']}/payments",
                     json={"amount": 1000.0, "method": "cash", "kind": "payment"}).json()
    assert admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"] == 0.0

    admin.post(f"{API}/folios/{s['folio_id']}/entries/{pay['entry']['id']}/void",
               json={"reason": "Payment reversed by bank"})
    after = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert after["balance"] == 1000.0
    assert next(e for e in after["entries"] if e["kind"] == "void")["direction"] == "debit"


def test_voiding_twice_is_refused(admin):
    s = _checked_in(admin, "2030-02-05", "2030-02-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 300.0, "description": "Laundry"}).json()
    body = {"reason": "Duplicate"}
    first = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void", json=body)
    assert first.status_code == 200
    second = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void", json=body)
    assert second.status_code == 409, second.text


def test_void_requires_a_reason(admin):
    s = _checked_in(admin, "2030-03-05", "2030-03-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 200.0, "description": "Laundry"}).json()
    r = admin.post(f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void",
                   json={"reason": "   "})
    assert r.status_code == 400, r.text


# ------------------------- Task 6: charge to room at the POS -------------------------
def _open_order(admin):
    """Open a bar order on a table of this test's own, and return it.

    It creates the table rather than picking a free one. Reusing whatever happened to
    be free made the whole suite fail with StopIteration against a busy property — the
    tests broke because the restaurant was full, which says nothing about the code.
    """
    table = admin.post(f"{API}/tables", json={
        "label": f"T{uuid.uuid4().hex[:5].upper()}", "capacity": 4, "zone": "Test"}).json()
    # A dish priced by portion refuses an order that does not name one, which is right —
    # the alternative is charging a guess. So pick a plain item rather than menu[0]: that
    # was only ever the right item by sort order, and one seed change away from failing
    # in a way that would look like a bug in portions rather than in this helper.
    menu = admin.get(f"{API}/menu").json()
    plain = next(m for m in menu if not (m.get("variants") or []))
    return admin.post(f"{API}/orders/table/{table['id']}/items", json={
        "items": [{"menu_item_id": plain["id"], "quantity": 2}], "source": "pos"}).json()


def test_settle_to_room_posts_an_outlet_debit(admin):
    s = _checked_in(admin, "2030-04-05", "2030-04-08")
    before = admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"]
    order = _open_order(admin)

    r = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "settled"
    assert r.json()["payment_method"] == "room"

    after = admin.get(f"{API}/folios/{s['folio_id']}").json()
    outlet = [e for e in after["entries"] if e["kind"] == "outlet"]
    assert len(outlet) == 1
    assert outlet[0]["direction"] == "debit"
    assert outlet[0]["ref_order_id"] == order["id"]
    assert after["balance"] == round(before + order["total"], 2)


def test_settle_to_room_still_counts_as_outlet_revenue(admin):
    """A room-settled order is an ordinary settled order — reports must not special-case it."""
    s = _checked_in(admin, "2030-05-05", "2030-05-08")
    before = admin.get(f"{API}/reports/summary").json()["revenue_today"]
    order = _open_order(admin)
    admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    after = admin.get(f"{API}/reports/summary").json()["revenue_today"]
    assert round(after - before, 2) == round(order["total"], 2)


def test_settle_to_room_requires_a_folio_id(admin):
    order = _open_order(admin)
    r = admin.post(f"{API}/orders/{order['id']}/settle", json={"payment_method": "room"})
    assert r.status_code == 400, r.text


def test_settle_to_a_closed_folio_is_refused(admin):
    s = _checked_in(admin, "2030-06-05", "2030-06-08")
    admin.post(f"{API}/bookings/{s['booking']['id']}/check-out",
               json={"force": True, "reason": "test"})
    order = _open_order(admin)
    r = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    assert r.status_code == 409, r.text


def test_cash_settle_is_unchanged(admin):
    """Regression guard: the existing settle path must behave exactly as before."""
    order = _open_order(admin)
    r = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "cash", "customer_name": "Walk In", "customer_phone": "9000000001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "settled"
    assert body["payment_method"] == "cash"
    assert body["customer_name"] == "Walk In"


def test_voiding_a_room_settled_order_reverses_both_the_folio_and_the_revenue(admin):
    """Task 5 built the void path that reverses an outlet entry and voids the underlying
    order, but nothing could create an outlet entry until Task 6. Exercise it end to end."""
    s = _checked_in(admin, "2030-07-05", "2030-07-08")
    before_balance = admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"]
    before_revenue = admin.get(f"{API}/reports/summary").json()["revenue_today"]

    order = _open_order(admin)
    settled = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]}).json()

    folio_after_bill = admin.get(f"{API}/folios/{s['folio_id']}").json()
    outlet_entry = next(e for e in folio_after_bill["entries"] if e["kind"] == "outlet")

    r = admin.post(
        f"{API}/folios/{s['folio_id']}/entries/{outlet_entry['id']}/void",
        json={"reason": "Bill voided by manager"})
    assert r.status_code == 200, r.text
    assert r.json()["voided_order_id"] == order["id"]
    assert r.json()["entry"]["ref_entry_id"] == outlet_entry["id"]

    folio_after_void = admin.get(f"{API}/folios/{s['folio_id']}").json()
    assert folio_after_void["balance"] == before_balance

    voided_order = admin.get(f"{API}/orders/{order['id']}").json()
    assert voided_order["status"] == "voided"

    after_revenue = admin.get(f"{API}/reports/summary").json()["revenue_today"]
    assert round(after_revenue - before_revenue, 2) == 0.0


# ------------------------- Pre-merge review fixes -------------------------
@pytest.fixture(scope="module")
def waiter():
    """Demo waiter login (DEMO_LOGINS defaults to true locally)."""
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": "waiter@barflow.io", "password": "waiter123"})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


@pytest.fixture(scope="module")
def front_desk():
    """Demo front-desk login (DEMO_LOGINS defaults to true locally)."""
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": "frontdesk@barflow.io", "password": "desk123"})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


def test_waiter_can_look_up_in_house_guests_but_not_the_front_desk_board(admin, waiter):
    """FIX 1: a waiter runs the POS and must be able to find the room to charge, but
    nothing more of front desk's screens."""
    s = _checked_in(admin, "2032-01-05", "2032-01-08")
    lookup = waiter.get(f"{API}/in-house")
    assert lookup.status_code == 200, lookup.text
    assert any(x["booking"]["id"] == s["booking"]["id"] for x in lookup.json())

    board = waiter.get(f"{API}/front-desk")
    assert board.status_code == 403, board.text


def test_in_house_guest_is_projected_to_only_name_and_phone(admin, waiter):
    """A waiter can look up an in-house guest to charge a bar bill to their room, but
    the POS only needs the room, the guest's name/phone and the folio id — the full
    guest document (id proof, address, email, notes) must never reach the POS."""
    s = _checked_in(admin, "2032-06-05", "2032-06-08")
    lookup = waiter.get(f"{API}/in-house")
    assert lookup.status_code == 200, lookup.text

    row = next(x for x in lookup.json() if x["booking"]["id"] == s["booking"]["id"])
    guest = row["guest"]
    assert set(guest.keys()) == {"id", "name", "phone"}
    assert guest["id"] == s["guest"]["id"]
    assert guest["name"] == s["guest"]["name"]
    assert guest["phone"] == s["guest"]["phone"]
    assert "id_proof_number" not in guest
    assert "address" not in guest


def test_settling_an_already_settled_order_is_refused(admin):
    """FIX 3: a double-tap or retry must not debit the folio/till twice."""
    order = _open_order(admin)
    first = admin.post(f"{API}/orders/{order['id']}/settle", json={"payment_method": "cash"})
    assert first.status_code == 200, first.text
    second = admin.post(f"{API}/orders/{order['id']}/settle", json={"payment_method": "cash"})
    assert second.status_code == 409, second.text


def test_settling_a_voided_order_is_refused(admin):
    """FIX 3: a voided order was deliberately reversed and must not be re-settled."""
    s = _checked_in(admin, "2032-02-05", "2032-02-08")
    order = _open_order(admin)
    admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()
    outlet_entry = next(e for e in folio["entries"] if e["kind"] == "outlet")
    admin.post(f"{API}/folios/{s['folio_id']}/entries/{outlet_entry['id']}/void",
               json={"reason": "Bill voided by manager"})

    again = admin.post(f"{API}/orders/{order['id']}/settle", json={"payment_method": "cash"})
    assert again.status_code == 409, again.text


def test_front_desk_discount_refused_but_ordinary_payment_allowed(admin, front_desk):
    """FIX 4: a discount is a credit like a refund, so it must be manager-only, or
    front desk could write a balance down to zero and check out normally."""
    s = _checked_in(admin, "2032-03-05", "2032-03-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 1000.0, "description": "Minibar"})

    discount = front_desk.post(
        f"{API}/folios/{s['folio_id']}/payments",
        json={"amount": 1000.0, "method": "cash", "kind": "discount"})
    assert discount.status_code == 403, discount.text

    payment = front_desk.post(
        f"{API}/folios/{s['folio_id']}/payments",
        json={"amount": 400.0, "method": "cash", "kind": "payment"})
    assert payment.status_code == 200, payment.text
    assert admin.get(f"{API}/folios/{s['folio_id']}").json()["balance"] == 600.0


def test_front_desk_cannot_void_an_entry(admin, front_desk):
    """FIX 5: voiding is a manager action; front desk only has payments."""
    s = _checked_in(admin, "2032-04-05", "2032-04-08")
    charge = admin.post(f"{API}/folios/{s['folio_id']}/charges",
                        json={"amount": 500.0, "description": "Laundry"}).json()
    r = front_desk.post(
        f"{API}/folios/{s['folio_id']}/entries/{charge['entry']['id']}/void",
        json={"reason": "Guest disputed"})
    assert r.status_code == 403, r.text


def test_front_desk_cannot_force_check_out(admin, front_desk):
    """FIX 5: forcing a check-out with a balance is a manager action."""
    s = _checked_in(admin, "2032-05-05", "2032-05-08")
    admin.post(f"{API}/folios/{s['folio_id']}/charges",
               json={"amount": 300.0, "description": "Minibar"})
    r = front_desk.post(
        f"{API}/bookings/{s['booking']['id']}/check-out",
        json={"force": True, "reason": "Company will settle"})
    assert r.status_code == 403, r.text


def test_seeded_admin_has_all_domains_and_is_active(admin):
    me = admin.get(f"{API}/auth/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["active"] is True
    assert set(body["domains"]) == {"hotel", "restaurant", "bar"}


def _staff_session(admin, email, password, role, domains, permissions=None):
    """Create a staff user directly via the seeded admin, and return a logged-in session.

    `permissions` left as None sends no ticks at all, which the API reads as "the screens
    this role has always reached" — the same set the startup backfill gives an existing
    account. Pass a list to narrow somebody deliberately.
    """
    import requests as _rq
    body = {"name": email.split("@")[0], "email": email, "password": password,
            "role": role, "domains": domains}
    if permissions is not None:
        body["permissions"] = permissions
    admin.post(f"{API}/staff", json=body)
    s = _rq.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


def test_restaurant_manager_is_refused_hotel_endpoints(admin):
    email = f"rest-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "rest12345", "manager", ["restaurant"])
    assert s.get(f"{API}/bookings").status_code == 403
    assert s.get(f"{API}/tables").status_code == 200


# ------------------------ staff administration ------------------------
def test_admin_creates_staff_with_domains(admin):
    email = f"new-{uuid.uuid4().hex[:6]}@barflow.io"
    r = admin.post(f"{API}/staff", json={
        "name": "New Person", "email": email, "password": "newpass123",
        "role": "waiter", "domains": ["bar"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domains"] == ["bar"]
    assert body["active"] is True
    assert "password_hash" not in body and "password" not in body


def test_duplicate_email_is_refused(admin):
    email = f"dup-{uuid.uuid4().hex[:6]}@barflow.io"
    body = {"name": "A", "email": email, "password": "pass12345",
            "role": "waiter", "domains": ["bar"]}
    assert admin.post(f"{API}/staff", json=body).status_code == 200
    assert admin.post(f"{API}/staff", json=body).status_code == 409


def test_non_admin_cannot_reach_staff(admin):
    email = f"mgr-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "mgr12345", "manager", ["restaurant"])
    assert s.get(f"{API}/staff").status_code == 403


def test_empty_domains_on_a_non_admin_is_refused(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Nobody", "email": f"none-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "pass12345", "role": "waiter", "domains": []})
    assert r.status_code == 400, r.text


def test_unknown_domain_is_refused(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Spa", "email": f"spa-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "pass12345", "role": "waiter", "domains": ["spa"]})
    assert r.status_code == 422, r.text


def test_admin_cannot_deactivate_themselves(admin):
    me = admin.get(f"{API}/auth/me").json()
    r = admin.post(f"{API}/staff/{me['id']}/active", json={"active": False})
    assert r.status_code == 409, r.text


def test_a_user_cannot_change_their_own_role(admin):
    """Refused by the self-guard, whatever else is true of the database. This is also
    what keeps an active admin in place: a demotion can only target someone else."""
    me = admin.get(f"{API}/auth/me").json()
    r = admin.put(f"{API}/staff/{me['id']}", json={
        "name": me["name"], "role": "manager", "domains": ["hotel"]})
    assert r.status_code == 409, r.text
    # And nothing was written.
    assert admin.get(f"{API}/auth/me").json()["role"] == "admin"


def test_a_user_cannot_change_their_own_domains(admin):
    """Same guard, with the role left alone — narrowing your own domains is refused too,
    or an admin could quietly cut themselves off from part of the property."""
    me = admin.get(f"{API}/auth/me").json()
    r = admin.put(f"{API}/staff/{me['id']}", json={
        "name": me["name"], "role": me["role"], "domains": ["hotel"]})
    assert r.status_code == 409, r.text
    assert set(admin.get(f"{API}/auth/me").json()["domains"]) == set(me["domains"])


def test_an_admin_can_deactivate_another_admin_but_never_themselves(admin):
    """With two admins present: neither can remove themselves, and A can still remove B.
    The old last-active-admin count blocked nothing here — the self-guards do the work."""
    email = f"admin2-{uuid.uuid4().hex[:6]}@barflow.io"
    b = _staff_session(admin, email, "admin2pass", "admin", [])
    a_id = admin.get(f"{API}/auth/me").json()["id"]
    b_id = b.get(f"{API}/auth/me").json()["id"]

    assert admin.post(f"{API}/staff/{a_id}/active",
                      json={"active": False}).status_code == 409
    assert b.post(f"{API}/staff/{b_id}/active",
                  json={"active": False}).status_code == 409

    r = admin.post(f"{API}/staff/{b_id}/active", json={"active": False})
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False
    assert b.get(f"{API}/auth/me").status_code == 403

    # A is untouched and still admin.
    assert admin.get(f"{API}/auth/me").json()["active"] is True


def test_the_old_auth_staff_roster_is_not_reachable(admin):
    """GET /auth/staff used to return every user behind require_roles("admin","manager"),
    which let a restaurant-only manager enumerate all staff around the admin-only gate on
    GET /api/staff. It is gone; this test stops it coming back quietly."""
    email = f"roster-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "roster12345", "manager", ["restaurant"])
    r = s.get(f"{API}/auth/staff")
    assert r.status_code in (403, 404), r.text
    assert "password_hash" not in r.text
    assert email not in r.text


def test_admin_edits_role_and_domains_of_another_staff_member(admin):
    email = f"edit-{uuid.uuid4().hex[:6]}@barflow.io"
    created = admin.post(f"{API}/staff", json={
        "name": "Edit Me", "email": email, "password": "edit12345",
        "role": "waiter", "domains": ["bar"]}).json()

    r = admin.put(f"{API}/staff/{created['id']}", json={
        "name": "Edited", "role": "front_desk", "domains": ["hotel", "restaurant"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Edited"
    assert body["role"] == "front_desk"
    assert body["domains"] == ["hotel", "restaurant"]
    assert "password_hash" not in body


def test_a_deactivated_staff_member_can_be_reactivated(admin):
    email = f"back-{uuid.uuid4().hex[:6]}@barflow.io"
    created = admin.post(f"{API}/staff", json={
        "name": "Boomerang", "email": email, "password": "back12345",
        "role": "waiter", "domains": ["bar"]}).json()

    off = admin.post(f"{API}/staff/{created['id']}/active", json={"active": False})
    assert off.status_code == 200, off.text
    assert off.json()["active"] is False

    on = admin.post(f"{API}/staff/{created['id']}/active", json={"active": True})
    assert on.status_code == 200, on.text
    assert on.json()["active"] is True

    import requests as _rq
    assert _rq.post(f"{API}/auth/login",
                    json={"email": email, "password": "back12345"}).status_code == 200


def test_staff_list_never_leaks_password_hashes(admin):
    r = admin.get(f"{API}/staff")
    assert r.status_code == 200, r.text
    assert r.json(), "the seeded admin should at least be listed"
    assert "password_hash" not in r.text


def test_deactivated_user_cannot_log_in(admin):
    import requests as _rq
    email = f"leaver-{uuid.uuid4().hex[:6]}@barflow.io"
    created = admin.post(f"{API}/staff", json={
        "name": "Leaver", "email": email, "password": "leave12345",
        "role": "waiter", "domains": ["bar"]}).json()

    ok = _rq.post(f"{API}/auth/login", json={"email": email, "password": "leave12345"})
    assert ok.status_code == 200

    assert admin.post(f"{API}/staff/{created['id']}/active",
                      json={"active": False}).status_code == 200

    after = _rq.post(f"{API}/auth/login", json={"email": email, "password": "leave12345"})
    assert after.status_code == 401, after.text

    # Byte-identical to a wrong password on purpose. A distinct "account disabled"
    # message would confirm to a former employee that their password guess was right.
    wrong = _rq.post(f"{API}/auth/login",
                     json={"email": email, "password": "not-the-password"})
    assert wrong.status_code == 401
    assert after.json()["detail"] == wrong.json()["detail"]


def test_deactivated_users_existing_token_stops_working(admin):
    email = f"tok-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "tok12345", "waiter", ["bar"])
    assert s.get(f"{API}/auth/me").status_code == 200

    uid = next(u["id"] for u in admin.get(f"{API}/staff").json() if u["email"] == email)
    admin.post(f"{API}/staff/{uid}/active", json={"active": False})

    # Same token, now refused — the active check runs per request, not only at login.
    assert s.get(f"{API}/auth/me").status_code == 403


def test_admin_resets_a_password(admin):
    import requests as _rq
    email = f"reset-{uuid.uuid4().hex[:6]}@barflow.io"
    created = admin.post(f"{API}/staff", json={
        "name": "Reset Me", "email": email, "password": "oldpass123",
        "role": "waiter", "domains": ["bar"]}).json()

    assert admin.post(f"{API}/staff/{created['id']}/password",
                      json={"password": "newpass456"}).status_code == 200

    assert _rq.post(f"{API}/auth/login",
                    json={"email": email, "password": "oldpass123"}).status_code == 401
    assert _rq.post(f"{API}/auth/login",
                    json={"email": email, "password": "newpass456"}).status_code == 200


# ------------------------ work domains on every endpoint ------------------------
def test_hotel_manager_is_refused_restaurant_writes(admin):
    """A manager IS on POST /menu's role list, so only the domain can refuse them.
    A front_desk user would 403 here on the pre-existing role gate alone, and the test
    would pass with the domain check deleted from can_access entirely."""
    email = f"hm-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "hm12345678", "manager", ["hotel"])
    assert s.get(f"{API}/folios").status_code == 200
    assert s.post(f"{API}/menu", json={
        "name": "Nope", "category": "Cocktails", "price": 100,
        "station": "bar", "description": ""}).status_code == 403


def test_restaurant_manager_is_refused_the_hotel_domain(admin):
    """A domain that is too wide has to be able to fail a test, not just one too narrow.
    Every assertion below uses a role the endpoint already admits — manager is on the
    folio role list, and /rooms and /tables declare no roles at all — so the only thing
    that can produce a 403 is the domain. Widen folios.py or rooms.py to shared and this
    test goes red."""
    email = f"rm-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "rm12345678", "manager", ["restaurant"])
    assert s.get(f"{API}/folios").status_code == 403
    assert s.get(f"{API}/rooms").status_code == 403
    # ...and is not simply refused everything: their own domain still opens.
    assert s.get(f"{API}/tables").status_code == 200


def test_hotel_manager_is_refused_the_outlet_domains(admin):
    """The mirror image: /reports/summary admits managers by role and /tables declares
    no roles, so a hotel manager's 403 can only come from the outlet domain."""
    email = f"ho-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "ho12345678", "manager", ["hotel"])
    assert s.get(f"{API}/reports/summary").status_code == 403
    assert s.get(f"{API}/tables").status_code == 403
    assert s.get(f"{API}/rooms").status_code == 200


def test_bar_waiter_reaches_in_house_and_sees_only_pos_fields(admin):
    """Charging a bar bill to a room is why waiter is on the /in-house role list, and a
    bar waiter never holds "hotel" — so the endpoint is shared. It stays safe only
    because the whole row is projected: _pos_guest, _pos_booking and _pos_folio each cut
    a record down to what the POS reads, so the booking notes and quote and the folio
    balance never reach an outlet."""
    stay = _checked_in(admin, "2032-07-05", "2032-07-08")
    email = f"bw-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "bw12345678", "waiter", ["bar"])
    r = s.get(f"{API}/in-house")
    assert r.status_code == 200, r.text
    rows = r.json()

    # Assert the exact key sets, not merely that today's sensitive keys are absent: a
    # negative-only assertion goes quiet the day a new field joins the record.
    for row in rows:
        assert set(row) == {"booking", "guest", "room", "folio"}, row
        assert set(row["guest"]) == {"id", "name", "phone"}, row["guest"]
        assert set(row["booking"]) == {"id"}, row["booking"]
        assert set(row["folio"]) == {"id"}, row["folio"]
        assert "notes" not in row["booking"], row["booking"]
        assert "quote" not in row["booking"], row["booking"]
        assert "balance" not in row["folio"], row["folio"]

    # Projected, but not starved: the POS still has the room, the guest and the folio
    # id it needs to label the row and settle against it.
    row = next(x for x in rows if x["booking"]["id"] == stay["booking"]["id"])
    assert row["folio"]["id"] == stay["folio_id"]
    assert row["guest"]["name"] == stay["guest"]["name"]
    assert row["room"]["number"] == stay["room"]["number"]

    # The board itself stays hotel-only: the widening is one lookup, not the front desk.
    assert s.get(f"{API}/front-desk").status_code == 403


def test_bar_only_waiter_reaches_the_order_screens(admin):
    # The order endpoints are declared ("restaurant", "bar"); declaring them restaurant
    # alone would lock a bar-only waiter out of the POS.
    email = f"bar-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "bar12345678", "waiter", ["bar"])
    assert s.get(f"{API}/tables").status_code == 200
    assert s.get(f"{API}/orders/kot").status_code == 200


def test_shared_endpoints_reachable_from_any_domain(admin):
    email = f"sh-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "sh12345678", "manager", ["bar"])
    assert s.get(f"{API}/guests").status_code == 200
    assert s.get(f"{API}/inventory").status_code == 200


def test_admin_still_reaches_everything(admin):
    for path in ("/bookings", "/tables", "/guests", "/inventory", "/folios", "/staff"):
        assert admin.get(f"{API}{path}").status_code == 200, path


# ------------------------- Task 10: combined revenue analytics -------------------------
# A fixed window shared by the *structural* tests below — the ones asserting status
# codes, domain lists and the shape of by_day. Nothing here asserts an absolute figure
# inside it, so leftovers from a previous run against the same db.json are fine, and it
# is deliberately empty of money. Any test asserting a number must create that number
# inside its own window instead: see the tests further down that do.
ANALYTICS_WINDOW = {"start": "2026-03-01", "end": "2026-03-31"}


def _property_today() -> date:
    """Today's date at the property.

    The server dates money by the property's local calendar day, not by UTC and not by
    whatever timezone this machine happens to sit in (services/clock.py). A test that
    mixes its own `date.today()` into an assertion about posted room nights is flaky by
    time of day — on a UTC machine the two disagree for the first 5½ hours of every
    local day.
    """
    from services.clock import today as _today
    return date.fromisoformat(_today())


def test_analytics_rejects_a_domain_the_user_does_not_hold(admin):
    # A manager who asks for hotel figures must not silently receive restaurant ones.
    email = f"ranl-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "rest12345", "manager", ["restaurant"])
    r = s.get(f"{API}/analytics/revenue", params={"domains": "hotel", **ANALYTICS_WINDOW})
    assert r.status_code == 403, r.text


def test_analytics_defaults_to_the_users_own_domains(admin):
    r = admin.get(f"{API}/analytics/revenue", params=ANALYTICS_WINDOW)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["domains"]) == ["bar", "hotel", "restaurant"]


def test_analytics_default_for_a_narrower_user_is_only_their_own_domains(admin):
    """Omitting `domains` must mean "everything I hold", not "everything there is"."""
    email = f"danl-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "rest12345", "manager", ["restaurant"])
    r = s.get(f"{API}/analytics/revenue", params=ANALYTICS_WINDOW)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domains"] == ["restaurant"]
    assert body["hotel"] is None
    assert body["outlets"] is not None
    # The figure spans both outlets whichever one you asked for — one till, one set of
    # orders — and says so.
    assert body["outlets"]["covers"] == ["restaurant", "bar"]


def test_analytics_hotel_only_omits_outlets(admin):
    r = admin.get(f"{API}/analytics/revenue", params={"domains": "hotel", **ANALYTICS_WINDOW})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outlets"] is None
    assert body["hotel"] is not None
    # The old flag told the screen what the user had already ticked rather than what the
    # figure contained. It was replaced by `outlets.covers`, not renamed alongside it.
    assert "outlets_combined" not in body


def test_analytics_restaurant_only_omits_hotel(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "restaurant", **ANALYTICS_WINDOW})
    assert r.status_code == 200, r.text
    assert r.json()["hotel"] is None


def test_selecting_both_outlet_domains_does_not_double_count(admin):
    """Both money assertions used to compare 0.0 to 0.0: the shared window is in March
    2026 and every settled order in the database settled today. So this test creates the
    revenue it asserts on, in a window that contains it."""
    today = _property_today().isoformat()
    window = {"start": today, "end": today}

    order = _open_order(admin)
    settled = admin.post(f"{API}/orders/{order['id']}/settle",
                         json={"payment_method": "cash"})
    assert settled.status_code == 200, settled.text
    bill = round(settled.json()["total"], 2)
    assert bill > 0

    one = admin.get(f"{API}/analytics/revenue",
                    params={"domains": "restaurant", **window}).json()
    both = admin.get(f"{API}/analytics/revenue",
                     params={"domains": "restaurant,bar", **window}).json()

    # There is real money in the window now, so "equal" means something.
    assert one["outlets"]["total"] >= bill
    assert one["outlets"]["orders"] >= 1
    assert one["total"] >= bill

    assert both["outlets"]["total"] == one["outlets"]["total"]
    assert both["outlets"]["orders"] == one["outlets"]["orders"]
    assert both["total"] == one["total"]
    # Ticking one box or two changes nothing about what the figure contains, and the
    # response reports the contents rather than the ticks.
    assert one["outlets"]["covers"] == ["restaurant", "bar"]
    assert both["outlets"]["covers"] == ["restaurant", "bar"]


def test_a_bar_only_manager_is_told_the_figure_covers_the_restaurant_too(admin):
    """This property's restaurant and bar share one till and one set of orders, and an
    order carries no domain — so a manager holding only `bar` receives every restaurant
    rupee. The response has to say so. The old `outlets_combined` flag was False for
    this user, because they had only ticked one box: it reported what they already knew
    instead of what the number contained."""
    email = f"barmgr-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "barmgr12345", "manager", ["bar"])
    today = _property_today().isoformat()
    window = {"start": today, "end": today}

    r = s.get(f"{API}/analytics/revenue", params=window)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domains"] == ["bar"]
    assert body["hotel"] is None
    assert body["outlets"]["covers"] == ["restaurant", "bar"]
    assert "outlets_combined" not in body

    # And it really is the whole till: the same figure an admin sees for both outlets.
    everything = admin.get(f"{API}/analytics/revenue",
                           params={"domains": "restaurant,bar", **window}).json()
    assert body["outlets"]["total"] == everything["outlets"]["total"]
    assert body["outlets"]["orders"] == everything["outlets"]["orders"]


def test_analytics_by_day_spans_the_whole_range(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "hotel", "start": "2026-03-01", "end": "2026-03-05"})
    days = [d["date"] for d in r.json()["by_day"]]
    assert days == ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]


def test_analytics_by_day_row_totals_are_hotel_plus_outlets(admin):
    """Against an empty window every row compared 0 == round(0 + 0, 2), which would pass
    just as happily if the endpoint returned hotel MINUS outlets, or shifted the by_day
    index by one — the exact bugs this test is named for. So it builds both halves of
    the sum first: a past stay for the hotel side, a settled bill for the outlet side."""
    ci, co = "2024-08-12", "2024-08-15"
    s = _checked_in(admin, ci, co)
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()   # posts the due nights
    nights = {e["charge_date"]: e["amount"]
              for e in folio["entries"] if e["kind"] == "room_night"}
    assert sorted(nights) == ["2024-08-12", "2024-08-13", "2024-08-14"], nights
    night_total = round(sum(nights.values()), 2)
    assert night_total > 0

    order = _open_order(admin)
    settled = admin.post(f"{API}/orders/{order['id']}/settle",
                         json={"payment_method": "cash"})
    assert settled.status_code == 200, settled.text
    bill = round(settled.json()["total"], 2)
    assert bill > 0

    today = _property_today().isoformat()
    body = admin.get(f"{API}/analytics/revenue", params={
        "domains": "hotel,restaurant", "start": ci, "end": today}).json()

    # Both sides carry real money, so the arithmetic below has something to be wrong about.
    assert body["hotel"]["total"] >= night_total
    assert body["outlets"]["total"] >= bill

    # And it is on the right rows: a by_day shifted by one day fails here.
    rows = {d["date"]: d for d in body["by_day"]}
    for night, amount in nights.items():
        assert rows[night]["hotel"] >= amount, (night, rows[night])
    assert rows[today]["outlets"] >= bill, rows[today]

    for row in body["by_day"]:
        assert row["total"] == round(row["hotel"] + row["outlets"], 2), row
    assert body["total"] == round(body["hotel"]["total"] + body["outlets"]["total"], 2)
    assert body["total"] >= round(night_total + bill, 2)


def test_a_settled_order_lands_in_the_by_day_row_for_the_day_it_settled(admin):
    """The outlet column of the chart the screen actually draws. Nothing asserted a
    non-zero figure in by_day[*]["outlets"] before, so a bucketing bug in the outlet
    path — the whole late session dated to yesterday, say — would have shipped."""
    today = _property_today()
    yesterday, tomorrow = today - timedelta(days=1), today + timedelta(days=1)
    window = {"domains": "restaurant", "start": yesterday.isoformat(),
              "end": tomorrow.isoformat()}

    before = admin.get(f"{API}/analytics/revenue", params=window).json()
    was = {d["date"]: d["outlets"] for d in before["by_day"]}

    order = _open_order(admin)
    settled = admin.post(f"{API}/orders/{order['id']}/settle",
                         json={"payment_method": "cash"})
    assert settled.status_code == 200, settled.text
    bill = round(settled.json()["total"], 2)
    assert bill > 0

    after = admin.get(f"{API}/analytics/revenue", params=window).json()
    now = {d["date"]: d["outlets"] for d in after["by_day"]}

    assert now[today.isoformat()] > 0
    assert round(now[today.isoformat()] - was[today.isoformat()], 2) == bill
    # ...and only that row moved: a day-early or day-late bucket shows up right here.
    for other in (yesterday, tomorrow):
        assert round(now[other.isoformat()] - was[other.isoformat()], 2) == 0.0, other


def test_analytics_rejects_start_after_end(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "hotel", "start": "2026-03-31", "end": "2026-03-01"})
    assert r.status_code == 400, r.text


def test_analytics_rejects_an_unparseable_date(admin):
    r = admin.get(f"{API}/analytics/revenue",
                  params={"domains": "hotel", "start": "March", "end": "2026-03-01"})
    assert r.status_code == 400, r.text


def test_analytics_rejects_an_unknown_domain(admin):
    r = admin.get(f"{API}/analytics/revenue", params={"domains": "spa", **ANALYTICS_WINDOW})
    assert r.status_code == 422, r.text


def test_a_waiter_cannot_reach_analytics(waiter):
    r = waiter.get(f"{API}/analytics/revenue", params=ANALYTICS_WINDOW)
    assert r.status_code == 403, r.text


def test_a_room_night_appears_in_hotel_revenue_on_the_night_it_covers(admin):
    """A stay's nights land on the nights they cover, not on the day they were posted."""
    # A past window of its own, so every night is due the moment the folio is read.
    ci, co = "2024-06-14", "2024-06-16"
    window = {"domains": "hotel", "start": ci, "end": "2024-06-15"}
    before = admin.get(f"{API}/analytics/revenue", params=window).json()

    s = _checked_in(admin, ci, co)
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()   # posts the due nights
    assert folio["balance"] > 0, folio
    nights = {e["charge_date"]: e["amount"]
              for e in folio["entries"] if e["kind"] == "room_night"}
    # Two nights: 06-14 and 06-15. 06-16 is the departure and is not slept in.
    assert sorted(nights) == ["2024-06-14", "2024-06-15"], nights

    after = admin.get(f"{API}/analytics/revenue", params=window).json()
    assert after["hotel"]["room_nights"] > before["hotel"]["room_nights"]
    assert round(after["hotel"]["room_nights"] - before["hotel"]["room_nights"], 2) == \
        round(sum(nights.values()), 2)

    was = {d["date"]: d["hotel"] for d in before["by_day"]}
    now = {d["date"]: d["hotel"] for d in after["by_day"]}
    for night, amount in nights.items():
        assert round(now[night] - was[night], 2) == round(amount, 2), night


def test_a_bar_bill_charged_to_a_room_is_outlet_revenue_and_not_hotel_revenue(admin):
    """The rule the whole endpoint exists for: hotel and outlet figures add cleanly
    because a room-charged bar bill is revenue at the bar and a receivable on the folio.
    It must be counted exactly once, on the outlet side."""
    # A stay wholly in the past, so all three nights are due the moment the folio is
    # read, whatever time of day the suite runs. Deriving check-in from date.today()
    # made this flaky: the machine's date and the date the server posts nights by are
    # not the same thing, and a run in the small hours posted only two nights and failed
    # on `len(nights) == 3`. A fixed window is what the room-night test above uses.
    ci, co = "2024-09-10", "2024-09-13"
    # The window ends today so it holds both the three due nights and today's bar bill.
    window = {"domains": "hotel,restaurant", "start": ci,
              "end": _property_today().isoformat()}

    before = admin.get(f"{API}/analytics/revenue", params=window).json()

    s = _checked_in(admin, ci, co)
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()   # posts the due nights
    nights = [e for e in folio["entries"] if e["kind"] == "room_night"]
    assert len(nights) == 3, folio
    night_total = round(sum(n["amount"] for n in nights), 2)

    mid = admin.get(f"{API}/analytics/revenue", params=window).json()
    # (a) hotel revenue rose by exactly the room nights; the outlet side did not move.
    assert round(mid["hotel"]["room_nights"] - before["hotel"]["room_nights"], 2) == night_total
    assert round(mid["outlets"]["total"] - before["outlets"]["total"], 2) == 0.0

    order = _open_order(admin)
    settled = admin.post(f"{API}/orders/{order['id']}/settle", json={
        "payment_method": "room", "folio_id": s["folio_id"]})
    assert settled.status_code == 200, settled.text
    bill = round(settled.json()["total"], 2)
    assert bill > 0

    after = admin.get(f"{API}/analytics/revenue", params=window).json()
    # (a) the bar bill did NOT raise hotel revenue, even though it sits on the folio.
    assert round(after["hotel"]["total"] - mid["hotel"]["total"], 2) == 0.0
    # (b) outlet revenue rose by exactly the bill, and by one order.
    assert round(after["outlets"]["total"] - mid["outlets"]["total"], 2) == bill
    assert after["outlets"]["orders"] - mid["outlets"]["orders"] == 1
    # (c) the combined total is the sum, with the bill counted once and only once.
    assert after["total"] == round(after["hotel"]["total"] + after["outlets"]["total"], 2)
    assert round(after["total"] - before["total"], 2) == round(night_total + bill, 2)

    both = admin.get(f"{API}/analytics/revenue",
                     params={**window, "domains": "hotel,restaurant,bar"}).json()
    assert both["total"] == after["total"]
    assert both["outlets"]["total"] == after["outlets"]["total"]
    assert both["outlets"]["covers"] == ["restaurant", "bar"]


def test_analytics_posts_due_nights_for_an_open_folio_nobody_has_opened(admin):
    """Hotel revenue must include the guests currently in the building.

    Room nights post lazily — `post_due_nights` writes them, and until this fix only
    GET /folios/{id} and check-out ever called it. So a stay whose nights were due but
    whose folio nobody had opened contributed ₹0 to this report, and then gained that
    money *retroactively*, on the nights it covered, the moment a receptionist happened
    to open the folio: the same range, queried twice a day apart, gave two different
    answers with nothing to explain the change.

    The folio is deliberately never fetched here. Every other analytics test in this file
    reads it first, with a `# posts the due nights` comment — which is precisely the
    dependency this test exists to remove.
    """
    ci, co = "2024-02-05", "2024-02-08"
    window = {"domains": "hotel", "start": ci, "end": "2024-02-07"}

    # Taken before the stay exists, and it settles any nights left open by an earlier run
    # of this suite — so the delta below is this stay's nights and nothing else.
    before = admin.get(f"{API}/analytics/revenue", params=window).json()

    s = _checked_in(admin, ci, co)
    # What the nights are worth, read off the booking's own quote snapshot rather than
    # off the folio: the whole point is that nothing opens the folio.
    quoted = {n["date"]: round(float(n["tariff"]) + float(n["gst_amount"]), 2)
              for n in s["booking"]["quote"]["nights"]}
    assert sorted(quoted) == ["2024-02-05", "2024-02-06", "2024-02-07"], quoted
    night_total = round(sum(quoted.values()), 2)
    assert night_total > 0

    after = admin.get(f"{API}/analytics/revenue", params=window).json()
    assert round(after["hotel"]["room_nights"] - before["hotel"]["room_nights"], 2) == \
        night_total, "the stay's nights were missing from hotel revenue"

    # And on the right days: each night lands on the night it covers, not on today.
    was = {d["date"]: d["hotel"] for d in before["by_day"]}
    now = {d["date"]: d["hotel"] for d in after["by_day"]}
    for night, amount in quoted.items():
        assert round(now[night] - was[night], 2) == amount, night

    # Idempotent under this new caller: a second report posts nothing, so the figure does
    # not creep upwards every time somebody looks at it.
    again = admin.get(f"{API}/analytics/revenue", params=window).json()
    assert again["hotel"]["room_nights"] == after["hotel"]["room_nights"]
    assert again["hotel"]["total"] == after["hotel"]["total"]
    assert again["by_day"] == after["by_day"]

    # Opening the folio afterwards still shows exactly those nights and adds nothing —
    # the ledger the report read is the same one the desk sees.
    folio = admin.get(f"{API}/folios/{s['folio_id']}").json()
    nights = {e["charge_date"]: e["amount"]
              for e in folio["entries"] if e["kind"] == "room_night"}
    assert nights == quoted, nights
    third = admin.get(f"{API}/analytics/revenue", params=window).json()
    assert third["hotel"]["room_nights"] == after["hotel"]["room_nights"]


def test_an_admin_created_with_no_domains_is_given_all_of_them(admin):
    """`{"role": "admin", "domains": []}` used to be stored as written. Harmless while
    the account stays an admin — admins are never domain-checked — but it is the state
    the startup backfill exists to repair, and a later demotion to manager turned it into
    an account that could reach nothing."""
    email = f"alldom-{uuid.uuid4().hex[:6]}@barflow.io"
    r = admin.post(f"{API}/staff", json={
        "name": "Domainless Admin", "email": email, "password": "admin12345",
        "role": "admin", "domains": []})
    assert r.status_code == 200, r.text
    assert set(r.json()["domains"]) == {"hotel", "restaurant", "bar"}, r.text

    # And it survives the round trip, so the roster does not have to special-case it.
    listed = next(u for u in admin.get(f"{API}/staff").json() if u["email"] == email)
    assert set(listed["domains"]) == {"hotel", "restaurant", "bar"}


def test_you_can_rename_yourself_but_not_change_your_own_role_or_domains(admin):
    """The self-guard exists to stop someone editing away their own access. A typo in
    your own name is not that, and a sole admin had no way to fix one."""
    email = f"rename-{uuid.uuid4().hex[:6]}@barflow.io"
    me = _staff_session(admin, email, "rename12345", "admin", ["hotel", "restaurant", "bar"])
    my = me.get(f"{API}/auth/me").json()

    renamed = me.put(f"{API}/staff/{my['id']}", json={
        "name": "Correctly Spelled", "role": my["role"], "domains": my["domains"]})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Correctly Spelled"
    assert me.get(f"{API}/auth/me").json()["name"] == "Correctly Spelled"

    # Role and domains are still refused, and the 409 says which field was the problem
    # rather than reciting both.
    role = me.put(f"{API}/staff/{my['id']}", json={
        "name": "Correctly Spelled", "role": "manager", "domains": my["domains"]})
    assert role.status_code == 409, role.text
    assert "role" in role.json()["detail"] and "domains" not in role.json()["detail"]

    doms = me.put(f"{API}/staff/{my['id']}", json={
        "name": "Correctly Spelled", "role": my["role"], "domains": ["hotel"]})
    assert doms.status_code == 409, doms.text
    assert "domains" in doms.json()["detail"] and "role" not in doms.json()["detail"]

    # Nothing was written by either refusal.
    still = me.get(f"{API}/auth/me").json()
    assert still["role"] == "admin"
    assert set(still["domains"]) == set(my["domains"])


# ------------------------ screen permissions ------------------------
# Domains say which part of the business somebody works in; the ticks say which screens
# within it. Both are enforced at the API, because hiding a sidebar entry protects
# against an accident, not against intent.

def _staff_id(admin, email):
    return next(u["id"] for u in admin.get(f"{API}/staff").json() if u["email"] == email)


def test_the_screen_catalogue_is_readable_by_any_signed_in_user(admin):
    """Served rather than duplicated in the frontend, so the staff screen and the
    sidebar cannot disagree about which screens exist."""
    email = f"cat-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "cat12345678", "waiter", ["bar"])
    r = s.get(f"{API}/permissions")
    assert r.status_code == 200, r.text
    catalogue = r.json()
    keys = {row["key"] for row in catalogue}
    assert keys == {
        "hotel.front_desk", "hotel.bookings", "hotel.calendar", "hotel.rooms",
        "hotel.rates", "hotel.guests", "hotel.housekeeping",
        "outlet.tables", "outlet.pos", "outlet.kot",
        "outlet.reservations", "outlet.menu", "outlet.inventory", "outlet.reports",
        "property.planner",
        "admin.staff", "admin.analytics", "admin.expenses"}
    for row in catalogue:
        assert row["label"] and row["section"] and row["domains"]
    # "Property" joins the three when the planner lands: it is neither the hotel's screen
    # nor the restaurant's nor the admin console's, and the sidebar has had a heading of
    # that name for the other two shared screens all along.
    assert {row["section"] for row in catalogue} == {"Hotel", "Restaurant", "Property",
                                                     "Admin"}


def test_a_manager_reaches_the_screens_ticked_and_no_others(admin):
    """The headline case: one manager, one domain, one screen. Bookings opens, rates
    does not — and the role is identical in both, so only the tick can explain it."""
    email = f"perm-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "perm12345678", "manager", ["hotel"],
                       permissions=["hotel.bookings"])
    assert s.get(f"{API}/bookings").status_code == 200
    assert s.get(f"{API}/rates").status_code == 403
    assert s.get(f"{API}/bookings/calendar",
                 params={"start": "2030-01-01", "end": "2030-01-07"}).status_code == 403


def test_granting_a_screen_takes_effect_without_a_re_login(admin):
    """Permissions are read from the user record on every request, not baked into the
    token — so an owner who ticks a box does not have to tell somebody to sign out."""
    email = f"grant-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "grant12345678", "manager", ["hotel"],
                       permissions=["hotel.bookings"])
    assert s.get(f"{API}/rates").status_code == 403

    uid = _staff_id(admin, email)
    r = admin.put(f"{API}/staff/{uid}", json={
        "name": "Granted", "role": "manager", "domains": ["hotel"],
        "permissions": ["hotel.bookings", "hotel.rates"]})
    assert r.status_code == 200, r.text
    assert set(r.json()["permissions"]) == {"hotel.bookings", "hotel.rates"}

    # Same session, same token.
    assert s.get(f"{API}/rates").status_code == 200


def test_removing_a_screen_takes_effect_on_the_next_request(admin):
    email = f"revoke-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "revoke12345678", "manager", ["hotel"],
                       permissions=["hotel.bookings", "hotel.rates"])
    assert s.get(f"{API}/rates").status_code == 200

    uid = _staff_id(admin, email)
    admin.put(f"{API}/staff/{uid}", json={
        "name": "Narrowed", "role": "manager", "domains": ["hotel"],
        "permissions": ["hotel.bookings"]})
    assert s.get(f"{API}/rates").status_code == 403
    assert s.get(f"{API}/bookings").status_code == 200


def test_a_tick_cannot_widen_someone_past_their_work_domains(admin):
    """Saved at all, this would be a lie the owner relies on: the domain check runs
    first, so the screen could never open."""
    email = f"wide-{uuid.uuid4().hex[:6]}@barflow.io"
    r = admin.post(f"{API}/staff", json={
        "name": "Too Wide", "email": email, "password": "wide12345678",
        "role": "manager", "domains": ["restaurant"],
        "permissions": ["outlet.pos", "hotel.rates"]})
    assert r.status_code == 400, r.text
    assert "hotel.rates" in r.json()["detail"]
    # And nothing was created.
    assert not any(u["email"] == email for u in admin.get(f"{API}/staff").json())


def test_an_unknown_screen_key_is_refused_by_name(admin):
    r = admin.post(f"{API}/staff", json={
        "name": "Typo", "email": f"typo-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "typo12345678", "role": "manager", "domains": ["hotel"],
        "permissions": ["hotel.bookings", "hotel.spa"]})
    assert r.status_code == 422, r.text
    assert "hotel.spa" in str(r.json()["detail"])


def test_a_non_admin_with_no_screens_at_all_is_refused(admin):
    """An account that reaches nothing is a mistake, not a state worth storing."""
    r = admin.post(f"{API}/staff", json={
        "name": "Nobody", "email": f"noscreen-{uuid.uuid4().hex[:6]}@barflow.io",
        "password": "none12345678", "role": "manager", "domains": ["hotel"],
        "permissions": []})
    assert r.status_code == 400, r.text


def test_an_admin_needs_no_ticks_and_is_stored_holding_every_screen(admin):
    """Ignored for an admin exactly as `domains` is — but stored anyway, so a later
    demotion to manager does not silently produce an account that reaches nothing."""
    email = f"adminperm-{uuid.uuid4().hex[:6]}@barflow.io"
    r = admin.post(f"{API}/staff", json={
        "name": "New Admin", "email": email, "password": "admin12345678",
        "role": "admin", "domains": [], "permissions": []})
    assert r.status_code == 200, r.text
    # Every key in the catalogue, read from the catalogue rather than counted against a
    # number written here. A literal has to be edited by every feature that adds a
    # screen, which is a merge conflict between two unrelated pieces of work and a test
    # that fails for a reason that is not the reason it exists; asking the API what it
    # serves also asserts more — that the two agree on the *keys*, not just how many.
    catalogue = {row["key"] for row in admin.get(f"{API}/permissions").json()}
    assert set(r.json()["permissions"]) == catalogue


def test_a_staff_member_created_without_ticks_gets_what_their_role_implied(admin):
    email = f"implied-{uuid.uuid4().hex[:6]}@barflow.io"
    r = admin.post(f"{API}/staff", json={
        "name": "Implied", "email": email, "password": "implied12345",
        "role": "front_desk", "domains": ["hotel"]})
    assert r.status_code == 200, r.text
    assert set(r.json()["permissions"]) == {
        "hotel.front_desk", "hotel.bookings", "hotel.calendar", "hotel.guests"}


def test_screens_outside_the_new_domains_are_dropped_when_domains_narrow(admin):
    """Narrowing somebody's domains must not refuse the edit for screens the edit itself
    made unreachable — it drops them, and the record stops claiming what it cannot do."""
    email = f"narrow-{uuid.uuid4().hex[:6]}@barflow.io"
    admin.post(f"{API}/staff", json={
        "name": "Narrow", "email": email, "password": "narrow12345",
        "role": "manager", "domains": ["hotel", "restaurant"]})
    uid = _staff_id(admin, email)

    r = admin.put(f"{API}/staff/{uid}", json={
        "name": "Narrow", "role": "manager", "domains": ["hotel"]})
    assert r.status_code == 200, r.text
    kept = set(r.json()["permissions"])
    assert "hotel.bookings" in kept
    assert not any(k.startswith("outlet.") and k != "outlet.inventory" for k in kept), kept


def test_the_migration_left_every_existing_account_reaching_what_it_did(admin):
    """The seeded demo logins predate the ticks. After the startup backfill each one
    still opens the screens their role opened before, and nothing else."""
    import requests as _rq

    def session(email, password):
        s = _rq.Session()
        r = s.post(f"{API}/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
        return s

    desk = session("frontdesk@barflow.io", "desk123")
    assert desk.get(f"{API}/front-desk").status_code == 200
    assert desk.get(f"{API}/bookings").status_code == 200
    assert desk.get(f"{API}/guests").status_code == 200

    waiter_s = session("waiter@barflow.io", "waiter123")
    assert waiter_s.get(f"{API}/tables").status_code == 200
    assert waiter_s.get(f"{API}/orders/kot").status_code == 200
    assert waiter_s.get(f"{API}/reservations").status_code == 200

    kitchen = session("kitchen@barflow.io", "kitchen123")
    assert kitchen.get(f"{API}/orders/kot").status_code == 200
    assert kitchen.get(f"{API}/inventory").status_code == 200

    manager = session("manager@barflow.io", "manager123")
    for path in ("/bookings", "/rates", "/rooms", "/tables", "/inventory",
                 "/reports/summary", "/guests"):
        assert manager.get(f"{API}{path}").status_code == 200, path


# ------------------------ configuration is the admin's ------------------------
def test_configuration_writes_are_refused_for_a_manager_who_holds_the_screen(admin):
    """The manager below holds every domain and every screen these endpoints sit behind,
    so a 403 here can only be the configuration rule — and it has to say so, or the
    message reads as a bug to somebody who can see the screen it came from."""
    email = f"cfg-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "cfg12345678", "manager",
                       ["hotel", "restaurant", "bar"])

    code = f"C{uuid.uuid4().hex[:6].upper()}"
    room_type = admin.post(f"{API}/room-types", json={
        "name": f"Config {code}", "code": code, "base_occupancy": 2,
        "max_occupancy": 3, "max_extra_beds": 1}).json()
    item = admin.get(f"{API}/inventory").json()[0]
    plan = admin.get(f"{API}/meal-plans").json()[0]

    writes = [
        ("post", "/room-types", {"name": "Nope", "code": f"N{uuid.uuid4().hex[:5].upper()}",
                                 "base_occupancy": 2, "max_occupancy": 2, "max_extra_beds": 0}),
        ("put", f"/room-types/{room_type['id']}", {
            "name": "Renamed", "code": room_type["code"], "base_occupancy": 2,
            "max_occupancy": 3, "max_extra_beds": 1}),
        ("delete", f"/room-types/{room_type['id']}", None),
        ("post", "/rooms", {"number": "X-99", "room_type_id": room_type["id"]}),
        ("post", "/rates", {"room_type_id": room_type["id"], "period_id": None,
                            "base_rate": 1.0, "extra_adult_rate": 0.0,
                            "extra_child_rate": 0.0}),
        ("post", "/rate-periods", {"name": "Peak", "start_date": "2033-01-01",
                                   "end_date": "2033-01-10", "priority": 1}),
        ("post", "/meal-plans", {"code": "XX", "name": "Nope",
                                 "price_per_adult_per_night": 0.0,
                                 "price_per_child_per_night": 0.0}),
        ("put", f"/meal-plans/{plan['id']}", {
            "code": plan["code"], "name": "Renamed",
            "price_per_adult_per_night": plan["price_per_adult_per_night"],
            "price_per_child_per_night": plan["price_per_child_per_night"]}),
        ("put", "/tax-slabs", [{"min_tariff": 0.0, "max_tariff": None,
                                "rate_percent": 5.0, "active": True}]),
        ("post", "/menu", {"name": "Nope", "category": "Cocktails", "price": 100,
                           "station": "bar", "description": ""}),
        ("post", "/inventory", {"name": "Nope", "unit": "bottle", "stock": 1,
                                "threshold": 1, "cost_per_unit": 1, "category": "spirits"}),
        ("put", f"/inventory/{item['id']}", {**{k: item[k] for k in
                 ("name", "unit", "stock", "threshold", "cost_per_unit", "category")},
                 "name": "Renamed"}),
        ("delete", f"/inventory/{item['id']}", None),
    ]
    for method, path, body in writes:
        call = getattr(s, method)
        r = call(f"{API}{path}") if body is None else call(f"{API}{path}", json=body)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code} {r.text}"
        assert "administrator" in r.json()["detail"], f"{method} {path}: {r.text}"

    # The reads behind those same screens still work — they are the manager's to see.
    assert s.get(f"{API}/rates").status_code == 200
    assert s.get(f"{API}/room-types").status_code == 200
    assert s.get(f"{API}/inventory").status_code == 200
    # ...and nothing was actually written by any of the refusals.
    assert admin.get(f"{API}/inventory").json(), "the inventory item survived"
    assert any(i["id"] == item["id"] for i in admin.get(f"{API}/inventory").json())


def test_operational_writes_still_work_for_a_permitted_non_admin(admin):
    """A receptionist who cannot take a booking is not a receptionist. Everything here
    is a write, and none of it is configuration."""
    email = f"ops-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "ops12345678", "front_desk", ["hotel"])

    # The room type, room and rate are the admin's to set up; the desk uses them.
    stay = _stay(admin, "2033-05-05", "2033-05-08")

    guest = s.post(f"{API}/guests", json={
        "name": "Walk In", "phone": f"97{uuid.uuid4().int % 100000000:08d}"})
    assert guest.status_code == 200, guest.text

    ep = next(p for p in admin.get(f"{API}/meal-plans").json() if p["code"] == "EP")
    booking = s.post(f"{API}/bookings", json={
        "guest_id": guest.json()["id"], "room_type_id": stay["room_type"]["id"],
        "meal_plan_id": ep["id"], "check_in": "2033-06-05", "check_out": "2033-06-07",
        "adults": 2, "children": 0})
    assert booking.status_code == 200, booking.text

    checked_in = s.post(f"{API}/bookings/{booking.json()['id']}/check-in", json={
        "room_id": stay["room"]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1111-2222-3333"})
    assert checked_in.status_code == 200, checked_in.text
    folio_id = checked_in.json()["folio"]["id"]

    charge = s.post(f"{API}/folios/{folio_id}/charges",
                    json={"amount": 250.0, "description": "Laundry"})
    assert charge.status_code == 200, charge.text
    balance = admin.get(f"{API}/folios/{folio_id}").json()["balance"]
    payment = s.post(f"{API}/folios/{folio_id}/payments",
                     json={"amount": balance, "method": "cash", "kind": "payment"})
    assert payment.status_code == 200, payment.text

    checked_out = s.post(f"{API}/bookings/{booking.json()['id']}/check-out", json={})
    assert checked_out.status_code == 200, checked_out.text


def test_an_outlet_waiter_can_seat_a_table_and_settle_a_bill_but_not_edit_the_menu(admin):
    """The other half of the same distinction, on the restaurant side: opening and
    settling an order is the job, and the menu it prices from is the owner's."""
    email = f"pos-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "pos12345678", "waiter", ["restaurant", "bar"])

    # Reading the menu is not gated at all — the QR code on the table is a guest's menu.
    assert s.get(f"{API}/menu").status_code == 200
    refused = s.post(f"{API}/menu", json={
        "name": "Nope", "category": "Cocktails", "price": 100,
        "station": "bar", "description": ""})
    assert refused.status_code == 403, refused.text
    assert "administrator" in refused.json()["detail"]

    seated = s.post(f"{API}/reservations", json={
        "guest_name": "Walk In", "phone": "9800000000", "party_size": 2,
        "date": "2033-07-01", "time": "20:00"})
    assert seated.status_code == 200, seated.text

    order = _open_order(admin)
    settled = s.post(f"{API}/orders/{order['id']}/settle", json={"payment_method": "cash"})
    assert settled.status_code == 200, settled.text


def test_a_waiter_without_the_kot_screen_keeps_the_pos(admin):
    """Two screens, one domain, one role: only the ticks separate them."""
    email = f"nokot-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "nokot12345678", "waiter", ["bar"],
                       permissions=["outlet.pos", "outlet.tables"])
    assert s.get(f"{API}/orders/kot").status_code == 403
    assert s.get(f"{API}/tables").status_code == 200

    order = _open_order(admin)
    assert s.get(f"{API}/orders/{order['id']}").status_code == 200
    assert s.post(f"{API}/orders/{order['id']}/settle",
                  json={"payment_method": "cash"}).status_code == 200


def test_a_manager_can_be_given_analytics_without_being_made_an_admin(admin):
    email = f"anl-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "anl12345678", "manager", ["restaurant"],
                       permissions=["outlet.pos", "admin.analytics"])
    r = s.get(f"{API}/analytics/revenue", params={"start": "2026-03-01", "end": "2026-03-05"})
    assert r.status_code == 200, r.text
    # Analytics is not staff administration: the console stays shut.
    assert s.get(f"{API}/staff").status_code == 403
    assert s.get(f"{API}/reports/summary").status_code == 403


# ---------------------------- the property record ----------------------------
# The tenant, over HTTP. The states themselves — pending, live, suspended — are exercised
# where they are decided, in tests/test_access.py and tests/test_tenancy.py; there is no
# endpoint that changes a status yet, because approving and suspending belong to the
# platform portal, which is a later task.

def test_the_property_is_readable_by_anyone_signed_in(admin):
    """A POS bill carries the hotel's name and GSTIN in its header, so this is `shared`
    rather than a hotel endpoint — a bar-only waiter has to be able to print it."""
    r = admin.get(f"{API}/property")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] and body["name"]
    assert body["status"] in ("pending", "live", "suspended")
    # The two times every folio's night count is worked out against.
    assert body["check_in_time"] and body["check_out_time"]

    email = f"prop-{uuid.uuid4().hex[:6]}@barflow.io"
    waiter = _staff_session(admin, email, "prop12345678", "waiter", ["bar"])
    seen = waiter.get(f"{API}/property")
    assert seen.status_code == 200, seen.text
    assert seen.json()["id"] == body["id"]


def test_the_admin_edits_the_property_and_a_manager_cannot(admin):
    before = admin.get(f"{API}/property").json()
    body = {k: v for k, v in before.items() if k in {
        "name", "legal_name", "address_line1", "address_line2", "city", "state",
        "pincode", "phone", "email", "gstin", "fssai_licence", "check_in_time",
        "check_out_time", "logo"}}

    email = f"pmgr-{uuid.uuid4().hex[:6]}@barflow.io"
    manager = _staff_session(admin, email, "pmgr12345678", "manager", ["hotel"])
    refused = manager.put(f"{API}/property", json={**body, "city": "Nowhere"})
    assert refused.status_code == 403, refused.text

    edited = admin.put(f"{API}/property", json={
        **body, "city": "Panaji", "gstin": "27AAPFU0939F1ZV",
        "fssai_licence": "12345678901234", "check_out_time": "11:30"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["city"] == "Panaji"
    assert edited.json()["gstin"] == "27AAPFU0939F1ZV"
    # The lifecycle stays the operator's: the body cannot carry a status, and the one
    # already stored is untouched by an edit to the address.
    assert edited.json()["status"] == before["status"]
    assert edited.json()["id"] == before["id"]

    restored = admin.put(f"{API}/property", json=body)
    assert restored.status_code == 200, restored.text


def test_a_malformed_gstin_is_refused_with_a_400_naming_the_field(admin):
    body = {k: v for k, v in admin.get(f"{API}/property").json().items() if k in {
        "name", "city", "check_in_time", "check_out_time"}}

    bad_gstin = admin.put(f"{API}/property", json={**body, "gstin": "27AAPFU0939F1Z"})
    assert bad_gstin.status_code == 400, bad_gstin.text
    assert "gstin" in bad_gstin.json()["detail"].lower()

    bad_fssai = admin.put(f"{API}/property", json={**body, "fssai_licence": "1234"})
    assert bad_fssai.status_code == 400, bad_fssai.text
    assert "fssai" in bad_fssai.json()["detail"].lower()

    # Neither attempt stored anything.
    current = admin.get(f"{API}/property").json()
    assert current["gstin"] != "27AAPFU0939F1Z"
    assert current["fssai_licence"] != "1234"


def test_a_new_staff_member_belongs_to_the_hiring_admins_hotel(admin):
    """A login that names no hotel is unplaceable, and an unplaceable request is what
    tenancy exists to stop — so a staff member created today must arrive stamped, not
    wait for the next startup migration to rescue them."""
    email = f"stamp-{uuid.uuid4().hex[:6]}@barflow.io"
    s = _staff_session(admin, email, "stamp12345678", "front_desk", ["hotel"])
    assert s.get(f"{API}/auth/me").status_code == 200
    # Reaching an operating endpoint at all means the property resolved and was usable.
    assert s.get(f"{API}/bookings").status_code == 200
    assert s.get(f"{API}/property").json()["id"] == admin.get(f"{API}/property").json()["id"]


# ------------------- Pre-assigning a room, over the wire -------------------
def _bookable(admin, rooms=2):
    """A room type with `rooms` rooms, a rate and a guest — enough to take bookings."""
    rt = _new_room_type(admin)
    made = _add_rooms(admin, rt["id"], rooms)
    _add_default_rate(admin, rt["id"])
    return rt, made, _new_guest(admin)


def _book(admin, rt, guest, ep_plan, ci, co):
    r = admin.post(f"{API}/bookings", json={
        "guest_id": guest["id"], "room_type_id": rt["id"],
        "meal_plan_id": ep_plan["id"], "check_in": ci, "check_out": co,
        "adults": 2, "children": 0})
    assert r.status_code == 200, r.text
    return r.json()


def test_one_room_cannot_be_held_by_two_overlapping_bookings(admin, ep_plan):
    """The end-to-end proof, with real status codes.

    Two rooms of the type, not one, only so that both bookings can be *taken* — the
    inventory check on POST /bookings would refuse the second otherwise. The whole
    point is about one specific door: room A is given to the first booking, and the
    second cannot have it while their stays overlap.
    """
    rt, rooms, guest = _bookable(admin)
    room_a = rooms[0]
    first = _book(admin, rt, guest, ep_plan, "2030-02-10", "2030-02-14")
    second = _book(admin, rt, guest, ep_plan, "2030-02-13", "2030-02-17")

    given = admin.put(f"{API}/bookings/{first['id']}/room", json={"room_id": room_a["id"]})
    assert given.status_code == 200, given.text
    assert given.json()["assigned_room_id"] == room_a["id"]
    assert given.json()["room"]["number"] == room_a["number"]

    clash = admin.put(f"{API}/bookings/{second['id']}/room", json={"room_id": room_a["id"]})
    assert clash.status_code == 409, clash.text
    detail = clash.json()["detail"]
    # Named, not merely refused: the desk can go and move BF-… out of the way.
    assert detail["booking_id"] == first["id"]
    assert detail["reference"] == first["reference"]
    assert first["reference"] in detail["message"]
    assert admin.get(f"{API}/bookings/{second['id']}").json()["assigned_room_id"] is None

    # Move the first booking out of the way — the second may now have the room.
    moved = admin.put(f"{API}/bookings/{first['id']}",
                      json={"check_in": "2030-02-10", "check_out": "2030-02-13"})
    assert moved.status_code == 200, moved.text

    now_free = admin.put(f"{API}/bookings/{second['id']}/room",
                         json={"room_id": room_a["id"]})
    assert now_free.status_code == 200, now_free.text
    assert now_free.json()["assigned_room_id"] == room_a["id"]
    # And the first still holds it for its own, now adjacent, nights: 10th→13th and
    # 13th→17th share a boundary day, not a night.
    assert admin.get(f"{API}/bookings/{first['id']}").json()["assigned_room_id"] == room_a["id"]


def test_a_room_of_the_wrong_type_is_refused_over_the_wire(admin, ep_plan):
    rt, _rooms, guest = _bookable(admin, rooms=1)
    other_rt, other_rooms, _g = _bookable(admin, rooms=1)
    booking = _book(admin, rt, guest, ep_plan, "2030-03-10", "2030-03-12")

    r = admin.put(f"{API}/bookings/{booking['id']}/room",
                  json={"room_id": other_rooms[0]["id"]})
    assert r.status_code == 409, r.text


def test_a_room_out_of_order_during_the_stay_is_refused_over_the_wire(admin, ep_plan):
    rt, rooms, guest = _bookable(admin, rooms=1)
    booking = _book(admin, rt, guest, ep_plan, "2030-04-10", "2030-04-15")
    blocked = admin.post(f"{API}/rooms/{rooms[0]['id']}/out-of-order",
                         json={"from": "2030-04-12", "to": "2030-04-13",
                               "reason": "Burst pipe"})
    assert blocked.status_code == 200, blocked.text

    r = admin.put(f"{API}/bookings/{booking['id']}/room", json={"room_id": rooms[0]["id"]})
    assert r.status_code == 409, r.text
    assert "Burst pipe" in r.json()["detail"]["message"]


def test_clearing_the_room_frees_it_over_the_wire(admin, ep_plan):
    rt, rooms, guest = _bookable(admin)
    first = _book(admin, rt, guest, ep_plan, "2030-05-10", "2030-05-14")
    second = _book(admin, rt, guest, ep_plan, "2030-05-12", "2030-05-16")

    admin.put(f"{API}/bookings/{first['id']}/room", json={"room_id": rooms[0]["id"]})
    assert admin.put(f"{API}/bookings/{second['id']}/room",
                     json={"room_id": rooms[0]["id"]}).status_code == 409

    cleared = admin.put(f"{API}/bookings/{first['id']}/room", json={"room_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["assigned_room_id"] is None

    retry = admin.put(f"{API}/bookings/{second['id']}/room", json={"room_id": rooms[0]["id"]})
    assert retry.status_code == 200, retry.text


def test_the_bookings_list_says_which_bookings_still_need_a_room(admin, ep_plan):
    rt, rooms, guest = _bookable(admin)
    assigned = _book(admin, rt, guest, ep_plan, "2030-06-10", "2030-06-12")
    waiting = _book(admin, rt, guest, ep_plan, "2030-06-12", "2030-06-14")
    admin.put(f"{API}/bookings/{assigned['id']}/room", json={"room_id": rooms[0]["id"]})

    rows = {b["id"]: b for b in admin.get(f"{API}/bookings").json()}
    assert rows[assigned["id"]]["room"]["number"] == rooms[0]["number"]
    assert rows[waiting["id"]]["room"] is None


def test_check_in_refuses_a_room_held_for_a_later_arrival(admin, ep_plan):
    """The regression the whole feature turns on. Before pre-assignment, check-in only
    had to look at bookings that were already checked in; now a confirmed booking holds
    a room too, and giving it away at the desk is the same double-booking from the
    other end."""
    rt, rooms, guest = _bookable(admin)
    later = _book(admin, rt, guest, ep_plan, "2030-07-12", "2030-07-16")
    admin.put(f"{API}/bookings/{later['id']}/room", json={"room_id": rooms[0]["id"]})

    arriving = _book(admin, rt, guest, ep_plan, "2030-07-10", "2030-07-14")
    r = admin.post(f"{API}/bookings/{arriving['id']}/check-in", json={
        "room_id": rooms[0]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1234-5678-9012"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reference"] == later["reference"]

    # The other room is free, so the guest is checked in there instead.
    ok = admin.post(f"{API}/bookings/{arriving['id']}/check-in", json={
        "room_id": rooms[1]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1234-5678-9012"})
    assert ok.status_code == 200, ok.text


def test_check_in_takes_the_room_the_booking_was_pre_assigned(admin, ep_plan):
    rt, rooms, guest = _bookable(admin, rooms=1)
    booking = _book(admin, rt, guest, ep_plan, "2030-08-10", "2030-08-12")
    admin.put(f"{API}/bookings/{booking['id']}/room", json={"room_id": rooms[0]["id"]})

    r = admin.post(f"{API}/bookings/{booking['id']}/check-in", json={
        "room_id": rooms[0]["id"], "id_proof_type": "Aadhaar",
        "id_proof_number": "1234-5678-9012"})
    assert r.status_code == 200, r.text
    assert r.json()["booking"]["assigned_room_id"] == rooms[0]["id"]


def test_front_desk_may_assign_a_room_and_a_waiter_may_not(admin, front_desk, waiter,
                                                           ep_plan):
    """Assignment is operational, not configuration: the receptionist does it. A waiter
    holds neither the hotel domain nor either screen this endpoint names."""
    rt, rooms, guest = _bookable(admin, rooms=1)
    booking = _book(admin, rt, guest, ep_plan, "2030-09-10", "2030-09-12")

    assert waiter.put(f"{API}/bookings/{booking['id']}/room",
                      json={"room_id": rooms[0]["id"]}).status_code == 403

    r = front_desk.put(f"{API}/bookings/{booking['id']}/room",
                       json={"room_id": rooms[0]["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["assigned_room_id"] == rooms[0]["id"]


def test_a_cancelled_booking_cannot_be_given_a_room(admin, ep_plan):
    rt, rooms, guest = _bookable(admin, rooms=1)
    booking = _book(admin, rt, guest, ep_plan, "2030-10-10", "2030-10-12")
    admin.post(f"{API}/bookings/{booking['id']}/cancel", json={"reason": "Plans changed"})

    r = admin.put(f"{API}/bookings/{booking['id']}/room", json={"room_id": rooms[0]["id"]})
    assert r.status_code == 409, r.text


def test_the_bookings_screen_alone_can_read_the_room_list_to_assign_from(admin, ep_plan):
    """A receptionist ticked only for bookings still has to see the rooms, or the assign
    picker on the booking screen is empty and the feature does not exist for them."""
    rt, rooms, guest = _bookable(admin, rooms=1)
    booking = _book(admin, rt, guest, ep_plan, "2030-11-10", "2030-11-12")
    s = _staff_session(admin, f"bookonly-{uuid.uuid4().hex[:6]}@barflow.io",
                       "bookonly12345", "front_desk", ["hotel"],
                       permissions=["hotel.bookings"])

    listing = s.get(f"{API}/rooms")
    assert listing.status_code == 200, listing.text
    assert any(r["id"] == rooms[0]["id"] for r in listing.json())
    # …and the front-desk board is still not theirs.
    assert s.get(f"{API}/front-desk").status_code == 403

    assigned = s.put(f"{API}/bookings/{booking['id']}/room",
                     json={"room_id": rooms[0]["id"]})
    assert assigned.status_code == 200, assigned.text


def test_a_stay_cannot_be_stretched_over_a_room_someone_else_holds(admin, ep_plan):
    """The other door into the same double-booking: never assign a clashing room, just
    move the dates of a booking that already holds one until they overlap."""
    rt, rooms, guest = _bookable(admin)
    early = _book(admin, rt, guest, ep_plan, "2031-03-01", "2031-03-05")
    late = _book(admin, rt, guest, ep_plan, "2031-03-05", "2031-03-09")
    # Adjacent, so both may legitimately hold the same door.
    assert admin.put(f"{API}/bookings/{early['id']}/room",
                     json={"room_id": rooms[0]["id"]}).status_code == 200
    assert admin.put(f"{API}/bookings/{late['id']}/room",
                     json={"room_id": rooms[0]["id"]}).status_code == 200

    stretched = admin.put(f"{API}/bookings/{early['id']}",
                          json={"check_in": "2031-03-01", "check_out": "2031-03-07"})
    assert stretched.status_code == 409, stretched.text
    assert stretched.json()["detail"]["reference"] == late["reference"]
    # Refused whole: neither the dates nor the room moved.
    unchanged = admin.get(f"{API}/bookings/{early['id']}").json()
    assert unchanged["check_out"] == "2031-03-05"
    assert unchanged["assigned_room_id"] == rooms[0]["id"]
