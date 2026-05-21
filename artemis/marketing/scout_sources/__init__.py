"""Scout source adapter registry — maps slug → adapter instance."""

from __future__ import annotations

from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter
from artemis.marketing.scout_sources.board_minutes import BoardMinutesAdapter
from artemis.marketing.scout_sources.federal_funding import FederalFundingAdapter
from artemis.marketing.scout_sources.leadership_transition import LeadershipTransitionAdapter
from artemis.marketing.scout_sources.legislative import LegislativeAdapter
from artemis.marketing.scout_sources.linkedin import LinkedInAdapter
from artemis.marketing.scout_sources.procurement import ProcurementAdapter
from artemis.marketing.scout_sources.regional_news import RegionalNewsAdapter
from artemis.marketing.scout_sources.starbridge import StarbridgeAdapter
from artemis.marketing.scout_sources.state_doe import StateDOEAdapter

SCOUT_SOURCE_ADAPTERS: dict[str, ScoutSourceAdapter] = {
    "starbridge_researcher": StarbridgeAdapter(),
    "regional_news": RegionalNewsAdapter(),
    "linkedin_observer": LinkedInAdapter(),
    "legislative": LegislativeAdapter(),
    "federal_funding": FederalFundingAdapter(),
    "state_doe": StateDOEAdapter(),
    "procurement": ProcurementAdapter(),
    "board_minutes": BoardMinutesAdapter(),
    "leadership_transition": LeadershipTransitionAdapter(),
}

__all__ = ["RawItem", "ScoutSourceAdapter", "SCOUT_SOURCE_ADAPTERS"]
