import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Wine } from "lucide-react";

/**
 * "Bar Comes Alive" splash — plays once per session on first mount.
 * Duration: ~2.6s. Skips instantly on prefers-reduced-motion.
 */
export default function Splash({ onDone }) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const t = setTimeout(() => setVisible(false), reduce ? 300 : 2600);
    return () => clearTimeout(t);
  }, []);

  const handleDone = () => {
    setVisible(false);
    onDone?.();
  };

  return (
    <AnimatePresence onExitComplete={handleDone}>
      {visible && (
        <motion.div
          key="splash"
          className="fixed inset-0 z-[100] bg-stone-950 overflow-hidden"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.6, ease: "easeInOut" }}
          data-testid="splash-screen"
        >
          {/* Neon reflections — flicker on */}
          <motion.div
            className="absolute inset-0"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.15, 0.05, 0.25, 0.4] }}
            transition={{ duration: 1.2, times: [0, 0.2, 0.35, 0.5, 1] }}
            style={{
              backgroundImage:
                "radial-gradient(600px 300px at 20% 20%, rgba(234,88,12,0.35), transparent 60%), radial-gradient(500px 260px at 80% 80%, rgba(234,88,12,0.28), transparent 60%)",
            }}
          />

          {/* Bar shelves lighting up */}
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 flex flex-col gap-6 px-8">
            {[0.35, 0.55, 0.75].map((delay, i) => (
              <motion.div
                key={i}
                className="h-[1px] bg-orange-500/70"
                initial={{ scaleX: 0, transformOrigin: i % 2 ? "right" : "left" }}
                animate={{ scaleX: 1 }}
                transition={{ delay, duration: 0.5, ease: "easeOut" }}
              />
            ))}
          </div>

          {/* Pour animation — glass silhouette + liquid */}
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="relative w-40 h-56">
              {/* Glass outline */}
              <motion.svg
                viewBox="0 0 100 140"
                className="absolute inset-0 w-full h-full"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.6, duration: 0.5 }}
              >
                <path
                  d="M 20 20 L 30 120 Q 30 130 40 130 L 60 130 Q 70 130 70 120 L 80 20 Z"
                  stroke="#f5f5f4"
                  strokeWidth="1.5"
                  fill="none"
                />
              </motion.svg>

              {/* Liquid rising */}
              <motion.div
                className="absolute bottom-[7%] left-1/2 -translate-x-1/2 w-[50%] rounded-b-lg"
                style={{
                  background:
                    "linear-gradient(180deg, #fb923c 0%, #ea580c 60%, #9a3412 100%)",
                }}
                initial={{ height: 0 }}
                animate={{ height: ["0%", "55%"] }}
                transition={{ delay: 1.0, duration: 0.9, ease: "easeOut" }}
              />

              {/* Pouring stream */}
              <motion.div
                className="absolute left-1/2 -translate-x-1/2 w-[3px] bg-orange-500"
                initial={{ height: 0, top: -20 }}
                animate={{ height: [0, 60, 0] }}
                transition={{ delay: 0.9, duration: 1.0, times: [0, 0.4, 1] }}
                style={{ boxShadow: "0 0 12px rgba(234,88,12,0.8)" }}
              />
            </div>
          </div>

          {/* Wordmark */}
          <div className="absolute bottom-16 inset-x-0 flex flex-col items-center gap-3">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 1.8, duration: 0.6 }}
              className="flex items-center gap-2"
            >
              <Wine className="text-orange-500" size={20} />
              <span className="font-display uppercase text-2xl tracking-tight">BarFlow</span>
            </motion.div>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 2.0, duration: 0.4 }}
              className="text-[10px] tracking-[0.4em] uppercase font-mono text-orange-500"
            >
              Pour · Bill · Never run dry
            </motion.div>
          </div>

          {/* Grain */}
          <div
            className="absolute inset-0 pointer-events-none opacity-10 mix-blend-overlay"
            style={{
              backgroundImage:
                "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence baseFrequency='0.9'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")",
            }}
          />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
