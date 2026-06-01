"""Pydantic schemas for campaign initiation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

_VALID_MODES = ("all_districts", "states", "district_tier", "named_districts")
_VALID_TIERS = ("D1", "D2", "D3", "D4")
_VALID_STATES = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
)


class TargetScope(BaseModel):
    """Tagged-union campaign targeting payload.

    named_districts is accepted for forward-compatibility even though the
    surrounding product flow still treats it as experimental.
    """

    model_config = ConfigDict(extra="forbid")

    mode: str
    states: list[str] | None = None
    tiers: list[str] | None = None
    district_ids: list[int] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_states_and_tiers(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "states" in normalized and isinstance(normalized["states"], list):
            normalized["states"] = [str(item).upper() for item in normalized["states"]]
        if "tiers" in normalized and isinstance(normalized["tiers"], list):
            normalized["tiers"] = [str(item).upper() for item in normalized["tiers"]]
        return normalized

    @model_validator(mode="after")
    def _validate_mode_payload(self) -> TargetScope:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of: {', '.join(_VALID_MODES)}")

        if self.mode == "all_districts":
            return self

        if self.mode == "states":
            if not self.states:
                raise ValueError("states mode requires a non-empty states list")
            invalid = sorted(state for state in self.states if state not in _VALID_STATES)
            if invalid:
                raise ValueError(
                    f"Unknown state code(s): {', '.join(invalid)}. "
                    f"Valid: {', '.join(sorted(_VALID_STATES))}"
                )
            return self

        if self.mode == "district_tier":
            if not self.tiers:
                raise ValueError("district_tier mode requires a non-empty tiers list")
            invalid = sorted(tier for tier in self.tiers if tier not in _VALID_TIERS)
            if invalid:
                raise ValueError(
                    f"Invalid tier(s): {', '.join(invalid)}. Valid: {', '.join(_VALID_TIERS)}"
                )
            return self

        if not self.district_ids:
            raise ValueError("named_districts mode requires a non-empty district_ids list")
        return self


class CampaignInitiationProposal(BaseModel):
    """LLM-proposed campaign identity + scope, pending operator confirmation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    recommended_deliverable_types: list[str] = Field(min_length=1)
    target_scope: TargetScope
    rationale: str | None = None

    @field_validator("recommended_deliverable_types")
    @classmethod
    def _validate_recommended_deliverable_types(
        cls,
        value: list[str],
        info: ValidationInfo,
    ) -> list[str]:
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if not normalized:
            raise ValueError("recommended_deliverable_types must contain at least one slug")

        active_slugs = sorted(
            {
                str(slug).strip()
                for slug in (info.context or {}).get("active_deliverable_slugs", [])
                if str(slug).strip()
            }
        )
        if active_slugs:
            invalid = sorted(slug for slug in normalized if slug not in active_slugs)
            if invalid:
                raise ValueError(
                    "recommended_deliverable_types must be active deliverable type slugs. "
                    f"Invalid: {', '.join(invalid)}. "
                    f"Active: {', '.join(active_slugs)}"
                )

        return normalized

    @classmethod
    def validate_with_active_slugs(
        cls,
        value: Any,
        active_deliverable_slugs: list[str],
    ) -> CampaignInitiationProposal:
        return cls.model_validate(
            value,
            context={"active_deliverable_slugs": list(active_deliverable_slugs)},
        )
