import React from "react";
import { motion } from "framer-motion";

/**
 * Ambient background: slow floating amber embers + subtle gradient drift.
 * Opacity kept under 10%. Sits behind content (z-0 absolute).
 */
export default function AmbientBG() {
  const embers = React.useMemo(
    () =>
      Array.from({ length: 14 }).map(() => ({
        left: Math.random() * 100,
        delay: Math.random() * 6,
        duration: 8 + Math.random() * 8,
        size: 2 + Math.random() * 4,
        opacity: 0.05 + Math.random() * 0.05,
      })),
    []
  );

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden z-0">
      <motion.div
        className="absolute inset-0 opacity-[0.08]"
        style={{
          background:
            "radial-gradient(600px 400px at 20% 20%, #ea580c, transparent 60%), radial-gradient(500px 400px at 80% 80%, #f59e0b, transparent 60%)",
        }}
        animate={{ backgroundPosition: ["0% 0%", "10% 20%", "0% 0%"] }}
        transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
      />
      {embers.map((e, i) => (
        <motion.span
          key={i}
          className="absolute rounded-full bg-orange-400"
          style={{
            left: `${e.left}%`,
            bottom: "-2%",
            width: e.size,
            height: e.size,
            opacity: e.opacity,
            filter: "blur(1px)",
          }}
          animate={{ y: ["0vh", "-105vh"], opacity: [e.opacity, e.opacity * 1.3, 0] }}
          transition={{
            duration: e.duration,
            delay: e.delay,
            repeat: Infinity,
            ease: "linear",
          }}
        />
      ))}
    </div>
  );
}
