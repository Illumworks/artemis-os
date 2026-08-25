"""Sentiment / narrative watch — the parent-sentiment theme layer.

Brief: ``briefs/parent-sentiment-watch.md``, Design §1 ("the theme layer is
the durable asset — build it first"). Angela's ask names three narrative
frames (voice recordings, children training AI, "Amira is a chatbot"); the
brief adds two more from the existing screen-time policy work (privacy /
surveillance, screen-time harm). Encoding them once here means every source
this watch adds later — news now, Reddit and petitions per the brief's
sourcing plan — inherits the same theme vocabulary for free.

This package is ONLY the theme layer: a pure, no-I/O matcher. Sources (§2),
scoring (§3), and delivery (§4) are separate, later briefs — do not grow this
module to fetch, store, or score anything.

Public surface:
  - ``themes`` — ``THEMES``, ``match_themes``, ``is_amira_specific``.
"""

from __future__ import annotations

__all__: list[str] = []
