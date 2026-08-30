/**
 * Which palette a person sees, and how they change it.
 *
 * The app defaults to **light**, not to the device. That is a deliberate reversal: the
 * first build followed `prefers-color-scheme`, which meant an owner who had asked for a
 * light product opened it on a dark-mode Mac and saw dark. Following the device is a
 * good default for a page somebody visits; it is the wrong one for a tool whose owner
 * has chosen how their business's software should look.
 *
 * Dark stays one click away, because it is genuinely better on a restaurant floor at
 * 9pm — which is the whole reason both palettes exist.
 *
 * The choice is per browser, in localStorage. A receptionist's lobby tablet and a
 * waiter's floor tablet are different browsers and can differ, which is exactly right.
 */
const KEY = "barflow_theme";

/** "light" | "dark" — never "system". A stored value wins; light is the default. */
export function currentTheme() {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Private windows and blocked site data both throw here. Falling through to the
    // default is correct: a person who cannot store a preference still gets a usable
    // app, and gets the one the owner chose.
  }
  return "light";
}

/** Stamp the choice on <html>, which is what index.css keys both palettes off. */
export function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Unstorable is not unusable — the attribute is already set for this page.
  }
}

/** Set the theme before React paints, so nothing flashes the wrong palette. */
export function initTheme() {
  document.documentElement.setAttribute("data-theme", currentTheme());
}
