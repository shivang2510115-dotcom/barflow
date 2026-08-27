import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { api, currency, formatApiErrorDetail } from "@/lib/api";

/**
 * Who the platform is on the invoices it issues.
 *
 * Not per-hotel and not a deployment variable. A legal name and a GSTIN on a tax document
 * are the operator's own registration; they change when the company's registration
 * changes, and nobody is redeploying a container to correct an address.
 *
 * `state` is the field that does work rather than decorating: the place-of-supply rule
 * reads it against each hotel's, and until it is filled in no invoice can be issued at
 * all. That refusal is deliberate — an invoice with a guessed split is worse than one
 * that has not been written — so it is said here, above the field, rather than met later
 * as an error the operator has to interpret.
 */

const FIELD =
  "block mt-2 w-full bg-transparent border-b border-stone-700 text-stone-100 py-1 " +
  "focus:border-orange-500 outline-none placeholder:text-stone-600";
const LABEL = "text-xs tracking-widest uppercase text-stone-500";
const PRIMARY =
  "bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 " +
  "text-sm tracking-widest uppercase";

// The worked figures in the copy below. Written through `currency()` like every other
// rupee in this app — there is no symbol typed into this file.
const EXAMPLE_PAID = 12000;
const EXAMPLE_GROSS = 14160;

const BLANK = {
  legal_name: "", gstin: "", address_line1: "", address_line2: "", city: "", state: "",
  pincode: "", email: "", phone: "", prices_include_gst: true,
};

const TEXT_FIELDS = [
  ["legal_name", "Legal name", "BarFlow Technologies Pvt Ltd"],
  ["gstin", "GSTIN", "27AAPFU0939F1ZV"],
  ["address_line1", "Address", "4 Church Street"],
  ["address_line2", "Address line 2", ""],
  ["city", "City", "Mumbai"],
  ["state", "State", "Maharashtra"],
  ["pincode", "PIN code", "400001"],
  ["email", "Email", "accounts@barflow.io"],
  ["phone", "Phone", "9990000000"],
];

export default function PlatformSettings({ onSaved }) {
  const [form, setForm] = useState(BLANK);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(
    () =>
      api
        .get("/platform/settings")
        .then((r) => {
          setForm({ ...BLANK, ...r.data });
          setLoaded(true);
        })
        .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail))),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const save = () => {
    setSaving(true);
    const { legal_name, gstin, address_line1, address_line2, city, state, pincode,
      email, phone, prices_include_gst } = form;
    api
      .put("/platform/settings", {
        legal_name, gstin, address_line1, address_line2, city, state, pincode, email,
        phone, prices_include_gst,
      })
      .then((r) => {
        setForm({ ...BLANK, ...r.data });
        toast.success("Platform details saved");
        if (onSaved) onSaved();
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setSaving(false));
  };

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  return (
    <div className="max-w-3xl" data-testid="platform-settings">
      <p className="text-sm text-stone-400 mb-8 max-w-2xl">
        These appear on every invoice you issue. The state is the one that decides
        something: a hotel in it is charged CGST and SGST, a hotel outside it IGST, and
        until it is filled in no invoice can be issued — a guessed split is a document the
        hotel&rsquo;s accountant cannot use.
      </p>

      <div className="border border-stone-800 bg-stone-900 p-6">
        <div className="grid gap-5 sm:grid-cols-2">
          {TEXT_FIELDS.map(([key, label, placeholder]) => (
            <label key={key} className={LABEL}>
              {label}
              <input
                data-testid={`platform-settings-${key}`}
                value={form[key] || ""}
                disabled={!loaded}
                onChange={set(key)}
                placeholder={placeholder}
                className={`${FIELD}${key === "gstin" ? " font-mono" : ""}`}
              />
            </label>
          ))}
        </div>

        <label className="flex items-start gap-3 mt-8 cursor-pointer">
          <input
            type="checkbox"
            data-testid="platform-settings-inclusive"
            checked={Boolean(form.prices_include_gst)}
            disabled={!loaded}
            onChange={(e) =>
              setForm({ ...form, prices_include_gst: e.target.checked })
            }
            className="mt-1 accent-orange-500 w-4 h-4"
          />
          <span>
            <span className="text-sm text-stone-200">
              Agreed prices already include GST
            </span>
            <span className="block text-xs text-stone-500 mt-1 max-w-xl">
              Tick this — it is the usual arrangement — and a {currency(EXAMPLE_PAID)}{" "}
              payment produces a {currency(EXAMPLE_PAID)} invoice with the tax inside it,
              which reconciles line for line against the bank statement you matched it
              from. Untick it and {currency(EXAMPLE_PAID)} is the taxable value, so the
              invoice totals {currency(EXAMPLE_GROSS)} and the transfer does not match it.
            </span>
          </span>
        </label>

        <div className="flex gap-3 mt-8">
          <button
            data-testid="platform-settings-save"
            onClick={save}
            disabled={saving || !loaded}
            className={PRIMARY}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <p className="text-xs text-stone-500 mt-6 max-w-2xl">
        Changing these does not change an invoice that has already been issued. Both
        parties are copied onto each document when it is written, so a change of address
        never restates a document somebody has already filed.
      </p>
    </div>
  );
}
