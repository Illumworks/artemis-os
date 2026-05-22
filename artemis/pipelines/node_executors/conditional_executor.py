"""Conditional node executor.

Handles: conditional nodes that evaluate a predicate and route to a branch.

Config shape:
  {
    "predicate": {
      "field":    "some.nested.key",   # dot-path into context
      "operator": "equals",            # see OPERATORS
      "value":    "expected_value"
    },
    "jsonlogic":  {...}                # Optional: full JSONLogic expression (power-user)
  }

Supported operators: equals, not_equals, greater_than, less_than, contains, in_list

Returns:
  {
    "status":   "succeeded",
    "branch":   "true_branch" | "false_branch",
    "output_summary": "Condition: <field> <op> <value> → <branch>"
  }
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

OPERATORS = frozenset(["equals", "not_equals", "greater_than", "less_than", "contains", "in_list"])


def _get_field(context: dict[str, Any], field_path: str) -> Any:
    """Traverse dot-notation path into context dict."""
    parts = field_path.split(".")
    current: Any = context
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _evaluate_predicate(predicate: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate a single predicate dict against *context*."""
    field: str = predicate.get("field", "")
    operator: str = predicate.get("operator", "equals")
    expected: Any = predicate.get("value")

    actual = _get_field(context, field)

    if operator == "equals":
        return bool(actual == expected)
    elif operator == "not_equals":
        return bool(actual != expected)
    elif operator == "greater_than":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    elif operator == "less_than":
        try:
            return float(actual) < float(expected)
        except (TypeError, ValueError):
            return False
    elif operator == "contains":
        try:
            return str(expected) in str(actual)
        except (TypeError, ValueError):
            return False
    elif operator == "in_list":
        try:
            return actual in list(expected)
        except TypeError:
            return False
    else:
        logger.warning("Unknown conditional operator %r; defaulting to false_branch", operator)
        return False


def _resolve_jsonlogic_value(expr: Any, context: dict[str, Any]) -> Any:
    """Resolve a JSONLogic expression to its raw value (not coerced to bool).

    This is called when we need the actual value (e.g., for numeric comparisons).
    """
    if not isinstance(expr, dict):
        return expr
    for op, args in expr.items():
        if op == "var":
            field = args if isinstance(args, str) else (args[0] if args else "")
            return _get_field(context, field)
    # Fallback: evaluate as boolean expression
    return _evaluate_jsonlogic(expr, context)


def _evaluate_jsonlogic(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    """Very basic JSONLogic subset: ==, !=, >, <, and, or, var.

    Full JSONLogic lib is not a dependency; this covers the V1 cases.
    """
    if not isinstance(rule, dict):
        return bool(rule)
    for op, args in rule.items():
        if op == "var":
            field = args if isinstance(args, str) else (args[0] if args else "")
            return bool(_get_field(context, field))
        elif op == "==":
            a, b = args[0], args[1]
            va = _resolve_jsonlogic_value(a, context)
            vb = _resolve_jsonlogic_value(b, context)
            return bool(va == vb)
        elif op == "!=":
            a, b = args[0], args[1]
            va = _resolve_jsonlogic_value(a, context)
            vb = _resolve_jsonlogic_value(b, context)
            return bool(va != vb)
        elif op == ">":
            a, b = args[0], args[1]
            va = _resolve_jsonlogic_value(a, context)
            vb = _resolve_jsonlogic_value(b, context)
            try:
                return float(va) > float(vb)
            except (TypeError, ValueError):
                return False
        elif op == "<":
            a, b = args[0], args[1]
            va = _resolve_jsonlogic_value(a, context)
            vb = _resolve_jsonlogic_value(b, context)
            try:
                return float(va) < float(vb)
            except (TypeError, ValueError):
                return False
        elif op == "and":
            return all(_evaluate_jsonlogic(sub, context) for sub in args)
        elif op == "or":
            return any(_evaluate_jsonlogic(sub, context) for sub in args)
        elif op == "!":
            return not _evaluate_jsonlogic(args, context)
    return False


async def execute_conditional_node(
    node: dict[str, Any],
    node_states: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate conditional node and return which branch to follow.

    Args:
        node:        Node dict from pipeline.nodes.
        node_states: Current node_states (prior node outputs available).
        context:     Merged execution context (signal data + prior outputs + env).

    Returns:
        NodeState-compatible dict with status, branch, output_summary.
    """
    config: dict[str, Any] = node.get("config") or {}
    ctx = context or {}

    result: bool = False

    jsonlogic = config.get("jsonlogic")
    predicate = config.get("predicate")

    if jsonlogic and isinstance(jsonlogic, dict):
        try:
            result = _evaluate_jsonlogic(jsonlogic, ctx)
        except Exception:
            logger.exception(
                "JSONLogic evaluation failed for node %r; defaulting to false_branch",
                node.get("id"),
            )
            result = False
    elif predicate and isinstance(predicate, dict):
        result = _evaluate_predicate(predicate, ctx)
    else:
        logger.warning(
            "Conditional node %r has no predicate or jsonlogic; defaulting to false_branch",
            node.get("id"),
        )
        result = False

    branch = "true_branch" if result else "false_branch"
    field = predicate.get("field", "") if predicate else "jsonlogic"
    op = predicate.get("operator", "") if predicate else ""
    expected = predicate.get("value", "") if predicate else ""
    summary = (
        f"Condition: {field} {op} {expected!r} → {branch}" if predicate else f"JSONLogic → {branch}"
    )

    return {
        "status": "succeeded",
        "branch": branch,
        "output_summary": summary,
        "cost_usd": 0.0,
    }
