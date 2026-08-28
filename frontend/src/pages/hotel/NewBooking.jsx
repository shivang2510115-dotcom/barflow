import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import RoomGrid from "@/components/app/RoomGrid";
import { typeOf, windowState } from "@/lib/roomGrid";
import { toast } from "sonner";

// Check-in/check-out are calendar dates, never instants — toISOString() converts to
// UTC, which for a user east of UTC between midnight and their UTC offset would make
// "today" resolve to yesterday's date. Build the string from local getters instead.
const toLocalISODate = (d) => {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const today = () => toLocalISODate(new Date());
const tomorrow = () => toLocalISODate(new Date(Date.now() + 86400000));

// A quote's identity, for the key and for "which card is selected".
//
// `/availability` answers with one quote per meal plan when the property sells them, and
// a single quote with `meal_plan: null` when it does not — the all-inclusive rate. Both
// arrive in the same `quotes` array, so the screen needs one way to tell quotes apart
// that does not assume a plan exists. Reading `q.meal_plan.id` directly is what breaks
// the moment the plan is null.
const ALL_INCLUSIVE = "all-inclusive";
const quoteKey = (q) => q.meal_plan?.id ?? ALL_INCLUSIVE;

export default function NewBooking() {
  const nav = useNavigate();
  const [form, setForm] = useState({
    check_in: today(),
    check_out: tomorrow(),
    adults: 2,
    children: 0,
  });
  // Everything one search produced, or null. Held as one object rather than four pieces
  // of state so there is no instant where the plan is drawn from this search's rooms and
  // last search's bookings — which is exactly the instant a room would be shown free
  // that is not.
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [choice, setChoice] = useState(null); // { room, room_type, quote }
  const [guest, setGuest] = useState({ name: "", phone: "" });
  const [saving, setSaving] = useState(false);

  const set = (k, v) => {
    setForm((f) => ({ ...f, [k]: v }));
    // Any change to the search params invalidates prior results/quotes — they were
    // priced for the old values, and the plan was drawn for the old window — so force a
    // fresh search before booking again.
    setResults(null);
    setChoice(null);
  };

  /**
   * The three answers the plan needs, all for the same window, all from the server.
   *
   * `/availability` is the type-level rule, and it is read rather than reimplemented: a
   * booking with no room assigned to it still consumes its type's inventory, so a
   * physically empty Suite can sit inside a type that is sold out. `/rooms` is the
   * building. `/bookings` narrowed to this window is who already holds which door.
   * lib/roomGrid.js puts the three together; the server decides at the end.
   */
  const search = async () => {
    if (form.check_out <= form.check_in) {
      toast.error("Check-out must be after check-in");
      return;
    }
    setSearching(true);
    setChoice(null);
    try {
      const [availability, rooms, bookings] = await Promise.all([
        api.get("/availability", { params: form }),
        api.get("/rooms"),
        api.get("/bookings", { params: { start: form.check_in, end: form.check_out } }),
      ]);
      setResults({
        window: { check_in: form.check_in, check_out: form.check_out },
        rows: availability.data,
        types: availability.data.map((r) => r.room_type),
        byType: new Map(availability.data.map((r) => [r.room_type.id, r])),
        rooms: rooms.data,
        bookings: bookings.data,
      });
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSearching(false);
    }
  };

  /**
   * A free door, chosen.
   *
   * Picking the room picks the room type — a door is only ever one type — so the party
   * is quoted against what they will actually sleep in rather than against a category
   * they then have to be matched to. The cheapest plan is pre-selected so the panel is
   * usable on arrival; every plan is still on the screen to switch to.
   */
  const chooseRoom = (room) => {
    const row = results.byType.get(room.room_type_id);
    if (!row?.quotes?.length) return;
    const cheapest = [...row.quotes].sort((a, b) => Number(a.total) - Number(b.total))[0];
    setChoice({ room, room_type: row.room_type, quote: cheapest });
  };

  const book = async () => {
    if (!guest.name.trim() || !guest.phone.trim()) {
      toast.error("Guest name and phone are required");
      return;
    }
    if (form.check_out <= form.check_in) {
      toast.error("Check-out must be after check-in");
      return;
    }
    setSaving(true);
    try {
      // Reuse the guest if the phone is already known, rather than failing on 409.
      let guestId;
      try {
        const created = await api.post("/guests", guest);
        guestId = created.data.id;
      } catch (e) {
        const existing = e.response?.data?.detail?.guest;
        if (e.response?.status === 409 && existing) {
          guestId = existing.id;
          toast.info(`Existing guest matched: ${existing.name}`);
        } else {
          throw e;
        }
      }

      const { data } = await api.post("/bookings", {
        guest_id: guestId,
        room_type_id: choice.room_type.id,
        // Null when this property sells one all-inclusive rate. The API takes the
        // booking without a plan in that case, and refuses one *without* a plan when the
        // property does quote per plan — so the desk cannot book against a model the
        // hotel does not run, whichever way the setting is set.
        meal_plan_id: choice.quote.meal_plan?.id ?? null,
        check_in: form.check_in,
        check_out: form.check_out,
        adults: Number(form.adults),
        children: Number(form.children),
      });

      /**
       * The door, given to the booking that now exists.
       *
       * Second call, and it has to be: a room is assigned to a booking id, and there is
       * no booking id until `POST /bookings` has answered. `PUT /bookings/{id}/room` is
       * the only thing that decides whether this room may be held — it re-reads the
       * blocks and the live bookings immediately before it writes and refuses with the
       * reference that is in the way. The plan drawn above is a convenience that stops
       * the desk clicking a visibly-taken door; this is the rule.
       *
       * A refusal here is not a failed booking. The reservation is made and priced; only
       * the room did not stick, because something took it in the seconds since the search.
       * Saying so and going to the booking anyway is the honest outcome — the desk lands
       * on the screen that can assign another room, holding the server's own sentence.
       * Swallowing it would send them away believing the guest is in 204.
       */
      let assigned = null;
      try {
        const res = await api.put(`/bookings/${data.id}/room`, { room_id: choice.room.id });
        assigned = res.data.room;
      } catch (e) {
        toast.error(
          `${data.reference} is booked, but room ${choice.room.number} could not be held: ` +
            formatApiErrorDetail(e.response?.data?.detail),
        );
      }

      if (assigned) toast.success(`Booked — ${data.reference} · room ${assigned.number}`);
      nav(`/app/hotel/bookings/${data.id}`);
    } catch (e) {
      // 409 = no room left for these dates by the time of write, 422 = a night in the
      // stay has no rate defined. Both come back as { message, ... } — surface it plainly
      // rather than letting the desk think nothing happened.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        New booking
      </h1>

      <div className="flex flex-wrap gap-4 items-end mb-8">
        {[
          ["check_in", "Check in", "date"],
          ["check_out", "Check out", "date"],
          ["adults", "Adults", "number"],
          ["children", "Children", "number"],
        ].map(([key, label, type]) => (
          <label key={key} className="text-xs tracking-widest uppercase text-stone-500">
            {label}
            <input
              type={type}
              min={type === "number" ? 0 : undefined}
              value={form[key]}
              onChange={(e) => set(key, e.target.value)}
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
        ))}
        <button
          onClick={search}
          disabled={searching}
          className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {results && results.rows.length === 0 && (
        <p className="text-stone-400">No room types are set up yet.</p>
      )}

      {results && results.rows.length > 0 && (
        <>
          {/* What each type costs and how many of it are left, on one line each. The
              plan below says which doors; this says what they are worth, so the desk
              can answer "anything cheaper?" without leaving the screen. */}
          <div className="mb-8 grid gap-2 md:grid-cols-2 lg:grid-cols-3">
            {results.rows.map((row) => (
              <div
                key={row.room_type.id}
                className="border border-stone-800 bg-stone-900 rounded px-4 py-3"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold">{row.room_type.name}</span>
                  <span
                    className={`font-mono text-xs ${
                      row.available > 0 ? "text-orange-400" : "text-stone-500"
                    }`}
                  >
                    {row.available} free
                  </span>
                </div>
                {row.unpriced_dates ? (
                  <p className="text-xs text-red-400 mt-2">
                    No rate set for {row.unpriced_dates.join(", ")} — add one under Rates.
                  </p>
                ) : !row.fits_party ? (
                  <p className="text-xs text-stone-500 mt-2">Too small for this party.</p>
                ) : (
                  <p className="text-xs text-stone-400 mt-2 flex flex-wrap gap-x-3">
                    {row.quotes.map((q) => (
                      <span key={quoteKey(q)}>
                        <span className="text-stone-500 tracking-widest uppercase text-[10px]">
                          {q.meal_plan ? q.meal_plan.code : "All-in"}
                        </span>{" "}
                        {currency(q.total)}
                      </span>
                    ))}
                  </p>
                )}
              </div>
            ))}
          </div>

          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Free for {results.window.check_in} → {results.window.check_out}
          </h2>
          <RoomGrid
            rooms={results.rooms}
            types={results.types}
            stateOf={(r) =>
              windowState(r, {
                from: results.window.check_in,
                to: results.window.check_out,
                bookings: results.bookings,
                availabilityByType: results.byType,
              })
            }
            legendStates={["free", "taken", "blocked", "full", "inactive"]}
            onSelect={(room) => chooseRoom(room)}
            selectable={(room, view) => view.state === "free"}
            selectedId={choice?.room?.id ?? null}
            empty="No rooms are set up yet — add some under Rooms."
          />
          <p className="text-xs text-stone-500 mt-4 max-w-2xl">
            A free door is a door nothing holds for these dates. The room is only really
            yours once the booking is written: the server re-checks it at that moment and
            says which booking took it if one did.
          </p>
        </>
      )}

      {choice && (
        <div className="mt-10 border border-stone-800 bg-stone-900 rounded p-5 max-w-xl">
          <h3 className="text-lg font-semibold mb-1">
            Room <span className="font-mono">{choice.room.number}</span> ·{" "}
            {choice.room_type.name}
          </h3>
          <p className="text-sm text-stone-400 mb-4">
            {typeOf(choice.room, results.types)?.code
              ? `${typeOf(choice.room, results.types).code} · `
              : ""}
            {choice.room.floor ? `Floor ${choice.room.floor} · ` : ""}
            {form.check_in} → {form.check_out}
          </p>

          {/* One card per meal plan, or one all-inclusive card. Unchanged in what it
              does; it now belongs to the room that was picked rather than standing in
              for the choice of room. */}
          <div className="grid gap-2 md:grid-cols-2 mb-6">
            {(results.byType.get(choice.room.room_type_id)?.quotes || []).map((q) => (
              <button
                key={quoteKey(q)}
                onClick={() => setChoice({ ...choice, quote: q })}
                className={`text-left border rounded p-3 transition-colors ${
                  quoteKey(choice.quote) === quoteKey(q)
                    ? "border-orange-500 bg-stone-800"
                    : "border-stone-800 hover:border-stone-600"
                }`}
              >
                <div className="text-xs tracking-widest uppercase text-stone-500">
                  {q.meal_plan ? `${q.meal_plan.code} · ${q.meal_plan.name}` : "All inclusive"}
                </div>
                <div className="text-xl font-semibold mt-1">{currency(q.total)}</div>
                <div className="text-xs text-stone-500 mt-1">
                  {q.nights.length} night{q.nights.length === 1 ? "" : "s"} incl.{" "}
                  {currency(q.tax_total)} tax
                </div>
              </button>
            ))}
          </div>

          <div className="flex gap-4 flex-wrap">
            {[["name", "Guest name"], ["phone", "Phone"]].map(([k, label]) => (
              <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
                {label}
                <input
                  value={guest[k]}
                  onChange={(e) => setGuest((g) => ({ ...g, [k]: e.target.value }))}
                  className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
                />
              </label>
            ))}
          </div>

          <button
            onClick={book}
            disabled={saving}
            className="mt-6 bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-8 py-2 text-sm tracking-widest uppercase"
          >
            {saving ? "Booking…" : `Book room ${choice.room.number}`}
          </button>
        </div>
      )}
    </div>
  );
}
