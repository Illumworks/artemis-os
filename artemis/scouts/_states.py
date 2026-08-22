"""Canonical US state table for every scout and watch pipeline.

ONE definition, because three had drifted. On 2026-08-21 the screen-time
fan-out swept 51 jurisdictions, ``national_news`` knew 51 names, and the
State-DoE source map covered **22** -- so 29 states were swept by one component
and invisible to another, with nothing to make the mismatch visible. Oklahoma
produced more screen-time signals than any other state while being absent from
both the DoE map and the territory config.

Anything that iterates states imports from here. Adding a state is one edit.
See ``artemis/scouts/tests/test_state_coverage.py``, which fails if a
jurisdiction where we support districts is missing from any layer.

Territories are deliberately excluded: ``districts`` holds a handful of rows in
GU, MP, PR and VI, but none has a state DoE, a governor's press feed or a state
board of education in the form these scouts fetch. Excluded by decision, not by
oversight -- and the coverage test knows to skip them rather than fail forever.
"""

from __future__ import annotations

# 50 states + DC. Keys are the two-letter USPS abbreviations used everywhere.
STATE_NAMES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

US_STATES_AND_DC: list[str] = list(STATE_NAMES)

# Jurisdictions that appear in ``districts`` but have no state-level DoE,
# governor feed or state board in the shape these scouts fetch.
NON_STATE_JURISDICTIONS: frozenset[str] = frozenset({"GU", "MP", "PR", "VI"})

# Education agencies whose common name is NOT "<State> Department of Education".
# Search engines index the local name, so a query built from the generic form
# finds materially less. Only the well-known exceptions are listed; everything
# else falls back to the generic form.
AGENCY_NAMES: dict[str, str] = {
    "AZ": "Arizona Department of Education OR ADE",
    "CA": "California Department of Education OR CDE",
    "GA": "Georgia Department of Education OR GaDOE",
    "IL": "Illinois State Board of Education OR ISBE",
    "IN": "Indiana Department of Education OR IDOE",
    "KY": "Kentucky Department of Education OR KDE",
    "MA": "Massachusetts DESE",
    "MO": "Missouri DESE",
    "NC": "North Carolina Department of Public Instruction OR NCDPI",
    "NM": "New Mexico Public Education Department OR NMPED",
    "NY": "New York State Education Department OR NYSED",
    "OK": "Oklahoma State Department of Education OR OSDE",
    "PA": "Pennsylvania Department of Education OR PDE",
    "TX": "Texas Education Agency OR TEA",
    "VA": "Virginia Department of Education OR VDOE",
    "WA": "Washington Office of Superintendent of Public Instruction OR OSPI",
}


def agency_name(state: str) -> str:
    """Common name of *state*'s education agency, for search-query text."""
    abbr = state.upper()
    return AGENCY_NAMES.get(abbr, f"{STATE_NAMES[abbr]} Department of Education")
