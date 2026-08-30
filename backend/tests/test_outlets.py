"""The rules an outlet obeys, with no database and no HTTP under them.

Kept separate from the router for the reason services/password.py is: a rule that can
only be exercised by starting a server is a rule nobody exercises.
"""
from services.outlets import (
    KINDS, KIND_DOMAIN, SERVICES, default_name, outlet_problem)


def test_every_kind_names_the_domain_it_sits_behind():
    # A kind with no domain would produce an outlet no staff member could ever be
    # granted, which is a row that exists and does nothing.
    for kind in KINDS:
        assert kind in KIND_DOMAIN, kind
        assert KIND_DOMAIN[kind]


def test_food_kinds_keep_the_domains_they_already_had():
    # Existing properties have staff holding these two. Remapping them would silently
    # move every waiter in production out of the screens they work in.
    assert KIND_DOMAIN["restaurant"] == "restaurant"
    assert KIND_DOMAIN["bar"] == "bar"


def test_the_new_kinds_share_one_domain_rather_than_inventing_three():
    # One `services` domain instead of salon/gym/laundry as three. A hotel that adds a
    # salon and a gym almost always staffs them from the same small group, and three
    # domains would make the staff screen ask three questions to express that.
    assert KIND_DOMAIN["salon"] == SERVICES
    assert KIND_DOMAIN["gym"] == SERVICES
    assert KIND_DOMAIN["laundry"] == SERVICES
    assert KIND_DOMAIN["other"] == SERVICES


def test_a_kind_has_a_default_name_so_the_form_is_never_blank():
    assert default_name("salon") == "Salon"
    assert default_name("restaurant") == "Restaurant"
    # An unknown kind still answers, because a caller that has already been validated
    # should not be able to crash the form by asking for a label.
    assert default_name("nonsense") == "Outlet"


def test_an_outlet_that_can_take_money_no_way_at_all_is_refused():
    problem = outlet_problem("Serenity Salon", "salon",
                             charges_to_folio=False, takes_direct_payment=False)
    assert problem
    assert "folio" in problem or "payment" in problem


def test_either_way_of_taking_money_on_its_own_is_enough():
    assert outlet_problem("Spa", "salon", True, False) is None
    assert outlet_problem("Spa", "salon", False, True) is None


def test_a_nameless_outlet_is_refused():
    assert outlet_problem("   ", "salon", True, True)


def test_an_unknown_kind_is_refused_and_the_message_names_it():
    problem = outlet_problem("Helipad", "helipad", True, True)
    assert problem
    assert "helipad" in problem
