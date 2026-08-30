import React, { useEffect, useState } from "react";

/**
 * The screen shown while the app boots. Three concentric arcs turning at different
 * speeds, and the word Loading.
 *
 * It replaced a 2.6-second animated sequence. A splash is a toll paid on every cold
 * start by someone who wants to be somewhere else — a receptionist opening the tablet
 * with a guest at the desk — so this one is quiet, short, and says the only thing worth
 * saying. It leaves as soon as the app is ready rather than holding the screen for a
 * fixed run of animation.
 *
 * Nothing here is imported from a component library: the arcs are three bordered circles
 * with three sides made transparent, which is a border-box trick that needs no SVG, no
 * dependency and no JavaScript to run the motion.
 */
const MIN_MS = 450;      // below this a flash of spinner reads as a glitch, not as loading
const REDUCED_MS = 120;  // no motion to see, so no reason to wait for it

export default function Splash({ onDone }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const t = setTimeout(() => setLeaving(true), reduce ? REDUCED_MS : MIN_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (!leaving) return;
    // Long enough for the fade to finish, short enough not to be felt as a delay.
    const t = setTimeout(() => onDone?.(), 260);
    return () => clearTimeout(t);
  }, [leaving, onDone]);

  return (
    <div
      data-testid="splash-screen"
      className={`fixed inset-0 z-[100] bg-ground flex flex-col items-center justify-center
                  transition-opacity duration-[260ms] ${leaving ? "opacity-0" : "opacity-100"}`}
    >
      <style>{`
        @keyframes bf-spin { to { transform: rotate(360deg); } }
        @keyframes bf-breathe { 0%,100% { opacity: .35 } 50% { opacity: 1 } }
        /* Someone who has asked for less motion gets a still ring that simply breathes,
           rather than three things turning in their peripheral vision all day. */
        @media (prefers-reduced-motion: reduce) {
          .bf-arc { animation: bf-breathe 1.6s ease-in-out infinite !important; }
        }
      `}</style>

      <div className="relative h-24 w-24">
        {[
          { size: "inset-0",   dur: "1.6s", dir: "normal",  colour: "border-t-orange-500" },
          { size: "inset-3",   dur: "2.2s", dir: "reverse", colour: "border-t-orange-400/70" },
          { size: "inset-6",   dur: "2.8s", dir: "normal",  colour: "border-t-stone-500" },
        ].map((ring, i) => (
          <div
            key={i}
            className={`bf-arc absolute ${ring.size} rounded-full border-2 border-transparent ${ring.colour}`}
            style={{ animation: `bf-spin ${ring.dur} linear infinite ${ring.dir}` }}
          />
        ))}
      </div>

      <div className="mt-8 text-[10px] tracking-[0.5em] uppercase text-faint">
        Loading
      </div>
    </div>
  );
}
