import { useEffect, useState } from "react";

/**
 * The palette, readable from JavaScript.
 *
 * Tailwind classes resolve tokens for us, but a chart does not take classes — recharts
 * wants a colour string as a prop, and an SVG wants a `fill`. Those were hardcoded hex
 * values, which meant the whole of `index.css` could flip from dark to light and every
 * chart would keep drawing the dark palette: a near-black gridline on porcelain, and a
 * bar in the old orange beside a brass button.
 *
 * So the tokens are read from the document at runtime instead. One definition of the
 * palette, in CSS, and everything follows it — including a viewer whose system theme
 * changes while the page is open, which is what the listener below is for.
 */

/** One token, as an `rgb()` string. `alpha` gives `rgb(r g b / a)`. */
export function token(name, alpha) {
  if (typeof document === "undefined") return "#78716c";
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(`--${name}`)
    .trim();
  // A missing token would otherwise produce `rgb()`, which paints nothing at all and is
  // very hard to see in a chart. Fall back to the neutral rather than to invisible.
  if (!raw) return "#78716c";
  return alpha === undefined ? `rgb(${raw})` : `rgb(${raw} / ${alpha})`;
}

/**
 * The colours a chart needs, re-read when the theme changes.
 *
 * Returns a fresh object on a theme flip so recharts re-renders. The subscription is to
 * `prefers-color-scheme` and to `data-theme` on the root element, which are the only two
 * things that move the palette — see the three blocks at the top of index.css.
 */
export function useChartColours() {
  const read = () => ({
    // The series colour. Brass on light, the brighter orange on dark — both are the
    // brand, expressed for the ground they sit on.
    accent: token("brass"),
    // The recessive second series. Near-neutral on purpose: that is what makes it read
    // as the background series rather than as a second accent.
    neutral: token("faint"),
    // Grid and axis. Hairline is the whole point — on a dark ground a light rule
    // recedes, and on porcelain a dark one would shout over the data it is measuring.
    grid: token("hairline"),
    axis: token("faint"),
    ink: token("ink"),
    surface: token("surface"),
    // Payment methods, kept distinguishable rather than pretty. These four appear
    // together in one pie, so they are spread around the wheel rather than shaded.
    pay: {
      cash: token("brass"),
      card: token("state-inspected"),
      online: token("state-free"),
      unknown: token("faint"),
    },
  });

  const [colours, setColours] = useState(read);

  useEffect(() => {
    const refresh = () => setColours(read());
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", refresh);
    // The viewer's explicit choice is stamped on <html>, so watch the attribute too.
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, {
      attributes: true, attributeFilter: ["data-theme"],
    });
    // One read after mount: the first render happens before the stylesheet is
    // necessarily applied, and a chart drawn with the fallback neutral would stay that
    // way until something else re-rendered it.
    refresh();
    return () => {
      media.removeEventListener("change", refresh);
      observer.disconnect();
    };
  }, []);

  return colours;
}
