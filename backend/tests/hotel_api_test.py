"""Hotel API integration tests. Requires a running server (see backend_test.py)."""
import os
import uuid

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
    r = admin.post("{}/auth/register".format(API), json={
        "email": "desk-test@barflow.io",
        "name": "Desk Tester",
        "password": "desk12345",
        "role": "front_desk",
    })
    assert r.status_code in (200, 400), r.text
    if r.status_code == 400:
        assert "exists" in r.text.lower()


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


def test_create_rate_period_overlap_same_priority_warns(admin):
    tag = uuid.uuid4().hex[:6]
    first = admin.post(f"{API}/rate-periods", json={
        "name": f"Diwali {tag}", "start_date": "2026-11-01", "end_date": "2026-11-15",
        "priority": 5,
    })
    assert first.status_code == 200, first.text
    assert first.json()["overlap_warning"] is None

    second = admin.post(f"{API}/rate-periods", json={
        "name": f"Xmas {tag}", "start_date": "2026-11-10", "end_date": "2026-11-20",
        "priority": 5,
    })
    assert second.status_code == 200, second.text
    assert second.json()["overlap_warning"] is not None
    assert first.json()["name"] in second.json()["overlap_warning"]


def test_create_rate_period_overlap_different_priority_no_warning(admin):
    tag = uuid.uuid4().hex[:6]
    first = admin.post(f"{API}/rate-periods", json={
        "name": f"Base Season {tag}", "start_date": "2027-02-01", "end_date": "2027-02-20",
        "priority": 1,
    })
    assert first.status_code == 200, first.text

    second = admin.post(f"{API}/rate-periods", json={
        "name": f"Valentine Special {tag}", "start_date": "2027-02-10", "end_date": "2027-02-16",
        "priority": 9,
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
