# WhatsApp Credentials Belong to the Restaurant — Design

**Date:** 2026-08-28
**Status:** Agreed, to be built after the customer-messaging branch merges

---

## Why

`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` and `OWNER_PHONE` are environment variables on the
Cloud Function — one set for the whole platform. Every message from every hotel would
therefore leave from the platform's number.

That was harmless while there was one tenant. It is wrong now: a guest of Anand Castle who
gets a birthday message should see *Anand Castle*, not a number belonging to the software
they have never heard of. A restaurant's relationship with its customer is the restaurant's.

## Decisions taken

| Decision | Choice | Reasoning |
|---|---|---|
| Whose number sends | **The restaurant's own, always** | The owner's decision, stated plainly: messages go from the restaurant number only. |
| A hotel with no WhatsApp of its own | **Cannot send. No platform fallback.** | A fallback would put the platform's name on a restaurant's customer relationship, and would quietly train a hotel to never set up its own. Refusing is the honest answer, and the screens say so. |
| Who enters the credentials | **The platform operator**, when onboarding | Same as the Razorpay design. The operator is the one doing the onboarding call, and the token is a credential the hotel should not have to handle twice. |
| Where the token lives | **Encrypted at rest, never returned** | The same mechanism that protects guest identity documents. The phone number id is not secret and may be returned; the token is write-only. |

## What Meta requires, which no code can supply

Per restaurant, and worth stating because the second item is where people get stuck:

1. A WhatsApp Business Account.
2. **A dedicated phone number.** It cannot already be on ordinary WhatsApp — if the
   restaurant uses that number today, that account must be deleted before the number can
   be registered to the Business API. Most properties buy a second SIM.
3. A **display name** approved by Meta, which must reflect the real business.
4. Business verification, with company documents, in Meta Business Manager.
5. **Approved message templates.** Free text only sends inside a 24-hour window from the
   customer's own last message; a follow-up ten days later and a birthday greeting are
   both outside it.

## The change

**On the property:** `whatsapp_phone_id`, `whatsapp_token` (encrypted, write-only),
`whatsapp_display_name` for showing in the console, and the template names already
introduced by the messaging work.

**Resolution moves from the environment to the property.** Every sender — the nightly
brief, the occasion greeting, the follow-up job — resolves credentials from the property
whose message it is. The environment variables stop being read for sending.

The nightly brief is the case to think about: it runs for every live property from a
scheduled function, so it must resolve per property inside the loop, and a property with
no credentials is skipped with a recorded reason rather than aborting the run for the
others.

**`OWNER_PHONE` becomes a property field too.** The brief goes to that hotel's owner, not
to one number shared across the platform — which is what it does today, and is why the
property name had to be prefixed to the message to tell them apart.

## What the screens say

The operator's console gains the credentials, alongside the subscription and the Razorpay
fields when those land. `GET /api/whatsapp/status` already names what is missing; it moves
from reading the environment to reading the caller's property, and its wording gains the
case that matters now — *this hotel has no WhatsApp number of its own yet*.

A staff member pressing **Send wishes** at a property with no credentials gets that same
sentence, not a generic failure.

## Testing

- A property with credentials sends with its own; a second property with different
  credentials sends with its own, and neither can read the other's token through any
  endpoint.
- A property with none refuses, with a message naming what is missing.
- The nightly brief skips an unconfigured property and still sends for a configured one in
  the same run.
- The stored token never appears in any response body, including the operator's own.

## Out of scope

Registering numbers with Meta on a hotel's behalf; template submission through the API;
a shared platform number for hotels that have not onboarded, which was considered and
rejected above.
