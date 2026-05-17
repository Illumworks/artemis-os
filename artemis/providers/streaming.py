"""Shared streaming event types for provider adapters.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union


@dataclass(slots=True, frozen=True)
class StreamTextDelta:
    text: str = ""
    type: Literal["text_delta"] = "text_delta"


@dataclass(slots=True, frozen=True)
class StreamToolUseStart:
    id: str
    name: str
    type: Literal["tool_use_start"] = "tool_use_start"


@dataclass(slots=True, frozen=True)
class StreamToolUseDelta:
    id: str
    partial_json: str
    type: Literal["tool_use_delta"] = "tool_use_delta"


@dataclass(slots=True, frozen=True)
class StreamMessageStop:
    stop_reason: str
    type: Literal["message_stop"] = "message_stop"


@dataclass(slots=True, frozen=True)
class StreamUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    type: Literal["usage"] = "usage"


StreamEvent = Union[
    StreamTextDelta,
    StreamToolUseStart,
    StreamToolUseDelta,
    StreamMessageStop,
    StreamUsage,
]
