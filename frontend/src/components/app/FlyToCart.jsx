import React from "react";
import { motion, AnimatePresence } from "framer-motion";

/**
 * Renders a floating clone that animates from `from` -> `to` viewport rects,
 * then unmounts. Used for cart fly-in feedback.
 * Props:
 *   flights: array of { id, from:{x,y,w,h}, to:{x,y}, image, label }
 *   onDone(id): called when a flight finishes.
 */
export default function FlyToCart({ flights, onDone }) {
  return (
    <div className="fixed inset-0 z-[70] pointer-events-none">
      <AnimatePresence>
        {flights.map((f) => (
          <motion.div
            key={f.id}
            initial={{
              x: f.from.x,
              y: f.from.y,
              width: f.from.w,
              height: f.from.h,
              opacity: 1,
              scale: 1,
              borderRadius: 8,
            }}
            animate={{
              x: [f.from.x, (f.from.x + f.to.x) / 2, f.to.x],
              y: [f.from.y, f.from.y - 120, f.to.y],
              width: [f.from.w, f.from.w * 0.6, 24],
              height: [f.from.h, f.from.h * 0.6, 24],
              opacity: [1, 1, 0.4],
              scale: [1, 0.9, 0.4],
              borderRadius: [8, 20, 999],
            }}
            transition={{ duration: 0.75, ease: [0.5, -0.2, 0.7, 1.2], times: [0, 0.5, 1] }}
            onAnimationComplete={() => onDone(f.id)}
            className="absolute origin-top-left overflow-hidden shadow-[0_10px_40px_-10px_rgba(234,88,12,0.7)] ring-1 ring-orange-500/60"
            style={{ top: 0, left: 0 }}
          >
            {f.image ? (
              <img src={f.image} alt="" className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full bg-orange-600" />
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
