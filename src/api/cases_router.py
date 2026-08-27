"""Cases, evidence and trend signals for the dashboard.

Each of these pages rendered a hardcoded array. That is worse here than in an ordinary
admin tool: the fixtures contained plausible Arabic hate-speech terms, invented
offender handles and fabricated offence counts, on pages titled "Evidence" and
"Intelligence Hub". Anyone opening the dashboard saw what looked like findings about
real accounts. In a system whose output is a human-rights record, a convincing
demonstration is indistinguishable from a false accusation.

So these read from the database and return nothing when there is nothing.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import session_scope
from src.models.case import Case, CaseState
from src.models.comment import Comment
from src.models.evidence_package import EvidencePackage
from src.models.post import Post
from src.models.target_group import TargetGroup
from src.models.trend_signal import TrendSignal

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["cases"])


@router.get("/cases")
async def list_cases(session: AsyncSession = Depends(session_scope)):
    """Open cases with their real item counts."""
    rows = (
        await session.execute(select(Case).order_by(desc(Case.created_at)).limit(200))
    ).scalars().all()

    groups = {
        g.id: g.display_name_en
        for g in (await session.execute(select(TargetGroup))).scalars().all()
    }

    # Two grouped queries rather than two per case — a hundred cases would otherwise
    # be two hundred round trips to render one page.
    totals = dict(
        (await session.execute(
            select(Post.case_id, func.count(Post.id)).group_by(Post.case_id)
        )).all()
    )
    flagged = dict(
        (await session.execute(
            select(Post.case_id, func.count(Post.id))
            .where(Post.hate_speech_flag.is_(True))
            .group_by(Post.case_id)
        )).all()
    )

    return {
        "cases": [
            {
                "id": case.id,
                "title": getattr(case, "narrative_pattern", None) or f"Case {case.id[:8]}",
                "target_group": groups.get(case.target_group_id, "Unknown"),
                "state": getattr(case.state, "value", str(case.state)),
                "severity": case.severity,
                "watch_keywords": case.watch_keywords or [],
                "dialect_scope": case.dialect_scope,
                "items_count": totals.get(case.id, 0),
                "flagged_count": flagged.get(case.id, 0),
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
            for case in rows
        ]
    }


@router.get("/evidence")
async def list_evidence(limit: int = 50, session: AsyncSession = Depends(session_scope)):
    """Sealed evidence packages — only what a reviewer actually confirmed.

    An evidence package is created when a human confirms a verdict, so this list is
    short by design. It being empty means nothing has been confirmed yet, not that the
    page is broken.
    """
    rows = (
        await session.execute(
            select(EvidencePackage)
            .order_by(desc(EvidencePackage.created_at))
            .limit(min(limit, 200))
        )
    ).scalars().all()

    packages = []
    for pkg in rows:
        post = (
            await session.execute(select(Post).where(Post.id == pkg.post_id))
        ).scalar_one_or_none() if pkg.post_id else None
        comment = (
            await session.execute(select(Comment).where(Comment.id == pkg.comment_id))
        ).scalar_one_or_none() if pkg.comment_id else None

        packages.append({
            "id": pkg.id,
            "post_id": pkg.post_id,
            "comment_id": pkg.comment_id,
            "platform": post.platform if post else None,
            "url": post.url if post else None,
            # The text that was judged, so the package can be read without a second
            # lookup. Truncated: this is a list view, not the evidence itself.
            "excerpt": ((comment.text if comment else None) or (post.content_text if post else "") or "")[:280],
            "trope_fired": pkg.trope_fired,
            "reviewer_id": pkg.reviewer_id,
            "confirmed_at": pkg.confirmed_at,
            "has_screenshot": bool(pkg.screenshot_path),
        })

    return {"evidence": packages}


@router.get("/intelligence/offenders")
async def repeat_offenders(session: AsyncSession = Depends(session_scope)):
    """Authors with more than one flagged item.

    Counted from flagged posts, not from a watchlist somebody curated — there is no
    such list, and inventing one would put names on a page on the agent's authority
    alone. One flagged item is not a pattern, so the threshold is two.
    """
    rows = (
        await session.execute(
            select(
                Post.author_name,
                Post.platform,
                func.count(Post.id).label("offenses"),
                func.max(Post.created_at).label("last_seen"),
            )
            .where(Post.hate_speech_flag.is_(True), Post.author_name.is_not(None))
            .group_by(Post.author_name, Post.platform)
            .having(func.count(Post.id) > 1)
            .order_by(desc("offenses"))
            .limit(100)
        )
    ).all()

    return {
        "offenders": [
            {
                "handle": handle,
                "platform": platform,
                "offenses": offenses,
                "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else last_seen,
            }
            for handle, platform, offenses, last_seen in rows
        ]
    }


@router.get("/intelligence/trends")
async def trends(session: AsyncSession = Depends(session_scope)):
    """Recent hate-density signals, spikes first."""
    rows = (
        await session.execute(
            select(TrendSignal).order_by(desc(TrendSignal.created_at)).limit(100)
        )
    ).scalars().all()

    return {
        "trends": [
            {
                "id": row.id,
                "target_group": row.target_group,
                "platform": row.platform,
                "hour_bucket": row.hour_bucket,
                "items_scanned": row.items_scanned,
                "items_flagged": row.items_flagged,
                "hate_density": row.hate_density,
                "observed": row.observed,
            }
            for row in rows
        ]
    }


@router.get("/target-groups")
async def list_target_groups(session: AsyncSession = Depends(session_scope)):
    """The groups the agent knows about, for the case form.

    The form's dropdown was a hardcoded list of seven names. Choosing one produced a
    string that matched no row, so a case could not have been attached to a real group
    even if the form had submitted anything.
    """
    rows = (
        await session.execute(
            select(TargetGroup).where(TargetGroup.enabled.is_(True)).order_by(TargetGroup.display_name_en)
        )
    ).scalars().all()
    return {
        "target_groups": [
            {"id": g.id, "slug": g.slug, "name": g.display_name_en} for g in rows
        ]
    }


class NewCase(BaseModel):
    target_group_id: str
    narrative_pattern: str
    watch_keywords: list[str] = []
    dialect_scope: str | None = None
    severity: int = 2


@router.post("/cases", status_code=201)
async def create_case(body: NewCase, session: AsyncSession = Depends(session_scope)):
    """Open a case.

    The form that calls this used to close its own modal and discard everything typed
    into it. A monitoring campaign that the operator believes they started, and which
    does not exist, is worse than no form at all.
    """
    group = (
        await session.execute(select(TargetGroup).where(TargetGroup.id == body.target_group_id))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=400, detail="No such target group.")

    if not body.narrative_pattern.strip():
        raise HTTPException(status_code=400, detail="A case needs a title.")

    case = Case(
        target_group_id=group.id,
        narrative_pattern=body.narrative_pattern.strip(),
        watch_keywords=[k.strip() for k in body.watch_keywords if k.strip()],
        dialect_scope=body.dialect_scope,
        severity=max(0, min(4, body.severity)),
        state=CaseState.ACTIVE,
    )
    session.add(case)
    await session.commit()

    log.info("Case opened from the dashboard", case_id=case.id, group=group.slug)
    return {"id": case.id}
