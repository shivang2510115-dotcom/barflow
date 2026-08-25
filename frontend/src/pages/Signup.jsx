import { useState } from "react";
import { Link } from "react-router-dom";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { ArrowRight, Check, Lock, Wine } from "lucide-react";
import { LOCKED_UNTIL_APPROVED, UNLOCKED_WHILE_PENDING } from "@/lib/tenancy";
import { PROPERTY_TYPE_CHOICES } from "@/lib/domains";

/**
 * `/signup` — public, unauthenticated, and the only way a new hotel comes into existence.
 *
 * Two screens in one file because they are one flow: the form, and then what the hotel
 * has actually got. The second half is the point. A hotel that fills this in and is shown
 * "success" learns nothing; a hotel shown that it can build its rooms and rates now, and
 * cannot take a booking until it is approved, knows what its next hour looks like and why
 * the front desk is greyed out when it gets there.
 *
 * The API's refusals are specific — an email already in use, a GSTIN of the wrong shape,
 * a password under eight characters, too many attempts from one address — so they are
 * shown as they come, in the form, rather than being flattened into "something went
 * wrong". A generic failure on the one screen with no signed-in person behind it is a
 * hotel that gives up.
 */

const BLANK = {
  hotel_name: "",
  city: "",
  gstin: "",
  admin_name: "",
  admin_email: "",
  admin_password: "",
  // Nothing pre-selected. The API defaults an omitted type to `both`, which is right for
  // an old client but wrong for a form: a restaurant that never notices the question and
  // is handed a hotel gets a front desk it cannot staff and screens it cannot open. So
  // the form asks, and refuses to submit until it has an answer.
  property_type: "",
};

const MIN_PASSWORD = 8;

const FIELDS = [
  ["hotel_name", "Name of the business", "text", "Hilltop Retreat", true],
  ["city", "City", "text", "Manali", false],
  ["admin_name", "Your name", "text", "Priya Nair", true],
  ["admin_email", "Your email", "email", "you@hilltop.co.in", true],
  ["admin_password", "Password", "password", "At least 8 characters", true],
];

function Field({ id, label, type, placeholder, required, value, onChange }) {
  return (
    <label className="block">
      <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">
        {label}
        {!required && <span className="text-stone-600 normal-case tracking-normal ml-2">optional</span>}
      </span>
      <input
        data-testid={`signup-${id}`}
        type={type}
        required={required}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent border-b border-stone-700 focus-neon py-2 text-base placeholder:text-stone-600"
      />
    </label>
  );
}

/**
 * What kind of business is signing up.
 *
 * Asked in the trade's words rather than ours — "Restaurant or bar", not "outlet" — and
 * with a line under each saying what it includes, because the choice decides which half
 * of the product exists for this tenant and is not something they can change later from
 * inside the app. A restaurant that picks the middle card never sees a rooms screen at
 * all; one that picks the wrong card sees a front desk it can never staff.
 */
function TypePicker({ value, onChange }) {
  return (
    <div>
      <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-3">
        What is it?
      </span>
      <div className="space-y-2">
        {PROPERTY_TYPE_CHOICES.map(({ key, label, blurb }) => {
          const on = value === key;
          return (
            <button
              key={key}
              type="button"
              data-testid={`signup-type-${key}`}
              aria-pressed={on}
              onClick={() => onChange(key)}
              className={`w-full text-left border px-4 py-3 transition-colors ${
                on
                  ? "border-orange-500 bg-orange-500/10"
                  : "border-stone-800 hover:border-stone-600"
              }`}
            >
              <div
                className={`text-sm font-mono uppercase tracking-widest ${
                  on ? "text-orange-400" : "text-stone-300"
                }`}
              >
                {label}
              </div>
              <div className="text-xs text-stone-500 mt-1 leading-relaxed">{blurb}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * What the hotel has, the moment the form is submitted.
 *
 * The two lists are the client's copy of the server's `setup_time` marking (see
 * backend/services/access.py), said in the words of the job rather than the endpoint:
 * configuring is open, operating is not. They come from lib/tenancy.js so this screen and
 * the banner inside the app cannot drift into promising different things.
 */
function Pending({ hotel }) {
  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 relative z-[2] flex items-center justify-center p-6 md:p-12">
      <div className="w-full max-w-3xl">
        <Link to="/" className="flex items-center gap-2 mb-12">
          <Wine className="text-orange-500" size={22} />
          <span className="font-display uppercase text-lg">BarFlow</span>
        </Link>

        <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-4">
          Registered · awaiting approval
        </div>
        <h1 className="font-display uppercase text-4xl md:text-6xl leading-[0.95] tracking-tight">
          {hotel} is on
          <br />
          the platform.
        </h1>
        <p className="text-stone-400 mt-6 max-w-xl leading-relaxed">
          We review each hotel before it starts trading. That check is on us, not on you —
          sign in now and set the place up while it runs. Nothing you build in the meantime
          is thrown away when you are approved.
        </p>

        <div className="grid md:grid-cols-2 gap-px bg-stone-800 border border-stone-800 mt-10">
          <div className="bg-stone-900 p-6">
            <div className="flex items-center gap-2 text-[10px] tracking-[0.25em] uppercase font-mono text-orange-400 mb-4">
              <Check size={14} /> Open now
            </div>
            <ul className="space-y-2 text-sm text-stone-300">
              {UNLOCKED_WHILE_PENDING.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div className="bg-stone-900 p-6">
            <div className="flex items-center gap-2 text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500 mb-4">
              <Lock size={14} /> Waiting on approval
            </div>
            <ul className="space-y-2 text-sm text-stone-500">
              {LOCKED_UNTIL_APPROVED.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>

        <Link
          to="/login"
          data-testid="signup-to-login"
          className="mt-10 inline-flex items-center gap-3 rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-7 py-3 font-mono uppercase tracking-widest text-xs transition-colors"
        >
          Sign in and start setting up
          <ArrowRight size={14} />
        </Link>
      </div>
    </div>
  );
}

export default function Signup() {
  const [form, setForm] = useState(BLANK);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(null); // the registered hotel's name

  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e?.preventDefault();
    // Checked here first so the two mistakes a hotel actually makes come back instantly
    // rather than after a round trip. The server checks both again — this is a courtesy,
    // not the rule.
    if (!form.hotel_name.trim() || !form.admin_name.trim() || !form.admin_email.trim()) {
      const msg = "The name of the business, your name and your email are all needed";
      setError(msg);
      toast.error(msg);
      return;
    }
    // Checked rather than defaulted. The server would accept the omission and give them
    // a hotel, which is the one answer that cannot be right for everybody.
    if (!form.property_type) {
      const msg = "Say what the business is — a hotel, a restaurant or bar, or both";
      setError(msg);
      toast.error(msg);
      return;
    }
    if (form.admin_password.length < MIN_PASSWORD) {
      const msg = `Password must be at least ${MIN_PASSWORD} characters`;
      setError(msg);
      toast.error(msg);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.post("/signup", form);
      setDone(form.hotel_name.trim());
    } catch (err) {
      // Every refusal this endpoint gives is worth reading: 409 names the email, 400 names
      // the GSTIN or the password, 429 says to come back later. formatApiErrorDetail also
      // flattens the 422 pydantic sends for an address that is not an email at all.
      const msg = formatApiErrorDetail(err.response?.data?.detail) || err.message;
      setError(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  if (done) return <Pending hotel={done} />;

  return (
    <div className="min-h-screen grid md:grid-cols-2 bg-stone-950 text-stone-100 relative z-[2]">
      <div className="hidden md:flex flex-col justify-between p-12 border-r border-stone-800">
        <Link to="/" className="flex items-center gap-2">
          <Wine className="text-orange-500" size={22} />
          <span className="font-display uppercase text-lg">BarFlow</span>
        </Link>

        <div>
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-4">
            Register your place
          </div>
          <h2 className="font-display uppercase text-4xl leading-[0.95] tracking-tight">
            Your rooms.
            <br />
            Your tables.
            <br />
            <span className="text-orange-500">Or just the tables.</span>
          </h2>
          <p className="text-stone-400 mt-8 max-w-sm leading-relaxed text-sm">
            One form creates the property and the first administrator together. Tell us what
            the business is and you get that console and no other — a restaurant never sees a
            front desk. You set the place up straight away; taking money waits until we have
            approved you.
          </p>
        </div>

        <div className="text-xs font-mono uppercase tracking-widest text-stone-500">
          Hotel, restaurant, bar · one console
        </div>
      </div>

      <div className="flex items-center justify-center p-8 md:p-12">
        <form onSubmit={submit} className="w-full max-w-sm">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-4">
            Sign up
          </div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight mb-10">
            Put your place
            <br />
            on the board.
          </h1>

          <div className="space-y-6">
            {FIELDS.slice(0, 2).map(([id, label, type, placeholder, required]) => (
              <Field
                key={id}
                id={id}
                label={label}
                type={type}
                placeholder={placeholder}
                required={required}
                value={form[id]}
                onChange={set(id)}
              />
            ))}

            {/* Asked early, straight after the name and the city: it is the question that
                decides what the rest of the console will be, and burying it under the
                password is how it gets answered without being read. */}
            <TypePicker
              value={form.property_type}
              onChange={(v) => setForm((f) => ({ ...f, property_type: v }))}
            />

            <div>
              <Field
                id="gstin"
                label="GSTIN"
                type="text"
                placeholder="27AAPFU0939F1ZV"
                required={false}
                value={form.gstin}
                onChange={set("gstin")}
              />
              {/* Said out loud because the certificate is usually in a drawer at the
                  office, and a blocked signup at nine in the evening is a hotel that
                  signs up with somebody else. The property screen asks again later. */}
              <p className="text-xs text-stone-500 mt-2">
                Leave it blank if the certificate is not to hand — you can add it on the
                property screen before you go live.
              </p>
            </div>

            {FIELDS.slice(2).map(([id, label, type, placeholder, required]) => (
              <Field
                key={id}
                id={id}
                label={label}
                type={type}
                placeholder={placeholder}
                required={required}
                value={form[id]}
                onChange={set(id)}
              />
            ))}
          </div>

          {/* Toasted and shown here both: the toast is missed by anyone who scrolled, and
              the mistake belongs next to the field it is about. */}
          {error && (
            <p
              data-testid="signup-error"
              className="mt-6 border border-red-500/40 bg-red-950/20 text-red-300 text-sm px-4 py-3"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            data-testid="signup-submit"
            className="mt-8 w-full rounded-full bg-orange-600 hover:bg-orange-500 disabled:opacity-60 text-stone-950 px-6 py-3 font-mono uppercase tracking-widest text-xs transition-colors flex items-center justify-center gap-2"
          >
            {busy ? "Registering…" : "Register"}
            {!busy && <ArrowRight size={14} />}
          </button>

          <p className="mt-8 text-xs text-stone-500">
            Already registered?{" "}
            <Link to="/login" data-testid="signup-login-link" className="text-orange-400 hover:text-orange-300">
              Sign in
            </Link>
            .
          </p>
        </form>
      </div>
    </div>
  );
}
