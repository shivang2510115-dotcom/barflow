import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  Plus,
  Repeat,
  Tags,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { api, formatApiErrorDetail } from "@/lib/api";
import {
  DEFAULT_VIEW,
  VIEWS,
  WEEKDAYS,
  byDay,
  categoriesById,
  daysBetween,
  inMonth,
  longDate,
  monthGrid,
  periodLabel,
  rangeFor,
  repeatLabel,
  startOfWeek,
  step,
  timeLabel,
  todayLocal,
} from "@/lib/planner";

/**
 * The planning calendar: what the people running this property have decided to do.
 *
 * Not bookings, not housekeeping jobs, not orders — a staff briefing on Tuesday, a fire
 * drill next month, a maintenance window, a wedding in the banquet hall. Everybody who
 * works here can read it; admins and managers write it, and the server says which of
 * those the person looking at it is (`can_edit`) rather than this screen working it out
 * from the role, because a second copy of that rule is how a button appears for somebody
 * who then gets a 403 when they press it.
 *
 * **Month is the default**, because that is how a manager plans. The view and the period
 * are two separate pieces of state and only the arrows touch the period, so paging
 * through the year never drops you back into a month view you did not ask for.
 *
 * **No calendar library.** The grid is `grid grid-cols-7` and the arithmetic is in
 * lib/planner.js, where it is a pure module and can be checked without a browser.
 *
 * **Dates are strings all the way down.** `new Date(iso).toISOString().slice(0,10)` is the
 * bug this feature exists around: for a user east of Greenwich, between midnight and their
 * offset, it answers yesterday. The day the server calls today arrives in the payload —
 * the property's own day, from `services/clock.py` — and it is what the grid highlights.
 */

const PILL =
  "border rounded-full px-3 py-1.5 text-[10px] font-mono uppercase tracking-widest transition-colors";
const CHOSEN = "border-orange-500 text-orange-400 bg-orange-500/10";
const UNCHOSEN = "border-stone-700 text-stone-500 hover:border-stone-500 hover:text-stone-300";
const FIELD =
  "block mt-2 w-full bg-stone-950 border border-stone-700 text-stone-100 py-2 px-3 rounded focus:border-orange-500 outline-none";
const LABEL = "text-[10px] tracking-[0.2em] uppercase text-stone-500";

const VIEW_LABELS = { month: "Month", week: "Week", day: "Day" };

/** A blank event, ready for the form. `date` is the day the user was looking at. */
const blankEvent = (date, categoryId) => ({
  title: "",
  description: "",
  date,
  start_time: "",
  end_time: "",
  category_id: categoryId || "",
  repeat: "",
  repeat_until: "",
});

/**
 * One event as it appears in a grid cell: a dot in its category's colour and the least
 * text that still identifies it.
 *
 * The colour comes from the category record and goes into an inline style. It is safe to
 * do that because the server validates it to `#rrggbb` — see
 * `services/planner.py::clean_colour`, which exists precisely because a category coloured
 * `red; background: url(...)` would otherwise be stored cross-site scripting with a colour
 * picker in front of it.
 */
function EventChip({ event, colour, onOpen, compact }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(event)}
      data-testid={`event-${event.occurrence_id}`}
      title={`${event.title} · ${timeLabel(event)}`}
      className="planner-chip w-full text-left flex items-baseline gap-1.5 px-1 py-0.5 rounded-sm hover:bg-stone-800/80 focus:bg-stone-800 outline-none"
    >
      <span
        aria-hidden="true"
        className="shrink-0 w-1.5 h-1.5 rounded-full translate-y-[-1px]"
        style={{ backgroundColor: colour || "#78716c" }}
      />
      {!event.all_day && (
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-stone-500">
          {event.start_time}
        </span>
      )}
      <span className={`truncate text-stone-300 ${compact ? "text-[11px]" : "text-xs"}`}>
        {event.title}
      </span>
    </button>
  );
}

/** One day of the month grid. */
function MonthCell({ day, anchor, today, events, colours, onOpen, onAdd, canEdit }) {
  const outside = !inMonth(day, anchor);
  const isToday = day === today;
  // Four fits the cell at every width the app is used at; the rest are reachable by
  // opening the day, which is what the count is a button for.
  const shown = events.slice(0, 4);
  const hidden = events.length - shown.length;
  return (
    <div
      data-testid={`day-${day}`}
      className={`planner-day min-h-[7rem] border-b border-r border-stone-800 p-1.5 flex flex-col gap-0.5 ${
        outside ? "bg-stone-950/60" : "bg-stone-950"
      }`}
    >
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => onOpen(null, day)}
          className={`font-mono text-[11px] tabular-nums px-1.5 py-0.5 rounded-full transition-colors ${
            isToday
              ? "bg-orange-500 text-stone-950 font-bold"
              : outside
              ? "text-stone-700 hover:text-stone-500"
              : "text-stone-500 hover:text-orange-400"
          }`}
          aria-label={longDate(day)}
        >
          {Number(day.slice(8))}
        </button>
        {canEdit && (
          <button
            type="button"
            onClick={() => onAdd(day)}
            aria-label={`Add an event on ${longDate(day)}`}
            className="opacity-0 focus:opacity-100 group-hover:opacity-100 hover:opacity-100 text-stone-600 hover:text-orange-400 transition-opacity"
          >
            <Plus size={12} />
          </button>
        )}
      </div>
      {shown.map((e) => (
        <EventChip
          key={e.occurrence_id}
          event={e}
          colour={colours[e.category_id]?.colour}
          onOpen={onOpen}
          compact
        />
      ))}
      {hidden > 0 && (
        <span className="pl-1 text-[10px] font-mono text-stone-600">+{hidden} more</span>
      )}
    </div>
  );
}

/** One day as a column (week view) or as the whole page (day view). */
function DayColumn({ day, today, events, colours, onOpen, heading }) {
  return (
    <div
      data-testid={`day-${day}`}
      className={`border-r border-stone-800 last:border-r-0 min-h-[16rem] ${
        day === today ? "bg-orange-500/[0.04]" : ""
      }`}
    >
      <div className="px-2 py-2 border-b border-stone-800 sticky top-0 bg-stone-950/95 backdrop-blur">
        <div className={`text-[10px] font-mono uppercase tracking-widest ${
          day === today ? "text-orange-400" : "text-stone-500"
        }`}>
          {heading}
        </div>
      </div>
      <div className="p-1.5 flex flex-col gap-1">
        {events.length === 0 ? (
          <span className="px-1 text-[11px] text-stone-700">—</span>
        ) : (
          events.map((e) => (
            <button
              key={e.occurrence_id}
              type="button"
              onClick={() => onOpen(e)}
              data-testid={`event-${e.occurrence_id}`}
              className="planner-chip text-left border-l-2 pl-2 pr-1 py-1 hover:bg-stone-800/80 focus:bg-stone-800 outline-none"
              style={{ borderLeftColor: colours[e.category_id]?.colour || "#78716c" }}
            >
              <div className="text-xs text-stone-200 leading-snug">{e.title}</div>
              <div className="font-mono text-[10px] uppercase tracking-widest text-stone-500 mt-0.5">
                {timeLabel(e)}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

/** The editor. One form for creating and for changing, so the two cannot drift apart. */
function EventForm({ draft, categories, onChange, onSave, onCancel, onDelete, busy }) {
  const set = (k) => (e) => onChange({ ...draft, [k]: e.target.value });
  const editing = Boolean(draft.id);
  const pickable = categories.filter((c) => c.active || c.id === draft.category_id);

  return (
    <form
      className="grid gap-4 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <label className={`${LABEL} sm:col-span-2`}>
        Title
        <input
          className={FIELD}
          data-testid="event-title"
          value={draft.title}
          onChange={set("title")}
          placeholder="Staff briefing"
          autoFocus
        />
      </label>

      <label className={LABEL}>
        Date
        <input type="date" className={FIELD} data-testid="event-date"
               value={draft.date} onChange={set("date")} />
      </label>

      <label className={LABEL}>
        Category
        <select className={FIELD} data-testid="event-category"
                value={draft.category_id} onChange={set("category_id")}>
          <option value="">Choose one…</option>
          {pickable.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
              {c.active ? "" : " (retired)"}
            </option>
          ))}
        </select>
      </label>

      <label className={LABEL}>
        Starts
        <input type="time" className={FIELD} data-testid="event-start"
               value={draft.start_time} onChange={set("start_time")} />
      </label>

      <label className={LABEL}>
        Ends
        <input type="time" className={FIELD} data-testid="event-end"
               value={draft.end_time} onChange={set("end_time")}
               disabled={!draft.start_time} />
      </label>

      {/* Said out loud rather than left to be inferred from two empty boxes. An all-day
          event is the common case in a hotel and it is a state, not a gap. */}
      <p className="sm:col-span-2 -mt-2 text-[11px] text-stone-500">
        {draft.start_time
          ? "Leave both times empty for an all-day event."
          : "No time set — this is an all-day event."}
      </p>

      <label className={LABEL}>
        Repeats
        <select className={FIELD} data-testid="event-repeat"
                value={draft.repeat} onChange={set("repeat")}>
          <option value="">Does not repeat</option>
          <option value="weekly">Every week</option>
          <option value="monthly">Every month</option>
        </select>
      </label>

      {draft.repeat && (
        <label className={LABEL}>
          Until
          <input type="date" className={FIELD} data-testid="event-repeat-until"
                 value={draft.repeat_until} min={draft.date}
                 onChange={set("repeat_until")} />
        </label>
      )}

      {draft.repeat && (
        <p className="sm:col-span-2 -mt-2 text-[11px] text-stone-500">
          A repeat is one event drawn on many days. Editing or deleting it changes the
          whole series — single occurrences cannot be moved or removed on their own.
        </p>
      )}

      <label className={`${LABEL} sm:col-span-2`}>
        Notes
        <textarea className={FIELD} rows={3} data-testid="event-description"
                  value={draft.description || ""} onChange={set("description")}
                  placeholder="Anything the person reading this needs to know." />
      </label>

      <div className="sm:col-span-2 flex flex-wrap items-center gap-3 pt-2">
        <button type="submit" disabled={busy} data-testid="event-save"
                className="border border-orange-500 text-orange-400 hover:bg-orange-500/10 disabled:opacity-40 px-5 py-2 text-xs font-mono uppercase tracking-widest transition-colors">
          {editing ? "Save changes" : "Add to the planner"}
        </button>
        <button type="button" onClick={onCancel}
                className="text-xs font-mono uppercase tracking-widest text-stone-500 hover:text-stone-300">
          Cancel
        </button>
        {editing && (
          <button type="button" onClick={onDelete} data-testid="event-delete"
                  className="ml-auto flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-stone-500 hover:text-red-400">
            <Trash2 size={14} /> Delete
          </button>
        )}
      </div>
    </form>
  );
}

/** The property's own vocabulary — the point of the categories being stored, not coded. */
function CategoryManager({ categories, onSaved, onClose }) {
  const [name, setName] = useState("");
  const [colour, setColour] = useState("#f97316");
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setBusy(true);
    try {
      await api.post("/planner/categories", { name, colour, active: true });
      setName("");
      toast.success(`${name} added`);
      onSaved();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const save = async (c, patch) => {
    try {
      await api.put(`/planner/categories/${c.id}`, {
        name: c.name, colour: c.colour, active: c.active, ...patch,
      });
      onSaved();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  const remove = async (c) => {
    try {
      await api.delete(`/planner/categories/${c.id}`);
      toast.success(`${c.name} removed`);
      onSaved();
    } catch (e) {
      // A category events are filed under refuses with a 409 that says how many and what
      // to do instead. Shown as it is written rather than replaced with "Failed".
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  return (
    <div className="border border-stone-800 bg-stone-900/60 rounded p-5" data-testid="category-manager">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-400">Categories</h2>
        <button type="button" onClick={onClose} aria-label="Close categories"
                className="text-stone-500 hover:text-stone-300">
          <X size={16} />
        </button>
      </div>
      <p className="text-xs text-stone-500 mb-4 max-w-xl">
        These are yours to name. A category events are already filed under cannot be
        removed — switch it off instead and it leaves the picker while those events keep
        their colour.
      </p>

      <ul className="divide-y divide-stone-800 mb-5">
        {categories.map((c) => (
          <li key={c.id} className="flex items-center gap-3 py-2" data-testid={`category-${c.id}`}>
            <input
              type="color"
              value={c.colour}
              aria-label={`Colour for ${c.name}`}
              onChange={(e) => save(c, { colour: e.target.value })}
              className="w-7 h-7 bg-transparent border border-stone-700 rounded cursor-pointer"
            />
            <span className={`flex-1 text-sm ${c.active ? "text-stone-200" : "text-stone-600 line-through"}`}>
              {c.name}
            </span>
            <button type="button" onClick={() => save(c, { active: !c.active })}
                    className={`${PILL} ${c.active ? UNCHOSEN : CHOSEN}`}>
              {c.active ? "Switch off" : "Switch on"}
            </button>
            <button type="button" onClick={() => remove(c)} aria-label={`Remove ${c.name}`}
                    className="text-stone-600 hover:text-red-400">
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-end gap-3">
        <label className={LABEL}>
          New category
          <input className={FIELD} data-testid="category-name" value={name}
                 onChange={(e) => setName(e.target.value)} placeholder="Spa" />
        </label>
        <input type="color" value={colour} aria-label="Colour for the new category"
               onChange={(e) => setColour(e.target.value)}
               className="w-10 h-10 bg-transparent border border-stone-700 rounded cursor-pointer" />
        <button type="button" onClick={add} disabled={busy || !name.trim()}
                data-testid="category-add"
                className="border border-stone-700 hover:border-orange-500 hover:text-orange-400 disabled:opacity-40 px-4 py-2 text-xs font-mono uppercase tracking-widest transition-colors">
          Add
        </button>
      </div>
    </div>
  );
}

export default function Planner() {
  const [view, setView] = useState(DEFAULT_VIEW);
  const [anchor, setAnchor] = useState(todayLocal);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState(null);
  const [showing, setShowing] = useState(null);
  const [managing, setManaging] = useState(false);
  const [busy, setBusy] = useState(false);
  // Paging quickly fires overlapping requests; without this the slower one can land last
  // and paint a month the arrows have already left. Same guard as Analytics.jsx.
  const latest = useRef(0);

  const { start, end } = useMemo(() => rangeFor(view, anchor), [view, anchor]);

  const load = useCallback(() => {
    const seq = ++latest.current;
    setLoading(true);
    api
      .get("/planner/events", { params: { start, end } })
      .then((r) => {
        if (seq === latest.current) setData(r.data);
      })
      .catch((e) => {
        if (seq !== latest.current) return;
        // Cleared rather than kept: a stale month sitting under changed arrows reads as
        // the answer to the period you are now looking at.
        setData(null);
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      })
      .finally(() => {
        if (seq === latest.current) setLoading(false);
      });
  }, [start, end]);

  useEffect(() => {
    load();
  }, [load]);

  const canEdit = Boolean(data?.can_edit);
  const categories = useMemo(() => data?.categories || [], [data]);
  const colours = useMemo(() => categoriesById(categories), [categories]);
  const days = useMemo(() => byDay(data?.events), [data]);
  // Until the property's own today arrives, the browser's is a good enough guess and is
  // replaced by the server's on the first response.
  const today = data?.today || todayLocal();

  const openEditor = (event, onDay) => {
    if (!event) {
      if (!canEdit) return;
      const firstActive = categories.find((c) => c.active);
      setShowing(null);
      setDraft(blankEvent(onDay || anchor, firstActive?.id));
      return;
    }
    setDraft(null);
    setShowing(event);
  };

  const editShowing = () => {
    setDraft({
      id: showing.id,
      title: showing.title,
      description: showing.description || "",
      date: showing.date,
      start_time: showing.start_time || "",
      end_time: showing.end_time || "",
      category_id: showing.category_id,
      repeat: showing.repeat || "",
      repeat_until: showing.repeat_until || "",
    });
    setShowing(null);
  };

  const save = async () => {
    setBusy(true);
    // Sent as written, including the empty strings: the server is what turns "" into "no
    // time at all", in one place, so that this form and any other caller cannot disagree
    // about what an untimed event looks like.
    const body = {
      title: draft.title,
      description: draft.description || null,
      date: draft.date,
      start_time: draft.start_time,
      end_time: draft.end_time,
      category_id: draft.category_id,
      repeat: draft.repeat || null,
      repeat_until: draft.repeat ? draft.repeat_until : null,
    };
    try {
      if (draft.id) await api.put(`/planner/events/${draft.id}`, body);
      else await api.post("/planner/events", body);
      toast.success(draft.id ? "Event updated" : "Added to the planner");
      setDraft(null);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.delete(`/planner/events/${draft.id}`);
      toast.success("Removed from the planner");
      setDraft(null);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const grid = useMemo(() => monthGrid(anchor), [anchor]);
  const weekDays = useMemo(
    () => (view === "day" ? [anchor] : daysBetween(startOfWeek(anchor), rangeFor("week", anchor).end)),
    [view, anchor],
  );

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Property</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Planner
      </h1>
      <p className="text-stone-500 font-mono text-xs mb-8" data-testid="planner-period">
        {periodLabel(view, anchor)}
      </p>

      {/* Controls. The arrows change only the period — the view they are pressed in is the
          view you stay in, which is why `step` cannot see it. */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex gap-2">
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              data-testid={`view-${v}`}
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={`${PILL} ${view === v ? CHOSEN : UNCHOSEN}`}
            >
              {VIEW_LABELS[v]}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 ml-auto">
          <button type="button" data-testid="period-prev" aria-label="Previous period"
                  onClick={() => setAnchor((a) => step(view, a, -1))}
                  className="p-2 text-stone-500 hover:text-orange-400 transition-colors">
            <ChevronLeft size={18} />
          </button>
          <button type="button" data-testid="period-today"
                  onClick={() => setAnchor(today)}
                  className={`${PILL} ${UNCHOSEN}`}>
            Today
          </button>
          <button type="button" data-testid="period-next" aria-label="Next period"
                  onClick={() => setAnchor((a) => step(view, a, 1))}
                  className="p-2 text-stone-500 hover:text-orange-400 transition-colors">
            <ChevronRight size={18} />
          </button>
        </div>

        {canEdit && (
          <div className="flex gap-2">
            <button type="button" data-testid="new-event" onClick={() => openEditor(null)}
                    className="flex items-center gap-2 border border-orange-500 text-orange-400 hover:bg-orange-500/10 px-4 py-2 text-xs font-mono uppercase tracking-widest transition-colors">
              <Plus size={14} /> New event
            </button>
            <button type="button" data-testid="manage-categories"
                    onClick={() => setManaging((m) => !m)}
                    className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-4 py-2 text-xs font-mono uppercase tracking-widest transition-colors">
              <Tags size={14} /> Categories
            </button>
          </div>
        )}
      </div>

      {managing && canEdit && (
        <div className="mb-6 max-w-3xl">
          <CategoryManager categories={categories} onSaved={load}
                           onClose={() => setManaging(false)} />
        </div>
      )}

      {/* The editor, above the grid rather than over it: this is a planning screen, and
          the month you are planning into is the context for what you are typing. */}
      {draft && (
        <div className="planner-sheet mb-6 max-w-3xl border border-stone-800 bg-stone-900/60 rounded p-5"
             data-testid="event-form">
          <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-400 mb-4">
            {draft.id ? "Edit event" : "New event"}
          </h2>
          <EventForm draft={draft} categories={categories} onChange={setDraft}
                     onSave={save} onCancel={() => setDraft(null)} onDelete={remove}
                     busy={busy} />
        </div>
      )}

      {showing && (
        <div className="planner-sheet mb-6 max-w-3xl border border-stone-800 bg-stone-900/60 rounded p-5"
             data-testid="event-detail">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span aria-hidden="true" className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: colours[showing.category_id]?.colour || "#78716c" }} />
                <span className="text-[10px] font-mono uppercase tracking-widest text-stone-500">
                  {colours[showing.category_id]?.name || "Uncategorised"}
                </span>
              </div>
              <h2 className="text-xl text-stone-100">{showing.title}</h2>
              <div className="flex flex-wrap items-center gap-4 mt-2 text-xs text-stone-400">
                <span className="flex items-center gap-1.5">
                  <CalendarDays size={13} /> {longDate(showing.occurrence_date)}
                </span>
                <span className="flex items-center gap-1.5">
                  <Clock size={13} /> {timeLabel(showing)}
                </span>
                {repeatLabel(showing) && (
                  <span className="flex items-center gap-1.5 text-orange-400/80">
                    <Repeat size={13} /> {repeatLabel(showing)}
                  </span>
                )}
              </div>
              {showing.description && (
                <p className="mt-3 text-sm text-stone-400 whitespace-pre-wrap">
                  {showing.description}
                </p>
              )}
              {showing.created_by_name && (
                <p className="mt-3 text-[10px] font-mono uppercase tracking-widest text-stone-600">
                  Added by {showing.created_by_name}
                </p>
              )}
            </div>
            <div className="flex items-center gap-3 shrink-0">
              {canEdit && (
                <button type="button" onClick={editShowing} data-testid="event-edit"
                        className="text-xs font-mono uppercase tracking-widest text-stone-400 hover:text-orange-400">
                  Edit
                </button>
              )}
              <button type="button" onClick={() => setShowing(null)} aria-label="Close"
                      className="text-stone-500 hover:text-stone-300">
                <X size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      {view === "month" ? (
        <div className="group border-t border-l border-stone-800 rounded overflow-hidden"
             data-testid="month-grid">
          <div className="grid grid-cols-7">
            {WEEKDAYS.map((d) => (
              <div key={d}
                   className="border-b border-r border-stone-800 px-2 py-2 text-[10px] font-mono uppercase tracking-widest text-stone-600">
                {d}
              </div>
            ))}
          </div>
          {grid.map((week) => (
            <div key={week[0]} className="grid grid-cols-7">
              {week.map((day) => (
                <MonthCell key={day} day={day} anchor={anchor} today={today}
                           events={days[day] || []} colours={colours}
                           onOpen={openEditor} onAdd={(d) => openEditor(null, d)}
                           canEdit={canEdit} />
              ))}
            </div>
          ))}
        </div>
      ) : (
        <div className={`border border-stone-800 rounded overflow-hidden grid ${
          view === "week" ? "grid-cols-2 sm:grid-cols-4 lg:grid-cols-7" : "grid-cols-1"
        }`} data-testid={`${view}-grid`}>
          {weekDays.map((day, i) => (
            <DayColumn key={day} day={day} today={today} events={days[day] || []}
                       colours={colours} onOpen={openEditor}
                       heading={view === "day"
                         ? longDate(day)
                         : `${WEEKDAYS[i]} ${Number(day.slice(8))}`} />
          ))}
        </div>
      )}

      {/* The legend, from the property's own categories rather than a list in this file. */}
      {categories.length > 0 && (
        <div className="flex flex-wrap gap-4 mt-4 text-[10px] font-mono uppercase tracking-widest text-stone-500">
          {categories.filter((c) => c.active).map((c) => (
            <span key={c.id} className="flex items-center gap-2">
              <span aria-hidden="true" className="w-3 h-2" style={{ backgroundColor: c.colour }} />
              {c.name}
            </span>
          ))}
        </div>
      )}

      {loading && <p className="text-stone-500 text-sm mt-6">Loading…</p>}
      {!loading && data && data.events.length === 0 && (
        <p className="text-stone-500 text-sm mt-6">
          Nothing planned for {periodLabel(view, anchor).toLowerCase()}.
          {canEdit ? " Add the first thing." : ""}
        </p>
      )}
    </div>
  );
}
