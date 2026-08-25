"""Queue writes locally, then drain them to the platform.

Two properties this buys, both of which matter on a residential connection:

**Nothing is lost.** A verdict is written to the outbox before any network call, so a
dropped line costs a delay rather than the classification. Re-collecting instead would
mean re-scraping content that may have been deleted, from accounts that may have been
blocked since.

**Nothing is duplicated.** Each item carries a stable `request_id` sent as an
Idempotency-Key. The retry loop genuinely cannot tell "the server never received it"
from "the server processed it and the response was lost" — so without a stable id,
retrying creates a second report about the same person.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ettok.client import AgentKeyRejected, EttokClient, EttokError
from src.models.outbox import OutboxItem, OutboxKind, OutboxStatus

log = structlog.get_logger()

# Beyond this an item is almost certainly malformed rather than unlucky, and retrying
# it forever would block the queue behind it.
MAX_ATTEMPTS = 8


async def enqueue(
    session: AsyncSession, kind: OutboxKind, payload: dict
) -> OutboxItem:
    """Record something to send. Call this before attempting any network I/O."""
    item = OutboxItem(kind=kind, payload=payload)
    session.add(item)
    await session.flush()
    return item


async def pending(session: AsyncSession, limit: int = 50) -> list[OutboxItem]:
    stmt = (
        select(OutboxItem)
        .where(
            OutboxItem.status == OutboxStatus.PENDING,
            OutboxItem.attempts < MAX_ATTEMPTS,
        )
        .order_by(OutboxItem.created_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def drain(session: AsyncSession, client: EttokClient, limit: int = 50) -> dict:
    """Send what is queued. Returns counts.

    Stops the whole drain on an auth failure rather than marking items failed: a
    revoked key is not the items' fault, and burning through the queue would waste
    every one of their attempts on a problem only a human can fix.
    """
    items = await pending(session, limit)
    sent = failed = 0

    for item in items:
        item.attempts += 1
        item.last_attempt_at = datetime.now(timezone.utc)

        try:
            await _send(client, item)
        except AgentKeyRejected:
            item.attempts -= 1  # not this item's fault; do not spend its budget
            await session.commit()
            log.error("Outbox drain stopped — agent key rejected", queued=len(items))
            raise
        except EttokError as exc:
            item.last_error = str(exc)[:1000]
            if item.attempts >= MAX_ATTEMPTS:
                item.status = OutboxStatus.FAILED
                log.error(
                    "Outbox item failed permanently",
                    kind=item.kind,
                    request_id=item.request_id,
                    error=item.last_error,
                )
            failed += 1
        else:
            item.status = OutboxStatus.SENT
            item.sent_at = datetime.now(timezone.utc)
            item.last_error = None
            sent += 1

    await session.commit()
    if sent or failed:
        log.info("Outbox drained", sent=sent, failed=failed, remaining=len(items) - sent)
    return {"sent": sent, "failed": failed, "queued": len(items)}


async def _send(client: EttokClient, item: OutboxItem) -> None:
    if item.kind == OutboxKind.VERDICT:
        await client.post_flagged_items(
            item.payload.get("items", []), request_id=item.request_id
        )
    elif item.kind == OutboxKind.SCAN_LOG:
        await client.post_scan_log(item.payload)
    elif item.kind == OutboxKind.LEXICON_GAP:
        await client.post_lexicon_gaps(item.payload.get("gaps", []))
    else:
        raise EttokError(f"unknown outbox kind {item.kind}")


async def depth(session: AsyncSession) -> dict:
    """How much is waiting. A rising pending count means submissions are not landing."""
    counts = {}
    for status in OutboxStatus:
        stmt = select(OutboxItem).where(OutboxItem.status == status)
        counts[status.value.lower()] = len((await session.execute(stmt)).scalars().all())
    return counts
