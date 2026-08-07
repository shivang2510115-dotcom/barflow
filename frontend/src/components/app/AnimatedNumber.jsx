import React, { useEffect, useRef, useState } from "react";

/**
 * Animated number counter: smoothly transitions from old to new value.
 * @param {number} value
 * @param {string} prefix
 * @param {number} decimals
 * @param {number} duration ms
 */
export default function AnimatedNumber({ value = 0, prefix = "", decimals = 0, duration = 900 }) {
  const [display, setDisplay] = useState(0);
  const startRef = useRef(0);
  const fromRef = useRef(0);

  useEffect(() => {
    fromRef.current = display;
    startRef.current = performance.now();
    let raf;
    const tick = (now) => {
      const t = Math.min(1, (now - startRef.current) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const val = fromRef.current + (value - fromRef.current) * eased;
      setDisplay(val);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return (
    <span>
      {prefix}
      {Number(display).toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}
    </span>
  );
}
