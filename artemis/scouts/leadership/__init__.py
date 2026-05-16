"""Leadership Transition Scout — D8 cross-source aggregator.

Monitors school district leadership transitions by combining board minutes,
state DoE feeds, and news articles. Applies two-source verification before
emitting findings.
"""

from __future__ import annotations

from artemis.scouts.leadership.scout import LeadershipTransitionScout

__all__ = ["LeadershipTransitionScout"]
