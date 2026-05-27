"""Tool factory registry package. Importing registers all tool factories as side-effects."""

from __future__ import annotations

# P3 — Tier 2: remaining catalog tools
import artemis.tools.board_minutes  # noqa: F401 — registers board_minutes.fetch
import artemis.tools.campaign_brief  # noqa: F401 — registers campaign_brief.write
import artemis.tools.contact_db  # noqa: F401 — registers contact_db_stub.has_contact (stub)
import artemis.tools.districts  # noqa: F401 — registers districts.get (stub)
import artemis.tools.federal_register  # noqa: F401 — registers federal_register.search (stub)
import artemis.tools.grants_gov  # noqa: F401 — registers grants_gov.search (stub)
import artemis.tools.legiscan  # noqa: F401 — registers legiscan.* (stubs)
import artemis.tools.linkedin  # noqa: F401 — registers linkedin_scraper.* (stubs)
import artemis.tools.memory_layer  # noqa: F401 — registers memory_layer.* (stubs)
import artemis.tools.news  # noqa: F401 — registers news_api.search
import artemis.tools.pdf_extractor  # noqa: F401 — registers pdf_extractor.extract
import artemis.tools.procurement  # noqa: F401 — registers procurement_portal.fetch (stub)
import artemis.tools.reason_codes  # noqa: F401 — registers reason_codes.*

# CC4 — qualifier + content tools (the full-chain unblock)
import artemis.tools.ruleset_storage  # noqa: F401 — registers ruleset_storage.*
import artemis.tools.signal_briefs  # noqa: F401 — registers signal_briefs.* + campaign_brief.read

# P2 — base signal write tool
import artemis.tools.signal_queue  # noqa: F401 — registers signal_queue.write
import artemis.tools.signal_queue_ops  # noqa: F401 — registers signal_queue.get/update_status/find_*
import artemis.tools.starbridge  # noqa: F401 — registers starbridge.* (stubs)
import artemis.tools.state_doe  # noqa: F401 — registers state_doe.fetch

# P3 — Tier 1: real tools that make scouts work
import artemis.tools.territory_config  # noqa: F401 — registers territory_config.*
import artemis.tools.unresolved_signals  # noqa: F401 — registers unresolved_signals.write (stub)

__all__: list[str] = []
