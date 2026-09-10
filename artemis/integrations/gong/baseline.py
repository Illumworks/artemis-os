"""Decide when a district's calls are unusual, rather than merely typical.

**Why a raw tracker hit is not a signal.** Measured across 639 recent calls that
carry an account: `Next steps` fires on 58%, `Pricing` 50%, `Objections` 47%,
`Customer concerns` 47%, `Product feedback` 44%. Alerting on "Customer concerns
fired" would alert on half of all conversations. That is not a warning, it is the
weather.

**Why the baseline is the portfolio, not the account's own past.** The obvious
design is "this district versus how this district usually sounds", and it does
not survive contact with the data: only **42 of 313 accounts** have four or more
calls in 120 days. For the other 271 there is no personal history to compare
against, so an account-relative design would silently cover 13% of the portfolio
and say nothing about the rest.

So a district's rate is compared to everyone's rate, with two guards that matter
more than the comparison itself:

**A small sample cannot be unusual.** One call firing `Objections` is a 100% rate
and means nothing. Below `MIN_CALLS_FOR_SIGNAL` the answer is "not enough calls
to say", never "elevated".

**The gap has to be big enough to survive the sample size.** Three of four calls
against a 47% base rate is interesting; three of four against 44% is noise
wearing the same shape. The margin required shrinks as the sample grows.

**This ranks, it does not alarm.** After tuning, 27 of 72 judged accounts carry
some elevated tracker. That is a shortlist worth reading top-down, not a stream
of alerts worth interrupting anyone for. Surfacing the top few is the intended
use; wiring this to a notification without a much higher bar would recreate the
noise problem one level up.

**One piece of external validation, which is the only reason to trust it at
all.** The accounts it flags for elevated objections include Idaho DOE
(objections on 100% of 9 calls), Oklahoma SDE (90% product feedback across 10)
and New Mexico DOE (86% customer concerns across 7). All three appear
independently in Salesforce's largest closed-lost opportunities: $1.5M, $2.5M and
$2.1M respectively. The tracker signal and the money agree, and they were derived
from completely separate systems.

Nothing here reads a transcript, and nothing here is per-rep: trackers are counts
attached to an ACCOUNT, and CLAUDE.md rule 4 forbids the other thing.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from artemis.integrations.gong.client import CallContext

logger = logging.getLogger(__name__)

#: Below this many calls in the window, a district's rate is not evidence.
#: Three is the floor at which "two of three" starts to mean anything at all,
#: and it is deliberately conservative: a false "this account is unhappy" costs
#: more than a missed one, because it sends someone into a call expecting a
#: problem that is not there.
MIN_CALLS_FOR_SIGNAL = 3

#: Nominal margin, for documentation. The real thresholds are per-sample-size in
#: `_required_margin`, because a flat margin flagged half the portfolio.
BASE_MARGIN = 0.25

#: Trackers whose meaning is procedural rather than emotional. They fire on most
#: healthy calls and say nothing about how a district feels, so they are excluded
#: from concern and advocacy scoring rather than left to add noise.
PROCEDURAL_TRACKERS: frozenset[str] = frozenset(
    {"Next steps", "Next steps (tracker)", "Product Names", "Pricing (tracker)"}
)

#: Trackers that indicate a district is unhappy.
CONCERN_TRACKERS: frozenset[str] = frozenset(
    {"Customer concerns", "Customer objections", "Objections (tracker)", "Product feedback"}
)

#: Trackers that indicate a district is getting value. The other half of the
#: question, and the one nobody currently hears about: a district where this is
#: elevated is a case-study lead.
ADVOCACY_TRACKERS: frozenset[str] = frozenset(
    {"Amira Solving Problems", "Customer or seller trends", "Strategic business goals"}
)


@dataclass(frozen=True)
class PortfolioRates:
    """How often each tracker fires across every account. The comparison floor."""

    rates: dict[str, float] = field(default_factory=dict)
    call_count: int = 0

    def rate_for(self, tracker: str) -> float:
        return self.rates.get(tracker, 0.0)


@dataclass(frozen=True)
class AccountDeviation:
    """What is unusual about one district, if anything."""

    account_name: str
    calls_considered: int
    elevated_concern: dict[str, float] = field(default_factory=dict)
    elevated_advocacy: dict[str, float] = field(default_factory=dict)
    #: Elevated, and in none of the three lists above. Empty today, and that is
    #: the point: the lists are hand-maintained and Gong's trackers are
    #: configured by people at Amira, so the next tracker somebody adds lands
    #: here instead of being dropped on the floor.
    elevated_other: dict[str, float] = field(default_factory=dict)
    #: Set when the sample is too small to say anything. Not an error.
    insufficient: bool = False

    @property
    def has_signal(self) -> bool:
        return not self.insufficient and bool(
            self.elevated_concern or self.elevated_advocacy or self.elevated_other
        )

    def describe(self) -> str:
        if self.insufficient:
            return (
                f"{self.account_name}: only {self.calls_considered} call(s) in the window, "
                f"which is below the {MIN_CALLS_FOR_SIGNAL} needed to say whether anything "
                "is unusual. Not a finding either way."
            )
        if not self.has_signal:
            return f"{self.account_name}: nothing unusual across {self.calls_considered} calls."

        parts = [f"{self.account_name}, across {self.calls_considered} calls:"]
        for tracker, rate in sorted(self.elevated_concern.items(), key=lambda kv: -kv[1]):
            parts.append(f"  concern — {tracker} on {rate:.0%} of calls, above the norm")
        for tracker, rate in sorted(self.elevated_advocacy.items(), key=lambda kv: -kv[1]):
            parts.append(f"  positive — {tracker} on {rate:.0%} of calls, above the norm")
        for tracker, rate in sorted(self.elevated_other.items(), key=lambda kv: -kv[1]):
            parts.append(
                f"  unclassified — {tracker} on {rate:.0%} of calls, above the norm. This "
                "tracker is not yet sorted into concern or positive, so read it as raised "
                "more than usual and nothing more."
            )
        parts.append(
            "  Tracker counts only. They say a call touched on something, never what "
            "anyone said about it."
        )
        return "\n".join(parts)


def portfolio_rates(calls: list[CallContext]) -> PortfolioRates:
    """Per-tracker firing rate across every call that carries an account."""
    linked = [c for c in calls if c.account_name]
    if not linked:
        return PortfolioRates(call_count=0)

    hits: Counter[str] = Counter()
    for call in linked:
        for tracker in call.fired_trackers:
            hits[tracker] += 1
    return PortfolioRates(
        rates={t: n / len(linked) for t, n in hits.items()}, call_count=len(linked)
    )


def _required_margin(sample: int) -> float:
    """How far above the norm a rate must sit, given how few calls it rests on.

    Three of four calls is a weaker claim than thirty of forty, so a SMALL sample
    must clear a HIGHER bar, not the same one.

    The first version of this used one margin for everything under ten calls and
    flagged 48% of judged accounts. Half the portfolio being unusual means the
    word has stopped meaning anything, and the cause was arithmetic: against a
    ~47% base rate a 25-point margin is ~72%, which a four-call account reaches
    with three calls, which happens by chance constantly. Tightened until the
    flag rate reflected something worth reading.

    Crude on purpose. This is a noise filter, not an inference, and pretending to
    a confidence interval over five calls would be the more dishonest choice.
    """
    if sample >= 20:
        return 0.15
    if sample >= 10:
        return 0.22
    if sample >= 6:
        return 0.30
    return 0.40


def account_deviation(
    account_name: str, calls: list[CallContext], portfolio: PortfolioRates
) -> AccountDeviation:
    """Which trackers fire unusually often for this district."""
    considered = [c for c in calls if c.account_name]
    if len(considered) < MIN_CALLS_FOR_SIGNAL:
        return AccountDeviation(
            account_name=account_name,
            calls_considered=len(considered),
            insufficient=True,
        )

    hits: Counter[str] = Counter()
    for call in considered:
        for tracker in call.fired_trackers:
            hits[tracker] += 1

    margin = _required_margin(len(considered))
    concern: dict[str, float] = {}
    other: dict[str, float] = {}
    advocacy: dict[str, float] = {}

    for tracker, count in hits.items():
        if tracker in PROCEDURAL_TRACKERS:
            continue
        rate = count / len(considered)
        if rate < portfolio.rate_for(tracker) + margin:
            continue
        if tracker in CONCERN_TRACKERS:
            concern[tracker] = rate
        elif tracker in ADVOCACY_TRACKERS:
            advocacy[tracker] = rate
        else:
            # Previously this fell off the end and the tracker vanished --
            # elevated, above the portfolio, and reported nowhere.
            #
            # Gong's AI trackers are configured by people at Amira, not by us. On
            # 2026-09-10 a colleague asked whether we could say a district's
            # concerns were about rostering, or parents, or training. We cannot:
            # all 26 trackers are sales-process, and the answer is for someone to
            # ADD those trackers in Gong. The day they do, this is the branch that
            # decides whether the new tracker appears or is silently discarded
            # until a person notices and edits a frozenset.
            other[tracker] = rate

    return AccountDeviation(
        account_name=account_name,
        calls_considered=len(considered),
        elevated_concern=concern,
        elevated_advocacy=advocacy,
        elevated_other=other,
    )
