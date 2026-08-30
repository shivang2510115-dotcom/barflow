import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { Cake, Clock, Send } from "lucide-react";

/**
 * Today's occasions, what tonight's follow-up will do, and what has actually been sent.
 *
 * Three sections and only one of them has a button, which is the shape of the feature
 * rather than a layout choice. A birthday greeting is a decision somebody makes — they
 * look at the name, they know whether it is the right morning for it, and they press
 * send. A follow-up to a customer who has not been back in ten days is not a decision
 * anybody makes per person, so it sends itself from a scheduled job and this screen only
 * says what it is going to do. Putting a button beside it would be how "automatic"
 * quietly becomes "somebody was supposed to".
 *
 * **Nothing here is allowed to look like it worked.** The send button reports exactly
 * what the server reported: the message id on success, and on failure the real reason,
 * which is usually that this property has not obtained a Meta template yet. The message
 * log below is the same information after the fact, and it is the answer to "did the
 * birthday message go out". A toast that says "Sent!" regardless is the failure this
 * whole feature is built to avoid.
 */
export default function Messaging() {
  const [today, setToday] = useState(null);
  const [followUps, setFollowUps] = useState(null);
  const [log, setLog] = useState([]);
  const [sending, setSending] = useState(null);
  // Keyed by occasion id: what the server said about the last press. Kept beside the row
  // rather than in a toast, because a refusal that names a missing template is something
  // to read twice and then act on, and a toast is gone in four seconds.
  const [outcome, setOutcome] = useState({});

  const load = useCallback(
    () =>
      Promise.all([
        api.get("/messaging/occasions/today"),
        api.get("/messaging/follow-ups"),
        api.get("/messaging/log", { params: { limit: 50 } }),
      ])
        .then(([a, b, c]) => {
          setToday(a.data);
          setFollowUps(b.data);
          setLog(c.data);
        })
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const send = (row) => {
    setSending(row.occasion_id);
    api
      .post(`/messaging/occasions/${row.occasion_id}/send`)
      .then((r) => {
        setOutcome((prev) => ({ ...prev, [row.occasion_id]: r.data }));
        if (r.data.sent) toast.success(`Sent to ${row.name}`);
        else toast.error("Not sent — see the reason below");
      })
      .catch((e) =>
        // A 409 is the claim refusing a second press. It is not an error the staff member
        // made, so it is shown in the row's own words rather than as a red failure.
        setOutcome((prev) => ({
          ...prev,
          [row.occasion_id]: {
            sent: false,
            error: formatApiErrorDetail(e.response?.data?.detail),
          },
        })),
      )
      .finally(() => {
        setSending(null);
        load();
      });
  };

  const whatsapp = today?.whatsapp;

  return (
    <div className="p-4 md:p-10">
      <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-brass mb-3">
        Customers
      </div>
      <h1 className="font-display text-4xl md:text-5xl uppercase tracking-tight leading-none mb-8">
        Messaging
      </h1>

      {whatsapp && !whatsapp.configured && (
        <div className="border border-red-500/30 bg-red-950/20 p-5 mb-10 max-w-3xl">
          <div className="text-[10px] tracking-[0.2em] uppercase font-mono text-faint mb-2">
            WhatsApp is not connected
          </div>
          <p className="text-sm text-muted2">{whatsapp.problem}</p>
          <p className="text-xs text-faint mt-3">
            Nothing will send until these are set on the server. Pressing send below will
            tell you the same thing rather than pretending it worked.
          </p>
        </div>
      )}

      {/* ---------------------------------------------------------- occasions */}
      <Section
        icon={Cake}
        title="Today's occasions"
        subtitle={today?.date ? `Falling on ${today.date}` : "Loading…"}
      >
        {today && today.occasions.length === 0 && (
          <p className="text-sm text-faint font-mono uppercase tracking-widest py-6">
            Nobody's occasion falls today.
          </p>
        )}
        <div className="divide-y divide-hairline">
          {today?.occasions.map((row) => {
            const result = outcome[row.occasion_id];
            return (
              <div key={row.occasion_id} className="py-4" data-testid={`occasion-${row.occasion_id}`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-lg text-ink">{row.name}</div>
                    <div className="text-[11px] font-mono text-faint mt-1">
                      {row.phone} · {row.label}
                      {row.template ? ` · template ${row.template}` : ""}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {row.already_sent ? (
                      <span className="text-[10px] font-mono uppercase tracking-widest border border-brass/50 text-brass px-3 py-1.5">
                        Sent
                      </span>
                    ) : row.claimed ? (
                      <span className="text-[10px] font-mono uppercase tracking-widest border border-hairline-strong text-faint px-3 py-1.5">
                        Attempted
                      </span>
                    ) : (
                      <button
                        onClick={() => send(row)}
                        disabled={sending === row.occasion_id}
                        data-testid={`send-${row.occasion_id}`}
                        className="flex items-center gap-2 rounded-full bg-brass hover:bg-brass-deep disabled:opacity-40 text-on-brass px-5 py-2 font-mono uppercase tracking-widest text-[10px] active:scale-95 transition"
                      >
                        <Send size={12} />
                        {sending === row.occasion_id ? "Sending…" : "Send wishes"}
                      </button>
                    )}
                  </div>
                </div>

                {/* The row's own problem — consent, a missing number, a missing template.
                    Shown whether or not anybody has pressed anything, because it is the
                    reason the button is not there. */}
                {row.problem && (
                  <p className="text-xs text-muted2 mt-3 border-l-2 border-hairline-strong pl-3">
                    {row.problem}
                  </p>
                )}
                {result && (
                  <p
                    className={`text-xs mt-3 border-l-2 pl-3 ${
                      result.sent
                        ? "border-brass/60 text-muted2"
                        : "border-red-500/40 text-muted2"
                    }`}
                  >
                    {result.sent
                      ? `Accepted by WhatsApp with id ${result.message_id}.`
                      : result.error}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </Section>

      {/* --------------------------------------------------------- follow-ups */}
      <Section
        icon={Clock}
        title="Visit follow-up"
        subtitle={
          followUps === null
            ? "Loading…"
            : followUps.enabled
              ? `Automatic · ${followUps.days} days since a customer's last visit`
              : "Switched off for this property"
        }
      >
        <p className="text-xs text-faint mb-5 max-w-2xl">
          This one sends itself, once per customer per visit. There is no button: it is a
          scheduled job, and this is what it will do next time it runs. An admin changes
          the window, or switches it off, under Admin &rarr; Notifications.
        </p>
        {followUps?.problem && (
          <p className="text-xs text-muted2 mb-5 border-l-2 border-hairline-strong pl-3 max-w-2xl">
            {followUps.problem}
          </p>
        )}
        {followUps?.enabled && followUps.customers.length === 0 && (
          <p className="text-sm text-faint font-mono uppercase tracking-widest py-2">
            Nobody is due.
          </p>
        )}
        <div className="divide-y divide-hairline">
          {followUps?.customers.map((c) => (
            <div key={c.guest_id} className="py-3 flex items-baseline justify-between gap-4">
              <div>
                <span className="text-ink">{c.name}</span>
                <span className="text-[11px] font-mono text-faint ml-3">{c.phone}</span>
              </div>
              <div className="text-[11px] font-mono text-faint whitespace-nowrap">
                {c.days_since} days · last in {c.last_visit}
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* --------------------------------------------------------------- log */}
      <Section
        icon={Send}
        title="Message log"
        subtitle="Every attempt, and what WhatsApp actually answered"
      >
        {log.length === 0 && (
          <p className="text-sm text-faint font-mono uppercase tracking-widest py-2">
            Nothing has been attempted yet.
          </p>
        )}
        <div className="divide-y divide-hairline">
          {log.map((row) => (
            <div key={row.id} className="py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div className="text-sm text-ink">
                  {row.guest_name || "—"}
                  <span className="text-[11px] font-mono text-faint ml-3">{row.to}</span>
                </div>
                <div className="flex items-center gap-3 text-[10px] font-mono uppercase tracking-widest">
                  <span className="text-faint">
                    {row.kind === "occasion" ? row.occasion_label || "occasion" : "follow-up"}
                  </span>
                  <span
                    className={
                      row.status === "sent"
                        ? "text-brass"
                        : row.status === "refused"
                          ? "text-faint"
                          : "text-red-400"
                    }
                  >
                    {row.status}
                  </span>
                </div>
              </div>
              <div className="text-[11px] font-mono text-faint mt-1">
                {row.sent_at?.slice(0, 19).replace("T", " ")}
                {row.template ? ` · ${row.template}` : ""}
                {row.sent_by ? "" : " · sent automatically"}
              </div>
              {row.status === "sent" ? (
                <p className="text-[11px] font-mono text-faint mt-1">id {row.message_id}</p>
              ) : (
                <p className="text-xs text-muted2 mt-2 border-l-2 border-hairline pl-3">
                  {row.error}
                </p>
              )}
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}

function Section({ icon: Icon, title, subtitle, children }) {
  return (
    <section className="mb-14 max-w-4xl">
      <div className="flex items-center gap-3 mb-1">
        <Icon size={16} className="text-brass" />
        <h2 className="text-[11px] tracking-[0.3em] uppercase font-mono text-muted2">
          {title}
        </h2>
      </div>
      <div className="text-[11px] font-mono text-faint mb-5 ml-7">{subtitle}</div>
      <div className="border-t border-hairline pt-2">{children}</div>
    </section>
  );
}
