"""Tool registry — pairs `Tool` definitions with their async implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from artemis.agent.types import Tool, ToolImpl

# jsonschema is a transitive dependency (via mcp>=1.7.0). Import lazily so
# the module stays importable without it. The import-untyped ignore is
# required because types-jsonschema stubs are not in the project's dev deps.
try:
    import jsonschema  # type: ignore[import-untyped]
    from jsonschema import ValidationError as _JSValidationError

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False
    _JSValidationError = Exception

_MAX_ENUM_DISPLAY = 10  # truncate long enum lists to this many + "...and N more"
_MAX_MSG_CHARS = 300  # self-teaching messages must stay under this limit


def _truncate(msg: str) -> str:
    """Ensure message stays under _MAX_MSG_CHARS."""
    if len(msg) <= _MAX_MSG_CHARS:
        return msg
    return msg[: _MAX_MSG_CHARS - 3] + "..."


def _enum_list(values: list[Any], *, max_items: int = _MAX_ENUM_DISPLAY) -> str:
    """Render enum values as a comma-separated string, truncating long lists."""
    strs = [str(v) for v in values]
    if len(strs) <= max_items:
        return ", ".join(strs)
    shown = ", ".join(strs[:max_items])
    remaining = len(strs) - max_items
    return f"{shown} ...and {remaining} more (see tool schema)"


def _build_self_teaching_message(
    tool_name: str,
    schema: dict[str, Any],
    raw_input: dict[str, Any],
    exc: Any,  # jsonschema.ValidationError
) -> str:
    """Convert a JSONSchema ValidationError into a self-teaching message.

    Returns a concise (<= 300 chars) message that names the failed field,
    explains why the value was rejected, and enumerates valid alternatives
    where the schema declares them (enum / type / required / constraints /
    additionalProperties).
    """
    # Field path — join absolute_path elements; fall back to "<root>"
    path_parts = list(exc.absolute_path)
    field_name = ".".join(str(p) for p in path_parts) if path_parts else "<root>"

    # The failing subschema is exc.schema (the schema node closest to the error).
    sub = exc.schema if isinstance(exc.schema, dict) else {}
    validator = exc.validator  # e.g. "enum", "type", "required", "minimum" …

    # 1. enum violation
    if validator == "enum":
        actual = raw_input.get(field_name, exc.instance)
        enum_values: list[Any] = sub.get("enum", exc.validator_value or [])
        msg = (
            f"Invalid value for parameter {field_name!r}: {actual!r}. "
            f"Valid values are: {_enum_list(enum_values)}."
        )
        return _truncate(msg)

    # 2. type mismatch
    if validator == "type":
        expected_type = sub.get("type", exc.validator_value)
        actual_type = type(exc.instance).__name__
        msg = (
            f"Invalid type for parameter {field_name!r}: "
            f"expected {expected_type}, got {actual_type} ({exc.instance!r})."
        )
        return _truncate(msg)

    # 3. missing required field (validator == "required")
    if validator == "required":
        # exc.validator_value is the list of required fields; exc.message names the missing one.
        # Extract the specific missing field from the message (standard format: "'X' is a required property").
        missing = str(exc.message).split("'")[1] if "'" in str(exc.message) else field_name
        # Look up type hint from the property sub-schema if available.
        props = sub.get("properties", {})
        prop_schema = props.get(missing, {})
        expected = prop_schema.get("type", "unknown")
        description = prop_schema.get("description", "")
        desc_part = f" {description}" if description else ""
        msg = f"Missing required parameter: {missing!r}. Expected type: {expected}.{desc_part}"
        return _truncate(msg)

    # 4. constraint violations (minimum, maximum, minLength, maxLength, pattern)
    constraint_validators = {
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "pattern",
    }
    if validator in constraint_validators:
        limit = exc.validator_value
        msg = (
            f"Value for parameter {field_name!r} violates constraint: "
            f"{validator}={limit}. Got: {exc.instance!r}."
        )
        return _truncate(msg)

    # 5. additionalProperties violation (extra unknown keys)
    if validator == "additionalProperties":
        allowed = sorted(sub.get("properties", {}).keys())
        # exc.message names the offending property
        offending = str(exc.message).split("'")[1] if "'" in str(exc.message) else field_name
        msg = f"Unexpected parameter: {offending!r}. Allowed parameters are: {_enum_list(allowed)}."
        return _truncate(msg)

    # Fallback: use the raw jsonschema message, truncated.
    return _truncate(
        f"Validation failed for tool {tool_name!r}, field {field_name!r}: {exc.message}"
    )


@dataclass(slots=True)
class ToolEntry:
    tool: Tool
    impl: ToolImpl


@dataclass(slots=True)
class ToolRegistry:
    _entries: dict[str, ToolEntry] = field(default_factory=dict)

    def register(self, tool: Tool, impl: ToolImpl) -> None:
        if tool.name in self._entries:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._entries[tool.name] = ToolEntry(tool=tool, impl=impl)

    def get(self, name: str) -> ToolEntry | None:
        return self._entries.get(name)

    def specs(self) -> list[Tool]:
        """Return tool definitions, model-facing. Order is registration order."""
        return [e.tool for e in self._entries.values()]

    def validate_input(
        self,
        tool_name: str,
        raw_input: dict[str, Any],
    ) -> str | None:
        """Validate *raw_input* against the tool's declared input_schema.

        Returns None when the input is valid.
        Returns a self-teaching error string when validation fails — the
        caller should surface this verbatim to the LLM via ToolResultBlock
        so the agent can self-correct on its next turn.

        If jsonschema is not installed (should not happen in production — it is
        a transitive dep via mcp), falls back to None (no validation).
        """
        if not _HAS_JSONSCHEMA:
            return None  # pragma: no cover

        entry = self._entries.get(tool_name)
        if entry is None:
            return None  # unknown tool — let the caller handle the 404

        schema = entry.tool.input_schema
        try:
            jsonschema.validate(instance=raw_input, schema=schema)
            return None  # valid
        except _JSValidationError as exc:
            return _build_self_teaching_message(tool_name, schema, raw_input, exc)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._entries
