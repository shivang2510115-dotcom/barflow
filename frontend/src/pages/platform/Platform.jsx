import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Building2, KeyRound, LogOut, ShieldCheck } from "lucide-react";

import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { LIVE, PENDING, STATUSES, STATUS_BLURB, SUSPENDED } from "@/lib/tenancy";
import SubscriptionPanel, { SubscriptionCell } from "@/pages/platform/SubscriptionPanel";

/**
 * `/platform` — the operator's console, and the only screen they have.
 *
 * It renders its own chrome rather than sitting inside AppLayout. That is not a style
 * choice: the sidebar is section-scoped and permission-filtered, and the operator holds no
 * sections and no permissions, so AppLayout would render a frame with an empty nav around
 * a page whose every neighbouring route 403s. A console with nothing in its menu is worse
 * than no menu.
 *
 * Everything here reads `/api/platform/*`, which is the only family of endpoints the
 * operator is allowed. There is deliberately no call to a hotel endpoint anywhere in this
 * file — not `/auth/me`, not `/api/property` — because every one of them answers 403, and
 * the design that makes them do so is the reason the operator's login is not a key into
 * customer data.
 */

const FILTERS = [{ key: "", label: "All" }, ...STATUSES.map((s) => ({ key: s, label: s }))];

const STATUS_TONE = {
  [PENDING]: "text-orange-400 border-orange-500/40",
  [LIVE]: "text-emerald-400 border-emerald-500/40",
  [SUSPENDED]: "text-red-400 border-red-500/40",
};

// What the button on a hotel in this state does next. `null` means there is nothing to
// offer — which today is nothing, but a state added later lands here rather than in a
// chain of ternaries that quietly picks the wrong verb for it.
const NEXT = {
  [PENDING]: { to: LIVE, label: "Approve", done: "Approved" },
  [LIVE]: { to: SUSPENDED, label: "Suspend", done: "Suspended" },
  [SUSPENDED]: { to: LIVE, label: "Restore", done: "Restored" },
};

function signupDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

function StatusPill({ status }) {
  return (
    <span
      className={`text-[10px] tracking-widest uppercase border rounded-full px-2 py-1 whitespace-nowrap ${
        STATUS_TONE[status] || "text-stone-500 border-stone-700"
      }`}
    >
      {status || "unknown"}
    </span>
  );
}

function Figure({ label, value }) {
  return (
    <div className="border border-stone-800 bg-stone-900 p-4">
      <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500">{label}</div>
      <div className="text-2xl font-bold tabular-nums text-stone-100 mt-2">{value}</div>
    </div>
  );
}

/**
 * The hotel's size and how far through setup it got — the detail endpoint's whole answer.
 *
 * `ready_to_trade` is the line the operator is actually here for: it is the difference
 * between a business that has built its rooms and its rate sheet and somebody who filled
 * in a form and never came back. It is stated as a verdict rather than left to be inferred
 * from six counts, because inferring it six times a day is how it stops being read.
 */
function Detail({ detail, payments, onChanged }) {
  const { counts = {}, setup = {} } = detail;
  const checks = [
    ["Rooms built", setup.has_rooms],
    ["Rates set", setup.has_rates],
    ["GSTIN on file", setup.has_gstin],
  ];
  return (
    <div className="mt-8 border border-stone-800 bg-stone-950 p-5" data-testid="platform-detail">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-5">
        <div>
          <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-orange-500">
            Selected hotel
          </div>
          <h2 className="text-2xl font-bold uppercase tracking-tight mt-1">{detail.name}</h2>
        </div>
        <StatusPill status={detail.status} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Figure label="Rooms" value={counts.rooms ?? "—"} />
        <Figure label="Room types" value={counts.room_types ?? "—"} />
        <Figure label="Rates" value={counts.rates ?? "—"} />
        <Figure label="Menu items" value={counts.menu_items ?? "—"} />
        <Figure label="Tables" value={counts.tables ?? "—"} />
        <Figure label="Staff" value={counts.staff ?? "—"} />
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-2">
        {checks.map(([label, done]) => (
          <span
            key={label}
            className={`text-xs tracking-widest uppercase ${done ? "text-emerald-400" : "text-stone-600"}`}
          >
            {done ? "✓" : "·"} {label}
          </span>
        ))}
      </div>

      <p
        data-testid="platform-ready"
        className={`mt-5 text-sm ${setup.ready_to_trade ? "text-stone-300" : "text-orange-300"}`}
      >
        {setup.ready_to_trade
          ? "Ready to trade — rooms and rates are both in place, so approving this one opens a hotel that can take a booking today."
          : "Not ready to trade — no rooms, no rates, or neither. Approving is allowed and harmless, but this may be an abandoned form rather than a business."}
      </p>

      {detail.status === SUSPENDED && detail.suspension_reason && (
        <p className="mt-4 text-xs text-stone-400">
          <span className="tracking-widest uppercase text-stone-500">Suspended because</span>{" "}
          {detail.suspension_reason}
        </p>
      )}
      {/* Keyed on the property so every form inside is reset when the operator opens a
          different business — a half-typed payment carried across would land on the wrong
          ledger, and the ledger cannot be edited afterwards. */}
      <SubscriptionPanel
        key={detail.id}
        detail={detail}
        payments={payments}
        onChanged={onChanged}
      />

      {/* The operator sees a hotel's size, never its guests: there is no route on the
          platform API that returns a booking, a folio or an identity document, and this
          panel is everything the detail endpoint answers. Money is the exception and it is
          the operator's own record: what was agreed, and what arrived. */}
      <p className="mt-10 border-t border-stone-800 pt-6 text-xs text-stone-500 max-w-2xl">
        Counts only. Guests, bookings, folios and identity documents are not reachable from
        this console — approving a business does not require reading its customers.
      </p>
    </div>
  );
}

export default function Platform() {
  const { user, logout } = useAuth();
  const nav = useNavigate();

  const [filter, setFilter] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  // Keyed by property id. The list endpoint carries no counts — it is a summary, by
  // design — so the room figure the operator scans the table for comes from the detail
  // endpoint, one call per hotel, cached here so switching filters does not re-ask. This
  // is an N+1 and it is fine at one page of hotels; the day the list runs to hundreds,
  // the count belongs in the list response rather than in more requests from here.
  const [details, setDetails] = useState({});
  // The ledger of the one property that is open, and only that one. Unlike the counts
  // above it is not cached per property: a payment recorded on another screen, or by
  // another operator, must not be missing from a list somebody is about to reconcile a
  // bank statement against. `null` means "still reading", which the table says out loud
  // rather than showing as an empty ledger.
  const [payments, setPayments] = useState(null);
  const [selected, setSelected] = useState(null); // property id
  const [confirming, setConfirming] = useState(null); // {id, name, status, to, label, reason}
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () =>
      api
        .get("/platform/properties", { params: filter ? { status: filter } : {} })
        .then((r) => setRows(r.data || []))
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
        .finally(() => setLoading(false)),
    [filter],
  );

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // The ids on screen, as a stable string, so a re-render that rebuilds the same array
  // does not re-fire the detail fetches below.
  const ids = rows.map((r) => r.id).join(",");

  const fetchDetail = useCallback(
    (id) =>
      api
        .get(`/platform/properties/${id}`)
        .then((r) => {
          setDetails((d) => ({ ...d, [id]: r.data }));
          return r.data;
        })
        // Swallowed: a detail that did not load costs a room figure, and an error toast
        // per hotel on a list that is otherwise working is noise, not information.
        .catch(() => null),
    [],
  );

  useEffect(() => {
    const wanted = ids ? ids.split(",") : [];
    wanted.filter((id) => id && !details[id]).forEach(fetchDetail);
    // `details` is written by this effect, so depending on it would loop. The ids are what
    // decide which fetches are owed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, fetchDetail]);

  const fetchPayments = useCallback(
    (id) =>
      api
        .get(`/platform/properties/${id}/payments`)
        .then((r) => setPayments(r.data || []))
        .catch((e) => {
          // Not swallowed the way a missing room count is. An empty ledger and a ledger
          // that failed to load look identical, and one of them means "this business has
          // never paid" — which is the sentence somebody is about to act on.
          setPayments([]);
          toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }),
    [],
  );

  useEffect(() => {
    if (!selected) {
      setPayments(null);
      return;
    }
    setPayments(null);
    fetchPayments(selected);
  }, [selected, fetchPayments]);

  // After any write from the detail panel: the row, the record and the ledger, all re-read
  // from the server. Nothing is patched in place from a response — the summary the write
  // answered with is the same shape as the list row, and trusting one of the two to stay in
  // step with the other is how a paid-until goes stale on screen but not in the database.
  const refresh = useCallback(async () => {
    await Promise.all([load(), fetchDetail(selected), fetchPayments(selected)]);
  }, [load, fetchDetail, fetchPayments, selected]);

  const counts = useMemo(() => {
    const out = { "": rows.length };
    for (const s of STATUSES) out[s] = rows.filter((r) => r.status === s).length;
    return out;
  }, [rows]);

  const signOut = () => {
    logout();
    nav("/login");
  };

  const apply = async () => {
    const { id, to, reason, done } = confirming;
    setBusy(true);
    try {
      await api.post(`/platform/properties/${id}/status`, {
        status: to,
        ...(to === SUSPENDED ? { reason: reason.trim() } : {}),
      });
      toast.success(done);
      setConfirming(null);
      await load();
      // The counts and the setup flags do not change with the status, but the record's
      // own fields do — re-read it so the panel is not showing the state before the press.
      await fetchDetail(id);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const detail = selected ? details[selected] : null;

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 relative z-[2]">
      <header className="border-b border-stone-800 px-6 md:px-10 py-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <ShieldCheck className="text-orange-500" size={22} />
          <div>
            <div className="font-display text-lg tracking-tight uppercase leading-none">
              BarFlow
            </div>
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500 mt-1">
              Platform
            </div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {/* The email, not the name: the operator is refused /auth/me, so after a page
              reload their identity comes from their own token, which carries an address
              and no display name. Showing the address in both cases means the header does
              not change wording depending on how you arrived. */}
          <div className="text-right hidden sm:block">
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500">
              Operator
            </div>
            <div className="text-xs font-mono text-stone-300 mt-0.5">{user?.email}</div>
          </div>
          {/* The operator's only route out of this console, and the reason /account is
              not inside the app shell: they belong to no hotel, so /app sends them
              straight back here. Their own password is not a hotel endpoint — see
              backend/routers/auth.py — and this is the only screen from which they can
              change it, because there is no admin above them to reset it for them. */}
          <Link
            to="/account"
            data-testid="platform-account-link"
            className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-3 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
          >
            <KeyRound size={14} /> Password
          </Link>
          <button
            data-testid="platform-logout"
            onClick={signOut}
            className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-3 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </header>

      <div className="p-6 md:p-10">
        <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Platform</div>
        <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
          Hotels
        </h1>

        <div className="flex flex-wrap gap-2 mb-8">
          {FILTERS.map((f) => (
            <button
              key={f.key || "all"}
              type="button"
              data-testid={`platform-filter-${f.key || "all"}`}
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={`text-[10px] tracking-widest uppercase border rounded-full px-4 py-1.5 transition-colors ${
                filter === f.key
                  ? "border-orange-500 text-orange-400 bg-orange-500/10"
                  : "border-stone-700 text-stone-500 hover:border-stone-500 hover:text-stone-300"
              }`}
            >
              {f.label}
              {/* Only meaningful while the filter is off — with one applied the list is
                  already narrowed and a per-status count would just repeat the total. */}
              {!filter && (
                <span className="tabular-nums ml-2 text-stone-500">{counts[f.key] ?? 0}</span>
              )}
            </button>
          ))}
        </div>

        {!loading && rows.length === 0 && (
          <p className="text-stone-400 text-sm">
            {filter
              ? `No hotel is ${filter} right now.`
              : "No hotels yet. The first one to sign up appears here as pending."}
          </p>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
                <th className="text-left py-2 px-3 border-b border-stone-800">Hotel</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">City</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">GSTIN</th>
                <th className="text-right py-2 px-3 border-b border-stone-800">Rooms</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">Subscription</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">Signed up</th>
                <th className="text-left py-2 px-3 border-b border-stone-800">Status</th>
                <th className="border-b border-stone-800" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const next = NEXT[r.status];
                const rooms = details[r.id]?.counts?.rooms;
                const isSelected = selected === r.id;
                return (
                  <tr
                    key={r.id}
                    data-testid={`platform-row-${r.id}`}
                    className={isSelected ? "bg-stone-900/60" : ""}
                  >
                    <td className="py-2 px-3 border-b border-stone-800">
                      <button
                        type="button"
                        onClick={() => setSelected(isSelected ? null : r.id)}
                        className="flex items-center gap-2 text-left hover:text-orange-400"
                      >
                        <Building2 size={14} className="text-stone-600 shrink-0" />
                        {r.name || "Unnamed"}
                      </button>
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-stone-400">
                      {r.city || "—"}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 font-mono text-xs text-stone-400">
                      {r.gstin || <span className="text-stone-600">not given</span>}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-right tabular-nums text-stone-300">
                      {rooms == null ? "…" : rooms}
                    </td>
                    {/* Carried on the list row itself — `subscription` is on every summary,
                        so unlike the room count this needs no second request and is never
                        briefly blank. */}
                    <td className="py-2 px-3 border-b border-stone-800">
                      <SubscriptionCell subscription={r.subscription} />
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-stone-400 tabular-nums whitespace-nowrap">
                      {signupDate(r.created_at)}
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800">
                      <StatusPill status={r.status} />
                    </td>
                    <td className="py-2 px-3 border-b border-stone-800 text-right whitespace-nowrap">
                      <button
                        onClick={() => setSelected(isSelected ? null : r.id)}
                        className="text-[10px] tracking-widest uppercase text-stone-500 hover:text-orange-400 mr-3"
                      >
                        {isSelected ? "Close" : "Open"}
                      </button>
                      {next && (
                        <button
                          data-testid={`platform-${next.label.toLowerCase()}-${r.id}`}
                          onClick={() =>
                            setConfirming({
                              id: r.id,
                              name: r.name || "this hotel",
                              status: r.status,
                              to: next.to,
                              label: next.label,
                              done: next.done,
                              reason: "",
                            })
                          }
                          disabled={busy}
                          className={`text-[10px] tracking-widest uppercase disabled:opacity-30 ${
                            next.to === SUSPENDED
                              ? "text-stone-500 hover:text-red-400"
                              : "text-stone-500 hover:text-orange-400"
                          }`}
                        >
                          {next.label}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Suspension is destructive in effect even though it deletes nothing, so it uses
            the same inline two-step panel the app already uses for cancelling a booking,
            voiding a folio entry and deactivating a staff member — never window.confirm.
            Approving and restoring are not destructive, so the panel drops the red
            treatment for them. */}
        {confirming && (
          <div
            data-testid="platform-confirm"
            className={`mt-8 p-5 max-w-2xl border ${
              confirming.to === SUSPENDED
                ? "border-red-500/40 bg-red-950/20"
                : "border-stone-800 bg-stone-900"
            }`}
          >
            <h3 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-2">
              {confirming.label} {confirming.name}?
            </h3>
            {confirming.to === SUSPENDED ? (
              <>
                <p className="text-sm text-red-300 mb-4">
                  Every login belonging to this hotel is signed out and refused from the
                  next request, its admin included — they are told only that the email or
                  password is wrong, exactly as a deactivated member of staff is. Nothing is
                  deleted: the rooms, rates, bookings, folios and guests sit untouched, so
                  restoring gives all of it back as it was.
                </p>
                <label className="block text-xs tracking-widest uppercase text-stone-500">
                  Reason
                  <input
                    autoFocus
                    data-testid="platform-reason"
                    value={confirming.reason}
                    onChange={(e) => setConfirming({ ...confirming, reason: e.target.value })}
                    placeholder="Invoice unpaid since June"
                    className="block mt-2 w-full bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none placeholder:text-stone-600"
                  />
                </label>
                <p className="text-xs text-stone-500 mt-2">
                  Recorded against the property and shown here when it is restored. The hotel
                  never sees it.
                </p>
              </>
            ) : (
              <p className="text-sm text-stone-400 mb-4">
                {confirming.status === PENDING
                  ? "The hotel starts trading immediately: bookings, check-in and billing unlock for everyone who works there. Setting up was already open to them."
                  : "The hotel comes back exactly as it was — its data was never touched — and its staff can log in again with their existing passwords."}
              </p>
            )}
            <div className="flex gap-3 mt-5">
              <button
                data-testid="platform-confirm-apply"
                onClick={apply}
                disabled={busy}
                className={`rounded-full px-6 py-2 text-sm tracking-widest uppercase disabled:opacity-50 ${
                  confirming.to === SUSPENDED
                    ? "bg-red-600 hover:bg-red-500 text-white"
                    : "bg-orange-600 hover:bg-orange-500 text-white"
                }`}
              >
                {busy ? "Working…" : `Confirm ${confirming.label.toLowerCase()}`}
              </button>
              <button
                onClick={() => setConfirming(null)}
                disabled={busy}
                className="border border-stone-700 text-stone-300 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase"
              >
                Never mind
              </button>
            </div>
          </div>
        )}

        {detail && <Detail detail={detail} payments={payments} onChanged={refresh} />}

        <div className="mt-10 border-t border-stone-800 pt-6 max-w-3xl space-y-1">
          {STATUSES.map((s) => (
            <p key={s} className="text-xs text-stone-500">
              <span className="tracking-widest uppercase text-stone-400">{s}</span> —{" "}
              {STATUS_BLURB[s]}
            </p>
          ))}
          {/* Overdue is deliberately not in that list. It is not a fourth status and it
              stops nothing: the amber flag in the Subscription column is about an invoice,
              and the only thing that ends trade is Suspend, above, pressed by a person. */}
          <p className="text-xs text-stone-500 pt-2">
            <span className="tracking-widest uppercase text-amber-400">overdue</span> — not a
            status. An invoice went past due and the business is still trading; suspending is a
            separate, deliberate press.
          </p>
        </div>
      </div>
    </div>
  );
}
