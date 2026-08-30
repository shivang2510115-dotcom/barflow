"""Whose number a message goes out from, and what is missing when it cannot.

Pure: a property record in, credentials or a list of reasons out. No network, no
database. The refusals matter more than the successes here — a hotel with no WhatsApp of
its own must be told exactly what to get, and must never quietly borrow somebody else's
number.
"""
from services.whatsapp import credentials_for, missing_for, can_send


def prop(**kw):
    return {"id": "p1", "name": "Anand Castle", **kw}


def test_a_property_with_both_credentials_can_send():
    p = prop(whatsapp_phone_id="123456", whatsapp_token="EAAG...")
    assert can_send(p) is True
    phone_id, token = credentials_for(p)
    assert phone_id == "123456"
    assert token == "EAAG..."


def test_a_property_with_neither_cannot_send_and_is_told_both():
    p = prop()
    assert can_send(p) is False
    missing = missing_for(p)
    assert len(missing) == 2
    assert any("phone number id" in m.lower() for m in missing)
    assert any("token" in m.lower() for m in missing)


def test_half_configured_is_not_configured():
    # A phone id with no token is the state somebody is left in halfway through an
    # onboarding call. It must not read as ready.
    assert can_send(prop(whatsapp_phone_id="123456")) is False
    assert can_send(prop(whatsapp_token="EAAG...")) is False


def test_blank_and_whitespace_are_not_credentials():
    # A form submitted with an empty box stores "", and "" is not a token.
    assert can_send(prop(whatsapp_phone_id="  ", whatsapp_token="EAAG...")) is False
    assert can_send(prop(whatsapp_phone_id="123", whatsapp_token="   ")) is False


def test_there_is_no_platform_fallback():
    """The decision stated plainly: messages go from the restaurant's own number only.

    A fallback would put the platform's name on a restaurant's customer relationship and
    would quietly train a hotel never to set up its own. `credentials_for` takes only the
    property — there is nowhere for an environment variable to enter.
    """
    import services.whatsapp as module
    # Structural rather than textual: the module does not import `os` at all, so there
    # is no environment for a fallback to come from. Asserting on the source text was
    # weaker and wrong — it matched the word "environment" in the docstring explaining
    # why the fallback does not exist.
    assert not hasattr(module, "os"), \
        "services/whatsapp.py must not reach the environment — see the module docstring"
    assert credentials_for(prop()) == (None, None)


def test_the_owner_phone_is_the_propertys_own():
    p = prop(owner_phone="919876543210")
    assert missing_for(p, need_owner_phone=True) == [
        m for m in missing_for(p, need_owner_phone=True) if "owner" not in m.lower()
    ]


def test_a_missing_owner_phone_is_only_reported_when_it_is_needed():
    # The nightly brief goes to the owner; a customer follow-up does not.
    p = prop(whatsapp_phone_id="1", whatsapp_token="t")
    assert missing_for(p, need_owner_phone=False) == []
    assert len(missing_for(p, need_owner_phone=True)) == 1
