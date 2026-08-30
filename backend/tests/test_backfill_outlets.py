"""The migration that gives every existing property the outlets it has been running.

Idempotence is the property under test, not a nicety: this runs from `on_startup()` on
every boot forever, because the deployment has no shell step to run a script from.
"""
from migrations.backfill_outlets import outlets_for_domains


def test_a_property_with_both_food_domains_gets_both_outlets():
    made = outlets_for_domains(["hotel", "restaurant", "bar"])
    assert {o["kind"] for o in made} == {"restaurant", "bar"}


def test_a_hotel_with_no_outlet_domain_gets_no_outlets():
    assert outlets_for_domains(["hotel"]) == []


def test_the_services_domain_alone_creates_nothing():
    # `services` is the domain a salon sits behind, but no existing property has ever
    # had a salon — the domain is new in this same change. Creating one here would
    # invent an outlet the hotel never ran.
    assert outlets_for_domains(["services"]) == []


def test_the_created_outlets_carry_their_default_names_and_can_take_money():
    made = outlets_for_domains(["restaurant"])
    assert len(made) == 1
    assert made[0]["name"] == "Restaurant"
    assert made[0]["kind"] == "restaurant"
    assert made[0]["domain"] == "restaurant"
    # Both true, because that is what a restaurant in this product has always been able
    # to do: charge a resident to their room, or take payment at the table.
    assert made[0]["charges_to_folio"] is True
    assert made[0]["takes_direct_payment"] is True
    assert made[0]["active"] is True


def test_deciding_what_to_create_is_stable_across_calls():
    # The function is pure — no uuid, no clock — precisely so this can be asserted.
    # The database half's idempotence is checked on a real boot; see the plan.
    assert outlets_for_domains(["restaurant", "bar"]) == outlets_for_domains(["restaurant", "bar"])
