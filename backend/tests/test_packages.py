"""What a guest's package includes, and how much of it is left.

Pure arithmetic over plain dicts. The periods are the difficulty: "two spa treatments"
means two for the whole stay, "breakfast" means one a night, and a gym is usually
unlimited every day. Getting the period wrong is how a hotel gives away five breakfasts
to a guest entitled to two, and nobody notices until the month's food cost lands.
"""
from services.packages import allowance, remaining, PER_STAY, PER_NIGHT, PER_DAY


def inc(period, quantity=1, **kw):
    return {"id": "i1", "period": period, "quantity": quantity, **kw}


def use(inclusion_id="i1", day="2026-08-30"):
    return {"inclusion_id": inclusion_id, "used_on": day}


def test_per_stay_is_a_flat_total_however_long_the_stay():
    assert allowance(inc(PER_STAY, 2), nights=1) == 2
    assert allowance(inc(PER_STAY, 2), nights=7) == 2


def test_per_night_multiplies_by_the_nights_booked():
    # Breakfast for two, three nights, is six breakfasts.
    assert allowance(inc(PER_NIGHT, 2), nights=3) == 6


def test_per_night_on_a_zero_night_booking_gives_nothing():
    # A day-use booking has no nights, so a per-night inclusion covers nothing. Giving
    # one anyway is the kind of rounding that becomes a free breakfast every time.
    assert allowance(inc(PER_NIGHT, 1), nights=0) == 0


def test_per_day_is_counted_against_one_day_not_the_stay():
    # A gym pass is "unlimited today", not "unlimited forever". The allowance a caller
    # gets back is the daily one; `remaining` filters the uses to that day.
    assert allowance(inc(PER_DAY, 1), nights=5) == 1


def test_nothing_used_leaves_the_whole_allowance():
    assert remaining(inc(PER_STAY, 2), [], nights=3, day="2026-08-30") == 2


def test_each_use_takes_one_from_the_allowance():
    uses = [use(), use()]
    assert remaining(inc(PER_STAY, 3), uses, nights=3, day="2026-08-30") == 1


def test_an_exhausted_allowance_reports_zero_and_never_goes_negative():
    uses = [use(), use(), use()]
    # Three used against two included. It can happen — a manual override, a late
    # correction — and the answer is "none left", not "minus one", because the caller
    # renders this number to a guest.
    assert remaining(inc(PER_STAY, 2), uses, nights=3, day="2026-08-30") == 0


def test_a_use_of_a_different_inclusion_does_not_consume_this_one():
    # Two spa treatments and three breakfasts are separate allowances. Counting every
    # use against every inclusion would exhaust the package on its first morning.
    uses = [use(inclusion_id="other"), use(inclusion_id="other")]
    assert remaining(inc(PER_STAY, 2), uses, nights=3, day="2026-08-30") == 2


def test_a_per_day_allowance_resets_the_next_morning():
    yesterday = [use(day="2026-08-29")]
    # Yesterday's gym visit does not come out of today's allowance.
    assert remaining(inc(PER_DAY, 1), yesterday, nights=3, day="2026-08-30") == 1
    today = [use(day="2026-08-30")]
    assert remaining(inc(PER_DAY, 1), today, nights=3, day="2026-08-30") == 0


def test_a_per_stay_allowance_does_not_reset():
    # The opposite of the case above, and the reason the two periods exist separately.
    yesterday = [use(day="2026-08-29")]
    assert remaining(inc(PER_STAY, 1), yesterday, nights=3, day="2026-08-30") == 0


def test_an_unknown_period_includes_nothing_rather_than_everything():
    # A hand-edited or imported row. "Nothing" is the safe guess: the guest is charged
    # and somebody notices, where "everything" gives the hotel's stock away silently.
    assert allowance(inc("per_fortnight", 5), nights=3) == 0


# --- what an inclusion applies to -----------------------------------------------------

from services.packages import covers, SCOPE_ITEM, SCOPE_CATEGORY, SCOPE_OUTLET


def test_an_inclusion_never_applies_outside_its_own_outlet():
    # The rule that has to hold whatever the scope says: a breakfast entitlement cannot
    # be spent in the salon.
    i = {"outlet_id": "restaurant", "scope": SCOPE_OUTLET}
    assert covers(i, "restaurant", None) is True
    assert covers(i, "salon", None) is False


def test_an_outlet_scope_covers_everything_that_outlet_sells():
    i = {"outlet_id": "salon", "scope": SCOPE_OUTLET}
    assert covers(i, "salon", {"id": "massage", "category": "Body"}) is True


def test_an_item_scope_covers_exactly_one_item():
    i = {"outlet_id": "restaurant", "scope": SCOPE_ITEM, "ref_id": "brk"}
    assert covers(i, "restaurant", {"id": "brk", "category": "Breakfast"}) is True
    assert covers(i, "restaurant", {"id": "din", "category": "Breakfast"}) is False


def test_a_category_scope_covers_a_whole_menu_section():
    i = {"outlet_id": "restaurant", "scope": SCOPE_CATEGORY, "ref_id": "Breakfast"}
    assert covers(i, "restaurant", {"id": "anything", "category": "Breakfast"}) is True
    assert covers(i, "restaurant", {"id": "anything", "category": "Desserts"}) is False


def test_an_item_scope_with_no_item_covers_nothing():
    # The POS asks about a specific line. Asked about nothing, an item-scoped inclusion
    # must not answer yes — that is how a whole order gets comped.
    i = {"outlet_id": "restaurant", "scope": SCOPE_ITEM, "ref_id": "brk"}
    assert covers(i, "restaurant", None) is False


def test_an_unknown_scope_covers_nothing():
    i = {"outlet_id": "restaurant", "scope": "everything_forever"}
    assert covers(i, "restaurant", {"id": "x", "category": "y"}) is False


def test_the_same_use_listed_twice_counts_once():
    """A use is its id. The deterministic id exists so a repeat is the same row, and
    this is the other half of that: the same row twice is still one consumption."""
    u = {"id": "u1", "inclusion_id": "i1", "used_on": "2026-08-30"}
    assert remaining(inc(PER_STAY, 2), [u, u], nights=3, day="2026-08-30") == 1


def test_distinct_uses_still_each_count():
    a = {"id": "u1", "inclusion_id": "i1", "used_on": "2026-08-30"}
    b = {"id": "u2", "inclusion_id": "i1", "used_on": "2026-08-30"}
    assert remaining(inc(PER_STAY, 2), [a, b], nights=3, day="2026-08-30") == 0


def test_uses_without_an_id_are_still_counted():
    # Nothing in this codebase writes one, but a caller building a hypothetical use to
    # ask "what if" must not have it silently ignored.
    u = {"inclusion_id": "i1", "used_on": "2026-08-30"}
    assert remaining(inc(PER_STAY, 2), [u], nights=3, day="2026-08-30") == 1


# --- what an entitlement is worth against an actual order -----------------------------

from services.packages import comp_value


def test_comping_a_line_is_worth_its_own_price():
    items = [{"id": "l1", "menu_item_id": "m1", "name": "Massage", "price": 1800, "quantity": 1}]
    assert comp_value(items, ["l1"]) == 1800.0


def test_comping_a_line_covers_every_unit_of_it():
    # Two breakfasts on one line is two breakfasts. Comping the line and charging for
    # the second is the kind of half-measure a guest notices.
    items = [{"id": "l1", "menu_item_id": "m1", "name": "Breakfast", "price": 450, "quantity": 2}]
    assert comp_value(items, ["l1"]) == 900.0


def test_a_line_that_is_not_on_the_order_is_worth_nothing():
    # The value is computed from the order the server holds, never from anything the
    # client sends. An id that names no line comps nothing rather than raising, so a
    # stale screen cannot refuse a sale.
    items = [{"id": "l1", "menu_item_id": "m1", "name": "Tea", "price": 60, "quantity": 1}]
    assert comp_value(items, ["nonsense"]) == 0.0


def test_comping_nothing_is_worth_nothing():
    items = [{"id": "l1", "menu_item_id": "m1", "name": "Tea", "price": 60, "quantity": 1}]
    assert comp_value(items, []) == 0.0


def test_the_same_line_named_twice_is_comped_once():
    # A double-tap on the screen must not comp a line twice and hand the guest the
    # difference. Same reasoning as the deterministic use id.
    items = [{"id": "l1", "menu_item_id": "m1", "name": "Massage", "price": 1800, "quantity": 1}]
    assert comp_value(items, ["l1", "l1"]) == 1800.0


# --- where a stay's package comes from -------------------------------------------------

from services.packages import package_for_stay


def test_a_room_types_package_is_what_a_stay_gets():
    """The owner's model: a Suite includes breakfast and spa, and says so once."""
    assert package_for_stay(rate={}, room_type={"package_id": "suite-pkg"}) == "suite-pkg"


def test_a_rate_can_override_the_room_types_package():
    # How a hotel sells the same Deluxe as Room Only and as Bed & Breakfast: two rates,
    # one room type. Rare, but the reason the rate is consulted first.
    assert package_for_stay(rate={"package_id": "bnb"},
                            room_type={"package_id": "room-only"}) == "bnb"


def test_neither_carrying_one_means_the_room_alone():
    assert package_for_stay(rate={}, room_type={}) is None
    assert package_for_stay(rate=None, room_type=None) is None


def test_a_blank_package_id_is_not_a_package():
    # An empty string is what a form submits when the picker is cleared, and it must
    # read as "none" rather than as an id that resolves to nothing.
    assert package_for_stay(rate={"package_id": ""},
                            room_type={"package_id": "suite-pkg"}) == "suite-pkg"
    assert package_for_stay(rate={"package_id": "  "}, room_type={}) is None
