import React from "react";
import { motion } from "framer-motion";

/**
 * Cocktail shaker loading indicator — used for slow async ops.
 * Compact: fits in a card or full-page overlay.
 */
export default function CocktailLoader({ label = "Mixing…", size = "md", overlay = false }) {
  const scale = size === "sm" ? 0.6 : size === "lg" ? 1.2 : 1;

  const shaker = (
    <div className="flex flex-col items-center gap-3">
      <motion.svg
        viewBox="0 0 60 80"
        style={{ width: 60 * scale, height: 80 * scale }}
        animate={{ rotate: [-14, 14, -14, 14, -6, 6, 0] }}
        transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
      >
          {/* Shaker cap */}
          <rect x="18" y="6" width="24" height="8" fill="rgb(var(--ink))" />
          <rect x="20" y="14" width="20" height="4" fill="rgb(var(--faint))" />
          {/* Body */}
          <path
            d="M 16 20 L 22 70 Q 22 76 28 76 L 32 76 Q 38 76 38 70 L 44 20 Z"
            fill="url(#g)"
            stroke="rgb(var(--ink))"
            strokeWidth="1"
          />
          <defs>
            <linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="rgb(var(--brass))" />
              <stop offset="1" stopColor="rgb(var(--brass-deep))" />
            </linearGradient>
          </defs>
          {/* Highlight */}
          <path d="M 20 24 L 24 68" stroke="rgba(255,255,255,0.35)" strokeWidth="1" />
      </motion.svg>
      {label && (
        <div className="text-[10px] font-mono uppercase tracking-[0.3em] text-muted2">
          {label}
        </div>
      )}
    </div>
  );

  if (!overlay) return <div className="flex items-center justify-center py-8">{shaker}</div>;
  return (
    <div className="fixed inset-0 z-[80] bg-ground/80 backdrop-blur flex items-center justify-center">
      {shaker}
    </div>
  );
}
