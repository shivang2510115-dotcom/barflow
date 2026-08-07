import React from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Wine, Zap, Boxes, Receipt, QrCode } from "lucide-react";

const BG =
  "https://images.unsplash.com/photo-1696062985889-de626efe0148?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA0MTJ8MHwxfHNlYXJjaHwyfHxwcmVtaXVtJTIwZGltbHklMjBsaXQlMjBiYXIlMjBpbnRlcmlvcnxlbnwwfHx8fDE3ODQyMTc5NjV8MA&ixlib=rb-4.1.0&q=85";

const COCKTAIL =
  "https://images.unsplash.com/photo-1694021256251-ca1607500703?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NDQ2MzR8MHwxfHNlYXJjaHwyfHxtb29keSUyMGNvY2t0YWlsJTIwZGFyayUyMGJhY2tncm91bmR8ZW58MHx8fHwxNzg0MjE3OTY2fDA&ixlib=rb-4.1.0&q=85";

export default function Landing() {
  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 relative overflow-hidden z-[2]">
      {/* Hero image */}
      <div className="absolute inset-0">
        <img src={BG} alt="" className="w-full h-full object-cover opacity-30" />
        <div className="absolute inset-0 bg-gradient-to-b from-stone-950/60 via-stone-950/80 to-stone-950" />
      </div>

      {/* Nav */}
      <header className="relative z-10 flex items-center justify-between px-6 md:px-12 py-6 border-b border-stone-800/60">
        <div className="flex items-center gap-2">
          <Wine className="text-orange-500" size={22} />
          <span className="font-display uppercase text-lg tracking-tight">BarFlow</span>
        </div>
        <Link
          to="/login"
          data-testid="landing-login-btn"
          className="rounded-full border border-stone-700 hover:border-orange-500 hover:text-orange-400 px-5 py-2 text-xs font-mono uppercase tracking-widest transition-colors"
        >
          Staff Sign in
        </Link>
      </header>

      {/* Hero */}
      <section className="relative z-10 px-6 md:px-12 pt-20 pb-24 max-w-6xl">
        <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-6">
          — Operating system for bars &amp; pubs
        </div>
        <h1 className="font-display uppercase text-5xl sm:text-7xl md:text-8xl leading-[0.9] tracking-tighter">
          Pour faster.
          <br />
          <span className="text-orange-500">Bill smarter.</span>
          <br />
          Never run dry.
        </h1>
        <p className="mt-8 text-stone-400 max-w-xl text-base leading-relaxed">
          QR ordering, live POS billing, kitchen tickets and stock control — one dark, precise
          console built for the busiest shift of the week.
        </p>
        <div className="mt-10 flex flex-wrap gap-4">
          <Link
            to="/login"
            data-testid="landing-get-started"
            className="group inline-flex items-center gap-3 rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-7 py-3 font-mono uppercase tracking-widest text-xs transition-colors"
          >
            Open Console
            <ArrowRight size={14} className="group-hover:translate-x-1 transition-transform" />
          </Link>
          <a
            href="#modules"
            className="inline-flex items-center gap-3 border border-stone-700 hover:border-stone-500 px-7 py-3 font-mono uppercase tracking-widest text-xs transition-colors"
          >
            See modules
          </a>
        </div>
      </section>

      {/* Modules */}
      <section
        id="modules"
        className="relative z-10 border-t border-stone-800 grid grid-cols-1 md:grid-cols-4"
      >
        {[
          { icon: QrCode, title: "QR Ordering", copy: "Every table gets a code. Guests order in taps." },
          { icon: Receipt, title: "POS Billing", copy: "Split, discount, settle at counter or online." },
          { icon: Boxes, title: "Live Inventory", copy: "Bottles, kegs, ingredients. Low-stock alerts." },
          { icon: Zap, title: "KOT Board", copy: "Bar and kitchen tickets, timed and routed." },
        ].map((m, i) => (
          <div
            key={m.title}
            className={`p-8 border-stone-800 ${i < 3 ? "md:border-r" : ""} border-t md:border-t-0 hover:bg-stone-900/40 transition-colors`}
            data-testid={`module-${m.title.toLowerCase().replace(/\s+/g, "-")}`}
          >
            <m.icon className="text-orange-500 mb-6" size={24} />
            <div className="font-display uppercase text-xl">{m.title}</div>
            <div className="text-sm text-stone-400 mt-2 leading-relaxed">{m.copy}</div>
          </div>
        ))}
      </section>

      {/* Split promo */}
      <section className="relative z-10 grid md:grid-cols-2 border-t border-stone-800">
        <div className="p-10 md:p-16 border-b md:border-b-0 md:border-r border-stone-800">
          <div className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500 mb-4">
            Roles that make sense
          </div>
          <h2 className="font-display uppercase text-3xl md:text-5xl leading-[0.95] tracking-tight">
            Admin sees revenue.
            <br />
            Waiter sees tables.
            <br />
            Kitchen sees tickets.
          </h2>
          <p className="text-stone-400 mt-6 max-w-md">
            Role-based access keeps the console focused. No one gets what they don&apos;t need.
          </p>
        </div>
        <div className="relative min-h-[360px]">
          <img src={COCKTAIL} alt="cocktail" className="absolute inset-0 w-full h-full object-cover" />
          <div className="absolute inset-0 bg-gradient-to-tr from-stone-950 via-transparent to-transparent" />
          <div className="absolute bottom-8 left-8 right-8">
            <div className="font-mono text-[10px] uppercase tracking-[0.4em] text-orange-400">
              A single console
            </div>
            <div className="font-display uppercase text-2xl md:text-3xl mt-2">
              Built for the after-dark rush.
            </div>
          </div>
        </div>
      </section>

      <footer className="relative z-10 border-t border-stone-800 py-8 px-6 md:px-12 flex flex-col md:flex-row justify-between gap-4 text-xs font-mono uppercase tracking-widest text-stone-500">
        <div>© BarFlow — Ops Console</div>
        <div>v1.0 — Neon Amber Edition</div>
      </footer>
    </div>
  );
}
