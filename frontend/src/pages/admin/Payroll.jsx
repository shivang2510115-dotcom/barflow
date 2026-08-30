import { useCallback, useEffect, useRef, useState } from "react";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Lock, RotateCcw, AlertTriangle } from "lucide-react";

/**
 * What a month came to, and what was handed over.
 *
 * Nothing here is edited once paid. A run is drafted, adjusted, marked paid — and after
 * that a correction is a reversal and a second run, never a change. "What did we pay
 * Priya in August" has exactly one answer, forever, and it has to survive the argument.
 *
 * The arithmetic is shown rather than summarised, because the first question anybody
 * asks about a payslip is why it is that much.
 */

const thisMonth = () => {
  const n = new Date();
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}`;
};

const monthLabel = (m) =>
  new Date(`${m}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });

function Payslip({ slip, editable, onAdjust }) {
  const [adding, setAdding] = useState(null);   // "additions" | "deductions" | null
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState("");

  const submit = () => {
    const value = Number(amount);
    if (!label.trim() || !value) return;
    const next = {
      additions: slip.additions || [],
      deductions: slip.deductions || [],
    };
    next[adding] = [...next[adding], { label: label.trim(), amount: value }];
    onAdjust(next);
    setAdding(null); setLabel(""); setAmount("");
  };

  return (
    <li className="border-b border-hairline last:border-0 px-5 py-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-[15px] text-ink">{slip.name}</span>
        {slip.designation && (
          <span className="text-[12px] text-faint">{slip.designation}</span>
        )}
        <span className="ml-auto font-display text-[18px] tabular-nums text-ink">
          {currency(slip.net)}
        </span>
      </div>

      {/* The working, not just the total. */}
      <dl className="mt-3 grid gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2 lg:grid-cols-3">
        <div className="flex justify-between gap-3">
          <dt className="text-faint">Monthly salary</dt>
          <dd className="tabular-nums text-muted2">{currency(slip.salary_monthly)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-faint">Present</dt>
          <dd className="tabular-nums text-muted2">
            {slip.present}
            {slip.unmarked > 0 && (
              <span className="text-faint"> + {slip.unmarked} unmarked</span>
            )}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-faint">Unpaid absence</dt>
          <dd className="tabular-nums text-muted2">{slip.unpaid_absence}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-faint">Gross</dt>
          <dd className="tabular-nums text-ink">{currency(slip.gross)}</dd>
        </div>
        {(slip.additions || []).map((a, i) => (
          <div key={`a${i}`} className="flex justify-between gap-3">
            <dt className="text-faint">{a.label}</dt>
            <dd className="tabular-nums text-state-free">+{currency(a.amount).replace("−", "")}</dd>
          </div>
        ))}
        {(slip.deductions || []).map((d, i) => (
          <div key={`d${i}`} className="flex justify-between gap-3">
            <dt className="text-faint">{d.label}</dt>
            <dd className="tabular-nums text-state-alert">−{currency(d.amount).replace("−", "")}</dd>
          </div>
        ))}
        {slip.advance_recovered > 0 && (
          <div className="flex justify-between gap-3">
            <dt className="text-faint">Advance recovered</dt>
            <dd className="tabular-nums text-state-alert">
              −{currency(slip.advance_recovered).replace("−", "")}
            </dd>
          </div>
        )}
      </dl>

      {editable && (
        adding ? (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <input value={label} onChange={(e) => setLabel(e.target.value)}
                   placeholder={adding === "additions" ? "Overtime" : "PF"}
                   aria-label="Line description"
                   className="bg-ground border border-hairline rounded px-3 text-[14px] text-ink" />
            <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)}
                   placeholder="0" aria-label="Amount"
                   className="w-28 bg-ground border border-hairline rounded px-3 text-[14px] text-ink" />
            <button onClick={submit}
                    className="px-4 rounded bg-brass hover:bg-brass-deep text-on-brass text-[13px]">
              Add
            </button>
            <button onClick={() => setAdding(null)}
                    className="px-3 rounded border border-hairline text-muted2 text-[13px]">
              Cancel
            </button>
          </div>
        ) : (
          <div className="mt-3 flex gap-3">
            {[["additions", "Add payment"], ["deductions", "Add deduction"]].map(([k, t]) => (
              <button key={k} onClick={() => setAdding(k)}
                      className="text-[12px] text-faint hover:text-brass transition-colors">
                {t}
              </button>
            ))}
          </div>
        )
      )}
    </li>
  );
}

export default function Payroll() {
  const [runs, setRuns] = useState([]);
  const [open, setOpen] = useState(null);
  const [month, setMonth] = useState(thisMonth);
  const [busy, setBusy] = useState(false);
  // Opening a run scrolls to it. The list of months grows without limit, so the detail
  // can open a screen and a half below the button that opened it — and a manager who
  // presses Open and sees nothing move concludes it did not work.
  const detail = useRef(null);

  const loadRuns = useCallback(async () => {
    try {
      setRuns((await api.get("/payroll/runs")).data);
    } catch {
      toast.error("Could not load payroll");
    }
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const openRun = async (id) => {
    try {
      setOpen((await api.get(`/payroll/runs/${id}`)).data);
      // After paint, and against the scrolling element rather than the window — the
      // app's <main> is what scrolls, so window-level scrolling moves nothing.
      requestAnimationFrame(() =>
        detail.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not open it");
    }
  };

  const act = async (fn, done) => {
    setBusy(true);
    try {
      await fn();
      toast.success(done);
      await loadRuns();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "That did not work");
    } finally {
      setBusy(false);
    }
  };

  const draft = () => act(async () => {
    const { data } = await api.post("/payroll/runs", { month });
    await openRun(data.id);
  }, `${monthLabel(month)} drafted`);

  const pay = () => act(async () => {
    const { data } = await api.post(`/payroll/runs/${open.id}/pay`);
    setOpen(data);
  }, "Marked paid — this run is now fixed");

  const reverse = () => act(async () => {
    await api.post(`/payroll/runs/${open.id}/reverse`);
    await openRun(open.id);
  }, "Reversed — draft a new run to replace it");

  return (
    <div className="p-6 md:p-10 max-w-4xl">
      <header className="mb-6">
        <h1 className="font-display text-2xl text-ink">Payroll</h1>
        <p className="text-sm text-muted2 mt-1 max-w-prose">
          Drafted from the month's attendance. Once a run is paid it does not change —
          a correction is a reversal and a new run, so what was paid stays answerable.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 bg-surface border border-hairline rounded p-5 mb-8">
        <label>
          <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-2">
            Month
          </span>
          <input type="month" value={month} onChange={(e) => setMonth(e.target.value)}
                 className="bg-ground border border-hairline rounded px-3 text-[15px] text-ink" />
        </label>
        <button onClick={draft} disabled={busy}
                className="inline-flex items-center gap-2 px-5 rounded bg-brass hover:bg-brass-deep
                           text-on-brass text-[13px] font-medium disabled:opacity-40 transition-colors">
          <Plus className="h-4 w-4" aria-hidden="true" /> Draft this month
        </button>
      </div>

      {runs.length === 0 ? (
        <p className="text-sm text-muted2">No payroll runs yet.</p>
      ) : (
        <ul className="border border-hairline rounded bg-surface divide-y divide-hairline mb-8">
          {runs.map((r) => (
            <li key={r.id} className="px-5 py-3 flex items-center gap-3">
              <span className="text-[15px] text-ink flex-1">{monthLabel(r.month)}</span>
              <span className={`text-[12px] ${
                r.status === "paid" ? "text-state-free"
                : r.status === "reversed" ? "text-state-alert" : "text-muted2"}`}>
                {r.status}
              </span>
              <button onClick={() => openRun(r.id)}
                      className="px-3 rounded border border-hairline text-[13px] text-muted2
                                 hover:border-hairline-strong transition-colors">
                Open
              </button>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <section ref={detail} className="border border-hairline rounded bg-surface scroll-mt-6">
          <header className="px-5 py-4 border-b border-hairline flex flex-wrap items-center gap-3">
            <h2 className="font-display text-[17px] text-ink flex-1">
              {monthLabel(open.month)}
              <span className="ml-3 text-[12px] text-faint">{open.status}</span>
            </h2>
            <span className="font-display text-[18px] tabular-nums text-ink">
              {currency(open.total_net)}
            </span>
            {open.status === "draft" && (
              <button onClick={pay} disabled={busy}
                      className="inline-flex items-center gap-2 px-4 rounded bg-brass
                                 hover:bg-brass-deep text-on-brass text-[13px] disabled:opacity-40">
                <Lock className="h-4 w-4" aria-hidden="true" /> Mark paid
              </button>
            )}
            {open.status === "paid" && !open.reversed_by && (
              <button onClick={reverse} disabled={busy}
                      className="inline-flex items-center gap-2 px-4 rounded border border-hairline
                                 text-muted2 text-[13px] hover:border-state-alert hover:text-state-alert">
                <RotateCcw className="h-4 w-4" aria-hidden="true" /> Reverse
              </button>
            )}
          </header>

          {(open.not_on_payroll || []).length > 0 && (
            <p className="px-5 py-3 border-b border-hairline text-[12px] text-muted2
                          flex items-start gap-2">
              <AlertTriangle className="h-3.5 w-3.5 text-state-dirty shrink-0 mt-0.5"
                             aria-hidden="true" />
              <span>
                Not on this run — no salary recorded:{" "}
                {open.not_on_payroll.map((p) => p.name).join(", ")}. Set one on the Staff
                screen if they should be paid.
              </span>
            </p>
          )}

          {open.payslips.length === 0 ? (
            <p className="px-5 py-6 text-[13px] text-faint">
              Nobody on this run. Record a salary against somebody first.
            </p>
          ) : (
            <ul>
              {open.payslips.map((s) => (
                <Payslip
                  key={s.id}
                  slip={s}
                  editable={open.status === "draft"}
                  onAdjust={(lines) => act(async () => {
                    await api.patch(`/payroll/runs/${open.id}/payslips/${s.id}`, lines);
                    await openRun(open.id);
                  }, "Adjusted")}
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
