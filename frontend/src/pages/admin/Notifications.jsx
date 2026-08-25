import { useCallback, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

/**
 * Whether the nightly WhatsApp brief can actually send, provable by sending one.
 *
 * The whole point of this screen is that WhatsApp cannot appear to work. Before it
 * existed, a deployment with no credentials logged a line and carried on, so a hotel
 * only discovered the brief was never arriving by noticing it had never arrived.
 */
export default function Notifications() {
  const [status, setStatus] = useState(null);
  const [to, setTo] = useState("");
  const [result, setResult] = useState(null);
  const [sending, setSending] = useState(false);

  const load = useCallback(
    () =>
      api
        .get("/whatsapp/status")
        .then((r) => {
          setStatus(r.data);
          setTo((prev) => prev || r.data.recipient || "");
        })
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const send = () => {
    setSending(true);
    setResult(null);
    api
      .post("/whatsapp/test", { to })
      .then((r) => {
        setResult(r.data);
        if (r.data.sent) toast.success("Sent — check the phone");
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setSending(false));
  };

  const ok = status?.configured;

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        Notifications
      </h1>

      <div className="max-w-3xl">
        <div
          className={`border rounded p-5 mb-8 ${
            ok ? "border-orange-500/40 bg-orange-500/5" : "border-stone-800 bg-stone-900"
          }`}
        >
          <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500 mb-2">
            WhatsApp daily brief
          </div>
          <div className="text-lg font-bold uppercase tracking-wide text-stone-100">
            {status === null ? "Checking…" : ok ? "Ready to send" : "Not configured"}
          </div>
          {status && !ok && (
            <p className="text-sm text-stone-400 mt-3">{status.problem}</p>
          )}
          {status && !ok && (
            <p className="text-xs text-stone-500 mt-4">
              These are environment variables on the server. Setting them needs a Meta
              WhatsApp Business account with a verified business — the token and the phone
              number ID both come from the app&rsquo;s API Setup page.
            </p>
          )}
        </div>

        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-4">
          Send a test message
        </h2>
        <div className="flex flex-wrap gap-4 items-end">
          <label className="text-xs tracking-widest uppercase text-stone-500">
            To
            <input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="919876543210"
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none font-mono"
            />
          </label>
          <button
            onClick={send}
            disabled={sending}
            className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
          >
            {sending ? "Sending…" : "Send test"}
          </button>
        </div>
        <p className="text-xs text-stone-500 mt-3">
          Country code, digits only, no plus sign. A real message is sent — it will appear
          on that phone, or the reason it did not is shown below.
        </p>

        {result && (
          <div
            className={`mt-8 border rounded p-5 ${
              result.sent ? "border-orange-500/40 bg-orange-500/5" : "border-red-500/30 bg-red-950/20"
            }`}
          >
            <div className="text-[10px] tracking-[0.2em] uppercase text-stone-500 mb-2">
              {result.sent ? "Delivered to WhatsApp" : "Not sent"}
            </div>
            {result.sent ? (
              <p className="text-sm text-stone-300">
                Accepted with id{" "}
                <span className="font-mono text-xs text-stone-400">{result.message_id}</span>.
                Find it in the Meta dashboard if it does not arrive.
              </p>
            ) : (
              <>
                <p className="text-sm text-stone-300">{result.error}</p>
                {result.error_code != null && (
                  <p className="text-xs text-stone-500 mt-3 font-mono">
                    Meta error code {result.error_code}
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
