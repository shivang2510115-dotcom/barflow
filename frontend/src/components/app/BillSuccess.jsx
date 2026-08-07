import React, { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Check } from "lucide-react";

/**
 * Bill-settled celebration: receipt slides up, coins sparkle, green check.
 * Auto-dismisses after ~2.4s.
 */
export default function BillSuccess({ open, amount = 0, onClose }) {
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => onClose?.(), 2400);
    return () => clearTimeout(t);
  }, [open, onClose]);

  const coins = Array.from({ length: 10 });

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="bill-success"
          className="fixed inset-0 z-[90] bg-stone-950/80 backdrop-blur flex items-center justify-center p-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          data-testid="bill-success-overlay"
          onClick={onClose}
        >
          {/* Coin sparkles */}
          <div className="absolute inset-0 pointer-events-none overflow-hidden">
            {coins.map((_, i) => {
              const x = Math.random() * 100;
              const delay = Math.random() * 0.4;
              return (
                <motion.span
                  key={i}
                  className="absolute w-2 h-2 rounded-full bg-amber-400"
                  style={{
                    left: `${x}%`,
                    top: "50%",
                    boxShadow: "0 0 12px rgba(251,191,36,0.9)",
                  }}
                  initial={{ y: 0, opacity: 0, scale: 0.4 }}
                  animate={{ y: -220 - Math.random() * 120, opacity: [0, 1, 0], scale: [0.4, 1, 0.6] }}
                  transition={{ delay, duration: 1.4, ease: "easeOut" }}
                />
              );
            })}
          </div>

          {/* Receipt */}
          <motion.div
            className="relative border border-orange-500 bg-stone-900 p-8 max-w-sm w-full text-center"
            initial={{ y: 120, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -80, opacity: 0 }}
            transition={{ type: "spring", stiffness: 220, damping: 22 }}
          >
            <motion.div
              className="w-14 h-14 rounded-full border-2 border-green-500 flex items-center justify-center mx-auto mb-4"
              initial={{ scale: 0, rotate: -30 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ delay: 0.2, type: "spring", stiffness: 260, damping: 16 }}
              style={{ boxShadow: "0 0 30px rgba(34,197,94,0.5)" }}
            >
              <Check className="text-green-400" size={30} strokeWidth={3} />
            </motion.div>
            <div className="text-[10px] uppercase tracking-[0.4em] font-mono text-green-400">
              Payment Successful
            </div>
            <div className="font-display uppercase text-5xl tracking-tight mt-3 text-orange-400">
              ${Number(amount || 0).toFixed(2)}
            </div>
            <div className="mt-4 text-stone-400 text-sm">Thank you · cheers to the next round.</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
