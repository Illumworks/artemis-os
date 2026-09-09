# The ESP decision — what actually has to be chosen

**Written 2026-09-09.** Grounded in the 2026-08-28 transition meeting
(`meetings/2026-08-28-hubspot-transition.md`) and in what the send pipeline
actually requires, read from the code rather than assumed.

## The deadline

HubSpot got a two-month extension from 2026-08-28, so it ends around **31
October 2026** — about seven weeks from writing. Marketing Cloud was floated and
"effectively ruled out on price by both Jon and Risa". **Brevo is the working
candidate**, not a decision.

## The thing worth knowing first: nothing we run depends on HubSpot

Artemis has no HubSpot integration — no client, no key, no hostname
(`FUNCTIONALITY-MAP.md`, integrations table). So HubSpot's termination breaks
nothing in this system. It does not threaten the brief, the scouts, the signal
queue, Callie, or anything else that runs today.

What it gates is one thing: the last inch of the send pipeline, which has never
run. `campaign_sends` has zero rows in the system's lifetime.

That matters for how the decision should be made — this is not a migration under
time pressure, it is a purchase that unblocks a feature.

## There are two decisions, and they are frequently confused

**1. Where the ~80–88k prospect contacts live.**
Deborah's ruling is firm and structural: prospects do NOT go into Salesforce.
Salesforce is the district-level revenue source of record, child accounts were
deliberately removed, and teacher- and campus-level contacts have nowhere to sit
there. So those contacts need a prospect hub with a prospect→lead flow into
Salesforce on qualification.

**2. What sends the email.**

These can be the same product or two. Choosing them together is the expensive
default; a provider good at holding 88k contacts is not necessarily the one you
want sending, and vice versa. Worth deciding deliberately rather than by
inheriting HubSpot's shape.

## What this system needs from a sending provider

Read from `artemis/marketing/sends.py` and `artemis/marketing/transport.py`. The
list is short, and that is the point.

| Need | Why | Hard requirement? |
|---|---|---|
| Send to an explicit recipient list with a subject and body | The pipeline resolves recipients itself, from our contacts and the Salesforce suppression check | **Yes** |
| A per-recipient result we can record | `campaign_sends.transport_log` exists to hold it; without it "sent" is a guess again | **Yes** |
| Honour and expose unsubscribes | Legal, and ours must not diverge from theirs | **Yes** |
| Bounce reporting | An 88k list that has sat in HubSpot will bounce; without this we degrade our own domain silently | **Strongly** |
| Open/click webhooks | Useful for the engagement-weighting Callie already does; not required to send | No |
| Campaign builder, workflows, CRM, landing pages | **We have these.** The pipeline builds the brief, the copy, the approval and the audience | **No — and paying for them is how HubSpot became "overpaying"** |

**The adapter surface is one function.** `Transport.send(OutboundMessage) ->
TransportResult`. Whichever provider is chosen, the integration is one file, and
everything expensive around it — recipient resolution, Salesforce suppression
that fails closed, the approval gate, row-locked idempotency, the outbox — is
already built and tested.

So the provider choice is **not** architecturally constrained by us. Choose on
price, deliverability and contact-hosting; do not choose on API convenience,
because the API surface we need is trivial and every candidate has it.

## The decision nobody has raised yet: sending reputation

Moving 88k contacts to a new sender means new domain and IP reputation. A list
that has been sitting in HubSpot unmailed is the worst kind to start on — high
bounce and complaint rates on the first send are exactly what gets a new sender
throttled or blocked.

This is an operational commitment with a calendar, not a config value: a warm-up
schedule, list hygiene before the first send, and a decision about which
subdomain sends. It should be part of the provider conversation rather than
discovered after it.

I am not the right source on the specifics — flag it to whoever owns
deliverability. The point here is only that it is a decision, it has a lead time,
and the seven-week clock is on it too.

## What I would ask before choosing

1. **Is the prospect hub the same product as the sender?** Answering this first
   changes the shortlist.
2. **What is the real contact count after hygiene?** Pricing is per-contact and
   88k unmailed HubSpot records is not 88k mailable people.
3. **Who owns deliverability and warm-up?** If the answer is nobody, that is the
   first hire-or-assign, not the last.
4. **What is the actual monthly figure we are trying to beat?** "Overpaying for
   HubSpot" is the stated reason for the whole move and the number is not written
   down anywhere I can find.

## What happens on our side once it is chosen

One adapter in `artemis/marketing/transport.py`, registered in `_TRANSPORTS`, and
named in the `ARTEMIS_CAMPAIGN_SEND_TRANSPORT` setting. Until then the default is
a dry run that renders exactly who would receive what and sends nothing, so the
first real send can be reviewed before it is a send.

**Do not skip that step.** Nothing has ever been sent from this system, so the
first live send is also the first end-to-end exercise of recipient resolution,
suppression and copy assembly at once.
