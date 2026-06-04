"""Pydantic result models for the Phase 1 marketing intelligence trend substrate.

All models are JSON-serializable, Pydantic v2 strict, snake_case fields.
Downstream workers (Decision-1 enrichment, Decision-2 prioritization) import
from this module to deserialize query results.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class BucketCount(BaseModel):
    """Single time-bucket entry in a momentum time-series."""

    model_config = ConfigDict(frozen=True)

    bucket_start: datetime
    bucket_end: datetime
    count: int


class MomentumResult(BaseModel):
    """Result of compute_momentum: time-series of signal counts + period delta.

    delta_ratio is current_window_count / prior_window_count.
    Returns None when prior_window_count == 0 (no meaningful ratio).
    """

    model_config = ConfigDict(frozen=True)

    theme: str
    region: str | None
    as_of: datetime
    window_days: int
    bucket_days: int
    buckets: list[BucketCount]
    current_window_count: int
    prior_window_count: int
    delta_ratio: float | None  # None when prior_window_count == 0


class DistrictStub(BaseModel):
    """Lightweight district summary used in comparables + velocity results."""

    model_config = ConfigDict(frozen=True)

    district_id: int
    name: str
    state: str | None
    tier: str | None


class ComparablesResult(BaseModel):
    """Result of count_comparable_districts."""

    model_config = ConfigDict(frozen=True)

    theme: str
    region: str | None
    as_of: datetime
    window_days: int
    comparable_count: int
    sample_districts: list[DistrictStub]  # up to N=10


class UrgencyBreakdown(BaseModel):
    """Per-urgency-tier signal count in a velocity ranking row."""

    model_config = ConfigDict(frozen=True)

    standard: int
    elevated: int
    high: int
    critical: int


class DistrictVelocityRow(BaseModel):
    """Single row in compute_velocity_ranking output."""

    model_config = ConfigDict(frozen=True)

    rank: int
    district: DistrictStub
    raw_signal_count: int
    weighted_score: float
    urgency_mix: UrgencyBreakdown


class TimeSensitiveSignalRow(BaseModel):
    """Single row in compute_time_sensitivity output.

    deadline_source documents which field was used as the deadline proxy.
    When no structured deadline field exists in the schema (see module
    docstring in trends.py), this will be 'created_at_plus_urgency'.
    """

    model_config = ConfigDict(frozen=True)

    signal_id: int
    headline: str
    campaign_family: str
    urgency_tier: str
    district: DistrictStub | None
    deadline_proxy: datetime
    deadline_source: str
    provenance_snippet: dict[str, Any] | None  # raw provenance JSONB excerpt


class TrendSnapshot(BaseModel):
    """Serialized trend snapshot persisted to memory_observations.

    content_summary is a short human-readable line used as the observation
    content prefix (enables FTS and semantic retrieval). The full numeric
    payload is appended as JSON so both retrieval paths work.
    """

    model_config = ConfigDict(frozen=True)

    as_of: datetime
    theme: str | None
    region: str | None
    snapshot_kind: str  # 'momentum' | 'comparables' | 'velocity' | 'time_sensitivity'
    content_summary: str
    payload: dict[str, Any]
