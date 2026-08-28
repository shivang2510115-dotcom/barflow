import { currency } from "./api";

/**
 * A dish's portions, as an array, whatever the record looks like.
 *
 * The mirror of `variants_of` in backend/routers/menu.py, and here for the same reason:
 * "no portions" has to be one answer rather than three. Every menu item written before
 * this feature has no `variants` key at all, a hand-edited one may have null, and an
 * admin who removed the last portion leaves an empty list. All three are no portions,
 * and no screen should have to remember that.
 */
export function variantsOf(item) {
  return Array.isArray(item?.variants) ? item.variants : [];
}

/**
 * What the card shows for a dish's price.
 *
 * A plain dish shows its price. A dish sold by portion shows the range it sells across —
 * ₹379 – ₹689 — because a single figure beside a dish that is charged at two is the
 * "₹379 on one screen, ₹689 on another" complaint, and the guest reading the card has
 * not chosen a portion yet.
 *
 * Always through `currency()`: rupees, Indian digit grouping, never a symbol typed here.
 */
export function priceLabel(item) {
  const variants = variantsOf(item);
  if (!variants.length) return currency(item?.price);
  const prices = variants.map((v) => Number(v?.price) || 0);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  return low === high ? currency(low) : `${currency(low)} – ${currency(high)}`;
}
