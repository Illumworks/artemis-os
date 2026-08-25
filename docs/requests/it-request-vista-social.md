# IT Request — Vista Social access for amiracentral@amiralearning.com

**To:** Scott
**From:** Jon Fila
**Re:** Vista Social — admin access or API key for the Amira Central service account

---

Hi Scott,

I need access to our Vista Social account for the `amiracentral@amiralearning.com`
service account. Either of these works — whichever is easier on your side:

1. **Admin (or Manager) seat** on Vista Social for `amiracentral@amiralearning.com`, or
2. **An API key / API access** issued to that account.

**What it's for.** We're standing up a brand-monitoring feed that watches public
conversation about Amira and about AI in schools generally — the New Mexico
situation, and the same pattern starting in Florida and Georgia. Right now our
team is finding these stories by hand and forwarding them one at a time. Vista
Social already aggregates the social side, so reading from it is far cheaper than
rebuilding that collection ourselves.

**Scope — read-only is fine.** We only need to *pull* published posts, mentions and
comments. We are not asking to publish, schedule, reply, or change any account
settings from this integration. If Vista Social supports a read-only or
analyst-level role, that is the right level of access.

**Why a service account rather than my own login.** The monitoring runs on a
schedule, unattended. Tying it to a personal login means it breaks the moment
that person is out or changes their password, and it makes the activity look like
it came from a human. `amiracentral@` is our existing service identity for this
system.

**Handling.** The credential goes straight into our encrypted integrations store —
it is never pasted into email, chat, or a config file in plain text. If you'd
rather install it yourself, I can send you the link to the credential form and you
can enter it directly without sharing it with me.

**Timing.** This supports the ongoing crisis-comms work with Angela, so sooner is
genuinely better — but I don't need it same-day.

Happy to jump on a call if it's easier to sort out live.

Thanks,
Jon
