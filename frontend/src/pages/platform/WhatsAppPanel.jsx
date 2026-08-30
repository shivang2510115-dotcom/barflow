import { useState } from "react";
import { toast } from "sonner";
import { api, formatApiErrorDetail } from "@/lib/api";
import { MessageCircle, Check, AlertTriangle } from "lucide-react";

/**
 * The credentials a hotel messages its own customers from.
 *
 * Entered here, by the operator, during onboarding — the same choice the Razorpay
 * design made and for the same reason: the operator is the one on the call, and a token
 * is a credential the hotel should not have to handle twice.
 *
 * **There is no platform fallback anywhere behind this screen.** A hotel with nothing
 * entered here cannot send, and is told so in words rather than quietly borrowing the
 * platform's number. That would put our name on their relationship with their own
 * customers, and would train them never to set up their own.
 *
 * The token is never displayed, because no route returns it. What the operator needs to
 * know is whether one is set, and that is what `token_set` says.
 */
export default function WhatsAppPanel({ propertyId, whatsapp, onSaved }) {
  const wa = whatsapp || {};
  const [form, setForm] = useState({
    phone_id: wa.phone_id || "",
    display_name: wa.display_name || "",
    owner_phone: wa.owner_phone || "",
    token: "",
  });
  const [saving, setSaving] = useState(false);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      // An empty token box means "leave it alone", not "clear it" — an operator fixing
      // a typo in the display name should not have to re-paste a secret. Clearing is
      // its own button below, so the two intentions cannot be confused.
      const body = { ...form };
      if (!body.token.trim()) delete body.token;
      const { data } = await api.put(`/platform/properties/${propertyId}/whatsapp`, body);
      toast.success(data.configured ? "WhatsApp is live for this hotel" : "Saved");
      setForm((f) => ({ ...f, token: "" }));
      onSaved?.(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const clearToken = async () => {
    try {
      const { data } = await api.put(`/platform/properties/${propertyId}/whatsapp`,
                                     { token: "" });
      toast.success("Messaging switched off for this hotel");
      onSaved?.(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Could not clear it");
    }
  };

  return (
    <section className="border border-hairline rounded bg-surface p-5">
      <header className="flex items-center gap-3 mb-4">
        <MessageCircle className="h-4 w-4 text-brass" aria-hidden="true" />
        <h3 className="font-display text-[15px] text-ink flex-1">WhatsApp</h3>
        {wa.configured ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] text-state-free">
            <Check className="h-3.5 w-3.5" aria-hidden="true" /> Can send
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[12px] text-state-dirty">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> Cannot send
          </span>
        )}
      </header>

      {!wa.configured && (wa.missing || []).length > 0 && (
        <ul className="mb-4 text-[13px] text-muted2 space-y-1">
          {wa.missing.map((m) => (
            <li key={m} className="flex gap-2">
              <span className="text-state-dirty" aria-hidden="true">·</span>{m}
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={save} className="space-y-4">
        {[
          ["phone_id", "Phone number ID",
           "The numeric id from Meta — not the phone number itself"],
          ["display_name", "Display name", "As Meta approved it, for your reference"],
          ["owner_phone", "Owner's phone", "Where the nightly brief goes. Digits and country code"],
        ].map(([key, label, hint]) => (
          <label key={key} className="block">
            <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-1.5">
              {label}
            </span>
            <input
              value={form[key]}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              className="w-full bg-ground border border-hairline rounded px-3 text-[15px] text-ink"
            />
            <span className="block text-[12px] text-faint mt-1">{hint}</span>
          </label>
        ))}

        <label className="block">
          <span className="block text-[11px] uppercase tracking-[0.2em] text-faint mb-1.5">
            Access token
          </span>
          <input
            type="password"
            value={form.token}
            onChange={(e) => setForm((f) => ({ ...f, token: e.target.value }))}
            placeholder={wa.token_set ? "A token is stored — leave blank to keep it" : "Paste the token from Meta"}
            className="w-full bg-ground border border-hairline rounded px-3 text-[15px] text-ink"
          />
          <span className="block text-[12px] text-faint mt-1">
            Stored encrypted and never shown again — not here, not anywhere.
          </span>
        </label>

        <div className="flex flex-wrap gap-3 pt-1">
          <button
            type="submit"
            disabled={saving}
            className="px-5 rounded bg-brass hover:bg-brass-deep text-on-brass text-[13px]
                       font-medium disabled:opacity-40 transition-colors"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {wa.token_set && (
            <button
              type="button"
              onClick={clearToken}
              className="px-4 rounded border border-hairline text-muted2 text-[13px]
                         hover:border-state-alert hover:text-state-alert transition-colors"
            >
              Switch messaging off
            </button>
          )}
        </div>
      </form>

      <p className="mt-5 pt-4 border-t border-hairline text-[12px] text-faint">
        Messages go from this hotel's own number only. A hotel with nothing here sends
        nothing — there is no platform number to fall back on, by design.
      </p>
    </section>
  );
}
