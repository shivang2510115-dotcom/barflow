import React from "react";
import { motion } from "framer-motion";

/**
 * Reusable empty state with a soft cocktail glass illustration.
 */
export default function EmptyState({ title = "Enjoy the calm.", subtitle = "No active items.", cta = null }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6" data-testid="empty-state">
      <motion.svg
        viewBox="0 0 120 120"
        className="w-24 h-24 mb-6"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <motion.path
          d="M 25 25 L 60 65 L 95 25"
          stroke="rgb(var(--faint))"
          strokeWidth="2"
          fill="none"
        />
        <motion.line x1="60" y1="65" x2="60" y2="100" stroke="rgb(var(--faint))" strokeWidth="2" />
        <motion.line x1="45" y1="100" x2="75" y2="100" stroke="rgb(var(--faint))" strokeWidth="2" />
        <motion.circle
          cx="60"
          cy="45"
          r="2.5"
          fill="rgb(var(--brass))"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
      </motion.svg>
      <div className="font-display uppercase text-2xl tracking-tight">{title}</div>
      <div className="text-faint text-sm mt-2 font-mono uppercase tracking-widest">
        {subtitle}
      </div>
      {cta && <div className="mt-6">{cta}</div>}
    </div>
  );
}
