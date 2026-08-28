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
  // The customer-messaging half: which approved Meta template carries which occasion, and
  // how long a gap counts as a lapsed customer. Here rather than on its own screen
  // because this *is* the WhatsApp screen — the credentials above and the template names
  // below are the two halves of the same "can this property message anybody" question,
  // and an owner who has just been told a template is missing should not have to go
  // looking for where to put one.
  const [settings, setSettings] = useState(null);
  const [savingSettings, setSavingSettings] = useState(false);

  const load = useCallback(
    () =>
      Promise.all([api.get("/whatsapp/status"), api.get("/messaging/settings")])
        .then(([s, m]) => {
          setStatus(s.data);
          setTo((prev) => prev || s.data.recipient || "");
          setSettings(m.data);
        })
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [],
  );

  const saveSettings = () => {
    setSavingSettings(true);
    api
      .put("/messaging/settings", {
        occasion_templates: settings.occasion_templates || {},
        default_occasion_template: settings.default_occasion_template || "",
        follow_up_template: settings.follow_up_template || "",
        template_language: settings.template_language || "en",
        follow_up_enabled: Boolean(settings.follow_up_enabled),
        follow_up_days: Number(settings.follow_up_days) || 10,
      })
      .then((r) => {
        setSettings(r.data);
        toast.success("Message templates saved");
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setSavingSettings(false));
  };

  const field = (key) => ({
    value: settings?.[key] ?? "",
    onChange: (e) => setSettings((prev) => ({ ...prev, [key]: e.target.value })),
    className:
      "block mt-2 w-full bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none font-mono text-sm",
  });

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

        {/* ------------------------------------------- customer messaging */}
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mt-16 mb-4">
          Customer messages
        </h2>
        <p className="text-sm text-stone-400 max-w-2xl mb-2">
          A birthday greeting, or a note to somebody who has not been in for a while,
          arrives more than 24 hours after that customer last messaged you. WhatsApp
          refuses plain text there. It can only be a <strong>template Meta has
          approved</strong> for your business, sent by name with the customer&rsquo;s
          details filled into it.
        </p>
        <p className="text-xs text-stone-500 max-w-2xl mb-8">
          Submit the templates in the Meta dashboard under WhatsApp Manager &rarr; Message
          templates, wait for approval, then put their exact names here. Until then
          nothing sends, and BarFlow will say so rather than pretending otherwise.
        </p>

        {settings && (
          <div className="border border-stone-800 bg-stone-900 p-5 max-w-2xl">
            <label className="block text-xs tracking-widest uppercase text-stone-500 mb-6">
              Birthday / occasion template
              <input {...field("default_occasion_template")} placeholder="guest_occasion_v1" />
              <span className="block text-[11px] normal-case tracking-normal text-stone-600 mt-2">
                Used for every occasion. Variables, in order: the customer&rsquo;s name,
                the occasion, your property&rsquo;s name.
              </span>
            </label>

            <label className="block text-xs tracking-widest uppercase text-stone-500 mb-6">
              Visit follow-up template
              <input {...field("follow_up_template")} placeholder="guest_follow_up_v1" />
              <span className="block text-[11px] normal-case tracking-normal text-stone-600 mt-2">
                Variables, in order: the customer&rsquo;s name, your property&rsquo;s name.
              </span>
            </label>

            <label className="block text-xs tracking-widest uppercase text-stone-500 mb-8">
              Template language
              <input {...field("template_language")} placeholder="en" />
              <span className="block text-[11px] normal-case tracking-normal text-stone-600 mt-2">
                The language code the template was approved under — often{" "}
                <code className="font-mono">en</code> or{" "}
                <code className="font-mono">en_US</code>. A mismatch is refused by Meta.
              </span>
            </label>

            <div className="border-t border-stone-800 pt-6">
              <label className="flex items-center gap-3 text-sm text-stone-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={Boolean(settings.follow_up_enabled)}
                  onChange={(e) =>
                    setSettings((prev) => ({ ...prev, follow_up_enabled: e.target.checked }))
                  }
                  className="accent-orange-500 w-4 h-4"
                />
                Follow up with customers who have not been back
              </label>
              <label className="block text-xs tracking-widest uppercase text-stone-500 mt-5">
                After how many days
                <input
                  type="number"
                  min={1}
                  value={settings.follow_up_days ?? 10}
                  onChange={(e) =>
                    setSettings((prev) => ({ ...prev, follow_up_days: e.target.value }))
                  }
                  className="block mt-2 w-28 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none font-mono"
                />
              </label>
              <p className="text-[11px] text-stone-600 mt-4 max-w-xl">
                This one sends itself, once per customer per visit — nobody presses
                anything. A customer who has asked not to be messaged is never included,
                whatever this says.
              </p>
            </div>

            <button
              onClick={saveSettings}
              disabled={savingSettings}
              className="mt-8 bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
            >
              {savingSettings ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
