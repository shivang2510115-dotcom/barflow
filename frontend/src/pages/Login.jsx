import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Wine, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { homePathFor } from "@/lib/tenancy";
import PasswordInput from "@/components/app/PasswordInput";

const ACCOUNTS = [
  { label: "Admin", email: "admin@barflow.io", password: "admin123" },
  { label: "Manager", email: "manager@barflow.io", password: "manager123" },
  { label: "Waiter", email: "waiter@barflow.io", password: "waiter123" },
  { label: "Kitchen", email: "kitchen@barflow.io", password: "kitchen123" },
];

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  // `identifier` rather than `email`, because a waiter with no email address signs in
  // here with their phone number and the variable should say what the box holds.
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e?.preventDefault();
    setLoading(true);
    const res = await login(identifier, password);
    setLoading(false);
    if (res.ok) {
      toast.success(`Welcome, ${res.user.name}`);
      // The operator lands on /platform and never on /app: they belong to no hotel, so
      // every screen in the app answers them 403. See lib/tenancy.js::homePathFor.
      nav(homePathFor(res.user));
    } else {
      toast.error(res.error);
    }
  };

  const quick = async (a) => {
    setIdentifier(a.email);
    setPassword(a.password);
    setLoading(true);
    const res = await login(a.email, a.password);
    setLoading(false);
    if (res.ok) {
      toast.success(`Welcome, ${res.user.name}`);
      // The operator lands on /platform and never on /app: they belong to no hotel, so
      // every screen in the app answers them 403. See lib/tenancy.js::homePathFor.
      nav(homePathFor(res.user));
    } else {
      toast.error(res.error);
    }
  };

  return (
    <div className="min-h-screen grid md:grid-cols-2 bg-ground text-ink relative z-[2]">
      {/* Left panel */}
      <div className="hidden md:flex flex-col justify-between p-12 border-r border-hairline relative overflow-hidden">
        <div className="absolute inset-0 opacity-30">
          <img
            src="https://images.unsplash.com/photo-1636144924623-b3aea3c5f16c?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NDQ2MzR8MHwxfHNlYXJjaHwxfHxtb29keSUyMGNvY2t0YWlsJTIwZGFyayUyMGJhY2tncm91bmR8ZW58MHx8fHwxNzg0MjE3OTY2fDA&ixlib=rb-4.1.0&q=85"
            alt=""
            className="w-full h-full object-cover"
          />
        </div>
        <div className="absolute inset-0 bg-gradient-to-br from-ground/70 via-ground/50 to-ground/90" />

        <Link to="/" className="relative flex items-center gap-2">
          <Wine className="text-brass" size={22} />
          <span className="font-display uppercase text-lg">BarFlow</span>
        </Link>

        <div className="relative">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-brass mb-4">
            Ops console
          </div>
          <h2 className="font-display uppercase text-4xl leading-[0.95] tracking-tight">
            Pour faster.
            <br />
            Bill smarter.
            <br />
            <span className="text-brass">Never run dry.</span>
          </h2>
        </div>

        <div className="relative text-xs font-mono uppercase tracking-widest text-faint">
          After-dark edition
        </div>
      </div>

      {/* Right form */}
      <div className="flex items-center justify-center p-8 md:p-12">
        <form onSubmit={submit} className="w-full max-w-sm">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-brass mb-4">
            Sign in
          </div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight mb-10">
            Step behind
            <br />
            the bar.
          </h1>

          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">
            Email or phone
          </label>
          {/* `type="text"`, and that is the load-bearing detail on this screen. With
              `type="email"` the browser refuses to submit a phone number before the
              request is ever made, so a waiter hired with a number would be stopped by
              their own keyboard with a validation bubble no server message could
              override. `autoComplete="username"` for the same reason: it is the value a
              password manager fills whichever of the two it saved. The testid keeps its
              old name so nothing that drives this form has to be edited. */}
          <input
            data-testid="login-email"
            type="text"
            inputMode="email"
            autoComplete="username"
            required
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            className="w-full bg-transparent border-b border-hairline-strong focus-neon py-2 mb-2 text-base placeholder:text-faint"
            placeholder="you@bar.com or 98765 43210"
          />
          <p className="text-[11px] text-faint mb-6">
            Whichever your manager set up for you. A number can be typed however you like
            — 98765 43210, 098765 43210 and +91 98765 43210 all reach the same account.
          </p>

          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-1">
            Password
          </label>
          <PasswordInput
            data-testid="login-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            wrapperClassName="mb-8"
            className="w-full bg-transparent border-b border-hairline-strong focus-neon py-2 text-base placeholder:text-faint"
            placeholder="••••••••"
          />

          <button
            type="submit"
            disabled={loading}
            data-testid="login-submit"
            className="w-full rounded-full bg-brass hover:bg-brass-deep disabled:opacity-60 text-on-brass px-6 py-3 font-mono uppercase tracking-widest text-xs transition-colors flex items-center justify-center gap-2"
          >
            {loading ? "Signing in…" : "Enter Console"}
            {!loading && <ArrowRight size={14} />}
          </button>

          <p className="mt-6 text-xs text-faint">
            Running a hotel that is not on BarFlow yet?{" "}
            <Link to="/signup" data-testid="login-signup-link" className="text-brass hover:text-brass">
              Register it
            </Link>
            .
          </p>

          <div className="mt-10 border-t border-hairline pt-6">
            <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-faint mb-3">
              Demo Accounts · tap to enter
            </div>
            <div className="grid grid-cols-2 gap-2">
              {ACCOUNTS.map((a) => (
                <button
                  key={a.email}
                  type="button"
                  data-testid={`demo-${a.label.toLowerCase()}`}
                  onClick={() => quick(a)}
                  className="border border-hairline hover:border-brass hover:text-brass py-2 text-[10px] font-mono uppercase tracking-widest transition-colors"
                >
                  {a.label}
                </button>
              ))}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
