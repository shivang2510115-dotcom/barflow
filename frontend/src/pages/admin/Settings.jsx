import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { gstSettings, outletTotals } from "@/lib/tax";

/**
 * The hotel's own tax settings: what the outlet charges, and whether the menu prices
 * already contain it.
 *
 * A screen rather than a deployment variable, because the person who knows what the GST
 * registration says is the owner and they cannot edit a container. Admin only — the route
 * behind it (`PUT /api/property`) names "admin", so a manager who can take a booking and
 * a waiter who can settle a bill both get a 403 here, and this page is not offered to
 * them in the sidebar either.
 *
 * It reads and writes the whole property record. `PUT /api/property` replaces the
 * editable half of it, so sending only the two fields on this form would blank the
 * hotel's name and address — the record is loaded first and the two values are set on top
 * of it. That is also why a failed load disables the form rather than showing an empty
 * one.
 *
 * The worked example under the form is the point of the screen. "Inclusive" is a word two
 * people read two ways, and a ₹100 dish that bills ₹105 one way and ₹100 the other
 * settles the argument before it reaches a guest.
 */

const FIELD =
  "block mt-2 w-full bg-transparent border-b border-stone-700 text-stone-100 py-1 " +
  "focus:border-orange-500 outline-none placeholder:text-stone-600";
const LABEL = "text-xs tracking-widest uppercase text-stone-500";
const PRIMARY =
  "bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 " +
  "text-sm tracking-widest uppercase";

// The rates a restaurant in India actually charges, offered as one press each so the
// common case is not typed. 5% is service without input tax credit, 18% the specified
// cases, and 0% a business under the registration threshold. The field stays free text
// because packaged goods vary and this list is a shortcut, not the vocabulary.
const COMMON_RATES = [0, 5, 12, 18];

// One dish, priced so the two branches differ by a number anybody can check.
const EXAMPLE_PRICE = 100;

export default function Settings() {
  const [property, setProperty] = useState(null);
  const [rate, setRate] = useState("");
  const [inclusive, setInclusive] = useState(false);
  const [mealPlans, setMealPlans] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(
    () =>
      api
        .get("/property")
        .then((r) => {
          setProperty(r.data);
          const gst = gstSettings(r.data);
          setRate(String(gst.rate));
          setInclusive(gst.inclusive);
          // `?? false` and not `|| false`: the API answers with a real boolean, and a
          // record the startup migration has not reached yet answers with nothing at
          // all. Both have to land on a defined value or the checkbox goes uncontrolled.
          setMealPlans(r.data.meal_plans_enabled ?? false);
        })
        .catch((e) => {
          setFailed(true);
          toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const save = () => {
    if (!property) return;
    setSaving(true);
    // Every editable field back, with these two changed. The API replaces the editable
    // half of the record wholesale, and a payload carrying only the tax fields would
    // clear the hotel's own name off its bills.
    const {
      name, legal_name, address_line1, address_line2, city, state, pincode, phone, email,
      gstin, fssai_licence, check_in_time, check_out_time, logo,
    } = property;
    api
      .put("/property", {
        name, legal_name, address_line1, address_line2, city, state, pincode, phone,
        email, gstin, fssai_licence, check_in_time, check_out_time, logo,
        outlet_gst_rate: Number(rate),
        gst_inclusive: inclusive,
        meal_plans_enabled: mealPlans,
      })
      .then((r) => {
        setProperty(r.data);
        toast.success("Settings saved");
      })
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setSaving(false));
  };

  const preview = outletTotals(EXAMPLE_PRICE, gstSettings({
    outlet_gst_rate: Number(rate),
    gst_inclusive: inclusive,
  }));

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Admin</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-2">
        Property settings
      </h1>
      <p className="text-stone-400 mb-10 max-w-2xl">
        What this outlet charges on a restaurant bill, and how you sell a room. Room night
        GST is neither — those are the statutory hotel slabs, worked out per night from the
        tariff, and they are not editable here or anywhere.
      </p>

      {failed && (
        <p className="text-sm text-red-300 mb-8" data-testid="settings-unreadable">
          This property could not be read, so nothing can be saved from here yet. Reload the
          page; if it keeps failing, the API is refusing it.
        </p>
      )}

      <div className="max-w-3xl border border-stone-800 bg-stone-900 p-6">
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mb-6">
          Outlet GST
        </h2>

        <label className={LABEL}>
          Rate
          <div className="flex items-center gap-3">
            <input
              inputMode="decimal"
              data-testid="gst-rate"
              value={rate}
              disabled={!property}
              onChange={(e) => setRate(e.target.value)}
              placeholder="5"
              className={`${FIELD} tabular-nums max-w-[8rem]`}
            />
            <span className="text-stone-500 mt-2">%</span>
          </div>
        </label>

        <div className="flex flex-wrap gap-2 mt-4">
          {COMMON_RATES.map((r) => (
            <button
              key={r}
              type="button"
              data-testid={`gst-rate-${r}`}
              disabled={!property}
              onClick={() => setRate(String(r))}
              className={`text-[10px] tracking-widest uppercase border rounded-full px-4 py-1.5 transition-colors ${
                Number(rate) === r
                  ? "border-orange-500 text-orange-400 bg-orange-500/10"
                  : "border-stone-700 text-stone-500 hover:border-stone-500 hover:text-stone-300"
              }`}
            >
              {r}%
            </button>
          ))}
        </div>
        <p className="text-xs text-stone-500 mt-3 max-w-2xl">
          5% is restaurant service without input tax credit, 18% the specified cases. 0% is
          for a business below the registration threshold — it is a rate, not a blank, and
          it is saved as one.
        </p>

        <label className="flex items-start gap-3 mt-8 cursor-pointer">
          <input
            type="checkbox"
            data-testid="gst-inclusive"
            checked={inclusive}
            disabled={!property}
            onChange={(e) => setInclusive(e.target.checked)}
            className="mt-1 accent-orange-500 w-4 h-4"
          />
          <span>
            <span className="text-sm text-stone-200">
              Menu prices already include GST
            </span>
            <span className="block text-xs text-stone-500 mt-1 max-w-xl">
              Tick this if the price on your card is what the guest pays. The tax is then
              taken out of that price rather than added to it — adding it on top would
              overcharge every guest by the tax on the tax.
            </span>
          </span>
        </label>

        {/* The worked example. Two words nobody agrees on, one number everybody can. */}
        <div
          className="mt-8 border border-stone-800 bg-stone-950 p-4"
          data-testid="gst-preview"
        >
          <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-orange-500">
            A {currency(EXAMPLE_PRICE)} dish
          </div>
          <dl className="mt-3 text-sm font-mono space-y-1">
            <div className="flex justify-between text-stone-400">
              <dt>{inclusive ? "Taxable value" : "Subtotal"}</dt>
              <dd className="tabular-nums">{currency(preview.taxableValue)}</dd>
            </div>
            <div className="flex justify-between text-stone-400">
              <dt>
                GST {Number(rate) || 0}%{inclusive ? " (in price)" : ""}
              </dt>
              <dd className="tabular-nums">{currency(preview.tax)}</dd>
            </div>
            <div className="flex justify-between text-stone-100 font-bold border-t border-stone-800 pt-1 mt-1">
              <dt>Guest pays</dt>
              <dd className="tabular-nums">{currency(preview.total)}</dd>
            </div>
          </dl>
        </div>

        <p className="text-xs text-stone-500 mt-6 max-w-2xl">
          Changing this affects bills opened from now on. A bill that has already been
          settled keeps the rate it was settled at — the guest paid what the printed bill
          said, and re-pricing it afterwards would put your books out.
        </p>

        {/* Room pricing. Same record, same Save, one PUT — so it lives in the same card
            rather than growing a second form that could be saved on its own and stale. */}
        <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500 mt-12 mb-6 pt-8 border-t border-stone-800">
          Room pricing
        </h2>

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            data-testid="meal-plans-enabled"
            checked={mealPlans}
            disabled={!property}
            onChange={(e) => setMealPlans(e.target.checked)}
            className="mt-1 accent-orange-500 w-4 h-4"
          />
          <span>
            <span className="text-sm text-stone-200">Quote rooms per meal plan</span>
            <span className="block text-xs text-stone-500 mt-1 max-w-xl">
              Tick this if you sell EP, CP and MAP — room only, with breakfast, half
              board — and want a separate price for each. Leave it clear and a room has
              one all-inclusive rate: the desk quotes a single price per room type, and
              anything a guest takes on top is a charge on their folio as it happens.
            </span>
          </span>
        </label>

        <p className="text-xs text-stone-500 mt-4 max-w-2xl">
          This changes what new bookings are quoted on. A booking already taken keeps the
          plan it was taken on and the price the guest was given — turning plans off does
          not re-price it, and turning them back on does not add one to it.
        </p>

        <div className="flex gap-3 mt-8 pt-8 border-t border-stone-800">
          <button
            data-testid="gst-save"
            onClick={save}
            disabled={saving || !property}
            className={PRIMARY}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
