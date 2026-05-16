"""Shared error response helpers.

Mirrors the Node app's routes/api-errors.js shape:
  { "error": <message>, "code": <code>, "details"?: {...} }
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def not_found(message: str = "not found", code: str = "not_found") -> HTTPException:
    return HTTPException(status_code=404, detail={"error": message, "code": code})


def conflict(message: str, code: str = "conflict") -> HTTPException:
    return HTTPException(status_code=409, detail={"error": message, "code": code})


def bad_request(message: str, code: str = "bad_request") -> HTTPException:
    return HTTPException(status_code=400, detail={"error": message, "code": code})


def internal(message: str = "Request failed", code: str = "internal_error") -> HTTPException:
    return HTTPException(status_code=500, detail={"error": message, "code": code})


def validation_failed(details: dict[str, Any]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error": "validation failed",
            "code": "validation_error",
            "details": details,
        },
    )
