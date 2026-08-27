import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, KeyRound, Wine } from "lucide-react";

import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { homePathFor } from "@/lib/tenancy";
import PasswordInput from "@/components/app/PasswordInput";

/**
 * `/account` — the one screen that belongs to both consoles.
 *
 * Until now the only way a password could be changed was an admin resetting somebody
 * else's from the staff screen. That left a waiter unable to change the password their
 * manager invented for them in front of the till, an admin having to find their own row
 * in a list of everybody, and the platform operator — who belongs to no hotel and is
 * refused every hotel screen — with no way at all.
 *
 * So it renders its own chrome rather than sitting inside AppLayout, for exactly the
 * reason `/platform` does: the app sidebar is section-scoped and permission-filtered, and
 * the operator holds neither, so the frame around this form would be empty for them. One
 * page, one address, reachable from both shells — the sidebar's account block in the app,
 * the header in the platform console.
 *
 * Everything it calls is the caller's own record. `POST /api/auth/password` hangs off
 * `get_current_user` rather than `require_access` (see backend/routers/auth.py), which is
 * what makes "both consoles" true rather than merely intended.
 */

// The server's rule, in `backend/services/password.py`, is length plus a denylist, and
// the denylist is not worth shipping to the browser. This mirrors only the length, to
// disable the button rather than to judge the password: the server is what decides, and
// its refusal is shown verbatim because it says what to do next.
const MIN_PASSWORD = 8;

const BLANK = { current: "", next: "", confirm: "" };

export default function Account() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState(BLANK);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const set = (k) => (e) => {
    setForm((f) => ({ ...f, [k]: e.target.value }));
    setProblem("");
  };

  const mismatched = Boolean(form.confirm) && form.next !== form.confirm;
  const ready =
    form.current.length > 0 &&
    form.next.length >= MIN_PASSWORD &&
    form.next === form.confirm;

  const submit = async (e) => {
    e?.preventDefault();
    // Checked here as well as by the disabled button, because Enter in a text field
    // submits a form whatever the button is doing.
    if (!ready) {
      setProblem(
        mismatched
          ? "The two new passwords do not match."
          : `Fill in your current password and a new one of at least ${MIN_PASSWORD} characters.`,
      );
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await api.post("/auth/password", {
        current_password: form.current,
        new_password: form.next,
      });
      // Cleared rather than left filled: the next person at this terminal should not find
      // three password fields populated.
      setForm(BLANK);
      toast.success("Password changed. Use the new one next time you sign in.");
    } catch (err) {
      // Shown as it comes. The API's refusals are specific — the wrong current password,
      // one of the commonest passwords in the world, your own email address — and each
      // one says what to do instead, which "something went wrong" does not.
      const detail = formatApiErrorDetail(err.response?.data?.detail) || err.message;
      setProblem(detail);
      toast.error(detail);
    } finally {
      setBusy(false);
    }
  };

  const signOut = () => {
    logout();
    nav("/login");
  };

  const home = homePathFor(user);

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 relative z-[2]">
      <header className="border-b border-stone-800 px-6 md:px-10 py-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Wine className="text-orange-500" size={22} />
          <div>
            <div className="font-display text-lg tracking-tight uppercase leading-none">
              BarFlow
            </div>
            <div className="text-[10px] tracking-[0.25em] uppercase font-mono text-stone-500 mt-1">
              Account
            </div>
          </div>
        </div>
        <Link
          to={home}
          data-testid="account-back"
          className="flex items-center gap-2 border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-3 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
        >
          <ArrowLeft size={14} /> Back
        </Link>
      </header>

      <div className="p-6 md:p-10">
        <div className="text-xs tracking-[0.4em] uppercase text-orange-500 mb-3">Account</div>
        <h1 className="text-4xl md:text-5xl font-extrabold uppercase tracking-tight mb-8">
          Your password
        </h1>

        <div className="border border-stone-800 bg-stone-900 rounded p-5 md:p-6 max-w-xl">
          <div className="flex items-center gap-2 mb-1">
            <KeyRound className="text-orange-500" size={16} />
            <h2 className="text-[11px] tracking-[0.2em] uppercase text-stone-500">
              Change it
            </h2>
          </div>
          <p className="text-xs text-stone-500 mb-6 leading-relaxed" data-testid="account-who">
            Signed in as <span className="text-stone-300">{user?.email}</span>
            {user?.role ? (
              <span className="font-mono uppercase text-orange-500/80"> · {user.role}</span>
            ) : null}
            . This changes your own password and nobody else's.
          </p>

          <form onSubmit={submit} className="space-y-6">
            <label className="block">
              <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">
                Current password
              </span>
              <PasswordInput
                data-testid="account-current-password"
                label="current password"
                autoComplete="current-password"
                required
                value={form.current}
                onChange={set("current")}
                placeholder="The one you use now"
                className="w-full bg-transparent border-b border-stone-700 focus-neon py-2 text-base placeholder:text-stone-600"
              />
              <span className="block text-[11px] text-stone-600 mt-2 leading-relaxed">
                Asked for so that a signed-in session left open on a shared terminal cannot
                be turned into permanent ownership of your account.
              </span>
            </label>

            <label className="block">
              <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">
                New password
              </span>
              <PasswordInput
                data-testid="account-new-password"
                label="new password"
                autoComplete="new-password"
                required
                value={form.next}
                onChange={set("next")}
                placeholder={`At least ${MIN_PASSWORD} characters`}
                className="w-full bg-transparent border-b border-stone-700 focus-neon py-2 text-base placeholder:text-stone-600"
              />
            </label>

            <label className="block">
              <span className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">
                New password again
              </span>
              <PasswordInput
                data-testid="account-confirm-password"
                label="the repeated new password"
                autoComplete="new-password"
                required
                value={form.confirm}
                onChange={set("confirm")}
                placeholder="Type it once more"
                className={`w-full bg-transparent border-b py-2 text-base placeholder:text-stone-600 focus-neon ${
                  mismatched ? "border-red-500/60" : "border-stone-700"
                }`}
              />
              {/* Kept, rather than dropped in favour of the reveal control, because there
                  is no email delivery in this application: a typo in a new password locks
                  you out until an admin resets it — and the platform operator has no
                  admin above them to do that. */}
              {mismatched && (
                <span
                  data-testid="account-mismatch"
                  className="block text-xs text-red-400 mt-2"
                >
                  The two do not match.
                </span>
              )}
            </label>

            {problem && (
              <p
                data-testid="account-error"
                className="text-sm text-red-400 border border-red-500/30 bg-red-950/20 rounded px-4 py-3"
              >
                {problem}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                type="submit"
                data-testid="account-submit"
                disabled={busy || !ready}
                className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-full px-6 py-2 text-sm tracking-widest uppercase transition-colors"
              >
                {busy ? "Changing…" : "Change password"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setForm(BLANK);
                  setProblem("");
                }}
                disabled={busy}
                className="border border-stone-700 text-stone-400 hover:text-stone-200 hover:border-stone-500 disabled:opacity-50 rounded-full px-6 py-2 text-sm tracking-widest uppercase transition-colors"
              >
                Clear
              </button>
            </div>
          </form>

          {/* Said plainly rather than left to be discovered. A sign-in token is a signed
              statement about who you are and carries nothing derived from the password, so
              changing it does not end a session that already exists. Somebody changing
              their password because they think they were watched needs to know that. */}
          <p className="text-xs text-stone-500 mt-6 pt-5 border-t border-stone-800 leading-relaxed">
            A device already signed in as you stays signed in until its session expires —
            changing the password does not end it. If you are worried about one in
            particular, sign out on it, and an admin can deactivate the account outright.
          </p>
        </div>

        <button
          type="button"
          onClick={signOut}
          data-testid="account-logout"
          className="mt-8 text-[10px] font-mono uppercase tracking-widest text-stone-600 hover:text-orange-400 transition-colors"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
