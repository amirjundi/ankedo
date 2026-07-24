"""
API Endpoints for serving Evidence Packages.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_session
from src.models.evidence_package import EvidencePackage

log = structlog.get_logger()
router = APIRouter(prefix="/api/evidence", tags=["evidence"])


@router.get("/{package_id}")
async def get_evidence_package(package_id: str, session: AsyncSession = Depends(get_session)):
    """Fetch an evidence package by ID for manual review and submission."""
    stmt = select(EvidencePackage).where(EvidencePackage.id == package_id)
    result = await session.execute(stmt)
    package = result.scalar_one_or_none()
    
    if not package:
        raise HTTPException(status_code=404, detail="Evidence package not found")
        
    return {
        "id": package.id,
        "post_id": package.post_id,
        "comment_id": package.comment_id,
        "screenshot_path": package.screenshot_path,
        "html_snapshot_path": package.html_snapshot_path,
        "reviewer_id": package.reviewer_id,
        "confirmed_at": package.confirmed_at,
        "trace": package.multi_agent_trace_snapshot,
        "trope_fired": package.trope_fired,
    }
