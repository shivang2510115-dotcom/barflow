import { Ban, BadgeCheck, Brush, Check, DoorOpen, PowerOff, Sparkles, User, Wrench } from "lucide-react";
import { countStates, groupRoomsByFloor, typeOf } from "@/lib/roomGrid";

/**
 * A hotel drawn as a building: floors stacked, doors along each floor.
 *
 * The screens that used to list rooms in a table all asked the receptionist to read
 * where they could have looked. "204" is a door on the second floor between 203 and
 * 205, not row seventeen — and at a front desk, mid-shift, with a guest waiting, looking
 * is the difference between five seconds and thirty.
 *
 * One component, three screens, three questions. What differs is only the *state* of a
 * tile, which is why this file takes `stateOf` and computes none of it: the answers come
 * from `lib/roomGrid.js`, which the three pages call with what each of them knows. This
 * file decides how a state looks, and nothing else.
 *
 * Colour is never the only signal. Every state also carries a word and an icon, and most
 * carry a border treatment as well — dashed for a room switched off, a hatched fill for
 * one out of order. Eight percent of men cannot separate the red tile from the green one,
 * and this is a screen someone reads at speed with a queue in front of them.
 */

// state → how it is drawn. `chip` is the little label strip; `tile` is the door itself.
// Every entry names a word and an icon, so removing every colour from this table would
// leave the grid still readable.
const LOOK = {
  active: {
    icon: Check,
    label: "Active",
    tile: "border-stone-600 bg-stone-900 text-stone-100",
    chip: "text-stone-400",
  },
  free: {
    icon: Check,
    label: "Free",
    tile: "border-emerald-500/60 bg-emerald-500/5 text-stone-100",
    chip: "text-emerald-300",
  },
  vacant: {
    icon: DoorOpen,
    label: "Vacant",
    tile: "border-stone-700 bg-stone-900 text-stone-300",
    chip: "text-stone-500",
  },
  taken: {
    icon: User,
    label: "Taken",
    tile: "border-orange-500/60 bg-orange-500/10 text-stone-100",
    chip: "text-orange-300",
  },
  occupied: {
    icon: User,
    label: "Occupied",
    tile: "border-orange-500/60 bg-orange-500/10 text-stone-100",
    chip: "text-orange-300",
  },
  blocked: {
    icon: Wrench,
    label: "Out of order",
    // The hatch is the point: an out-of-order room reads as struck through even in a
    // photocopy, and it is the one state where acting on the wrong answer means walking
    // a guest to a room with no water.
    tile: "border-red-500/60 room-tile-hatch text-stone-100",
    chip: "text-red-300",
  },
  inactive: {
    icon: PowerOff,
    label: "Inactive",
    tile: "border-dashed border-stone-700 bg-stone-950 text-stone-500",
    chip: "text-stone-500",
  },
  full: {
    icon: Ban,
    label: "Not bookable",
    tile: "border-stone-700 bg-stone-950 text-stone-400",
    chip: "text-stone-500",
  },

  // The fourth question, and the reason this table is a table: housekeeping asks whether
  // the room is *made up*, which is a different axis from whether it is sold, occupied or
  // switched on. The state keys are the housekeeping statuses themselves
  // (`lib/housekeeping.js::housekeepingState`), so nothing translates between the API's
  // vocabulary and this one.
  //
  // `out_of_order` is drawn exactly as `blocked` is, and that is the only thing the two
  // share: one is a date range that stops the room being *sold*, the other means it is not
  // usable *right now*. They stay separate everywhere else — see services/housekeeping.py.
  clean: {
    icon: Sparkles,
    label: "Clean",
    tile: "border-emerald-500/60 bg-emerald-500/5 text-stone-100",
    chip: "text-emerald-300",
  },
  dirty: {
    icon: Brush,
    label: "Dirty",
    tile: "border-amber-500/60 bg-amber-500/10 text-stone-100",
    chip: "text-amber-300",
  },
  inspected: {
    icon: BadgeCheck,
    label: "Inspected",
    tile: "border-sky-500/60 bg-sky-500/5 text-stone-100",
    chip: "text-sky-300",
  },
  out_of_order: {
    icon: Wrench,
    label: "Out of order",
    tile: "border-red-500/60 room-tile-hatch text-stone-100",
    chip: "text-red-300",
  },
};

const FALLBACK = {
  icon: DoorOpen,
  label: "Unknown",
  tile: "border-stone-700 bg-stone-900",
  chip: "text-stone-500",
};
const look = (state) => LOOK[state] || FALLBACK;

/** One door. */
function RoomTile({ room, type, view, onSelect, selectable, selected, testId }) {
  const { icon: Icon, tile, chip } = look(view.state);
  const Wrapper = onSelect && selectable ? "button" : "div";

  return (
    <Wrapper
      type={Wrapper === "button" ? "button" : undefined}
      onClick={onSelect && selectable ? () => onSelect(room, view) : undefined}
      disabled={Wrapper === "button" ? false : undefined}
      data-testid={testId}
      data-state={view.state}
      // `title` rather than a tooltip component: it is the one hover text that works
      // identically on a laptop and does not steal a tap on the tablet this runs on.
      title={[room.number, view.label, view.note].filter(Boolean).join(" — ")}
      aria-label={`Room ${room.number}${type ? `, ${type.name}` : ""}, ${view.label}${
        view.note ? `, ${view.note}` : ""
      }`}
      aria-pressed={Wrapper === "button" && selected ? true : undefined}
      className={`room-tile relative text-left rounded border p-3 min-h-[5.5rem] flex flex-col justify-between
        transition-[color,background-color,border-color,box-shadow] duration-300 ease-out
        ${tile}
        ${selected ? "ring-2 ring-orange-500 ring-offset-2 ring-offset-stone-950" : ""}
        ${
          Wrapper === "button"
            ? "hover:border-orange-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 cursor-pointer"
            : ""
        }
        ${onSelect && !selectable ? "opacity-70" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        {/* Big enough to read standing up, at arm's length, without leaning in. */}
        <span className="font-mono tabular-nums text-2xl md:text-3xl leading-none tracking-tight">
          {room.number}
        </span>
        <Icon className={`w-4 h-4 shrink-0 mt-1 ${chip}`} aria-hidden="true" />
      </div>

      <div className="mt-2">
        {type && (
          <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500 truncate">
            {type.code || type.name}
          </div>
        )}
        {/* The word. Never removed, whatever the colour is doing. */}
        <div className={`text-[10px] tracking-[0.2em] uppercase truncate ${chip}`}>
          {view.label}
        </div>
        {view.note && (
          <div className="text-[11px] text-stone-300 truncate mt-1" title={view.note}>
            {view.note}
          </div>
        )}
        {(view.lines || []).filter(Boolean).map((line) => (
          <div key={line} className="text-[10px] font-mono tabular-nums text-stone-500 truncate">
            {line}
          </div>
        ))}
      </div>
    </Wrapper>
  );
}

/** The words above the grid, with a count each. Same words as the tiles use. */
function Legend({ entries, order }) {
  if (!order?.length) return null;
  const counts = countStates(entries, order);
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2 mb-5" data-testid="room-grid-legend">
      {counts.map(({ state, count }) => {
        const { icon: Icon, chip, label: fallbackLabel } = look(state);
        // A state with a count of zero has no tile to borrow a label from — the legend
        // still has to say the word, or "no rooms are out of order" reads as the legend
        // being broken.
        const label = entries.find((e) => e.state === state)?.label || fallbackLabel;
        return (
          <span
            key={state}
            className={`inline-flex items-center gap-2 text-[10px] tracking-[0.2em] uppercase ${chip}`}
          >
            <Icon className="w-3.5 h-3.5" aria-hidden="true" />
            {label}
            <span className="font-mono text-stone-500">{count}</span>
          </span>
        );
      })}
    </div>
  );
}

/**
 * @param rooms            every room, unsorted — this groups and orders them
 * @param types            room types, for the code on each tile
 * @param stateOf          room → the descriptor from lib/roomGrid.js
 * @param onSelect         (room, view) => void; omit for a read-only plan
 * @param selectable       (room, view) => boolean; which tiles may be clicked
 * @param selectedId       the room drawn as chosen
 * @param legendStates     state keys to count above the grid, in the order shown
 * @param empty            what to say when there are no rooms at all
 */
export default function RoomGrid({
  rooms,
  types = [],
  stateOf,
  onSelect,
  selectable = () => true,
  selectedId = null,
  legendStates = [],
  empty = "No rooms yet.",
  testIdPrefix = "room-tile",
}) {
  const floors = groupRoomsByFloor(rooms);
  if (floors.length === 0) return <p className="text-stone-500 text-sm">{empty}</p>;

  const views = new Map((rooms || []).map((r) => [r.id, stateOf(r)]));

  return (
    <div data-testid="room-grid">
      <Legend entries={[...views.values()]} order={legendStates} />

      <div className="space-y-7">
        {floors.map((floor, i) => (
          <section
            key={floor.floor ?? "none"}
            data-testid={`room-floor-${floor.floor ?? "none"}`}
            // Each floor settles in a beat after the one below it. It runs once, on
            // load, and is over in a third of a second — long enough to show the
            // building assembling itself, short enough that a receptionist who came
            // here to find room 204 is not waiting on it. `prefers-reduced-motion`
            // removes it outright (index.css).
            className="room-floor"
            style={{ animationDelay: `${Math.min(i, 6) * 60}ms` }}
          >
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-3 flex items-baseline gap-3">
              {floor.label}
              <span className="font-mono text-stone-600">{floor.rooms.length}</span>
            </h3>
            {/* auto-fill down to 7rem: four or five doors across a tablet held in
                portrait, more on a laptop, and never a horizontal scrollbar. */}
            <div className="grid gap-2 md:gap-3 grid-cols-[repeat(auto-fill,minmax(7rem,1fr))]">
              {floor.rooms.map((room) => {
                const view = views.get(room.id);
                return (
                  <RoomTile
                    key={room.id}
                    room={room}
                    type={typeOf(room, types)}
                    view={view}
                    onSelect={onSelect}
                    selectable={selectable(room, view)}
                    selected={selectedId === room.id}
                    testId={`${testIdPrefix}-${room.number}`}
                  />
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
