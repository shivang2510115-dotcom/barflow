"""Pure authorization tests — no server, no database."""
import pytest
from services.access import (
    DOMAINS, LIVE, OUTLET, PENDING, PLATFORM_ADMIN, SCREENS, SCREEN_KEYS, SHARED,
    SUSPENDED,
    AccessError, can_access, normalise_domains, normalise_permissions,
    permission_in_domains,
)


def u(role="manager", domains=("restaurant",), active=True):
    return {"role": role, "domains": list(domains), "active": active}


MGR = ("admin", "manager")


# --- tenancy helpers ---------------------------------------------------------------

def tenant(role="manager", domains=("restaurant",), active=True, property_id="p1"):
    """A user who belongs to a hotel — what every real login is once tenancy exists."""
    user = u(role, domains, active)
    user["property_id"] = property_id
    return user


def prop(status, id="p1"):
    return {"id": id, "status": status, "name": "The Grand"}


def test_role_and_domain_both_required():
    assert can_access(u(domains=("restaurant",)), "restaurant", MGR) is True
    assert can_access(u(domains=("restaurant",)), "hotel", MGR) is False


def test_wrong_role_is_refused_even_with_the_domain():
    assert can_access(u(role="waiter", domains=("hotel",)), "hotel", MGR) is False


def test_admin_reaches_every_domain_regardless_of_its_own_domains():
    admin = u(role="admin", domains=())
    for d in DOMAINS:
        assert can_access(admin, d, MGR) is True


def test_inactive_user_is_refused():
    assert can_access(u(domains=("restaurant",), active=False), "restaurant", MGR) is False


def test_inactive_admin_is_also_refused():
    # Deactivating a compromised admin must actually lock them out.
    assert can_access(u(role="admin", active=False), "hotel", MGR) is False


def test_shared_is_satisfied_by_any_domain():
    assert can_access(u(domains=("bar",)), SHARED, MGR) is True
    assert can_access(u(domains=("hotel",)), SHARED, MGR) is True


def test_shared_still_enforces_role():
    assert can_access(u(role="kitchen", domains=("bar",)), SHARED, MGR) is False


def test_user_with_no_domains_reaches_nothing():
    assert can_access(u(domains=()), "restaurant", MGR) is False
    assert can_access(u(domains=()), SHARED, MGR) is False


def test_several_domains_on_the_endpoint_grant_on_any_match():
    # The order screens are declared restaurant AND bar; a bar-only waiter must reach them.
    waiter = u(role="waiter", domains=("bar",))
    assert can_access(waiter, ("restaurant", "bar"), ("admin", "manager", "waiter")) is True


def test_several_domains_on_the_user_grant_on_any_match():
    duty = u(domains=("hotel", "restaurant"))
    assert can_access(duty, "hotel", MGR) is True
    assert can_access(duty, "restaurant", MGR) is True
    assert can_access(duty, "bar", MGR) is False


def test_empty_roles_means_any_authenticated_role():
    # Used by endpoints that were previously bare get_current_user.
    assert can_access(u(role="kitchen", domains=("bar",)), "bar", ()) is True


def test_unknown_domain_raises_rather_than_denying_silently():
    with pytest.raises(AccessError):
        normalise_domains("spa")
    with pytest.raises(AccessError):
        can_access(u(), "spa", MGR)


def test_no_domain_declared_raises():
    with pytest.raises(AccessError):
        normalise_domains(())


def test_shared_cannot_be_mixed_with_a_specific_domain():
    with pytest.raises(AccessError):
        normalise_domains((SHARED, "hotel"))


def test_single_domain_string_and_tuple_are_equivalent():
    assert normalise_domains("hotel") == ("hotel",)
    assert normalise_domains(("hotel",)) == ("hotel",)


def test_missing_domains_raises_access_error_not_type_error():
    with pytest.raises(AccessError):
        normalise_domains(None)


def test_bare_string_role_does_not_become_substring_matching():
    # "man" must not pass a check for roles="manager" just because "man" in "manager".
    assert can_access(u(role="man"), "restaurant", "manager") is False
    assert can_access(u(role="manager"), "restaurant", "manager") is True


# --- tenancy: is the caller's property usable at all? --------------------------------


def test_a_live_property_behaves_exactly_as_today():
    # Every case above, replayed with a live property attached. Going live must change
    # nothing about who reaches what, or the whole existing suite is a lie.
    live = prop(LIVE)

    assert can_access(tenant(domains=("restaurant",)), "restaurant", MGR, live) is True
    assert can_access(tenant(domains=("restaurant",)), "hotel", MGR, live) is False
    assert can_access(tenant(role="waiter", domains=("hotel",)), "hotel", MGR, live) is False

    admin = tenant(role="admin", domains=())
    for d in DOMAINS:
        assert can_access(admin, d, MGR, live) is True

    assert can_access(tenant(domains=("bar",)), SHARED, MGR, live) is True
    assert can_access(tenant(role="kitchen", domains=("bar",)), SHARED, MGR, live) is False
    assert can_access(tenant(domains=()), "restaurant", MGR, live) is False
    assert can_access(tenant(domains=()), SHARED, MGR, live) is False

    waiter = tenant(role="waiter", domains=("bar",))
    assert can_access(waiter, ("restaurant", "bar"), ("admin", "manager", "waiter"), live) is True

    duty = tenant(domains=("hotel", "restaurant"))
    assert can_access(duty, "hotel", MGR, live) is True
    assert can_access(duty, "bar", MGR, live) is False

    assert can_access(tenant(role="kitchen", domains=("bar",)), "bar", (), live) is True
    assert can_access(tenant(role="man"), "restaurant", "manager", live) is False

    with pytest.raises(AccessError):
        can_access(tenant(), "spa", MGR, live)


def test_a_live_property_is_unaffected_by_the_setup_time_marking():
    # setup_time widens who may act while pending; it says nothing about a live hotel.
    live = prop(LIVE)
    assert can_access(tenant(), "restaurant", MGR, live, setup_time=True) is True
    assert can_access(tenant(), "restaurant", MGR, live, setup_time=False) is True


def test_an_inactive_user_in_a_live_property_is_still_refused():
    # Rule 1 passing must not skip the rules after it.
    live = prop(LIVE)
    assert can_access(tenant(active=False), "restaurant", MGR, live) is False
    assert can_access(tenant(role="admin", active=False), "hotel", MGR, live) is False


def test_a_suspended_property_refuses_its_own_admin():
    suspended = prop(SUSPENDED)
    assert can_access(tenant(role="admin", domains=()), "hotel", MGR, suspended) is False


def test_a_suspended_property_refuses_every_role():
    suspended = prop(SUSPENDED)
    for role in ("admin", "manager", "waiter", "kitchen", "front_desk"):
        user = tenant(role=role, domains=DOMAINS)
        assert can_access(user, "hotel", (), suspended) is False
        assert can_access(user, SHARED, (), suspended) is False


def test_a_suspended_property_beats_the_admin_domain_bypass():
    # The admin bypass skips the domain check, not the tenancy check. If suspension were
    # applied after the bypass, suspending a hotel would lock out everyone but the one
    # person whose account is worth the most.
    suspended = prop(SUSPENDED)
    admin = tenant(role="admin", domains=())
    for d in DOMAINS:
        assert can_access(admin, d, MGR, suspended) is False
    assert can_access(admin, SHARED, MGR, suspended) is False


def test_setup_time_does_not_rescue_a_suspended_property():
    # Suspension is total: an unpaid hotel does not get to keep editing its rates.
    suspended = prop(SUSPENDED)
    assert can_access(tenant(role="admin"), "hotel", MGR, suspended, setup_time=True) is False


def test_a_pending_property_allows_a_setup_time_endpoint():
    pending = prop(PENDING)
    assert can_access(tenant(role="admin", domains=()), "hotel", MGR, pending, setup_time=True) is True
    assert can_access(tenant(domains=("hotel",)), "hotel", MGR, pending, setup_time=True) is True


def test_a_pending_property_refuses_an_operating_endpoint():
    pending = prop(PENDING)
    assert can_access(tenant(domains=("hotel",)), "hotel", MGR, pending) is False
    assert can_access(tenant(role="waiter", domains=("bar",)), SHARED, (), pending) is False


def test_a_pending_property_refuses_an_operating_endpoint_even_for_an_admin():
    # No guest money moves through an unvetted property, whoever is signed in.
    pending = prop(PENDING)
    admin = tenant(role="admin", domains=())
    for d in DOMAINS:
        assert can_access(admin, d, MGR, pending) is False


def test_a_pending_property_still_enforces_the_rules_after_rule_one():
    # Setup-time is a widening of rule 1 only — role, domain and active still apply.
    pending = prop(PENDING)
    assert can_access(tenant(role="waiter"), "restaurant", MGR, pending, setup_time=True) is False
    assert can_access(tenant(domains=("bar",)), "hotel", MGR, pending, setup_time=True) is False
    assert can_access(tenant(active=False), "restaurant", MGR, pending, setup_time=True) is False


def test_an_unknown_property_status_is_refused():
    # A status nobody has taught this function about is not a licence to operate.
    assert can_access(tenant(role="admin"), "hotel", MGR, prop("archived")) is False
    assert can_access(tenant(role="admin"), "hotel", MGR, prop(None)) is False
    assert can_access(tenant(role="admin"), "hotel", MGR, {"id": "p1"}) is False


def test_a_platform_admin_is_refused_a_hotel_endpoint():
    # The operator's login is not a master key into customer data.
    operator = {"role": PLATFORM_ADMIN, "domains": [], "active": True, "property_id": None}
    for d in DOMAINS:
        assert can_access(operator, d, (), None) is False
        assert can_access(operator, d, (), None, setup_time=True) is False
    assert can_access(operator, SHARED, (), None) is False


def test_a_platform_admin_cannot_slip_through_by_having_no_property():
    # Refused on the role, not merely on the absent record — so an operator who somehow
    # arrives at a hotel endpoint that never resolved a property is still refused, and
    # one handed a live property (a stamping bug, a hand-edited record) is too.
    operator = {"role": PLATFORM_ADMIN, "domains": list(DOMAINS), "active": True}
    assert can_access(operator, "hotel", ()) is False
    assert can_access(operator, "hotel", (), prop(LIVE)) is False
    operator_with_property = dict(operator, property_id="p1")
    assert can_access(operator_with_property, "hotel", (), prop(LIVE), setup_time=True) is False


def test_a_user_with_no_property_is_refused():
    # A startup migration is meant to make this unreachable; the predicate must not
    # depend on the migration having run.
    stray = tenant(role="admin", property_id=None)
    assert can_access(stray, "hotel", MGR, None) is False
    assert can_access(stray, "hotel", MGR, prop(LIVE)) is False
    assert can_access(stray, "hotel", MGR, prop(LIVE), setup_time=True) is False

    missing_key = u(role="admin")
    assert can_access(missing_key, "hotel", MGR, prop(LIVE)) is False


def test_a_property_that_could_not_be_found_is_refused():
    # A property_id pointing at nothing is a broken tenant, not an unrestricted one.
    assert can_access(tenant(role="admin"), "hotel", MGR, None) is False
    assert can_access(tenant(role="admin"), "hotel", MGR, None, setup_time=True) is False


def test_omitting_the_property_leaves_the_pre_tenancy_rules_alone():
    # The default exists so the seventeen tests written before tenancy still describe the
    # truth. require_access always passes a property, so this shape is a test convenience
    # rather than a route anyone can reach.
    assert can_access(u(), "restaurant", MGR) is True
    assert can_access(u(active=False), "restaurant", MGR) is False


# --- screen permissions -------------------------------------------------------------
#
# Domains say which part of the business a person works in; permissions say which
# screens within it. A tick is only meaningful inside a domain the user holds, so the
# domain check still runs first — ticking a screen must never widen anybody.

def p(role="manager", domains=("hotel",), permissions=("hotel.bookings",), active=True):
    user = u(role, domains, active)
    user["permissions"] = list(permissions)
    return user


def test_holding_the_permission_and_the_domain_is_allowed():
    assert can_access(p(), "hotel", MGR, permission="hotel.bookings") is True


def test_holding_the_domain_but_not_the_permission_is_refused():
    assert can_access(p(permissions=("hotel.guests",)), "hotel", MGR,
                      permission="hotel.bookings") is False


def test_an_admin_is_allowed_regardless_of_permissions():
    admin = p(role="admin", domains=(), permissions=())
    assert can_access(admin, "hotel", MGR, permission="hotel.bookings") is True
    assert can_access(admin, SHARED, MGR, permission="admin.staff") is True


def test_a_permission_cannot_widen_someone_past_their_domains():
    # The whole point of running the domain check first: a restaurant-only manager who
    # has somehow been given a hotel screen key still reaches no hotel endpoint.
    outsider = p(domains=("restaurant",), permissions=("hotel.bookings",))
    assert can_access(outsider, "hotel", MGR, permission="hotel.bookings") is False


def test_an_inactive_user_with_both_is_still_refused():
    assert can_access(p(active=False), "hotel", MGR, permission="hotel.bookings") is False


def test_a_user_with_no_permissions_field_reaches_a_permissioned_endpoint_never():
    # An account the migration has not reached must fail closed, not open.
    assert can_access(u(domains=("hotel",)), "hotel", MGR, permission="hotel.bookings") is False


def test_an_endpoint_declaring_no_permission_is_unaffected():
    # Every endpoint that predates the catalogue keeps answering exactly as before.
    assert can_access(u(domains=("hotel",)), "hotel", MGR) is True
    assert can_access(p(permissions=()), "hotel", MGR) is True


def test_an_endpoint_read_by_two_screens_accepts_either_key():
    # GET /rooms is read by the Rooms screen and by the front desk's room picker.
    both = ("hotel.rooms", "hotel.front_desk")
    assert can_access(p(permissions=("hotel.rooms",)), "hotel", MGR, permission=both) is True
    assert can_access(p(permissions=("hotel.front_desk",)), "hotel", MGR, permission=both) is True
    assert can_access(p(permissions=("hotel.bookings",)), "hotel", MGR, permission=both) is False


def test_the_role_check_still_runs_ahead_of_the_permission():
    waiter = p(role="waiter", domains=("hotel",), permissions=("hotel.bookings",))
    assert can_access(waiter, "hotel", MGR, permission="hotel.bookings") is False


def test_a_permission_is_refused_inside_a_suspended_property():
    holder = p(permissions=("hotel.bookings",))
    holder["property_id"] = "p1"
    assert can_access(holder, "hotel", MGR, prop(SUSPENDED),
                      permission="hotel.bookings") is False


def test_an_unknown_screen_key_raises_rather_than_denying_silently():
    # A typo in an endpoint's declared permission must break startup, exactly as a typo
    # in its declared domain does — not silently refuse every user at runtime.
    with pytest.raises(AccessError):
        normalise_permissions("hotel.spa")
    with pytest.raises(AccessError):
        can_access(p(), "hotel", MGR, permission="hotel.spa")


def test_the_catalogue_is_well_formed():
    assert set(SCREENS) == set(SCREEN_KEYS)
    for key, screen in SCREENS.items():
        assert screen["label"] and screen["section"]
        # Each key's domains must be a declaration the domain checker accepts, or the
        # key names a screen no endpoint could serve.
        assert normalise_domains(screen["domains"]) == tuple(screen["domains"])


def test_the_catalogue_covers_the_keys_the_design_named():
    assert set(SCREENS) == {
        "hotel.front_desk", "hotel.bookings", "hotel.calendar", "hotel.rooms",
        "hotel.rates", "hotel.guests", "hotel.housekeeping", "hotel.bills",
        "outlet.tables", "outlet.pos", "outlet.kot", "outlet.reservations",
        "outlet.menu", "outlet.inventory", "outlet.reports",
        "property.planner",
        "admin.staff", "admin.outlets", "admin.analytics", "admin.expenses",
    }


def test_a_screen_is_within_a_users_domains_only_when_they_hold_one_of_them():
    assert permission_in_domains("hotel.bookings", ["hotel"]) is True
    assert permission_in_domains("hotel.bookings", ["restaurant"]) is False
    assert permission_in_domains("outlet.pos", ["bar"]) is True
    assert permission_in_domains("outlet.pos", ["hotel"]) is False
    # A shared screen sits behind endpoints any domain reaches, so any domain will do.
    assert permission_in_domains("hotel.guests", ["bar"]) is True
    assert permission_in_domains("outlet.inventory", ["hotel"]) is True
    # ...but holding no domain at all still reaches nothing.
    assert permission_in_domains("hotel.guests", []) is False


def test_permission_in_domains_rejects_an_unknown_key():
    with pytest.raises(AccessError):
        permission_in_domains("hotel.spa", ["hotel"])


def test_the_services_domain_exists_and_counts_as_an_outlet_domain():
    from services.outlets import SERVICES
    assert SERVICES in DOMAINS
    # Widening OUTLET is the point: a salon's staff reach the POS, the menu and the
    # tables screens through the same keys a waiter does, because those screens are
    # about selling from a catalogue and a salon sells from a catalogue.
    assert SERVICES in OUTLET


def test_the_food_domains_survive_the_widening():
    # Production data holds these strings on user records. Reordering is harmless;
    # renaming or dropping either would strand every waiter and bartender.
    assert "restaurant" in DOMAINS
    assert "bar" in DOMAINS
    assert "hotel" in DOMAINS


def test_managing_outlets_is_an_admin_screen():
    assert "admin.outlets" in SCREENS
    assert SCREENS["admin.outlets"]["section"] == "Admin"
