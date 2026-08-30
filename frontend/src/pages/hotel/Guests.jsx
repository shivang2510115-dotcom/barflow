import { useEffect, useState } from "react";
import axios from "axios";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

export default function Guests() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .get("/guests", { params: { q: debouncedQ }, signal: controller.signal })
      .then((r) => setRows(r.data))
      .catch((e) => {
        if (axios.isCancel(e) || e.code === "ERR_CANCELED") return;
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [debouncedQ]);

  const open = (id) =>
    api
      .get(`/guests/${id}`)
      .then((r) => setSelected(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));

  /**
   * Mark this guest as not to be messaged, or undo it.
   *
   * `PUT /guests/{id}` replaces the editable half of the record, so the whole of it is
   * sent back rather than the one field — anything omitted here would be cleared. The
   * server treats a *missing* `no_messages` as "leave it alone" for exactly that reason
   * (see backend/models/hotel.py), which is the safety net rather than the mechanism.
   *
   * This is the only place the flag can be set, and it is worth its own control rather
   * than a line in the notes: unsolicited commercial messaging is regulated in India, the
   * property carries that risk, and "we wrote it in the notes" is not a record of consent.
   */
  const setConsent = (noMessages) => {
    const fields = [
      "name", "phone", "email", "address", "nationality", "id_proof_type",
      "id_proof_number", "notes",
    ];
    const body = Object.fromEntries(fields.map((k) => [k, selected[k] ?? null]));
    api
      .put(`/guests/${selected.id}`, { ...body, no_messages: noMessages })
      .then(() => open(selected.id))
      .then(() =>
        toast.success(
          noMessages ? "This customer will not be messaged" : "Messaging allowed again",
        ),
      )
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  };

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-brass mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Guests
      </h1>

      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by name or phone"
        className="mb-6 w-full max-w-md bg-transparent border-b border-hairline-strong text-ink py-2 focus:border-brass outline-none"
      />

      <div className="grid gap-8 lg:grid-cols-2">
        <div>
          {loading ? (
            <p className="text-muted2">Searching…</p>
          ) : rows.length === 0 ? (
            <p className="text-muted2">No guests match.</p>
          ) : (
            <ul className="divide-y divide-hairline">
              {rows.map((g) => (
                <li key={g.id}>
                  <button
                    onClick={() => open(g.id)}
                    className={`w-full text-left py-3 hover:text-brass ${selected?.id === g.id ? "text-brass" : ""}`}
                  >
                    {g.name}
                    <span className="block text-xs text-faint font-mono">{g.phone}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {selected && (
          <div className="border border-hairline bg-surface rounded p-5 h-fit">
            <h2 className="text-lg font-semibold">{selected.name}</h2>
            <p className="text-xs text-faint font-mono mb-4">{selected.phone}</p>

            <div className="grid grid-cols-2 gap-3 mb-5">
              <div>
                <div className="text-[11px] tracking-[0.2em] uppercase text-faint">Stays</div>
                <div className="text-2xl font-semibold">{selected.stays?.length || 0}</div>
              </div>
              <div>
                <div className="text-[11px] tracking-[0.2em] uppercase text-faint">
                  Bar & restaurant
                </div>
                <div className="text-2xl font-semibold text-brass">
                  {currency(selected.outlet_spend)}
                </div>
                <div className="text-xs text-faint">
                  {selected.outlet_orders} bill{selected.outlet_orders === 1 ? "" : "s"}
                </div>
              </div>
            </div>

            {(selected.stays || []).length > 0 && (
              <>
                <div className="text-[11px] tracking-[0.2em] uppercase text-faint mb-2">
                  Stay history
                </div>
                <ul className="text-sm space-y-1">
                  {selected.stays.map((s) => (
                    <li key={s.id} className="font-mono text-xs text-muted2">
                      {s.check_in} → {s.check_out} · {s.reference} · {s.status}
                    </li>
                  ))}
                </ul>
              </>
            )}

            {/* The dates this property marks for them. Recorded here or at the till; the
                greeting itself is pressed from Customers -> Messaging on the day. */}
            <div className="mt-6 pt-5 border-t border-hairline">
              <div className="text-[11px] tracking-[0.2em] uppercase text-faint mb-2">
                Occasions
              </div>
              {(selected.occasions || []).length === 0 ? (
                <p className="text-xs text-faint">None recorded.</p>
              ) : (
                <ul className="text-sm space-y-1">
                  {selected.occasions.map((o) => (
                    <li key={o.id} className="font-mono text-xs text-muted2">
                      {o.date} · {o.label}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="mt-5 pt-5 border-t border-hairline">
              <label className="flex items-start gap-3 text-sm text-muted2 cursor-pointer">
                <input
                  type="checkbox"
                  data-testid="guest-no-messages"
                  checked={Boolean(selected.no_messages)}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="accent-brass w-4 h-4 mt-0.5"
                />
                <span>
                  Do not message this customer
                  <span className="block text-[11px] text-faint mt-1">
                    Honoured everywhere — no greeting and no follow-up, whatever else is
                    configured.
                  </span>
                </span>
              </label>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
