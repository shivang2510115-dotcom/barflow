import { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight } from "lucide-react";

/**
 * Who worked which day.
 *
 * The screen a manager opens daily, so it is built for speed rather than for ceremony:
 * one tap cycles a day through the statuses, and a mis-tap is corrected by tapping
 * again. There is no save button — marking IS the save, and the API is idempotent, so
 * a double tap corrects rather than duplicating.
 *
 * **An unmarked day is not an absence.** The grid shows it as blank and payroll credits
 * it as present. Docking pay for a day nobody recorded would mean a manager who missed a
 * week silently cut everybody's salary, and that is discovered on payday.
 */

// The cycle, in the order somebody taps through it. Present first because it is the
// answer most days, and week-off last because it is set once a week rather than daily.
const CYCLE = ["present", "absent", "leave", "half_day", "week_off"];

const LOOK = {
  present:  { short: "P", cls: "bg-state-free/15 text-state-free border-state-free/40" },
  absent:   { short: "A", cls: "bg-state-alert/15 text-state-alert border-state-alert/40" },
  leave:    { short: "L", cls: "bg-state-inspected/15 text-state-inspected border-state-inspected/40" },
  half_day: { short: "½", cls: "bg-state-dirty/15 text-state-dirty border-state-dirty/40" },
  week_off: { short: "—", cls: "bg-raised text-faint border-hairline" },
};
const BLANK = { short: "", cls: "border-hairline text-faint hover:border-hairline-strong" };

const monthLabel = (m) =>
  new Date(`${m}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });

const shift = (month, by) => {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + by, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};

const daysIn = (month) => {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m, 0).getDate();
};

export default function Attendance() {
  const [month, setMonth] = useState(() => {
    const n = new Date();
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}`;
  });
  const [board, setBoard] = useState(null);
  const [failed, setFailed] = useState(false);
  // Marks applied locally the instant they are tapped. A manager marking thirty people
  // should not wait for thirty round trips to see what they have done.
  const [pending, setPending] = useState({});

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const { data } = await api.get("/attendance", { params: { month } });
      setBoard(data);
      setPending({});
    } catch {
      setFailed(true);
    }
  }, [month]);

  useEffect(() => { load(); }, [load]);

  const marks = useMemo(() => {
    const out = {};
    for (const m of board?.marks || []) out[`${m.user_id}|${m.on}`] = m.status;
    return { ...out, ...pending };
  }, [board, pending]);

  const cycle = async (userId, day) => {
    const key = `${userId}|${day}`;
    const now = marks[key];
    const next = CYCLE[(CYCLE.indexOf(now) + 1) % CYCLE.length];
    // Optimistic: the tap is the point, and the request confirming it is not something a
    // manager should have to watch.
    setPending((p) => ({ ...p, [key]: next }));
    try {
      await api.put("/attendance", { user_id: userId, on: day, status: next });
    } catch (e) {
      setPending((p) => {
        const back = { ...p };
        if (now) back[key] = now; else delete back[key];
        return back;
      });
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not mark that");
    }
  };

  if (failed) return <div className="p-6 md:p-10 text-sm text-muted2">Could not load attendance.</div>;
  if (!board) return <div className="p-6 md:p-10 text-sm text-faint">Loading…</div>;

  const days = Array.from({ length: daysIn(month) }, (_, i) => i + 1);
  const today = board.today;

  return (
    <div className="p-6 md:p-10">
      <header className="mb-6">
        <h1 className="font-display text-2xl text-ink">Attendance</h1>
        <p className="text-sm text-muted2 mt-1 max-w-prose">
          Tap a day to cycle it. A day left blank is not an absence — payroll counts it as
          present, so a month nobody marked pays everybody in full.
        </p>
      </header>

      <div className="flex items-center gap-3 mb-5">
        <button onClick={() => setMonth(shift(month, -1))} aria-label="Previous month"
                className="px-3 rounded border border-hairline text-muted2 hover:border-hairline-strong">
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
        </button>
        <span className="font-display text-[17px] text-ink min-w-[11rem]">
          {monthLabel(month)}
        </span>
        <button onClick={() => setMonth(shift(month, 1))} aria-label="Next month"
                className="px-3 rounded border border-hairline text-muted2 hover:border-hairline-strong">
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </button>

        <div className="ml-auto flex flex-wrap gap-3 text-[12px] text-muted2">
          {CYCLE.map((s) => (
            <span key={s} className="inline-flex items-center gap-1.5">
              <span className={`inline-flex items-center justify-center w-5 h-5 rounded border text-[11px] ${LOOK[s].cls}`}>
                {LOOK[s].short}
              </span>
              {s.replace("_", " ")}
            </span>
          ))}
        </div>
      </div>

      {board.staff.length === 0 ? (
        <p className="text-sm text-muted2">Nobody on the roster yet.</p>
      ) : (
        <div className="border border-hairline rounded bg-surface overflow-x-auto">
          <table className="text-[13px]">
            <caption className="sr-only">Attendance for {monthLabel(month)}</caption>
            <thead>
              <tr>
                <th scope="col" className="sticky left-0 bg-surface text-left font-normal
                                           px-4 py-3 text-[11px] uppercase tracking-[0.2em] text-faint
                                           min-w-[12rem] border-r border-hairline">
                  Name
                </th>
                {days.map((d) => {
                  const iso = `${month}-${String(d).padStart(2, "0")}`;
                  return (
                    <th key={d} scope="col"
                        className={`font-normal px-1 py-3 text-[11px] w-8
                          ${iso === today ? "text-brass" : "text-faint"}`}>
                      {d}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {board.staff.map((person) => (
                <tr key={person.id}>
                  <th scope="row" className="sticky left-0 bg-surface text-left font-normal
                                             px-4 py-2 border-r border-hairline">
                    <span className="block text-ink truncate">{person.name}</span>
                    {person.designation && (
                      <span className="block text-[11px] text-faint truncate">
                        {person.designation}
                      </span>
                    )}
                  </th>
                  {days.map((d) => {
                    const iso = `${month}-${String(d).padStart(2, "0")}`;
                    const look = LOOK[marks[`${person.id}|${iso}`]] || BLANK;
                    return (
                      <td key={d} className="px-0.5 py-1 text-center">
                        <button
                          data-compact
                          onClick={() => cycle(person.id, iso)}
                          aria-label={`${person.name}, ${iso}`}
                          className={`w-7 h-7 rounded border text-[12px] transition-colors ${look.cls}`}
                        >
                          {look.short}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
