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
    quote = next(q for q in row["quotes"] if q["meal_plan"]["code"] == "EP")
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
    """Open a bar order on a free table and return it."""
    tables = admin.get(f"{API}/tables").json()
    table = next(t for t in tables if t["status"] == "free")
    menu = admin.get(f"{API}/menu").json()
    return admin.post(f"{API}/orders/table/{table['id']}/items", json={
        "items": [{"menu_item_id": menu[0]["id"], "quantity": 2}], "source": "pos"}).json()


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


def _staff_session(admin, email, password, role, domains):
    """Create a staff user directly via the seeded admin, and return a logged-in session."""
    import requests as _rq
    admin.post(f"{API}/staff", json={
        "name": email.split("@")[0], "email": email, "password": password,
        "role": role, "domains": domains})
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


def test_admin_cannot_change_their_own_role_or_domains(admin):
    me = admin.get(f"{API}/auth/me").json()
    r = admin.put(f"{API}/staff/{me['id']}", json={
        "name": me["name"], "role": "waiter", "domains": ["bar"]})
    assert r.status_code == 409, r.text


def test_last_active_admin_cannot_be_demoted(admin):
    # The seeded admin is the only admin in a fresh database.
    me = admin.get(f"{API}/auth/me").json()
    others = [u for u in admin.get(f"{API}/staff").json()
              if u["role"] == "admin" and u["active"] and u["id"] != me["id"]]
    if others:
        return  # another admin exists; the rule under test does not apply
    r = admin.put(f"{API}/staff/{me['id']}", json={
        "name": me["name"], "role": "manager", "domains": ["hotel"]})
    assert r.status_code == 409, r.text


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
