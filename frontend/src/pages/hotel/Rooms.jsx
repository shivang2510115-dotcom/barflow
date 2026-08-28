import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";

// What POST /room-types is sent when nothing has been typed. The numbers match the
// server's own defaults in models/hotel.py::RoomTypeIn, so an owner who accepts the form
// as it stands gets exactly what the API would have given them anyway.
const BLANK_TYPE = {
  name: "",
  code: "",
  base_occupancy: "2",
  max_occupancy: "3",
  max_extra_beds: "1",
};

/**
 * What is wrong with this room type, in the owner's words, or null.
 *
 * The occupancy rule is the server's — `rooms.py::_validate_occupancy` answers 400 to a
 * max below the base — and it is repeated here rather than left to the round trip
 * because it is the mistake people actually make: "sleeps 2, up to 1" is a typo, not an
 * intention, and being told so while the field is still under the cursor is the
 * difference between fixing it and wondering what happened.
 *
 * Nothing here is a substitute for the server check. It is the same rule said sooner.
 */
function typeProblem(draft) {
  const base = Number(draft.base_occupancy);
  const max = Number(draft.max_occupancy);
  const beds = Number(draft.max_extra_beds);

  if (!draft.name.trim()) return "A room type needs a name";
  // Not cosmetic: the code is what the rate sheet, the calendar and the housekeeping
  // sheet label this type by, and a blank one leaves every one of them with a gap.
  if (!draft.code.trim()) return "A room type needs a short code — DLX, STD, SUITE";
  if (!Number.isInteger(base) || base < 1) return "Base occupancy is a whole number, at least 1";
  if (!Number.isInteger(max) || max < 1) return "Max occupancy is a whole number, at least 1";
  if (max < base)
    return `Max occupancy cannot be below base occupancy — ${max} is fewer than the ${base} this type sleeps as standard`;
  if (!Number.isInteger(beds) || beds < 0) return "Extra beds cannot be a negative number";
  return null;
}

// The three occupancy fields, drawn identically wherever they appear. Both the create
// card and the edit panel use this: two copies of five inputs is how the two forms end
// up disagreeing about which of them is required.
function OccupancyFields({ value, onChange, prefix }) {
  return (
    <>
      {[
        ["base_occupancy", "Sleeps", "the standard party this room is priced for"],
        ["max_occupancy", "Up to", "the most people it will take at all"],
        ["max_extra_beds", "Extra beds", "on top of the beds already in it"],
      ].map(([k, label, hint]) => (
        <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
          {label}
          <input
            type="number"
            min={k === "max_extra_beds" ? "0" : "1"}
            step="1"
            data-testid={`${prefix}-${k}`}
            value={value[k]}
            onChange={(e) => onChange({ ...value, [k]: e.target.value })}
            className="block mt-2 w-24 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
          />
          <span className="block mt-1 text-[10px] normal-case tracking-normal text-stone-600 max-w-[9rem]">
            {hint}
          </span>
        </label>
      ))}
    </>
  );
}

export default function Rooms() {
  // Configuration is admin-only on the server: `rooms.py` guards every write with
  // require_configuration("hotel"), so a manager or a receptionist pressing any of the
  // buttons below would get a 403 saying so. They still see the list — the front desk
  // needs to know what rooms exist — but are never shown a control that can only fail.
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [types, setTypes] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [creating, setCreating] = useState(BLANK_TYPE);
  const [editingType, setEditingType] = useState(null); // the whole record, not a subset

  const load = useCallback(
    () =>
      Promise.all([api.get("/room-types"), api.get("/rooms")])
        .then(([t, r]) => {
          setTypes(t.data);
          setRooms(r.data);
        })
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
        .finally(() => setLoading(false)),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Every write goes through here: one place that reloads on success and turns an axios
  // error into the server's own sentence. Client-side checks run *before* it, so the
  // catch below only ever handles a real API response.
  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const createType = () => {
    const problem = typeProblem(creating);
    if (problem) {
      toast.error(problem);
      return;
    }
    run(async () => {
      await api.post("/room-types", {
        name: creating.name.trim(),
        code: creating.code.trim().toUpperCase(),
        base_occupancy: Number(creating.base_occupancy),
        max_occupancy: Number(creating.max_occupancy),
        max_extra_beds: Number(creating.max_extra_beds),
      });
      setCreating(BLANK_TYPE);
      toast.success("Room type added — now give it some rooms");
    });
  };

  const saveType = () => {
    const problem = typeProblem(editingType);
    if (problem) {
      toast.error(problem);
      return;
    }
    run(async () => {
      // PUT /room-types/{id} takes a whole RoomTypeIn and `$set`s it, so anything left
      // out of the body is reset to the model's default. The untouched record is spread
      // back for exactly that reason: this screen does not edit `description`,
      // `amenities` or `images`, and sending only the fields it does edit would silently
      // wipe the ones it does not.
      const { id, ...rest } = editingType;
      await api.put(`/room-types/${id}`, {
        ...rest,
        name: rest.name.trim(),
        code: rest.code.trim().toUpperCase(),
        base_occupancy: Number(rest.base_occupancy),
        max_occupancy: Number(rest.max_occupancy),
        max_extra_beds: Number(rest.max_extra_beds),
      });
      setEditingType(null);
      toast.success("Room type saved");
    });
  };

  if (loading) return <div className="p-6 md:p-10 text-stone-400">Loading rooms…</div>;

  const empty = types.length === 0;

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Rooms
      </h1>

      {isAdmin && (
        <div
          className={`rounded p-5 mb-8 max-w-4xl border ${
            empty ? "border-orange-500/40 bg-stone-900" : "border-stone-800 bg-stone-900"
          }`}
        >
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            {empty ? "Start here — your first room type" : "Add a room type"}
          </h2>
          {/* The chain, said once and only while it is still broken. A hotel with no
              room type cannot have rooms, cannot have a rate, and cannot be quoted a
              booking — which is why an empty screen leads with the form rather than
              with a sentence describing a button that is not there. */}
          {empty && (
            <p className="text-sm text-stone-400 mb-5 max-w-2xl">
              A room type is the thing guests book — Deluxe, Standard, Suite — and
              everything else hangs off it. Add one here, put rooms in it, then set a rate
              on the Rates screen. Until all three exist, no booking can be priced.
            </p>
          )}
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Name
              <input
                data-testid="room-type-name"
                value={creating.name}
                onChange={(e) => setCreating({ ...creating, name: e.target.value })}
                placeholder="Deluxe Double"
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Code
              <input
                data-testid="room-type-code"
                value={creating.code}
                onChange={(e) => setCreating({ ...creating, code: e.target.value })}
                placeholder="DLX"
                className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 font-mono focus:border-orange-500 outline-none"
              />
            </label>
            <OccupancyFields value={creating} onChange={setCreating} prefix="room-type" />
            <button
              onClick={createType}
              disabled={busy}
              data-testid="room-type-create"
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : empty ? "Create it" : "Add type"}
            </button>
          </div>
          <p className="text-xs text-stone-500 mt-4 max-w-2xl">
            Sleeps is what the rate is quoted for; anyone above it is charged as an extra
            adult or child. Up to is the hard ceiling — a booking for more people than
            that is refused. Max occupancy cannot be below what the type sleeps as
            standard.
          </p>
        </div>
      )}

      {empty ? (
        <p className="text-stone-400 max-w-2xl">
          {isAdmin
            ? "No room types yet — the form above is the whole of what is needed to make one."
            : "No room types yet. Setting them up is the owner's to do: rooms, room types and rates can only be changed by an administrator, so ask them to add one before you try to take a booking."}
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {types.map((t) => {
            const mine = rooms.filter((r) => r.room_type_id === t.id);
            return (
              <div
                key={t.id}
                data-testid={`room-type-${t.id}`}
                className={`border border-stone-800 bg-stone-900 rounded p-5 ${
                  t.active === false ? "opacity-60" : ""
                }`}
              >
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="text-lg font-semibold">{t.name}</h3>
                  <span className="text-xs font-mono text-stone-500">{t.code}</span>
                </div>
                <p className="text-sm text-stone-400 mt-2">
                  Sleeps {t.base_occupancy}, up to {t.max_occupancy}
                  {t.max_extra_beds
                    ? ` plus ${t.max_extra_beds} extra bed${t.max_extra_beds === 1 ? "" : "s"}`
                    : ""}
                </p>
                {t.active === false && (
                  <p className="text-[10px] tracking-widest uppercase text-stone-500 mt-2">
                    not bookable
                  </p>
                )}
                <p className="text-sm text-orange-400 mt-3 font-mono">
                  {mine.length} room{mine.length === 1 ? "" : "s"}
                </p>

                {isAdmin && (
                  <div className="mt-4 pt-4 border-t border-stone-800 flex flex-wrap gap-3">
                    <button
                      onClick={() => setEditingType({ ...t })}
                      disabled={busy}
                      data-testid={`room-type-edit-${t.id}`}
                      className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400 disabled:opacity-30"
                    >
                      Edit
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {editingType && (
        <div className="mt-8 border border-stone-800 bg-stone-900 rounded p-5 max-w-4xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
            Edit {editingType.name}
          </h3>
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Name
              <input
                data-testid="room-type-edit-name"
                value={editingType.name}
                onChange={(e) => setEditingType({ ...editingType, name: e.target.value })}
                className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-stone-500">
              Code
              <input
                data-testid="room-type-edit-code"
                value={editingType.code}
                onChange={(e) => setEditingType({ ...editingType, code: e.target.value })}
                className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 font-mono focus:border-orange-500 outline-none"
              />
            </label>
            <OccupancyFields
              value={editingType}
              onChange={setEditingType}
              prefix="room-type-edit"
            />
            {/* Deactivating a type takes it out of availability without touching the
                rooms in it — the way to stop selling a floor being refurbished without
                deleting the record every past booking still points at. */}
            <label className="flex items-center gap-2 text-xs tracking-widest uppercase text-stone-500 pb-2">
              <input
                type="checkbox"
                data-testid="room-type-edit-active"
                checked={editingType.active !== false}
                onChange={(e) => setEditingType({ ...editingType, active: e.target.checked })}
                className="accent-orange-500"
              />
              Bookable
            </label>
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={saveType}
              disabled={busy}
              data-testid="room-type-edit-save"
              className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setEditingType(null)}
              disabled={busy}
              className="border border-stone-700 text-stone-400 hover:text-stone-200 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-stone-500 mt-4 max-w-2xl">
            Renaming a type renames it everywhere it is shown; bookings already taken
            against it keep the price they were quoted. Unticking Bookable stops it being
            offered for new dates and leaves its rooms and its history alone.
          </p>
        </div>
      )}
    </div>
  );
}
