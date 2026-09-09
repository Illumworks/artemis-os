"""The seam where a campaign send would actually leave the building.

**Nothing has ever left it.** `campaign_sends` has zero rows in the system's
lifetime, and `mark_send_sent` has always written `status='sent'` with a
`transport_log` reading "NO REAL EMAIL — transport pending ESP". The model's own
comment says as much: *"sent — transport stub has recorded the send (no real
email)"*.

That is the failure this codebase has spent a week removing everywhere else: a
status field claiming work that did not happen. `sent` is what every other
surface reads — the route filter, the state machine, any future report — and none
of them read the log line underneath that admits the truth.

So this module makes the transport an explicit thing with an explicit result, and
a send may only be called `sent` when a transport says it delivered something.

**No provider is wired, deliberately.** HubSpot terminates around October 2026
and its replacement is an open decision (Brevo is a candidate, Marketing Cloud
was ruled out on price). Picking one in code would be making a business decision
in a commit. The seam exists so that when the decision is made, one adapter is
the whole job.

**The default is a dry run**, and it is the useful thing to have today: it
renders exactly who would receive what, so the first real send is reviewed before
it is a send rather than after. Enabling a real transport must be a deliberate
configuration change, never a default that quietly starts emailing districts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundMessage:
    """What would go out. Assembled by the caller; a transport never invents it."""

    recipients: list[str]
    subject: str
    body: str
    deliverable_id: int
    candidate_id: int | None = None


@dataclass(frozen=True)
class TransportResult:
    """What a transport actually did.

    ``delivered`` is the only field permitted to make a send read as ``sent``.
    A transport that returns ``delivered=False`` has not sent anything, however
    successfully it ran, and the caller must not record otherwise.
    """

    transport: str
    delivered: bool
    detail: str
    #: For a dry run: exactly what would have gone out, so a human can read it.
    rendered: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """The `campaign_sends.status` this result justifies."""
        return "sent" if self.delivered else "simulated"


class Transport(Protocol):
    """Anything that can take a message and either deliver it or say it did not."""

    name: str

    async def send(self, message: OutboundMessage) -> TransportResult: ...


class DryRunTransport:
    """Renders the message and delivers nothing. The default, on purpose.

    The point is not that it is safe — it is that it produces the artefact
    nobody has ever seen: the actual recipient list and copy for a real
    candidate. A first send reviewed beforehand is a different risk from a first
    send discovered afterwards.
    """

    name = "dry_run"

    async def send(self, message: OutboundMessage) -> TransportResult:
        logger.info(
            "dry-run send: deliverable=%s recipients=%d subject=%r",
            message.deliverable_id,
            len(message.recipients),
            message.subject[:80],
        )
        return TransportResult(
            transport=self.name,
            delivered=False,
            detail=(
                f"DRY RUN — nothing was sent. {len(message.recipients)} recipient(s) "
                "would have received this."
            ),
            rendered={
                "recipients": message.recipients,
                "subject": message.subject,
                "body": message.body,
                "recipient_count": len(message.recipients),
            },
        )


class NullTransport:
    """Records that no transport is configured. Distinct from a dry run.

    A dry run is a deliberate rehearsal. This is "nobody has chosen an email
    provider", which is a different thing to see in a log six months from now.
    """

    name = "none"

    async def send(self, message: OutboundMessage) -> TransportResult:
        return TransportResult(
            transport=self.name,
            delivered=False,
            detail=(
                "No email provider is configured, so nothing was sent. This is not a "
                "failure — no ESP has been chosen since HubSpot's termination was "
                "announced."
            ),
        )


#: Registered transports, by the name used in configuration. A real provider
#: adapter is added here and nowhere else.
_TRANSPORTS: dict[str, type] = {
    DryRunTransport.name: DryRunTransport,
    NullTransport.name: NullTransport,
}


def resolve_transport(name: str | None = None) -> Transport:
    """The transport to use. Defaults to a dry run.

    An unknown name resolves to `NullTransport` rather than raising or guessing:
    a typo in configuration must never fall through to something that sends, and
    it must not take the pipeline down either.
    """
    from artemis.config import get_settings

    chosen = (name or getattr(get_settings(), "campaign_send_transport", "") or "dry_run").strip()
    impl = _TRANSPORTS.get(chosen)
    if impl is None:
        logger.warning(
            "campaign send: unknown transport %r — falling back to no-send. Known: %s",
            chosen,
            sorted(_TRANSPORTS),
        )
        return NullTransport()
    return impl()  # type: ignore[no-any-return]
