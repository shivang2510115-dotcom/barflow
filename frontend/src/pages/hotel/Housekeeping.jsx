import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BedDouble,
  Check,
  ClipboardList,
  History,
  Plus,
  User,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { api, formatApiErrorDetail } from "@/lib/api";
import RoomGrid from "@/components/app/RoomGrid";
import {
  DEFAULT_PRIORITY,
  JOB_STATUS_LABELS,
  OPEN,
  PRIORITIES,
  PRIORITY_LABELS,
  STATUS_BLURB,
  STATUS_LABELS,
  compareJobs,
  housekeepingState,
  isGuestRaised,
  jobIsLive,
  noOptionsReason,
  noteRequired,
  statusOf,
} from "@/lib/housekeeping";

/**
 * The attendant's screen: every room, what state it is in, and what has been asked for.
 *
 * Written for a phone held in one hand, standing in a corridor, and everything else
 * follows from that. No table — the rooms are the building's own floor plan, drawn by the
 * same `RoomGrid` the front desk and the rooms screen use, because "204" is a door on the
 * second floor and not row seventeen. Tapping a door opens a sheet at the bottom of the
 * screen, where a thumb already is, and the sheet offers **only the statuses the server
 * will accept**: `can_set` is computed by `services/housekeeping.py::can_set` and sent on
 * every card, so this screen never works the transition table out for itself and never
 * offers a button that comes back 403.
 *
 * An `out_of_order` room offers an attendant nothing at all, and says why. That is the
 * design's rule, not an oversight: the attendant reports the fault and somebody
 * accountable confirms it is fixed before the room is sold again.
 */

/** `2026-08-28T09:14:00Z` → `28 Aug 09:14`, in the reader's own timezone. */
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const PILL =
  "border rounded-full px-3 py-1.5 text-[10px] font-mono uppercase tracking-widest transition-colors";
const CHOSEN = "border-brass text-brass bg-brass/10";
const UNCHOSEN = "border-hairline-strong text-faint hover:border-hairline-strong hover:text-muted2";

/** A guest asked, or somebody working here did. Different things to whoever reads the list. */
function SourceBadge({ job }) {
  const guest = isGuestRaised(job);
  return (
    <span
      data-testid={guest ? "job-source-guest" : "job-source-staff"}
      className={`inline-flex items-center gap-1 text-[9px] font-mono uppercase tracking-widest px-1.5 py-0.5 border ${
        guest ? "border-brass/60 text-brass bg-brass/10" : "border-hairline-strong text-faint"
      }`}
    >
      {guest ? <User size={10} aria-hidden="true" /> : <BedDouble size={10} aria-hidden="true" />}
      {guest ? "Guest asked" : "Staff raised"}
    </span>
  );
}

function PriorityBadge({ priority }) {
  return (
    <span
      className={`text-[9px] font-mono uppercase tracking-widest px-1.5 py-0.5 border ${
        priority === "high" ? "border-red-500/60 text-red-300" : "border-hairline-strong text-muted2"
      }`}
    >
      {PRIORITY_LABELS[priority] || priority}
    </span>
  );
}

/**
 * Raising a request. Used twice: on the requests tab with a room picker, and inside a
 * room's sheet with the room already known — the attendant standing in 204 should not
 * have to find 204 in a list.
 */
function RequestForm({ rooms, roomId, onRaised }) {
  const [room, setRoom] = useState(roomId || "");
  const [priority, setPriority] = useState(DEFAULT_PRIORITY);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const target = roomId || room;
    if (!target) {
      toast.error("Pick a room");
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post("/housekeeping/jobs", {
        room_id: target,
        priority,
        reason,
      });
      toast.success(`Request raised for room ${data.room_number || ""}`.trim());
      setReason("");
      setPriority(DEFAULT_PRIORITY);
      if (onRaised) onRaised();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3" data-testid="raise-request-form">
      {!roomId && (
        <label className="block text-[10px] font-mono uppercase tracking-widest text-faint">
          Room
          <select
            value={room}
            data-testid="request-room"
            onChange={(e) => setRoom(e.target.value)}
            className="block w-full mt-2 bg-ground border border-hairline-strong text-ink py-2.5 px-2 rounded"
          >
            <option value="">Choose…</option>
            {rooms.map((r) => (
              <option key={r.id} value={r.id}>
                {r.number}
              </option>
            ))}
          </select>
        </label>
      )}

      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-faint mb-2">
          Priority
        </div>
        <div className="flex gap-2">
          {PRIORITIES.map((p) => (
            <button
              key={p}
              type="button"
              data-testid={`priority-${p}`}
              aria-pressed={priority === p}
              onClick={() => setPriority(p)}
              className={`${PILL} ${priority === p ? CHOSEN : UNCHOSEN}`}
            >
              {PRIORITY_LABELS[p]}
            </button>
          ))}
        </div>
      </div>

      <label className="block text-[10px] font-mono uppercase tracking-widest text-faint">
        What is needed
        <textarea
          value={reason}
          rows={2}
          data-testid="request-reason"
          placeholder="Spill on the carpet"
          onChange={(e) => setReason(e.target.value)}
          className="block w-full mt-2 bg-ground border border-hairline-strong text-ink text-sm p-2 rounded focus:border-brass outline-none"
        />
      </label>

      <button
        type="button"
        onClick={submit}
        disabled={busy}
        data-testid="raise-request"
        className="w-full bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full py-3 text-xs font-mono uppercase tracking-widest"
      >
        {busy ? "Raising…" : "Raise request"}
      </button>
    </div>
  );
}

/** One request in a list, with whatever can still be done to it. */
function JobRow({ job, onAct, busy }) {
  return (
    <li
      data-testid={`job-${job.id}`}
      className="py-3 border-b border-hairline last:border-b-0"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono tabular-nums text-xl leading-none">{job.room_number || "—"}</span>
        <PriorityBadge priority={job.priority} />
        <SourceBadge job={job} />
        <span className="text-[9px] font-mono uppercase tracking-widest text-faint">
          {JOB_STATUS_LABELS[job.status] || job.status}
        </span>
        <span className="ml-auto text-[10px] font-mono text-faint">{when(job.created_at)}</span>
      </div>

      {job.reason ? (
        /* Pre-line, because a guest's second press appends a line rather than replacing
           the first — see `merge_reason` on the server. */
        <p className="mt-1.5 text-sm text-muted2 whitespace-pre-line break-words">{job.reason}</p>
      ) : (
        <p className="mt-1.5 text-sm text-faint italic">No reason given</p>
      )}

      {jobIsLive(job) && (
        <div className="flex flex-wrap gap-2 mt-3">
          {job.status === OPEN && (
            <button
              type="button"
              data-testid={`job-acknowledge-${job.id}`}
              disabled={busy}
              onClick={() => onAct(job, "acknowledge")}
              className={`${PILL} border-brass/50 text-brass hover:bg-brass-deep/10 disabled:opacity-50`}
            >
              On it
            </button>
          )}
          <button
            type="button"
            data-testid={`job-complete-${job.id}`}
            disabled={busy}
            onClick={() => onAct(job, "complete")}
            className={`${PILL} border-emerald-500/50 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50`}
          >
            Done
          </button>
          <button
            type="button"
            data-testid={`job-cancel-${job.id}`}
            disabled={busy}
            onClick={() => onAct(job, "cancel")}
            className={`${PILL} ${UNCHOSEN} disabled:opacity-50`}
          >
            Call off
          </button>
        </div>
      )}
    </li>
  );
}

/**
 * The sheet a tapped door opens: what this room is, what it can become, and what has been
 * asked for in it.
 *
 * At the bottom of the screen and not in the middle, because a thumb is at the bottom of
 * the screen. Every control in here clears the 44px a finger needs.
 */
function RoomSheet({ card, onClose, onChanged }) {
  const [pending, setPending] = useState(null); // the status chosen, awaiting a note
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [raising, setRaising] = useState(false);
  const [events, setEvents] = useState(null);

  // A different door: nothing half-typed about the last one may carry across.
  useEffect(() => {
    setPending(null);
    setNote("");
    setRaising(false);
    setEvents(null);
  }, [card?.id]);

  if (!card) return null;
  const status = statusOf(card);
  const blocked = noOptionsReason(card);

  const set = async (to, withNote) => {
    setBusy(true);
    try {
      const { data } = await api.put(`/rooms/${card.id}/housekeeping`, {
        status: to,
        note: withNote || undefined,
      });
      // `changed: false` is the server answering a double-tap in a corridor: the room was
      // already in that status, nothing was written and no log line exists. Saying so is
      // better than a success message for something that did not happen.
      toast.success(
        data.changed
          ? `Room ${card.number} is ${STATUS_LABELS[to].toLowerCase()}`
          : `Room ${card.number} was already ${STATUS_LABELS[to].toLowerCase()}`,
      );
      setPending(null);
      setNote("");
      onChanged();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const choose = (to) => {
    // Only `out_of_order` has to say why, and the API answers 400 without it. Asking here
    // rather than sending it and reporting the refusal is the difference between one tap
    // and a tap, an error and a tap.
    if (noteRequired(to)) {
      setPending(to);
      return;
    }
    set(to);
  };

  const act = async (job, action) => {
    setBusy(true);
    try {
      await api.post(`/housekeeping/jobs/${job.id}/${action}`, action === "cancel" ? {} : undefined);
      onChanged();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const loadEvents = async () => {
    if (events) {
      setEvents(null);
      return;
    }
    try {
      const { data } = await api.get(`/rooms/${card.id}/housekeeping/events`);
      setEvents(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  return (
    <>
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="fixed inset-0 z-40 bg-ground/70 backdrop-blur-sm"
      />
      <div
        data-testid="room-sheet"
        role="dialog"
        aria-label={`Room ${card.number}`}
        className="fixed z-50 inset-x-0 bottom-0 md:inset-x-auto md:right-6 md:bottom-6 md:w-[26rem]
                   max-h-[85vh] overflow-y-auto border-t md:border border-hairline-strong bg-surface
                   p-5 pb-8 shadow-2xl shadow-black/60"
      >
        <div className="flex items-start gap-3">
          <div>
            <div className="font-mono tabular-nums text-4xl leading-none">{card.number}</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-mono uppercase tracking-widest text-muted2">
                {STATUS_LABELS[status]}
              </span>
              {card.occupied && (
                <span className="text-[10px] font-mono uppercase tracking-widest text-brass">
                  In house
                </span>
              )}
              {card.departing_today && (
                <span className="text-[10px] font-mono uppercase tracking-widest text-brass">
                  Departs today
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="close-room-sheet"
            aria-label="Close"
            className="ml-auto text-faint hover:text-ink p-2 -m-2"
          >
            <X size={20} />
          </button>
        </div>

        {card.housekeeping_note && (
          <p className="mt-4 text-sm text-muted2 border-l-2 border-red-500/60 pl-3 whitespace-pre-line">
            {card.housekeeping_note}
          </p>
        )}

        {/* The statuses this role may set on this room, straight off the board. Never a
            list this screen worked out for itself. */}
        <div className="mt-5 space-y-2" data-testid="status-options">
          {blocked ? (
            <p className="text-sm text-faint" data-testid="no-status-options">
              {blocked}
            </p>
          ) : (
            card.can_set.map((to) => (
              <button
                key={to}
                type="button"
                data-testid={`set-${to}`}
                disabled={busy}
                onClick={() => choose(to)}
                className={`w-full text-left border rounded p-4 min-h-[3.5rem] disabled:opacity-50 transition-colors ${
                  pending === to
                    ? "border-brass bg-brass/10"
                    : "border-hairline-strong hover:border-brass/60"
                }`}
              >
                <div className="text-sm font-mono uppercase tracking-widest text-ink">
                  {STATUS_LABELS[to]}
                </div>
                <div className="text-xs text-faint mt-0.5">{STATUS_BLURB[to]}</div>
              </button>
            ))
          )}
        </div>

        {pending && (
          <div className="mt-3" data-testid="note-required">
            <label className="block text-[10px] font-mono uppercase tracking-widest text-faint">
              What is wrong with the room
              <textarea
                autoFocus
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Burst pipe in the bathroom"
                className="block w-full mt-2 bg-ground border border-hairline-strong text-ink text-sm p-2 rounded focus:border-brass outline-none"
              />
            </label>
            <p className="text-xs text-faint mt-2">
              A manager takes the room back out of this, once the fault is fixed.
            </p>
            <button
              type="button"
              data-testid="confirm-out-of-order"
              disabled={busy || !note.trim()}
              onClick={() => set(pending, note.trim())}
              className="mt-3 w-full bg-brass hover:bg-brass-deep disabled:opacity-50 text-white rounded-full py-3 text-xs font-mono uppercase tracking-widest"
            >
              {busy ? "Saving…" : `Mark ${STATUS_LABELS[pending].toLowerCase()}`}
            </button>
          </div>
        )}

        {/* What has been asked for in this room, on the same sheet as the status: an
            attendant standing at the door needs both, and two screens to learn them is
            two chances to miss one. */}
        {(card.jobs || []).length > 0 && (
          <div className="mt-6">
            <h3 className="text-[10px] font-mono uppercase tracking-[0.25em] text-faint mb-1">
              Requests
            </h3>
            <ul>
              {[...card.jobs].sort(compareJobs).map((job) => (
                <JobRow key={job.id} job={job} busy={busy} onAct={act} />
              ))}
            </ul>
          </div>
        )}

        <div className="mt-6 flex gap-2">
          <button
            type="button"
            data-testid="sheet-raise"
            onClick={() => setRaising((r) => !r)}
            className={`${PILL} ${raising ? CHOSEN : UNCHOSEN} flex items-center gap-1`}
          >
            <Plus size={12} /> Request
          </button>
          <button
            type="button"
            data-testid="sheet-history"
            onClick={loadEvents}
            className={`${PILL} ${events ? CHOSEN : UNCHOSEN} flex items-center gap-1`}
          >
            <History size={12} /> History
          </button>
        </div>

        {raising && (
          <div className="mt-4">
            <RequestForm
              rooms={[]}
              roomId={card.id}
              onRaised={() => {
                setRaising(false);
                onChanged();
              }}
            />
          </div>
        )}

        {events && (
          /* The append-only log, newest first. It is the reason attendants have their own
             logins: when a guest says the room was filthy, this answers who marked it
             clean and when. */
          <ul className="mt-4 space-y-2" data-testid="room-events">
            {events.length === 0 && <li className="text-sm text-faint">Nothing recorded yet.</li>}
            {events.map((e) => (
              <li key={e.id} className="text-xs text-muted2 border-l border-hairline-strong pl-3">
                <span className="font-mono text-faint">{when(e.changed_at)}</span>{" "}
                {STATUS_LABELS[e.from_status] || e.from_status} →{" "}
                <span className="text-ink">{STATUS_LABELS[e.to_status] || e.to_status}</span>
                {e.note ? <div className="text-faint mt-0.5">{e.note}</div> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

const FILTERS = [
  { key: "live", label: "Waiting" },
  { key: "done", label: "Done" },
  { key: "", label: "All" },
];

export default function Housekeeping() {
  const [board, setBoard] = useState(null);
  const [tab, setTab] = useState("rooms");
  const [openRoomId, setOpenRoomId] = useState(null);
  const [filter, setFilter] = useState("live");
  const [jobs, setJobs] = useState([]);
  const [raising, setRaising] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadBoard = useCallback(async () => {
    try {
      const { data } = await api.get("/housekeeping");
      setBoard(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
      setBoard({ date: "", rooms: [] });
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const { data } = await api.get("/housekeeping/jobs", { params: { status: filter } });
      setJobs(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  }, [filter]);

  useEffect(() => {
    loadBoard();
  }, [loadBoard]);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  const refresh = useCallback(() => {
    loadBoard();
    loadJobs();
  }, [loadBoard, loadJobs]);

  // Memoised on the board rather than written inline, so the two derivations below do not
  // see a new empty array on every render.
  const rooms = useMemo(() => board?.rooms || [], [board]);
  // Re-read out of the board on every render rather than held in state: acting on a room
  // reloads the board, and a copy taken when the sheet opened would still show the status
  // the room was in before the tap.
  const openRoom = useMemo(() => rooms.find((r) => r.id === openRoomId) || null, [rooms, openRoomId]);
  const waiting = useMemo(() => rooms.reduce((n, r) => n + (r.jobs || []).length, 0), [rooms]);

  const act = async (job, action) => {
    setBusy(true);
    try {
      await api.post(`/housekeeping/jobs/${job.id}/${action}`, action === "cancel" ? {} : undefined);
      refresh();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!board) return <div className="p-6 md:p-10 text-muted2">Loading housekeeping…</div>;

  return (
    /* `pb-40` clears the alert that sits across the bottom of a phone. */
    <div className="p-4 md:p-10 pb-40">
      <div className="text-xs tracking-[0.4em] uppercase text-brass mb-3">Hotel</div>
      <h1 className="text-3xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Housekeeping
      </h1>
      <p className="text-faint font-mono text-xs mb-6">{board.date}</p>

      <div className="flex gap-2 mb-6" role="tablist">
        <button
          type="button"
          role="tab"
          data-testid="tab-rooms"
          aria-selected={tab === "rooms"}
          onClick={() => setTab("rooms")}
          className={`${PILL} ${tab === "rooms" ? CHOSEN : UNCHOSEN} flex items-center gap-1.5`}
        >
          <BedDouble size={12} /> Rooms
        </button>
        <button
          type="button"
          role="tab"
          data-testid="tab-requests"
          aria-selected={tab === "requests"}
          onClick={() => setTab("requests")}
          className={`${PILL} ${tab === "requests" ? CHOSEN : UNCHOSEN} flex items-center gap-1.5`}
        >
          <ClipboardList size={12} /> Requests
          {waiting > 0 && <span className="font-mono text-brass">{waiting}</span>}
        </button>
      </div>

      {tab === "rooms" && (
        <RoomGrid
          rooms={rooms}
          types={[]}
          stateOf={housekeepingState}
          legendStates={["dirty", "clean", "inspected", "out_of_order"]}
          onSelect={(room) => setOpenRoomId((cur) => (cur === room.id ? null : room.id))}
          selectedId={openRoomId}
          testIdPrefix="hk-room"
          empty="No rooms yet."
        />
      )}

      {tab === "requests" && (
        <div className="max-w-2xl">
          <button
            type="button"
            data-testid="new-request"
            onClick={() => setRaising((r) => !r)}
            className={`${PILL} ${raising ? CHOSEN : UNCHOSEN} flex items-center gap-1.5 mb-4`}
          >
            {raising ? <X size={12} /> : <Plus size={12} />} New request
          </button>

          {raising && (
            <div className="border border-hairline bg-surface rounded p-4 mb-6">
              <RequestForm
                rooms={rooms}
                onRaised={() => {
                  setRaising(false);
                  refresh();
                }}
              />
            </div>
          )}

          <div className="flex gap-2 mb-4">
            {FILTERS.map((f) => (
              <button
                key={f.key || "all"}
                type="button"
                data-testid={`filter-${f.key || "all"}`}
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
                className={`${PILL} ${filter === f.key ? CHOSEN : UNCHOSEN}`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {jobs.length === 0 ? (
            <p className="text-faint text-sm flex items-center gap-2">
              <Check size={14} /> Nothing waiting.
            </p>
          ) : (
            <ul data-testid="jobs-list">
              {jobs.map((job) => (
                <JobRow key={job.id} job={job} busy={busy} onAct={act} />
              ))}
            </ul>
          )}
        </div>
      )}

      {openRoom && (
        <RoomSheet card={openRoom} onClose={() => setOpenRoomId(null)} onChanged={refresh} />
      )}
    </div>
  );
}
