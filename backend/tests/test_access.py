"""Pure authorization tests — no server, no database."""
import pytest
from services.access import (
    DOMAINS, SHARED, AccessError, can_access, normalise_domains,
)


def u(role="manager", domains=("restaurant",), active=True):
    return {"role": role, "domains": list(domains), "active": active}


MGR = ("admin", "manager")


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
