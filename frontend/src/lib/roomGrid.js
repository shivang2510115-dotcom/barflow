/**
 * The arithmetic behind the floor plan: which floor a room is on, and what colour it is.
 *
 * Pure, dependency-free and deliberately kept out of the component, for two reasons.
 * The first is that three screens ask three different questions of the same 23 rooms and
 * the answers have to be derived in one place. The second is that a room drawn free that
 * is not free is the one failure that costs a hotel a guest at the door, so this file has
 * to be runnable outside a browser and fed real API responses — which it cannot be if it
 * imports React.
 *
 * Every predicate here is the *same* rule as `backend/services/availability.py`, said in
 * JavaScript: half-open ranges, the same three consuming statuses, the same out-of-order
 * test. It is a mirror, never an authority. Nothing in this file decides whether a room
 * may be given to a guest — `PUT /api/bookings/{id}/room` decides that, re-checking
 * immediately before it writes, and answers 409 naming whatever is in the way. What this
 * file does is stop the desk from clicking a door that is visibly already taken. If the
 * two ever disagree the server wins and the receptionist is told why.
 */

// The statuses that hold a room. Cancelled, no-show and checked-out release it, even
// though the record keeps the `assigned_room_id` it was given.
// Mirrors services/availability.py::CONSUMING_STATUSES.
export const CONSUMING_STATUSES = ["tentative", "confirmed", "checked_in"];

/**
 * True when two half-open date ranges share at least one night.
 *
 * `YYYY-MM-DD` strings compare lexicographically in exactly the order they compare as
 * dates, which is why no Date object appears anywhere in this file: a stay is a run of
 * calendar days, and turning it into an instant is how a hotel east of UTC loses a night.
 * Mirrors services/availability.py::ranges_overlap.
 */
export function rangesOverlap(aFrom, aTo, bFrom, bTo) {
  return aFrom < bTo && aTo > bFrom;
}

/**
 * The first out-of-order block covering any night of [from, to), or null.
 *
 * The block itself rather than a boolean, because the tile has to say *why* — "out of
 * order 6th to 7th, burst pipe" is something the desk can act on.
 * Mirrors services/availability.py::blocking_out_of_order. The API writes these blocks
 * with the keys `from` and `to` (routers/rooms.py::mark_out_of_order).
 */
export function blockingOutOfOrder(room, from, to) {
  for (const block of room?.out_of_order || []) {
    if (rangesOverlap(from, to, block.from, block.to)) return block;
  }
  return null;
}

/**
 * The live booking already holding this physical door across [from, to), or null.
 *
 * Not the same question as "how many rooms of this type are left": a type with two rooms
 * free still cannot put two guests behind one door.
 * Mirrors services/availability.py::booking_holding_room.
 */
export function bookingHoldingRoom(roomId, from, to, bookings, excludeBookingId = null) {
  for (const b of bookings || []) {
    if (b.assigned_room_id !== roomId) continue;
    if (excludeBookingId !== null && b.id === excludeBookingId) continue;
    if (!CONSUMING_STATUSES.includes(b.status)) continue;
    if (rangesOverlap(from, to, b.check_in, b.check_out)) return b;
  }
  return null;
}

/** The day after a `YYYY-MM-DD`, as a `YYYY-MM-DD`. */
export function nextDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}

/**
 * Room numbers in the order a building has them: 2 before 10, 101 before 101A.
 *
 * The same comparator the table used, kept so the grid does not silently renumber a
 * hotel that pads its rooms or suffixes them.
 */
export function compareRoomNumbers(a, b) {
  return String(a.number).localeCompare(String(b.number), undefined, { numeric: true });
}

/**
 * The rooms as floors, in building order, each floor's rooms in number order.
 *
 * The floor is whatever was typed into the room record — it is an optional free-text
 * field in `models/hotel.py::RoomIn` — and it is never guessed from the number. A hotel
 * whose 204 is on the ground floor exists, and a grid that quietly moved it upstairs
 * would be lying about the building rather than drawing it. Rooms with no floor recorded
 * are grouped together and put last, labelled as what they are, so they stay visible and
 * fixable instead of disappearing.
 */
export function groupRoomsByFloor(rooms) {
  const byFloor = new Map();
  for (const room of rooms || []) {
    const floor = room.floor != null && String(room.floor).trim() !== ""
      ? String(room.floor).trim()
      : null;
    if (!byFloor.has(floor)) byFloor.set(floor, []);
    byFloor.get(floor).push(room);
  }

  const floors = [...byFloor.keys()];
  const placed = floors.filter((f) => f !== null).sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true }),
  );
  if (byFloor.has(null)) placed.push(null);

  return placed.map((floor) => ({
    floor,
    label: floor === null ? "No floor recorded" : `Floor ${floor}`,
    rooms: [...byFloor.get(floor)].sort(compareRoomNumbers),
  }));
}

/** The room type record for a room, from a list of types. */
export function typeOf(room, types) {
  return (types || []).find((t) => t.id === room.room_type_id) || null;
}

// ---------------------------------------------------------------------------
// The three questions. Each returns the same shape, so one tile renders all of
// them: a state key the component maps to colour, icon and border; a short label
// that says the state in words for anyone the colour does not reach; and up to two
// lines of detail.
// ---------------------------------------------------------------------------

const tile = (state, label, extra = {}) => ({ state, label, note: null, lines: [], ...extra });

/**
 * ROOMS — the inventory. What exists, and whether it can be sold at all.
 *
 * `asOf` is the day the answer is about, and defaults to nothing: with no date, an
 * out-of-order block is still counted, because on the inventory screen the question is
 * "is anything wrong with this room", not "is it free tonight". Passing a date narrows it
 * to blocks covering that night.
 */
export function inventoryState(room, { asOf = null } = {}) {
  if (room.active === false)
    return tile("inactive", "Inactive", {
      note: "Not counted towards availability",
    });

  const blocks = room.out_of_order || [];
  const block = asOf
    ? blockingOutOfOrder(room, asOf, nextDay(asOf))
    : blocks[0] || null;
  if (block)
    return tile("blocked", "Out of order", {
      note: block.reason || null,
      lines: [`${block.from} → ${block.to}`],
    });

  return tile("active", "Active");
}

/**
 * NEW BOOKING — free or taken for the searched window.
 *
 * Two separate refusals live here and they are not the same thing:
 *
 * `taken` / `blocked` are about this door. Another live booking holds it across part of
 * the window, or maintenance does. That is the per-room rule, mirrored from the server.
 *
 * `full` is about the type. Bookings that have not been given a room yet still consume
 * their type's inventory, so a physically empty Suite can sit inside a type that is sold
 * out — click it and `POST /bookings` refuses with "No Suite free for these dates". The
 * count is not recomputed here: it is `available` straight off `GET /api/availability`,
 * the server's own answer, keyed by type. Same for a type that cannot be quoted for these
 * dates, or is too small for the party — both come back on that response and both make
 * the room unbookable however empty it is.
 *
 * `availabilityByType` is a Map or plain object of room_type_id → the `/availability` row.
 * A type missing from it is treated as unbookable rather than free: `/availability` only
 * returns active types, so an absent one is a type that has been switched off.
 */
export function windowState(room, { from, to, bookings = [], availabilityByType } = {}) {
  if (room.active === false)
    return tile("inactive", "Inactive", { note: "Not bookable" });

  const block = blockingOutOfOrder(room, from, to);
  if (block)
    return tile("blocked", "Out of order", {
      note: block.reason || null,
      lines: [`${block.from} → ${block.to}`],
    });

  const held = bookingHoldingRoom(room.id, from, to, bookings);
  if (held)
    return tile("taken", "Taken", {
      note: held.guest?.name || held.reference || null,
      lines: [`${held.check_in} → ${held.check_out}`],
    });

  const row = availabilityByType instanceof Map
    ? availabilityByType.get(room.room_type_id)
    : (availabilityByType || {})[room.room_type_id];

  if (!row)
    return tile("full", "Not bookable", { note: "This type is not on sale" });
  if (row.unpriced_dates?.length)
    return tile("full", "No rate", {
      note: `No rate set for ${row.unpriced_dates.join(", ")}`,
    });
  if (row.fits_party === false)
    return tile("full", "Too small", { note: "Too small for this party" });
  if (!(row.available > 0))
    return tile("full", "Type full", {
      note: "The door is empty but this type is sold out for these dates",
    });

  return tile("free", "Free");
}

/**
 * BOOKINGS — who is in which room on a given night.
 *
 * One night, [date, date+1), because "who is in 204" is a question about tonight and a
 * room that changes hands at noon is two different answers over a week. The booking is
 * carried back on the tile so the guest's name and their dates can be read off the door.
 */
export function occupancyState(room, { date, bookings = [] } = {}) {
  const from = date;
  const to = nextDay(date);

  const held = bookingHoldingRoom(room.id, from, to, bookings);
  if (held)
    return tile("occupied", held.status === "checked_in" ? "In house" : "Reserved", {
      note: held.guest?.name || "Guest",
      lines: [`${held.check_in} → ${held.check_out}`, held.reference],
      booking: held,
    });

  if (room.active === false)
    return tile("inactive", "Inactive", { note: "Not counted towards availability" });

  const block = blockingOutOfOrder(room, from, to);
  if (block)
    return tile("blocked", "Out of order", {
      note: block.reason || null,
      lines: [`${block.from} → ${block.to}`],
    });

  return tile("vacant", "Vacant");
}

/**
 * A one-line count of each state, for the legend above a grid.
 *
 * Returned in the order the states are listed rather than the order they happen to
 * appear, so the legend does not reshuffle itself as the night goes on.
 */
export function countStates(entries, order) {
  const counts = new Map(order.map((s) => [s, 0]));
  for (const e of entries) if (counts.has(e.state)) counts.set(e.state, counts.get(e.state) + 1);
  return order.map((state) => ({ state, count: counts.get(state) }));
}
