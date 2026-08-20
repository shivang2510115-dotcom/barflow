"""The tenant record — defaults and the format checks it enforces.

Pure: a Pydantic model over plain values, no database and no request, so the rules
about what a property may be are readable and testable on their own.
"""
import pytest
from pydantic import ValidationError

from models.property import Property, PropertyIn, PropertyStatus
from services.access import LIVE, PENDING, SUSPENDED


def test_a_new_property_starts_pending():
    # The safe default: a hotel nobody has approved cannot be one that operates.
    assert Property(name="The Grand").status == PENDING


def test_a_property_gets_its_own_id_not_a_shared_constant():
    a, b = Property(name="A"), Property(name="B")
    assert a.id and b.id and a.id != b.id


def test_check_in_and_check_out_times_default_to_the_indian_norm():
    p = Property(name="The Grand")
    assert p.check_in_time == "14:00"
    assert p.check_out_time == "11:00"


def test_the_lifecycle_stamps_start_empty():
    p = Property(name="The Grand")
    assert p.created_at
    assert p.approved_at is None and p.approved_by is None
    assert p.suspended_at is None and p.suspension_reason is None


def test_the_three_statuses_are_the_ones_access_enforces():
    from typing import get_args
    assert set(get_args(PropertyStatus)) == {PENDING, LIVE, SUSPENDED}


def test_an_unknown_status_is_refused():
    with pytest.raises(ValidationError):
        Property(name="The Grand", status="approved")


def test_a_well_formed_gstin_is_accepted():
    assert PropertyIn(name="The Grand", gstin="27AAPFU0939F1ZV").gstin == "27AAPFU0939F1ZV"


def test_a_malformed_gstin_is_refused_and_the_error_names_the_field():
    with pytest.raises(ValidationError) as exc:
        PropertyIn(name="The Grand", gstin="27AAPFU0939F1Z")
    assert "gstin" in str(exc.value).lower()


def test_a_blank_gstin_is_not_yet_an_error():
    # The property record is seeded and saved long before every certificate is found.
    assert PropertyIn(name="The Grand", gstin="").gstin == ""
    assert PropertyIn(name="The Grand").gstin is None


def test_a_well_formed_fssai_licence_is_accepted():
    assert PropertyIn(name="The Grand", fssai_licence="12345678901234")


def test_a_malformed_fssai_licence_is_refused():
    with pytest.raises(ValidationError) as exc:
        PropertyIn(name="The Grand", fssai_licence="1234")
    assert "fssai" in str(exc.value).lower()


def test_a_property_needs_a_name():
    with pytest.raises(ValidationError):
        PropertyIn()
