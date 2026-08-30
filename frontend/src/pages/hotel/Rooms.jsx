import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import RoomGrid from "@/components/app/RoomGrid";
import { inventoryState, typeOf } from "@/lib/roomGrid";
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

// What the "add rooms" panel holds. `mode` is which of the two ways is open: one room
// typed out, or a block of them numbered in sequence.
const BLANK_ROOM = {
  mode: "one",
  number: "",
  prefix: "",
  first: "",
  last: "",
  floor: "",
  block: "",
};

// A ceiling on one bulk submission. Each number is its own POST — the API has no batch
// endpoint and this screen does not invent one — so a fat-fingered "1 to 99999" would be
// a hundred thousand requests before anybody could stop it. Two hundred is more rooms
// than any single floor of any hotel this serves.
const MAX_RANGE = 200;

/**
 * The room numbers a range describes, or why it describes none.
 *
 * Deliberately dumb and predictable: two whole numbers and an optional prefix, so that
 * what the preview shows is exactly what the loop will send. Leading zeros in the first
 * number set the width — 001 to 012 gives 001…012, not 1…12 — because a hotel that pads
 * its numbers pads all of them.
 */
function expandRange({ prefix, first, last }) {
  if (!/^\d+$/.test(first) || !/^\d+$/.test(last))
    return {
      numbers: [],
      problem: "A range runs between two whole numbers — 101 to 110. Anything else goes in the prefix.",
    };

  const from = Number(first);
  const to = Number(last);
  if (to < from)
    return { numbers: [], problem: `${last} is below ${first} — a range counts upwards` };

  const count = to - from + 1;
  if (count > MAX_RANGE)
    return {
      numbers: [],
      problem: `${first} to ${last} is ${count} rooms; ${MAX_RANGE} at a time is the most this will do in one go`,
    };

  const width = first.startsWith("0") ? first.length : 0;
  const numbers = [];
  for (let n = from; n <= to; n += 1) numbers.push(prefix.trim() + String(n).padStart(width, "0"));
  return { numbers, problem: null };
}

// The preview line. Long ranges are elided in the middle rather than truncated at the
// end, so both ends of what is about to be created stay visible.
function listNumbers(numbers) {
  if (numbers.length <= 12) return numbers.join(", ");
  return `${numbers.slice(0, 5).join(", ")} … ${numbers.slice(-3).join(", ")}`;
}

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
        <label key={k} className="text-xs tracking-widest uppercase text-faint">
          {label}
          <input
            type="number"
            min={k === "max_extra_beds" ? "0" : "1"}
            step="1"
            data-testid={`${prefix}-${k}`}
            value={value[k]}
            onChange={(e) => onChange({ ...value, [k]: e.target.value })}
            className="block mt-2 w-24 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
          />
          <span className="block mt-1 text-[10px] normal-case tracking-normal text-faint max-w-[9rem]">
            {hint}
          </span>
        </label>
      ))}
    </>
  );
}

// The floor/block pair, shared by both ways of adding. Optional in the model and
// optional here; a hotel that does not think in blocks leaves them empty.
function PlacementFields({ draft, set }) {
  return (
    <>
      {[["floor", "Floor", "2"], ["block", "Block", "A"]].map(([k, label, ph]) => (
        <label key={k} className="text-xs tracking-widest uppercase text-faint">
          {label}
          <input
            value={draft[k]}
            data-testid={`room-${k}`}
            onChange={(e) => set({ [k]: e.target.value })}
            placeholder={ph}
            className="block mt-2 w-20 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
          />
        </label>
      ))}
    </>
  );
}

/**
 * Adding rooms to one type: one at a time, or a numbered block at once.
 *
 * The block exists because a hotel numbers its rooms 101 to 110 and typing ten forms is
 * not a thing anyone will do. It never fires blind: the exact list it is about to create
 * is on the screen first, with any number that already exists called out before the
 * button is pressed rather than discovered as a 409 halfway through.
 */
function AddRoomsPanel({ draft, setDraft, busy, existing, onAddOne, onAddRange, result }) {
  const set = (patch) => setDraft({ ...draft, ...patch });

  const asked = draft.first !== "" && draft.last !== "";
  const range = asked ? expandRange(draft) : { numbers: [], problem: null };
  // Checked against the rooms already loaded, so the warning is there while the range is
  // still being typed. Not a substitute for the server's 409 — another tab could take a
  // number between this render and the POST — which is why the loop still reports what
  // came back rather than trusting this.
  const clashes = range.numbers.filter((n) => existing.some((r) => r.number === n));

  return (
    <div className="mt-4 pt-4 border-t border-hairline">
      <div className="flex gap-2 mb-4">
        {[["one", "One room"], ["range", "A range"]].map(([mode, label]) => (
          <button
            key={mode}
            type="button"
            onClick={() => set({ mode })}
            data-testid={`room-mode-${mode}`}
            className={`text-[10px] tracking-widest uppercase border rounded-full px-3 py-1 ${
              draft.mode === mode
                ? "border-brass text-brass"
                : "border-hairline-strong text-faint hover:border-hairline-strong"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {draft.mode === "one" ? (
        <div className="flex flex-wrap gap-4 items-end">
          <label className="text-xs tracking-widest uppercase text-faint">
            Number
            <input
              value={draft.number}
              data-testid="room-number"
              onChange={(e) => set({ number: e.target.value })}
              placeholder="101"
              className="block mt-2 w-28 bg-transparent border-b border-hairline-strong text-ink py-1 font-mono focus:border-brass outline-none"
            />
          </label>
          <PlacementFields draft={draft} set={set} />
          <button
            onClick={onAddOne}
            disabled={busy}
            data-testid="room-add-one"
            className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            {busy ? "Adding…" : "Add room"}
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-faint">
              Prefix
              <input
                value={draft.prefix}
                data-testid="room-prefix"
                onChange={(e) => set({ prefix: e.target.value })}
                placeholder="optional"
                className="block mt-2 w-24 bg-transparent border-b border-hairline-strong text-ink py-1 font-mono focus:border-brass outline-none"
              />
            </label>
            {[["first", "From"], ["last", "To"]].map(([k, label]) => (
              <label key={k} className="text-xs tracking-widest uppercase text-faint">
                {label}
                <input
                  value={draft[k]}
                  data-testid={`room-${k}`}
                  onChange={(e) => set({ [k]: e.target.value })}
                  placeholder={k === "first" ? "101" : "110"}
                  className="block mt-2 w-24 bg-transparent border-b border-hairline-strong text-ink py-1 font-mono focus:border-brass outline-none"
                />
              </label>
            ))}
            <PlacementFields draft={draft} set={set} />
            <button
              onClick={onAddRange}
              disabled={busy || !!range.problem || range.numbers.length === 0}
              data-testid="room-add-range"
              className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy
                ? "Adding…"
                : range.numbers.length
                  ? `Create ${range.numbers.length}`
                  : "Create"}
            </button>
          </div>

          {/* Said before it happens, not after. */}
          <div className="mt-4 text-xs" data-testid="room-range-preview">
            {range.problem ? (
              <p className="text-red-300">{range.problem}</p>
            ) : range.numbers.length ? (
              <>
                <p className="text-muted2">
                  Will create {range.numbers.length} room
                  {range.numbers.length === 1 ? "" : "s"}:{" "}
                  <span className="font-mono text-muted2">
                    {listNumbers(range.numbers)}
                  </span>
                </p>
                {clashes.length > 0 && (
                  <p className="text-amber-300/90 mt-1">
                    {clashes.length} of these already exist and will be refused:{" "}
                    <span className="font-mono">{listNumbers(clashes)}</span>. The rest
                    will still be created.
                  </p>
                )}
              </>
            ) : (
              <p className="text-faint">
                Two numbers and the rooms between them, inclusive — 101 to 110 makes ten
                rooms. Leading zeros are kept, so 001 to 012 numbers them 001…012.
              </p>
            )}
          </div>
        </>
      )}

      {/* What the last block actually did. A partial run is reported as a partial run:
          the count that landed, and every number that did not with the server's own
          reason for it. */}
      {result && (
        <div
          data-testid="room-bulk-result"
          className={`mt-4 rounded p-4 border text-xs ${
            result.failed.length
              ? "border-red-500/40 bg-red-950/20"
              : "border-hairline bg-ground"
          }`}
        >
          <p className={result.failed.length ? "text-red-200" : "text-muted2"}>
            Created {result.made} of {result.asked}.
          </p>
          {result.failed.length > 0 && (
            <ul className="mt-2 space-y-1 text-red-300/90">
              {result.failed.map((f) => (
                <li key={f.number}>
                  <span className="font-mono">{f.number}</span> — {f.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * One door, opened.
 *
 * The table carried its two links on every row; the plan carries none, because a tile has
 * to be readable at a glance from across the desk and a row of tiny words on each of
 * twenty-three of them is not. Clicking a door opens this instead — everything the row
 * said, plus the things it could not fit: the type, the maintenance blocks in full, and
 * an edit, which the table never had at all even though `PUT /rooms/{id}` has always
 * taken one. A room typed onto the wrong floor could previously only be deleted and
 * retyped.
 */
function RoomPanel({ room, type, types, edit, setEdit, busy, onSave, onClose, onToggle, onDelete }) {
  const blocks = room.out_of_order || [];

  return (
    <div
      data-testid="room-panel"
      className="mt-6 border border-brass/40 bg-surface rounded p-5 max-w-3xl"
    >
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-2xl font-mono tabular-nums">{room.number}</h3>
        <button
          onClick={onClose}
          className="text-[10px] tracking-widest uppercase text-faint hover:text-muted2"
        >
          Close
        </button>
      </div>
      <p className="text-sm text-muted2 mt-1">
        {type ? `${type.name} · ${type.code}` : "Type not found"}
        {room.floor ? ` · floor ${room.floor}` : " · no floor recorded"}
        {room.block ? ` · block ${room.block}` : ""}
        {room.active === false ? " · inactive" : ""}
      </p>

      {/* The blocks in full. The table could only say "2 blocks", which tells the owner
          something is wrong with the room and nothing about what or when. */}
      {blocks.length > 0 && (
        <ul className="mt-3 text-xs text-red-300/90 space-y-1" data-testid="room-panel-blocks">
          {blocks.map((b, i) => (
            <li key={`${b.from}-${b.to}-${i}`}>
              <span className="font-mono">
                {b.from} → {b.to}
              </span>
              {b.reason ? ` — ${b.reason}` : ""}
            </li>
          ))}
        </ul>
      )}

      {edit ? (
        <div className="mt-5 pt-5 border-t border-hairline">
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-faint">
              Number
              <input
                value={edit.number}
                data-testid="room-edit-number"
                onChange={(e) => setEdit({ ...edit, number: e.target.value })}
                className="block mt-2 w-28 bg-transparent border-b border-hairline-strong text-ink py-1 font-mono focus:border-brass outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-faint">
              Type
              <select
                value={edit.room_type_id}
                data-testid="room-edit-type"
                onChange={(e) => setEdit({ ...edit, room_type_id: e.target.value })}
                className="block mt-2 bg-ground border border-hairline-strong text-ink py-1 px-2 rounded"
              >
                {types.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            {[["floor", "Floor"], ["block", "Block"]].map(([k, label]) => (
              <label key={k} className="text-xs tracking-widest uppercase text-faint">
                {label}
                <input
                  value={edit[k] ?? ""}
                  data-testid={`room-edit-${k}`}
                  onChange={(e) => setEdit({ ...edit, [k]: e.target.value })}
                  className="block mt-2 w-20 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
                />
              </label>
            ))}
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={onSave}
              disabled={busy}
              data-testid="room-edit-save"
              className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : "Save room"}
            </button>
            <button
              onClick={() => setEdit(null)}
              disabled={busy}
              className="border border-hairline-strong text-muted2 hover:text-ink disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-faint mt-4 max-w-xl">
            Moving a room to another type changes what it is sold as from now on. Bookings
            already taken keep the price they were quoted, and a live booking assigned to
            this room is not moved with it — check the plan for these dates first.
          </p>
        </div>
      ) : (
        <div className="mt-5 pt-5 border-t border-hairline flex flex-wrap gap-4">
          <button
            onClick={() => setEdit({ ...room })}
            disabled={busy}
            data-testid="room-panel-edit"
            className="text-[10px] tracking-widest uppercase text-brass hover:text-brass disabled:opacity-30"
          >
            Edit
          </button>
          <button
            onClick={onToggle}
            disabled={busy}
            data-testid={`room-toggle-${room.id}`}
            className="text-[10px] tracking-widest uppercase text-faint hover:text-brass disabled:opacity-30"
          >
            {room.active === false ? "Reactivate" : "Deactivate"}
          </button>
          <button
            onClick={onDelete}
            disabled={busy}
            data-testid={`room-delete-${room.id}`}
            className="text-[10px] tracking-widest uppercase text-faint hover:text-red-400 disabled:opacity-30"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

// What the panel says, per action. Kept together so the three read as one voice and
// the destructive ones cannot drift into being milder than what they do.
const CONFIRM_COPY = {
  "delete-type": (c) => ({
    danger: true,
    title: `Delete ${c.type?.name}?`,
    body: c.rooms?.length
      ? `This type still has ${c.rooms.length} room${c.rooms.length === 1 ? "" : "s"} in it (${c.rooms
          .map((r) => r.number)
          .join(", ")}). The API refuses to delete a type while rooms point at it, so this will come back as a refusal naming them — delete or move the rooms first.`
      : "The type goes for good. Rates set against it stop applying, and any past booking that used it keeps the price it was quoted but loses the name.",
    go: "Delete the type",
  }),
  "delete-room": (c) => ({
    danger: true,
    title: `Delete room ${c.room?.number}?`,
    body: "The room goes for good and stops counting towards availability. A room with a live booking assigned to it is refused — move that booking to another room first. If it is only out of service for a while, deactivate it instead.",
    go: "Delete the room",
  }),
  "toggle-room": (c) => ({
    danger: c.room?.active !== false,
    title:
      c.room?.active === false
        ? `Put room ${c.room?.number} back in service?`
        : `Deactivate room ${c.room?.number}?`,
    body:
      c.room?.active === false
        ? "It counts towards availability again from now on."
        : "It stops counting towards availability, so the type sells one fewer room a night. Nothing already booked into it is touched — this is not a way to empty a room that has a guest in it.",
    go: c.room?.active === false ? "Put it back" : "Deactivate it",
  }),
};

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
  const [addingTo, setAddingTo] = useState(null); // room type id whose panel is open
  const [roomDraft, setRoomDraft] = useState(BLANK_ROOM);
  // What the last bulk submission actually did. Kept on the screen rather than only in a
  // toast: a toast that says "7 of 10" and then disappears leaves the owner with three
  // rooms missing and no record of which three.
  const [bulkResult, setBulkResult] = useState(null);
  // The one thing waiting to be confirmed, and which of the three it is. Inline and
  // two-step, the way cancelling a booking and deactivating a staff member already are —
  // never window.confirm, which cannot say what is about to happen in more than a
  // sentence and cannot be styled to look as final as it is.
  const [confirming, setConfirming] = useState(null);
  // Which door is open on the plan, by id rather than by record: the room list is
  // reloaded after every write, and holding the old object would leave the panel
  // describing a room as it was before the edit that just succeeded.
  const [openRoomId, setOpenRoomId] = useState(null);
  const [roomEdit, setRoomEdit] = useState(null);

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

  const openAdd = (typeId) => {
    setAddingTo(typeId);
    setRoomDraft(BLANK_ROOM);
    setBulkResult(null);
  };

  // Blank means "not recorded", which the model spells `None`. Sent as null rather than
  // "" so a room whose floor nobody typed is not stored as a room on floor empty-string.
  const optional = (v) => (v.trim() ? v.trim() : null);

  const addOneRoom = () => {
    if (!roomDraft.number.trim()) {
      toast.error("A room needs a number");
      return;
    }
    run(async () => {
      await api.post("/rooms", {
        number: roomDraft.number.trim(),
        room_type_id: addingTo,
        floor: optional(roomDraft.floor),
        block: optional(roomDraft.block),
      });
      setRoomDraft({ ...roomDraft, number: "" });
      setBulkResult(null);
      toast.success(`Room ${roomDraft.number.trim()} added`);
    });
  };

  /**
   * The range, one POST at a time, reporting what actually happened.
   *
   * Not routed through `run`, because `run` aborts on the first error and a block of ten
   * rooms where the fifth number is already taken must still create the other nine. Each
   * failure is caught, kept with the number that caused it and the server's own sentence,
   * and shown afterwards. Nothing here reports success for a room that was refused.
   */
  const addRange = async () => {
    const { numbers, problem } = expandRange(roomDraft);
    if (problem) {
      toast.error(problem);
      return;
    }

    setBusy(true);
    setBulkResult(null);
    const failed = [];
    let made = 0;
    try {
      for (const number of numbers) {
        try {
          // Sequential on purpose. Ten parallel POSTs against a mock database that reads,
          // checks and writes without a transaction is how two rooms end up sharing a
          // number despite the duplicate check; in order, each one sees the last.
          // eslint-disable-next-line no-await-in-loop
          await api.post("/rooms", {
            number,
            room_type_id: addingTo,
            floor: optional(roomDraft.floor),
            block: optional(roomDraft.block),
          });
          made += 1;
        } catch (e) {
          failed.push({ number, reason: formatApiErrorDetail(e.response?.data?.detail) });
        }
      }
      await load();
    } finally {
      setBusy(false);
    }

    setBulkResult({ asked: numbers.length, made, failed });
    if (failed.length === 0) {
      setRoomDraft({ ...roomDraft, first: "", last: "" });
      toast.success(`Added ${made} room${made === 1 ? "" : "s"}`);
    } else {
      // The count first, because the question is "how much of this worked" and the
      // answer is not "it failed" — most of it usually landed.
      toast.error(`Added ${made} of ${numbers.length}. ${failed.length} refused — see below.`);
    }
  };

  /**
   * The confirmed half of a destructive action.
   *
   * The two refusals this can meet are the point of the whole panel, and neither is a
   * generic failure: DELETE /room-types/{id} answers 409 "Room type still has rooms" with
   * the numbers, and DELETE /rooms/{id} answers 409 "Room has live bookings assigned to
   * it" with the references. `run` puts the server's own sentence in the toast, and
   * `formatApiErrorDetail` now keeps the list attached to it, so what comes back names
   * exactly what is in the way.
   *
   * The panel stays open on a refusal — closing it would take the reason off the screen
   * along with the question — and closes only when something actually happened.
   */
  const confirmAction = () =>
    run(async () => {
      if (confirming.kind === "delete-type") {
        await api.delete(`/room-types/${confirming.type.id}`);
        // Both panels are addressed to a record that no longer exists. Closed here rather
        // than left to render against a stale copy — an "add rooms" form still open on a
        // deleted type would POST an unknown room_type_id and be answered with a 400.
        if (addingTo === confirming.type.id) setAddingTo(null);
        if (editingType?.id === confirming.type.id) setEditingType(null);
        toast.success(`${confirming.type.name} deleted`);
      } else if (confirming.kind === "delete-room") {
        await api.delete(`/rooms/${confirming.room.id}`);
        // The panel is addressed to a door that no longer exists. Closed here rather than
        // left to render against a room the next load will not return.
        if (openRoomId === confirming.room.id) {
          setOpenRoomId(null);
          setRoomEdit(null);
        }
        toast.success(`Room ${confirming.room.number} deleted`);
      } else {
        // PUT /rooms/{id} takes a whole RoomIn and $sets it, so the record is spread back
        // for the same reason the room type is: this flips `active` and must not quietly
        // clear the floor and block somebody typed. `out_of_order` is dropped rather than
        // sent — it is not part of RoomIn, and maintenance blocks belong to the front
        // desk's own endpoint.
        const { id, out_of_order: _blocks, ...rest } = confirming.room;
        await api.put(`/rooms/${id}`, { ...rest, active: confirming.room.active === false });
        toast.success(
          confirming.room.active === false
            ? `Room ${confirming.room.number} is back in service`
            : `Room ${confirming.room.number} deactivated`,
        );
      }
      setConfirming(null);
    });

  /**
   * The room panel's edit, saved.
   *
   * `PUT /rooms/{id}` takes a whole `RoomIn` and `$set`s it, so the untouched record is
   * spread back for the same reason the room-type edit does it — sending only the changed
   * fields would reset `active` to the model default and quietly put a deactivated room
   * back on sale. `out_of_order` is dropped rather than sent: it is not part of `RoomIn`,
   * and maintenance blocks are the front desk's, not configuration's.
   */
  const saveRoom = () => {
    if (!roomEdit.number.trim()) {
      toast.error("A room needs a number");
      return;
    }
    if (!roomEdit.room_type_id) {
      toast.error("A room belongs to a room type");
      return;
    }
    run(async () => {
      const { id, out_of_order: _blocks, ...rest } = roomEdit;
      await api.put(`/rooms/${id}`, {
        ...rest,
        number: roomEdit.number.trim(),
        floor: optional(roomEdit.floor ?? ""),
        block: optional(roomEdit.block ?? ""),
      });
      setRoomEdit(null);
      toast.success(`Room ${roomEdit.number.trim()} saved`);
    });
  };

  if (loading) return <div className="p-6 md:p-10 text-muted2">Loading rooms…</div>;

  const empty = types.length === 0;
  // Read back off the freshly loaded list, so a deleted room closes its own panel.
  const openRoom = rooms.find((r) => r.id === openRoomId) || null;

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-brass mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Rooms
      </h1>

      {/* The building first. A hotel is floors and doors, and the inventory question —
          what exists, and can it be sold — is answered by looking at it rather than by
          reading twenty-three rows. The room types below still own the configuration:
          this is the same rooms, arranged the way the receptionist already holds them. */}
      {rooms.length > 0 && (
        <section className="mb-10" data-testid="rooms-plan">
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
            The building · {rooms.length} room{rooms.length === 1 ? "" : "s"}
          </h2>
          <RoomGrid
            rooms={rooms}
            types={types}
            stateOf={(r) => inventoryState(r)}
            legendStates={["active", "inactive", "blocked"]}
            // Every action a door has here is configuration, and configuration is
            // admin-only on the server (`rooms.py` guards each write with
            // require_configuration). A manager or receptionist sees the plan — the desk
            // needs to know what rooms exist — and is never given a tile that opens a
            // panel of buttons that can only come back 403.
            onSelect={
              isAdmin
                ? (r) => {
                    setRoomEdit(null);
                    setOpenRoomId((cur) => (cur === r.id ? null : r.id));
                  }
                : undefined
            }
            selectedId={openRoomId}
          />
          {!isAdmin && (
            <p className="text-xs text-faint mt-4 max-w-2xl">
              Rooms, room types and rates can only be changed by an administrator.
            </p>
          )}
        </section>
      )}

      {openRoom && isAdmin && (
        <RoomPanel
          room={openRoom}
          type={typeOf(openRoom, types)}
          types={types}
          edit={roomEdit}
          setEdit={setRoomEdit}
          busy={busy}
          onSave={saveRoom}
          onClose={() => {
            setOpenRoomId(null);
            setRoomEdit(null);
          }}
          onToggle={() => setConfirming({ kind: "toggle-room", room: openRoom })}
          onDelete={() => setConfirming({ kind: "delete-room", room: openRoom })}
        />
      )}

      {isAdmin && (
        <div
          className={`rounded p-5 mb-8 max-w-4xl border ${
            empty ? "border-brass/40 bg-surface" : "border-hairline bg-surface"
          }`}
        >
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
            {empty ? "Start here — your first room type" : "Add a room type"}
          </h2>
          {/* The chain, said once and only while it is still broken. A hotel with no
              room type cannot have rooms, cannot have a rate, and cannot be quoted a
              booking — which is why an empty screen leads with the form rather than
              with a sentence describing a button that is not there. */}
          {empty && (
            <p className="text-sm text-muted2 mb-5 max-w-2xl">
              A room type is the thing guests book — Deluxe, Standard, Suite — and
              everything else hangs off it. Add one here, put rooms in it, then set a rate
              on the Rates screen. Until all three exist, no booking can be priced.
            </p>
          )}
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-faint">
              Name
              <input
                data-testid="room-type-name"
                value={creating.name}
                onChange={(e) => setCreating({ ...creating, name: e.target.value })}
                placeholder="Deluxe Double"
                className="block mt-2 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-faint">
              Code
              <input
                data-testid="room-type-code"
                value={creating.code}
                onChange={(e) => setCreating({ ...creating, code: e.target.value })}
                placeholder="DLX"
                className="block mt-2 w-28 bg-transparent border-b border-hairline-strong text-ink py-1 font-mono focus:border-brass outline-none"
              />
            </label>
            <OccupancyFields value={creating} onChange={setCreating} prefix="room-type" />
            <button
              onClick={createType}
              disabled={busy}
              data-testid="room-type-create"
              className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : empty ? "Create it" : "Add type"}
            </button>
          </div>
          <p className="text-xs text-faint mt-4 max-w-2xl">
            Sleeps is what the rate is quoted for; anyone above it is charged as an extra
            adult or child. Up to is the hard ceiling — a booking for more people than
            that is refused. Max occupancy cannot be below what the type sleeps as
            standard.
          </p>
        </div>
      )}

      {empty ? (
        <p className="text-muted2 max-w-2xl">
          {isAdmin
            ? "No room types yet — the form above is the whole of what is needed to make one."
            : "No room types yet. Setting them up is the owner's to do: rooms, room types and rates can only be changed by an administrator, so ask them to add one before you try to take a booking."}
        </p>
      ) : (
        <div className="space-y-4 max-w-4xl">
          {types.map((t) => {
            const mine = rooms
              .filter((r) => r.room_type_id === t.id)
              .sort((a, b) =>
                a.number.localeCompare(b.number, undefined, { numeric: true }),
              );
            return (
              <div
                key={t.id}
                data-testid={`room-type-${t.id}`}
                className={`border border-hairline bg-surface rounded p-5 ${
                  t.active === false ? "opacity-60" : ""
                }`}
              >
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="text-lg font-semibold">
                    {t.name}
                    {t.active === false && (
                      <span className="text-[10px] tracking-widest uppercase text-faint ml-3">
                        not bookable
                      </span>
                    )}
                  </h3>
                  <span className="text-xs font-mono text-faint">{t.code}</span>
                </div>
                <p className="text-sm text-muted2 mt-2">
                  Sleeps {t.base_occupancy}, up to {t.max_occupancy}
                  {t.max_extra_beds
                    ? ` plus ${t.max_extra_beds} extra bed${t.max_extra_beds === 1 ? "" : "s"}`
                    : ""}
                </p>

                {/* A room type with no rooms in it is the second half of the same
                    defect: it can be quoted a rate and never actually sold, because
                    availability counts rooms and there are none. Said here, where the
                    button that fixes it is. */}
                {mine.length === 0 ? (
                  <p className="text-sm text-faint mt-3">
                    No rooms in this type yet — nothing can be booked into it until there
                    is at least one.
                  </p>
                ) : (
                  // The numbers, not a table of them: which doors this type is, in one
                  // line, with the plan above answering everything the other four columns
                  // used to. Clicking one opens the same door the plan opens.
                  <p className="mt-3 flex flex-wrap gap-x-3 gap-y-1" data-testid={`room-type-numbers-${t.id}`}>
                    {mine.map((r) => (
                      <button
                        key={r.id}
                        type="button"
                        data-testid={`room-${r.id}`}
                        disabled={!isAdmin}
                        onClick={() => {
                          setRoomEdit(null);
                          setOpenRoomId(r.id);
                        }}
                        className={`font-mono text-sm ${
                          r.active === false ? "text-faint line-through" : "text-muted2"
                        } ${isAdmin ? "hover:text-brass" : "cursor-default"}`}
                      >
                        {r.number}
                      </button>
                    ))}
                  </p>
                )}
                <p className="text-sm text-brass mt-3 font-mono">
                  {mine.length} room{mine.length === 1 ? "" : "s"}
                </p>

                {isAdmin && (
                  <div className="mt-4 pt-4 border-t border-hairline flex flex-wrap gap-4">
                    <button
                      onClick={() => (addingTo === t.id ? setAddingTo(null) : openAdd(t.id))}
                      disabled={busy}
                      data-testid={`room-add-open-${t.id}`}
                      className="text-[10px] tracking-widest uppercase text-brass hover:text-brass disabled:opacity-30"
                    >
                      {addingTo === t.id ? "Close" : "Add rooms"}
                    </button>
                    <button
                      onClick={() => setEditingType({ ...t })}
                      disabled={busy}
                      data-testid={`room-type-edit-${t.id}`}
                      className="text-[10px] tracking-widest uppercase text-faint hover:text-brass disabled:opacity-30"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() =>
                        setConfirming({ kind: "delete-type", type: t, rooms: mine })
                      }
                      disabled={busy}
                      data-testid={`room-type-delete-${t.id}`}
                      className="text-[10px] tracking-widest uppercase text-faint hover:text-red-400 disabled:opacity-30"
                    >
                      Delete
                    </button>
                  </div>
                )}

                {isAdmin && addingTo === t.id && (
                  <AddRoomsPanel
                    draft={roomDraft}
                    setDraft={setRoomDraft}
                    busy={busy}
                    existing={rooms}
                    onAddOne={addOneRoom}
                    onAddRange={addRange}
                    result={bulkResult}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {editingType && (
        <div className="mt-8 border border-hairline bg-surface rounded p-5 max-w-4xl">
          <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-4">
            Edit {editingType.name}
          </h3>
          <div className="flex flex-wrap gap-4 items-end">
            <label className="text-xs tracking-widest uppercase text-faint">
              Name
              <input
                data-testid="room-type-edit-name"
                value={editingType.name}
                onChange={(e) => setEditingType({ ...editingType, name: e.target.value })}
                className="block mt-2 bg-transparent border-b border-hairline-strong text-ink py-1 focus:border-brass outline-none"
              />
            </label>
            <label className="text-xs tracking-widest uppercase text-faint">
              Code
              <input
                data-testid="room-type-edit-code"
                value={editingType.code}
                onChange={(e) => setEditingType({ ...editingType, code: e.target.value })}
                className="block mt-2 w-28 bg-transparent border-b border-hairline-strong text-ink py-1 font-mono focus:border-brass outline-none"
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
            <label className="flex items-center gap-2 text-xs tracking-widest uppercase text-faint pb-2">
              <input
                type="checkbox"
                data-testid="room-type-edit-active"
                checked={editingType.active !== false}
                onChange={(e) => setEditingType({ ...editingType, active: e.target.checked })}
                className="accent-brass"
              />
              Bookable
            </label>
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={saveType}
              disabled={busy}
              data-testid="room-type-edit-save"
              className="bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setEditingType(null)}
              disabled={busy}
              className="border border-hairline-strong text-muted2 hover:text-ink disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-faint mt-4 max-w-2xl">
            Renaming a type renames it everywhere it is shown; bookings already taken
            against it keep the price they were quoted. Unticking Bookable stops it being
            offered for new dates and leaves its rooms and its history alone.
          </p>
        </div>
      )}

      {confirming && (() => {
        const copy = CONFIRM_COPY[confirming.kind](confirming);
        return (
          <div
            data-testid="rooms-confirm"
            className={`mt-8 rounded p-5 max-w-2xl border ${
              copy.danger ? "border-red-500/40 bg-red-950/20" : "border-hairline bg-surface"
            }`}
          >
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-faint mb-2">
              {copy.title}
            </h3>
            <p className={`text-sm mb-4 ${copy.danger ? "text-red-300" : "text-muted2"}`}>
              {copy.body}
            </p>
            <div className="flex gap-3">
              <button
                onClick={confirmAction}
                disabled={busy}
                data-testid="rooms-confirm-go"
                className={`rounded-full px-6 py-2 text-sm tracking-widest uppercase disabled:opacity-50 text-white ${
                  copy.danger ? "bg-red-600 hover:bg-red-500" : "bg-brass hover:bg-brass-deep"
                }`}
              >
                {busy ? "Working…" : copy.go}
              </button>
              <button
                onClick={() => setConfirming(null)}
                disabled={busy}
                className="border border-hairline-strong text-muted2 hover:border-hairline-strong disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
              >
                Never mind
              </button>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
