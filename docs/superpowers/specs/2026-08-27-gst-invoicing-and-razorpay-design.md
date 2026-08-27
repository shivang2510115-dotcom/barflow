# GST, Platform Invoicing and Online Payment — Design

**Date:** 2026-08-27
**Status:** Approved, ready for implementation
**Context:** BarFlow is live on Firebase. Hotels register, the platform operator approves
them, and pricing is agreed manually and recorded per hotel.

---

## Three pieces

1. **Outlet GST becomes the hotel's own setting** — and the 10% currently hardcoded is
   wrong for India and has to go.
2. **The operator issues a GST invoice** for each subscription payment.
3. **A guest can pay online**, but only for a hotel the operator has enabled.

---

## Piece 1 — Outlet GST

### The bug being fixed

`routers/orders.py::compute_totals` reads:

```python
tax = round(subtotal * 0.10, 2)
```

Ten percent is not an Indian GST rate. Restaurant service is **5%** without input tax
credit, or 18% in specified cases; packaged goods vary. Every bill this POS has printed
carries a tax figure that matches nothing a guest could be charged.

Room nights are unaffected: `services/pricing.py` already computes the correct hotel slab
(12% at or under ₹7,500 a night, 18% above), and that stays exactly as it is.

### The change

The rate moves onto the property, set by the hotel's own admin:

| Field | Notes |
|---|---|
| `outlet_gst_rate` | percent, default **5.0** |
| `gst_inclusive` | whether menu prices already contain the tax; default **false** |

`gst_inclusive` matters more than it looks. Most Indian restaurants print
tax-inclusive menu prices, and computing tax *on top* of an inclusive price overcharges
the guest by the tax on the tax. When inclusive, the tax is extracted from the price
(`price − price ÷ (1 + rate)`) and the total equals the menu price.

**A settings screen for the hotel admin**, not a config file: the person who knows their
GST registration is the owner, and they cannot edit a deployment.

**The arithmetic is a pure function** in `backend/services/` beside `pricing.py`, tested
without a server. Both branches, rounding at the paise, and a zero rate for an
unregistered business.

### Migration

Existing properties get the default 5% and `gst_inclusive: false`. Historic orders keep
the totals they were settled at — **never recompute a settled bill**. The guest paid what
the printed bill said, and changing it retrospectively would put the books out.

---

## Piece 2 — The operator's GST invoice

The operator already records what a hotel agreed to pay and when money arrived. What is
missing is the tax document the hotel needs for its own accounts.

### Why the rate differs per hotel

This is India's place-of-supply rule, not a preference:

- **Hotel in the same state as the platform** → CGST 9% + SGST 9%
- **Hotel in a different state** → IGST 18%

Same 18% either way; what differs is how it is split, and a wrong split makes the invoice
useless to the hotel's accountant. It follows from the hotel's `state` against the
platform's own — so the platform's GSTIN, legal name, address and state become settings,
and an override exists for the cases the rule does not cover.

### What it produces

An invoice per recorded payment: a sequential number, both parties' GSTIN and address,
the period covered, the taxable value, the tax split, and the total in words — which
Indian invoices conventionally carry.

**Numbering is sequential per financial year** (`BF/2026-27/0001`), never reused and never
reordered, because a gap in an invoice series is a question from an auditor.

**An invoice is immutable once issued.** A correction is a credit note referencing the
original — the same append-only rule the folio ledger already follows, for the same
reason: this is a tax document and rewriting one is not a thing you can do.

Printable, matching the existing print styling.

---

## Piece 3 — Online payment

### Who can accept it

A guest ordering from the QR menu chooses **Pay at counter** or **Pay online**. Pay online
appears *only* when the platform operator has enabled it for that hotel and stored its
Razorpay credentials.

Razorpay rather than Stripe: it takes UPI, which is how India actually pays.

The existing Stripe path stays. It is unused in this deployment (`STRIPE_WEBHOOK_SECRET`
is unset, so every webhook is refused) and removing it is a separate cleanup.

### Credentials

`razorpay_key_id` and `razorpay_key_secret` per property, **set by the operator**, because
the operator is the one onboarding the business.

**The secret is encrypted at rest**, reusing the mechanism that already protects guest
identity documents, and is **never returned by any endpoint** — not to the hotel, not to
the operator. The key id is public by design and may be returned; the secret is
write-only.

### The flow

1. Guest picks Pay online → the API creates a Razorpay order and returns the key id and
   order id. The **amount comes from the server's own total**, never from the client.
2. Guest pays in the Razorpay widget.
3. Razorpay calls the webhook. **The signature is verified** — the same rule the Stripe
   webhook was hardened to: an unverifiable webhook that accepts anything lets a stranger
   settle any bill for free. No signature secret configured → refuse every request.
4. The order settles with `payment_method: "online"`, and the bill shows it as paid,
   with the Razorpay payment id recorded against it so it can be reconciled.

**Revenue recognition is unchanged**: an online-paid order is outlet revenue when it
settles, exactly as cash is. The GST in it is the tax already computed on the bill.

### Refusals

| Case | Behaviour |
|---|---|
| Hotel has no Razorpay configured | Pay online is not offered, and the endpoint refuses with a message naming why |
| Hotel is pending or suspended | Refused, as every other operating action already is |
| Webhook with no signature secret set | 503, every request |
| Webhook with a bad signature | 400, and nothing settles |
| Webhook for an already-settled order | 200, no second settlement — Razorpay retries, and a retry must not double-settle |
| Amount tampered client-side | Impossible: the server sets it |

---

## Money and currency

Rupees everywhere, formatted through the existing `currency()` (Indian digit grouping)
and `_money()` on the backend. No new formatter, no hardcoded symbol.

---

## Testing

**Pure functions, no database:** the two GST branches, the place-of-supply split, invoice
numbering across a financial-year boundary, and amount-in-words.

**Integration:** a hotel admin can set its GST rate and a waiter cannot; an inclusive
menu price yields a total equal to the printed price; an existing settled order is
unchanged by a rate change; the operator issues an invoice for a payment and cannot edit
it; a hotel with no Razorpay is not offered online payment; a webhook without a signature
is refused; a replayed webhook settles nothing twice.

---

## Out of scope

Filing GST returns; e-invoicing (IRN/QR) with the government portal, which needs
registration on the IRP; refunds through Razorpay; payment links or subscriptions inside
Razorpay; and removing the Stripe path.
