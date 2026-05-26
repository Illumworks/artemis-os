"""Tool factory registry package. Importing registers all tool factories as side-effects."""

from __future__ import annotations

import artemis.tools.signal_queue  # noqa: F401 — registers signal_queue.write

__all__: list[str] = []
