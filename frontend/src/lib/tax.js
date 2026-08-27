/**
 * Outlet GST on the client: the same two branches as backend/services/tax.py.
 *
 * The server is the authority on what a bill costs — every total shown on a saved order
 * comes back from it. This exists for the two moments where there is no order yet and a
 * number is on screen anyway:
 *
 *   * the POS bill foot, while a waiter is typing a discount;
 *   * the QR page's cart, before the guest has sent anything.
 *
 * Both of those printed a hardcoded 10% before this file existed, which is not an Indian
 * GST rate at all — so a guest watched their cart add up to one figure and was handed a
 * bill with another. A second copy of a money rule is a liability, so this is kept as
 * small as it can be: two functions, the same rounding, and no state.
 *
 * Rupees are formatted by `currency()` in lib/api.js and nowhere else. Nothing here
 * returns a string with a symbol in it.
 */

/** Restaurant service without input tax credit — the same default the server stamps. */
export const DEFAULT_OUTLET_GST_RATE = 5.0;

const paise = (n) => Math.round((n + Number.EPSILON) * 100) / 100;

/**
 * The rate and the inclusive flag off whatever payload carries them — a `GET
 * /api/property` body for the POS, a `GET /api/tables/public/{id}` body for the QR page.
 *
 * Never raises and never leaves the caller with `undefined`: a property that has not
 * loaded yet, or one that predates the field, reads as 5% exclusive, which is what the
 * server bills it at. Zero is honoured — `|| 5` would re-register an unregistered
 * business at 5% on screen while the bill said nothing of the sort.
 */
export function gstSettings(source) {
  const raw = source?.outlet_gst_rate;
  const rate = Number(raw);
  const usable = raw !== null && raw !== undefined && raw !== "" &&
    Number.isFinite(rate) && rate >= 0 && rate <= 28;
  return {
    rate: usable ? rate : DEFAULT_OUTLET_GST_RATE,
    inclusive: Boolean(source?.gst_inclusive),
  };
}

/**
 * The foot of a bill, from what its lines add up to.
 *
 * `subtotal` is the menu prices as printed. `taxableValue` is what the tax was worked
 * out on: the same figure when the rate is exclusive, and the price with the tax taken
 * back out when it is inclusive. `taxableValue + tax === total` before any discount, so
 * the two lines on screen add up to the one under them.
 */
export function outletTotals(itemsTotal, { rate, inclusive }, discount = 0) {
  const items = Number(itemsTotal) || 0;
  const off = Number(discount) || 0;
  const r = Number(rate) || 0;

  if (inclusive) {
    const taxableValue = paise(items / (1 + r / 100));
    return {
      subtotal: paise(items),
      taxableValue,
      tax: paise(items - taxableValue),
      total: paise(items - off),
    };
  }
  const tax = paise(items * (r / 100));
  return {
    subtotal: paise(items),
    taxableValue: paise(items),
    tax,
    total: paise(items + tax - off),
  };
}

/** How the tax line is labelled: the rate, and whether it is already in the price. */
export function gstLabel({ rate, inclusive }) {
  // `Number()` drops a trailing ".0" so a 5% rate reads "GST 5%" rather than "GST 5.0%",
  // while 2.5 keeps its half.
  return `GST ${Number(rate)}%${inclusive ? " (in price)" : ""}`;
}
