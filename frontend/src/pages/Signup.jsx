import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { ArrowRight, Check, Lock, Wine } from "lucide-react";
import { lockedUntilApproved, unlockedWhilePending } from "@/lib/tenancy";
import { PROPERTY_TYPE_CHOICES } from "@/lib/domains";
import { hasAnIdentifier } from "@/lib/identity";
import PasswordInput from "@/components/app/PasswordInput";

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
  // Either one, at least one — the same rule the staff screen applies, because the
  // account this form creates is a staff account like any other. An owner registering
  // from a phone at the end of service has the same problem their waiters do.
  admin_email: "",
  admin_phone: "",
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
  // Neither identifier is starred. `required` here is the browser's own rule and it
  // cannot express "one of these two", so marking either would stop a registration the
  // API accepts. The either/or is checked in `submit` instead, where it can be said in
  // words.
  ["admin_email", "Your email", "email", "you@hilltop.co.in", false],
  ["admin_phone", "Your phone", "tel", "98765 43210", false],
  ["admin_password", "Password", "password", "At least 8 characters", true],
];

function Field({ id, label, type, placeholder, required, value, onChange }) {
  // The one password on this form is the owner's own, invented thirty seconds ago and
  // never typed before, so it is the field on this screen most worth being able to read
  // back. Same input treatment either way — only the reveal control is added.
  const Control = type === "password" ? PasswordInput : "input";
  const extra =
    type === "password"
      ? { label: "password", autoComplete: "new-password" }
      : { type };
  return (
    <label className="block">
      <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">
        {label}
        {!required && <span className="text-faint normal-case tracking-normal ml-2">optional</span>}
      </span>
      <Control
        data-testid={`signup-${id}`}
        {...extra}
        required={required}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent border-b border-hairline-strong focus-neon py-2 text-base placeholder:text-faint"
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
      <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-3">
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
                  ? "border-brass bg-brass/10"
                  : "border-hairline hover:border-hairline-strong"
              }`}
            >
              <div
                className={`text-sm font-mono uppercase tracking-widest ${
                  on ? "text-brass" : "text-muted2"
                }`}
              >
                {label}
              </div>
              <div className="text-xs text-faint mt-1 leading-relaxed">{blurb}</div>
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
function Pending({ hotel, propertyType }) {
  // The same two lists the in-app banner shows, narrowed the same way: a restaurant is
  // not waiting on approval to build its room types, it will never have any, and
  // promising three hotel screens on the first page it sees is the wrong first
  // impression of a product it has just paid attention to.
  const open = unlockedWhilePending(propertyType);
  const locked = lockedUntilApproved(propertyType);
  return (
    <div className="min-h-screen bg-ground text-ink relative z-[2] flex items-center justify-center p-6 md:p-12">
      <div className="w-full max-w-3xl">
        <Link to="/" className="flex items-center gap-2 mb-12">
          <Wine className="text-brass" size={22} />
          <span className="font-display uppercase text-lg">BarFlow</span>
        </Link>

        <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-brass mb-4">
          Registered · awaiting approval
        </div>
        <h1 className="font-display uppercase text-4xl md:text-6xl leading-[0.95] tracking-tight">
          {hotel} is on
          <br />
          the platform.
        </h1>
        <p className="text-muted2 mt-6 max-w-xl leading-relaxed">
          We review each business before it starts trading. That check is on us, not on you —
          sign in now and set the place up while it runs. Nothing you build in the meantime
          is thrown away when you are approved.
        </p>

        <div className="grid md:grid-cols-2 gap-px bg-raised border border-hairline mt-10">
          <div className="bg-surface p-6">
            <div className="flex items-center gap-2 text-[10px] tracking-[0.25em] uppercase font-mono text-brass mb-4">
              <Check size={14} /> Open now
            </div>
            <ul className="space-y-2 text-sm text-muted2">
              {open.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div className="bg-surface p-6">
            <div className="flex items-center gap-2 text-[10px] tracking-[0.25em] uppercase font-mono text-faint mb-4">
              <Lock size={14} /> Waiting on approval
            </div>
            <ul className="space-y-2 text-sm text-faint">
              {locked.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>

        <Link
          to="/login"
          data-testid="signup-to-login"
          className="mt-10 inline-flex items-center gap-3 rounded-full bg-brass hover:bg-brass-deep text-on-brass px-7 py-3 font-mono uppercase tracking-widest text-xs transition-colors"
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
  const [done, setDone] = useState(null); // { name, type } of the registered business

  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e?.preventDefault();
    // Checked here first so the two mistakes a hotel actually makes come back instantly
    // rather than after a round trip. The server checks both again — this is a courtesy,
    // not the rule.
    if (!form.hotel_name.trim() || !form.admin_name.trim()) {
      const msg = "The name of the business and your name are both needed";
      setError(msg);
      toast.error(msg);
      return;
    }
    // Either identifier, at least one. `required` on the inputs cannot say "one of these
    // two" — marking either would refuse a registration the API accepts — so it is said
    // here, in words, where it can name what the missing one is for.
    if (!hasAnIdentifier({ email: form.admin_email, phone: form.admin_phone })) {
      const msg = "Give an email address or a phone number — you need one of the two to sign in";
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
      // Blank identifiers are omitted rather than sent as `""`. The API types both as
      // `EmailStr | None` / `str | None`, so an empty string is a 422 about a malformed
      // address for somebody who deliberately did not give one.
      const { admin_email, admin_phone, ...rest } = form;
      await api.post("/signup", {
        ...rest,
        ...(admin_email.trim() ? { admin_email: admin_email.trim() } : {}),
        ...(admin_phone.trim() ? { admin_phone: admin_phone.trim() } : {}),
      });
      setDone({ name: form.hotel_name.trim(), type: form.property_type });
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

  if (done) return <Pending hotel={done.name} propertyType={done.type} />;

  return (
    <div className="min-h-screen grid md:grid-cols-2 bg-ground text-ink relative z-[2]">
      <div className="hidden md:flex flex-col justify-between p-12 border-r border-hairline">
        <Link to="/" className="flex items-center gap-2">
          <Wine className="text-brass" size={22} />
          <span className="font-display uppercase text-lg">BarFlow</span>
        </Link>

        <div>
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-brass mb-4">
            Register your place
          </div>
          <h2 className="font-display uppercase text-4xl leading-[0.95] tracking-tight">
            Your rooms.
            <br />
            Your tables.
            <br />
            <span className="text-brass">Or just the tables.</span>
          </h2>
          <p className="text-muted2 mt-8 max-w-sm leading-relaxed text-sm">
            One form creates the property and the first administrator together. Tell us what
            the business is and you get that console and no other — a restaurant never sees a
            front desk. You set the place up straight away; taking money waits until we have
            approved you.
          </p>
        </div>

        <div className="text-xs font-mono uppercase tracking-widest text-faint">
          Hotel, restaurant, bar · one console
        </div>
      </div>

      <div className="flex items-center justify-center p-8 md:p-12">
        <form onSubmit={submit} className="w-full max-w-sm">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-brass mb-4">
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
              <p className="text-xs text-faint mt-2">
                Leave it blank if the certificate is not to hand — you can add it on the
                property screen before you go live.
              </p>
            </div>

            {FIELDS.slice(2).map(([id, label, type, placeholder, required]) => (
              <Fragment key={id}>
                <Field
                  id={id}
                  label={label}
                  type={type}
                  placeholder={placeholder}
                  required={required}
                  value={form[id]}
                  onChange={set(id)}
                />
                {/* Said once, under the second of the pair, because "optional" on each of
                    two fields reads as though both could be skipped and they cannot. */}
                {id === "admin_phone" && (
                  <p className="text-[11px] text-faint -mt-3">
                    One of the two is enough — whichever you will actually sign in with.
                    Give both if you want to be reachable either way.
                  </p>
                )}
              </Fragment>
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
            className="mt-8 w-full rounded-full bg-brass hover:bg-brass-deep disabled:opacity-60 text-on-brass px-6 py-3 font-mono uppercase tracking-widest text-xs transition-colors flex items-center justify-center gap-2"
          >
            {busy ? "Registering…" : "Register"}
            {!busy && <ArrowRight size={14} />}
          </button>

          <p className="mt-8 text-xs text-faint">
            Already registered?{" "}
            <Link to="/login" data-testid="signup-login-link" className="text-brass hover:text-brass">
              Sign in
            </Link>
            .
          </p>
        </form>
      </div>
    </div>
  );
}
