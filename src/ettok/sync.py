"""Pull the platform lexicon into the local cache.

The platform owns the terms; this keeps a local copy so prefiltering can run without
a round trip per post, and so a connectivity drop on residential WiFi does not stop
the scan outright (`lexicon_max_stale_hours`).

The interesting work is group resolution. Upstream `target_group` is a free-text
CharField, so "Yazidi", "yazidi" and "الإيزيديين" arrive as different strings. Each is
resolved to a canonical TargetGroup here, and anything that does not resolve is
counted and reported rather than silently dropped — an unresolved group means terms
that will never match their tropes, which is exactly the failure this is meant to
prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.group_resolver import GroupResolver
from src.classifiers.lexicon import LexiconMatcher
from src.core.settings import get_settings
from src.ettok.client import EttokClient
from src.models.lexicon_entry import LexiconEntry, TermScope
from src.models.trope_entry import TropeDictionaryEntry

log = structlog.get_logger()


@dataclass
class SyncResult:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    unresolved_groups: dict[str, int] = field(default_factory=dict)
    bad_regexes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unresolved_groups and not self.bad_regexes


async def sync_lexicon(
    session: AsyncSession,
    client: EttokClient,
    languages: list[str] | None = None,
) -> SyncResult:
    """Fetch the active lexicon and reconcile it into the local cache."""
    payload = await client.get_lexicon(languages=languages)
    terms = payload.get("terms") or []
    result = SyncResult(fetched=len(terms))

    resolver = GroupResolver(session)
    existing = {
        row.platform_id: row
        for row in (
            await session.execute(
                select(LexiconEntry).where(LexiconEntry.platform_id.is_not(None))
            )
        ).scalars()
    }
    seen: set[int] = set()

    for term in terms:
        platform_id = term.get("id")
        if platform_id is None:
            continue
        seen.add(platform_id)

        if term.get("is_regex"):
            # Contract: skip an entry that fails to compile, do not abort the scan.
            try:
                re.compile(term["term"])
            except re.error as exc:
                result.bad_regexes.append(f"{platform_id}: {exc}")
                log.warning("Skipping uncompilable regex term", id=platform_id, error=str(exc))
                continue

        row = existing.get(platform_id)
        if row is None:
            row = LexiconEntry(platform_id=platform_id)
            session.add(row)
            result.created += 1
        else:
            result.updated += 1

        raw_group = (term.get("target_group") or "").strip()
        groups = []
        if raw_group:
            slug = await resolver.resolve(raw_group)
            group = await resolver.get(slug) if slug else None
            if group is None:
                result.unresolved_groups[raw_group] = (
                    result.unresolved_groups.get(raw_group, 0) + 1
                )
            else:
                groups = [group]

        row.term = term["term"]
        row.language = term.get("language")
        row.category = term.get("category")
        row.is_regex = bool(term.get("is_regex"))
        row.raw_target_group = raw_group or None
        row.severity = term.get("severity_weight")
        row.target_groups = groups
        # Upstream has no is_explicit flag yet; an unresolvable group means the term
        # cannot be gated on context, so treat it as explicit rather than lose it.
        row.scope = TermScope.GROUP_SPECIFIC if groups else TermScope.UNIVERSAL
        row.is_explicit = True
        row.enabled = True
        row.source = f"ettok:{platform_id}"
        row.pack_source = "ettok-platform"

    # Terms the platform no longer returns were deleted or deactivated upstream.
    # Disable rather than delete, so a bad sync is recoverable and the audit
    # trail survives.
    for platform_id, row in existing.items():
        if platform_id not in seen and row.enabled:
            row.enabled = False
            result.deactivated += 1

    await session.commit()
    LexiconMatcher.invalidate_cache()

    log.info(
        "Lexicon synced",
        fetched=result.fetched,
        created=result.created,
        updated=result.updated,
        deactivated=result.deactivated,
        unresolved=len(result.unresolved_groups),
    )
    if result.unresolved_groups:
        log.warning(
            "Upstream target groups did not resolve to a canonical group",
            groups=result.unresolved_groups,
        )
    return result


async def sync_tropes(session: AsyncSession, client: EttokClient) -> SyncResult:
    """Pull the trope dictionary into the local cache.

    The critical decision is how to read an empty `activation_topics`. Ettok shipped
    the schema before the content: the seeded tropes carry empty activation data until
    a curator backfills them from the Duhok transcript.

    Empty means **"no deterministic gate yet"**, never "always active". Reading it the
    permissive way would fire the devil-worship trope on every pious comment on the
    platform — flagging the ordinary religious speech of the community this system
    exists to protect. So `requires_target_group` defaults to True and an unsatisfied
    match surfaces as a candidate for human review instead of a flag.
    """
    payload = await client.get_tropes()
    tropes = payload.get("tropes") or []
    result = SyncResult(fetched=len(tropes))

    resolver = GroupResolver(session)
    existing = {
        row.trope_id: row
        for row in (
            await session.execute(
                select(TropeDictionaryEntry).where(
                    TropeDictionaryEntry.pack_source == "ettok-platform"
                )
            )
        ).scalars()
    }
    seen: set[str] = set()

    for trope in tropes:
        trope_id = f"ettok-{trope['id']}"
        seen.add(trope_id)

        raw_group = (trope.get("target_group") or "").strip()
        slug = trope.get("target_group_slug") or ""
        group = await resolver.get(slug) if slug else None
        if group is None and raw_group:
            resolved = await resolver.resolve(raw_group)
            group = await resolver.get(resolved) if resolved else None
        if group is None and raw_group:
            result.unresolved_groups[raw_group] = result.unresolved_groups.get(raw_group, 0) + 1

        row = existing.get(trope_id)
        if row is None:
            row = TropeDictionaryEntry(trope_id=trope_id)
            session.add(row)
            result.created += 1
        else:
            result.updated += 1

        row.target_groups = [group] if group else []
        row.scope = TermScope.GROUP_SPECIFIC
        row.surface_forms = [
            {"text": form, "register": "unspecified"} for form in trope.get("surface_forms") or []
        ]
        row.activation = {
            # Absent means strict. The permissive default is the one that over-flags.
            "requires_target_group": trope.get("requires_target_group", True),
            "post_topic_any": trope.get("activation_topics") or [],
            "negation_cancels": trope.get("negation_cancels", True),
        }
        row.implicature = trope.get("description")
        row.severity = trope.get("severity_weight")
        row.positive_examples = [{"comment_text": trope["example"]}] if trope.get("example") else []
        row.negative_examples = [
            {"comment_text": example} for example in trope.get("negative_examples") or []
        ]
        row.enabled = True
        row.pack_source = "ettok-platform"
        row.pack_version = str(payload.get("version") or "live")

        if not row.negative_examples:
            # Not fatal — the trope still cannot fire without its activation condition
            # — but a trope with only positive examples is one a curator has not
            # finished, and it belongs in the report rather than passing silently.
            result.bad_regexes.append(
                f"{trope_id} ({raw_group or 'no group'}): no negative examples — "
                "needs the benign half of the minimal pair"
            )

    for trope_id, row in existing.items():
        if trope_id not in seen and row.enabled:
            row.enabled = False
            result.deactivated += 1

    await session.commit()
    log.info(
        "Tropes synced",
        fetched=result.fetched,
        created=result.created,
        updated=result.updated,
        ungated=sum(
            1 for t in tropes if not (t.get("activation_topics") or []) and t.get("target_group")
        ),
    )
    return result


async def lexicon_freshness(session: AsyncSession) -> tuple[int, datetime | None]:
    """Return (cached term count, most recent sync time)."""
    rows = (
        await session.execute(
            select(LexiconEntry).where(
                LexiconEntry.platform_id.is_not(None), LexiconEntry.enabled.is_(True)
            )
        )
    ).scalars().all()
    latest = max((r.updated_at for r in rows), default=None)
    return len(rows), latest


async def lexicon_is_usable(session: AsyncSession) -> bool:
    """Whether the cached lexicon is fresh enough to scan with.

    `lexicon_max_stale_hours = 0` means refuse to scan without a fresh pull, matching
    the contract's per-run caching exactly. Anything higher trades a little staleness
    for the ability to keep working through a connectivity drop.
    """
    settings = get_settings()
    count, latest = await lexicon_freshness(session)
    if not count or latest is None:
        return False
    if settings.lexicon_max_stale_hours == 0:
        return False
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - latest
    return age <= timedelta(hours=settings.lexicon_max_stale_hours)
