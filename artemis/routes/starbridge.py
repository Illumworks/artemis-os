"""Starbridge webhook endpoint.

Starbridge POSTs a bridge row the moment its columns finish processing. The
alternative is a 4-hour scout, which is the difference between knowing an RFP
posted and knowing it posted this morning.

Deliveries are Ed25519-signed. Verification uses the RAW body -- parsing and
re-serialising the JSON changes whitespace and key order and breaks every
signature, which Starbridge calls out explicitly.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import SessionLocal
from artemis.starbridge.router import route_delivery
from artemis.starbridge.webhook import (
    WebhookHeaders,
    WebhookVerificationError,
    load_public_key,
    verify_delivery,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/starbridge", tags=["starbridge"])

#: Recently handled webhook-ids. Starbridge sends bridge.row.updated repeatedly
#: for one row as later columns finish, and retries anything >= 429 three times.
#: Bounded so a long-running process cannot grow it without limit; the durable
#: guard is the source_id check in route_delivery, this only saves the work.
_SEEN_IDS: dict[str, float] = {}
_SEEN_LIMIT = 2048


def _already_handled(webhook_id: str) -> bool:
    import time

    if webhook_id in _SEEN_IDS:
        return True
    if len(_SEEN_IDS) >= _SEEN_LIMIT:
        for stale in sorted(_SEEN_IDS, key=lambda k: _SEEN_IDS[k])[: _SEEN_LIMIT // 4]:
            _SEEN_IDS.pop(stale, None)
    _SEEN_IDS[webhook_id] = time.time()
    return False


@router.post("/webhook")
async def starbridge_webhook(
    request: Request,
    webhook_id: str = Header(default="", alias="webhook-id"),
    webhook_timestamp: str = Header(default="", alias="webhook-timestamp"),
    webhook_signature: str = Header(default="", alias="webhook-signature"),
) -> Response:
    """Accept one signed bridge-row delivery.

    Returns 401 on a delivery we cannot prove is Starbridge's, and 400 on one we
    cannot read. Neither is retried by Starbridge (only >= 429 is), which is
    correct: replaying a bad signature or an unparseable body three times just
    repeats the same failure.
    """
    raw_body = await request.body()

    configured = getattr(settings, "starbridge_webhook_public_key", "") or ""
    if not configured:
        # Fail closed and say which. An endpoint that accepts unsigned bodies
        # because a key is missing is an open write path into the signal queue.
        logger.error("starbridge webhook: no public key configured; refusing delivery")
        return Response(
            content=json.dumps({"error": "webhook signing key not configured"}),
            status_code=503,
            media_type="application/json",
        )

    try:
        verify_delivery(
            raw_body=raw_body,
            headers=WebhookHeaders(webhook_id, webhook_timestamp, webhook_signature),
            public_key=load_public_key(configured),
        )
    except WebhookVerificationError as exc:
        logger.warning("starbridge webhook rejected: %s", exc)
        return Response(
            content=json.dumps({"error": str(exc)}),
            status_code=401,
            media_type="application/json",
        )

    if webhook_id and _already_handled(webhook_id):
        logger.info("starbridge webhook %s already handled; acking", webhook_id)
        return Response(
            content=json.dumps({"status": "duplicate"}),
            status_code=200,
            media_type="application/json",
        )

    try:
        payload = json.loads(raw_body)
    except ValueError:
        logger.warning("starbridge webhook %s: body is not JSON", webhook_id)
        return Response(
            content=json.dumps({"error": "body is not JSON"}),
            status_code=400,
            media_type="application/json",
        )

    session: AsyncSession
    async with SessionLocal() as session:
        result = await route_delivery(session, payload)
        await session.commit()

    logger.info(
        "starbridge webhook %s -> %s (signal_id=%s codes=%s) %s",
        webhook_id,
        result.outcome,
        result.signal_id,
        result.reason_codes,
        result.detail,
    )
    return Response(
        content=json.dumps({"status": result.outcome, "signalId": result.signal_id}),
        status_code=200,
        media_type="application/json",
    )
