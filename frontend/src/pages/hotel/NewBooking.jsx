import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, currency, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";

const today = () => new Date().toISOString().slice(0, 10);
const tomorrow = () =>
  new Date(Date.now() + 86400000).toISOString().slice(0, 10);

export default function NewBooking() {
  const nav = useNavigate();
  const [form, setForm] = useState({
    check_in: today(),
    check_out: tomorrow(),
    adults: 2,
    children: 0,
  });
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [choice, setChoice] = useState(null); // { room_type, quote }
  const [guest, setGuest] = useState({ name: "", phone: "" });
  const [saving, setSaving] = useState(false);

  const set = (k, v) => {
    setForm((f) => ({ ...f, [k]: v }));
    // Any change to the search params invalidates prior results/quotes — they were
    // priced for the old values, so force a fresh search before booking again.
    setResults(null);
    setChoice(null);
  };

  const search = async () => {
    if (form.check_out <= form.check_in) {
      toast.error("Check-out must be after check-in");
      return;
    }
    setSearching(true);
    setChoice(null);
    try {
      const { data } = await api.get("/availability", { params: form });
      setResults(data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSearching(false);
    }
  };

  const book = async () => {
    if (!guest.name.trim() || !guest.phone.trim()) {
      toast.error("Guest name and phone are required");
      return;
    }
    if (form.check_out <= form.check_in) {
      toast.error("Check-out must be after check-in");
      return;
    }
    setSaving(true);
    try {
      // Reuse the guest if the phone is already known, rather than failing on 409.
      let guestId;
      try {
        const created = await api.post("/guests", guest);
        guestId = created.data.id;
      } catch (e) {
        const existing = e.response?.data?.detail?.guest;
        if (e.response?.status === 409 && existing) {
          guestId = existing.id;
          toast.info(`Existing guest matched: ${existing.name}`);
        } else {
          throw e;
        }
      }

      const { data } = await api.post("/bookings", {
        guest_id: guestId,
        room_type_id: choice.room_type.id,
        meal_plan_id: choice.quote.meal_plan.id,
        check_in: form.check_in,
        check_out: form.check_out,
        adults: Number(form.adults),
        children: Number(form.children),
      });
      toast.success(`Booked — ${data.reference}`);
      nav(`/app/hotel/bookings/${data.id}`);
    } catch (e) {
      // 409 = no room left for these dates by the time of write, 422 = a night in the
      // stay has no rate defined. Both come back as { message, ... } — surface it plainly
      // rather than letting the desk think nothing happened.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 md:p-10">
      <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Hotel</div>
      <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
        New booking
      </h1>

      <div className="flex flex-wrap gap-4 items-end mb-8">
        {[
          ["check_in", "Check in", "date"],
          ["check_out", "Check out", "date"],
          ["adults", "Adults", "number"],
          ["children", "Children", "number"],
        ].map(([key, label, type]) => (
          <label key={key} className="text-xs tracking-widest uppercase text-stone-500">
            {label}
            <input
              type={type}
              min={type === "number" ? 0 : undefined}
              value={form[key]}
              onChange={(e) => set(key, e.target.value)}
              className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
            />
          </label>
        ))}
        <button
          onClick={search}
          disabled={searching}
          className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {results && results.length === 0 && (
        <p className="text-stone-400">No room types are set up yet.</p>
      )}

      <div className="grid gap-4">
        {(results || []).map((row) => (
          <div key={row.room_type.id} className="border border-stone-800 bg-stone-900 rounded p-5">
            <div className="flex items-baseline justify-between flex-wrap gap-2">
              <h3 className="text-lg font-semibold">{row.room_type.name}</h3>
              <span
                className={
                  row.available > 0
                    ? "text-orange-400 font-mono text-sm"
                    : "text-stone-500 font-mono text-sm"
                }
              >
                {row.available} free
              </span>
            </div>

            {row.unpriced_dates && (
              <p className="text-sm text-red-400 mt-3">
                No rate set for {row.unpriced_dates.join(", ")} — add one under Rates
                before booking this type.
              </p>
            )}
            {!row.fits_party && (
              <p className="text-sm text-stone-500 mt-3">Too small for this party.</p>
            )}
            {row.fits_party && row.available <= 0 && (
              <p className="text-sm text-stone-500 mt-3">Nothing free for these dates.</p>
            )}

            {row.available > 0 && row.fits_party && (
              <div className="grid gap-2 mt-4 md:grid-cols-3">
                {row.quotes.map((q) => (
                  <button
                    key={q.meal_plan.id}
                    onClick={() => setChoice({ room_type: row.room_type, quote: q })}
                    className={`text-left border rounded p-3 transition-colors ${
                      choice?.quote?.meal_plan?.id === q.meal_plan.id &&
                      choice?.room_type?.id === row.room_type.id
                        ? "border-orange-500 bg-stone-800"
                        : "border-stone-800 hover:border-stone-600"
                    }`}
                  >
                    <div className="text-xs tracking-widest uppercase text-stone-500">
                      {q.meal_plan.code} · {q.meal_plan.name}
                    </div>
                    <div className="text-xl font-semibold mt-1">{currency(q.total)}</div>
                    <div className="text-xs text-stone-500 mt-1">
                      {q.nights.length} night{q.nights.length === 1 ? "" : "s"} incl.{" "}
                      {currency(q.tax_total)} tax
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {choice && (
        <div className="mt-10 border border-stone-800 bg-stone-900 rounded p-5 max-w-xl">
          <h3 className="text-lg font-semibold mb-1">
            {choice.room_type.name} · {choice.quote.meal_plan.code}
          </h3>
          <p className="text-sm text-stone-400 mb-4">
            {form.check_in} → {form.check_out} · {currency(choice.quote.total)}
          </p>

          <div className="flex gap-4 flex-wrap">
            {[["name", "Guest name"], ["phone", "Phone"]].map(([k, label]) => (
              <label key={k} className="text-xs tracking-widest uppercase text-stone-500">
                {label}
                <input
                  value={guest[k]}
                  onChange={(e) => setGuest((g) => ({ ...g, [k]: e.target.value }))}
                  className="block mt-2 bg-transparent border-b border-stone-700 text-stone-100 py-1 focus:border-orange-500 outline-none"
                />
              </label>
            ))}
          </div>

          <button
            onClick={book}
            disabled={saving}
            className="mt-6 bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-8 py-2 text-sm tracking-widest uppercase"
          >
            {saving ? "Booking…" : "Confirm booking"}
          </button>
        </div>
      )}
    </div>
  );
}
