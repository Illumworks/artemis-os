# HubSpot Transition and Coordinated Contact Management

**Date:** 2026-08-28
**Present:** Jon Fila, Deborah Molloy, Joshua Mukai, Neil Martin, Risa Ochiai, Angela Miata

Stored verbatim because several decisions here govern work that is still open a
week later, and because two commitments made in this room have not happened.

---

## The decisions that constrain everything downstream

**HubSpot is terminating.** A two-month extension, then no renewal. The reason
given is cost against usage: "we're not using HubSpot to its full capacity,
overpaying for it." Marketing Cloud was floated and effectively ruled out on
price by both Jon and Risa. Brevo is the working candidate.

**Prospects do NOT go into Salesforce.** This is Deborah's ruling and it is
firm, with a structural reason behind it:

> "It's been the real direction from Mark and Ashish that Salesforce is the
> revenue source of record. And so that's why we've gotten rid of the child
> accounts. So these teachers and all those are not gonna be associated with
> district, they're gonna be associated with campuses, and we don't do that
> anymore in Salesforce. Like, everything's at the district level."

So the ~80-88k HubSpot contacts are NOT a Salesforce migration. They need a
separate prospect hub, with a flow from prospect to lead into Salesforce when one
qualifies. **Any plan that assumes a contact merge into Salesforce is planning
against an explicit decision.**

**Read-only, deliberately.** Jon: "right now read only access, the only writing
we're gonna be doing is emails and other types of marketing content... I'm not
gonna request write access right now."

---

## What Josh actually asked for, in his words

This is the workflow the whole build serves, and it is worth quoting rather than
paraphrasing:

> "John's built this amazing tool where there are signals that pop up for
> accounts that would be a good fit for Amira... part of my challenge is
> identifying for these districts all of the appropriate context. So are they a
> customer? If they're an existing customer, it's probably not -- I mean, there's
> potentially expansion opportunities, but we're primarily focused on new
> business. So at the very least, customer status would be hugely helpful. Then
> all the other context on the account would be great as well. Did they have a
> couple licenses at the site level that would also be a legitimate target?
> What's the opportunity history for that account? Maybe we had a bunch of
> conversations with them previously, and we could refer to some of what we
> talked about before. So all of that information is useful for Anne Marie, the
> sellers, and me in figuring out the best approach for prospecting into those
> accounts, reengaging those accounts. So ultimately, the vision is we're able to
> pull that information from Salesforce and then craft the appropriate messaging
> for reengaging them."

Five things, in his order of stated priority:

1. **Customer status** -- "at the very least"
2. **Site-level licences** -- an existing small footprint is still a target
3. **Opportunity history**
4. **Prior conversations** -- "refer to some of what we talked about before"
5. **Messaging built from all four**

Item 4 is the Gong dependency. Nothing else supplies it.

---

## Two commitments made here, neither delivered

**Salesforce permission set -- promised same-day.**

> Neil: "That's easy. That's just updating your permission set." / "What I can do
> for you today is update your permission set... for Salesforce so you can get
> access to those different items."

Jon's list, in his stated priority order: **Task, Lead, EmailMessage, Event,
Campaign, CampaignMember, Case**.

Verified 2026-09-04, seven days later: **none of them are readable.** `describe`
returns 404 for Lead, Task, Event, Campaign, CampaignMember, Case and
EmailMessage. Readable today remains what Jon listed as already working on the
call -- Account, Contact, Opportunity, User, OpportunityContactRole,
AccountContactRelation, ContentDocument.

**Gong -- promised "next week sometime."**

> Neil: "Gong is just give me some time with that because although I'm an
> administrator for that, I need to do a little bit more research on how to get
> you access. I actually got an email from Gong literally yesterday on how to do
> it... I probably won't be able to get to that until, like, next week sometime."

That was 2026-08-28. As of 2026-09-04 there is no Gong credential.

---

## The political shape, which matters

Risa named an overlap that is still unresolved: three routes to roughly the same
capability, owned by different people.

> "We have an interface in Onyx here. We have Claude here. We have John's special
> tool here. So that's why I want to kind of align and say, okay, which is the
> best tool to use?"

Onyx is Mark's, tied to the product data lake, and Risa says there is no
immediate plan to widen access. Claude MCP is Neil's, for "reporting and other
things." Angela's ask out of this was explicit and has not been produced:

> "I think what's helpful is if we articulate a plan... we just need to see a
> master strategy on what we're trying to accomplish, because then that will at
> least allow us to look at shared language and understand where there's overlap
> ... the question is, how are we prioritizing the use cases?"

**That document is an open deliverable, requested by the person who also asked
Jon to define his own role.** It is not a technical artifact.

---

## Jon's stated position on sequencing

> "I don't want to deliver this and it'd be janky and broken. Like, we import new
> features, and then we test the hell out of it and fix it."

And on the goal, which is the sentence to keep:

> "What we're basically building is a highway of information that can hold these
> various different tools together."

---

## Other threads opened here

- **Website consolidation to Webflow.** Jon has the site copied and 301s ready;
  outstanding is the DNS/domain move. Story of America sits in HubSpot, Bernie is
  point of contact.
- **File storage** -- ~35GB, "$5 a month to host somewhere." Missy and Hannah to
  confirm what can be deleted.
- **Forms** -- HubSpot forms need a home; Typeform is the candidate, and form
  data needs to reach Salesforce.
- **A separate Salesforce access request from Laurie/Mel** that Jon knew nothing
  about, to be handled on its own call.
