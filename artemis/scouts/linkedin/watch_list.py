"""Hardcoded V1 watch list for the LinkedIn Observer Scout.

LEGAL NOTE: confirm scraper service ToS compliance before production deploy.
We use only services with established legal precedent for public-data access.
"""

from __future__ import annotations

from typing import Any

# V1 watch list — placeholder LinkedIn profile URLs.
# TODO: populate real LinkedIn URLs from territory_config when districts table lands.
# Watch list should be 1 supe + 1-2 senior leaders per watch-list district.
_DEFAULT_WATCH_PROFILES: list[dict[str, Any]] = [
    {
        "profile_id": "https://www.linkedin.com/in/sample-supe-fl-pinellas",  # TODO: replace with real URL
        "district_id": "FL_pinellas",
        "state": "FL",
        "role": "superintendent",
        "name": "Unknown",  # TODO: seed from territory_config
    },
    {
        "profile_id": "https://www.linkedin.com/in/sample-supe-fl-duval",
        "district_id": "FL_duval",
        "state": "FL",
        "role": "superintendent",
        "name": "Unknown",
    },
    {
        "profile_id": "https://www.linkedin.com/in/sample-supe-tx-dallas",
        "district_id": "TX_dallas",
        "state": "TX",
        "role": "superintendent",
        "name": "Unknown",
    },
    {
        "profile_id": "https://www.linkedin.com/in/sample-supe-in-pike",
        "district_id": "IN_msd_pike",
        "state": "IN",
        "role": "superintendent",
        "name": "Unknown",
    },
    {
        "profile_id": "https://www.linkedin.com/in/sample-supe-md-baltimore",
        "district_id": "MD_baltimore_city",
        "state": "MD",
        "role": "superintendent",
        "name": "Unknown",
    },
]
