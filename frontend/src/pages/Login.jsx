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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e?.preventDefault();
    setLoading(true);
    const res = await login(email, password);
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
    setEmail(a.email);
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
    <div className="min-h-screen grid md:grid-cols-2 bg-stone-950 text-stone-100 relative z-[2]">
      {/* Left panel */}
      <div className="hidden md:flex flex-col justify-between p-12 border-r border-stone-800 relative overflow-hidden">
        <div className="absolute inset-0 opacity-30">
          <img
            src="https://images.unsplash.com/photo-1636144924623-b3aea3c5f16c?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NDQ2MzR8MHwxfHNlYXJjaHwxfHxtb29keSUyMGNvY2t0YWlsJTIwZGFyayUyMGJhY2tncm91bmR8ZW58MHx8fHwxNzg0MjE3OTY2fDA&ixlib=rb-4.1.0&q=85"
            alt=""
            className="w-full h-full object-cover"
          />
        </div>
        <div className="absolute inset-0 bg-gradient-to-br from-stone-950/70 via-stone-950/50 to-stone-950/90" />

        <Link to="/" className="relative flex items-center gap-2">
          <Wine className="text-orange-500" size={22} />
          <span className="font-display uppercase text-lg">BarFlow</span>
        </Link>

        <div className="relative">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-4">
            Ops console
          </div>
          <h2 className="font-display uppercase text-4xl leading-[0.95] tracking-tight">
            Pour faster.
            <br />
            Bill smarter.
            <br />
            <span className="text-orange-500">Never run dry.</span>
          </h2>
        </div>

        <div className="relative text-xs font-mono uppercase tracking-widest text-stone-500">
          After-dark edition
        </div>
      </div>

      {/* Right form */}
      <div className="flex items-center justify-center p-8 md:p-12">
        <form onSubmit={submit} className="w-full max-w-sm">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-4">
            Sign in
          </div>
          <h1 className="font-display uppercase text-4xl md:text-5xl leading-none tracking-tight mb-10">
            Step behind
            <br />
            the bar.
          </h1>

          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">
            Email
          </label>
          <input
            data-testid="login-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full bg-transparent border-b border-stone-700 focus-neon py-2 mb-6 text-base placeholder:text-stone-600"
            placeholder="you@bar.com"
          />

          <label className="block text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-1">
            Password
          </label>
          <PasswordInput
            data-testid="login-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            wrapperClassName="mb-8"
            className="w-full bg-transparent border-b border-stone-700 focus-neon py-2 text-base placeholder:text-stone-600"
            placeholder="••••••••"
          />

          <button
            type="submit"
            disabled={loading}
            data-testid="login-submit"
            className="w-full rounded-full bg-orange-600 hover:bg-orange-500 disabled:opacity-60 text-stone-950 px-6 py-3 font-mono uppercase tracking-widest text-xs transition-colors flex items-center justify-center gap-2"
          >
            {loading ? "Signing in…" : "Enter Console"}
            {!loading && <ArrowRight size={14} />}
          </button>

          <p className="mt-6 text-xs text-stone-500">
            Running a hotel that is not on BarFlow yet?{" "}
            <Link to="/signup" data-testid="login-signup-link" className="text-orange-400 hover:text-orange-300">
              Register it
            </Link>
            .
          </p>

          <div className="mt-10 border-t border-stone-800 pt-6">
            <div className="text-[10px] uppercase tracking-[0.25em] font-mono text-stone-500 mb-3">
              Demo Accounts · tap to enter
            </div>
            <div className="grid grid-cols-2 gap-2">
              {ACCOUNTS.map((a) => (
                <button
                  key={a.email}
                  type="button"
                  data-testid={`demo-${a.label.toLowerCase()}`}
                  onClick={() => quick(a)}
                  className="border border-stone-800 hover:border-orange-500 hover:text-orange-400 py-2 text-[10px] font-mono uppercase tracking-widest transition-colors"
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
