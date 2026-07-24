"""
Evidence Builder - Constructs the evidence package for human review.
Takes screenshots, HTML snapshots, and formats the multi-agent trace.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.browsers.screenshot_worker import ScreenshotWorker
from src.core.settings import get_settings
from src.models.evidence_package import EvidencePackage

log = structlog.get_logger()


class AutoSubmitGuardrailError(Exception):
    """Raised when any code path attempts to submit a report without explicit human initiation."""
    pass


class EvidenceBuilder:
    """Builds evidence packages when a reviewer confirms a flag."""

    def __init__(self, session: AsyncSession, screenshot_worker: ScreenshotWorker):
        self.session = session
        self.screenshot_worker = screenshot_worker
        self.settings = get_settings()

    async def build_package(self, reviewer_id: str, post_id: str, comment_id: str | None, item_url: str, mode: str, trace_snapshot: dict) -> EvidencePackage:
        """Capture screenshot, save trace, and create EvidencePackage record."""
        log.info("Building evidence package", reviewer_id=reviewer_id, post_id=post_id, comment_id=comment_id)
        
        # Ensure evidence directory exists
        os.makedirs(self.settings.evidence_dir, exist_ok=True)
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target_id = comment_id if comment_id else post_id
        
        filename_base = f"evidence_{target_id}_{timestamp}"
        screenshot_path = os.path.join(self.settings.evidence_dir, f"{filename_base}.{self.settings.screenshot_format}")
        html_path = os.path.join(self.settings.evidence_dir, f"{filename_base}.html")
        
        # Take screenshot
        success = await self.screenshot_worker.capture(
            item_url=item_url, 
            mode=mode, 
            output_path=screenshot_path
        )
        
        # T048: Screenshot failure fallback
        if not success:
            log.warning("Failed to take screenshot for evidence, fallback to HTML snapshot only", item_url=item_url)
            screenshot_path = ""
            
        # Capture HTML snapshot (stub)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<html><body>Stub HTML snapshot</body></html>")

        # T047: Full metadata package
        package = EvidencePackage(
            post_id=post_id,
            comment_id=comment_id,
            screenshot_path=screenshot_path,
            html_snapshot_path=html_path,
            reviewer_id=reviewer_id,
            confirmed_at=datetime.now(timezone.utc).isoformat(),
            multi_agent_trace_snapshot=trace_snapshot
        )
        
        self.session.add(package)
        await self.session.commit()
        
        log.info("Evidence package built successfully", package_id=package.id)
        return package

    def submit_report(self, package: EvidencePackage, explicit_human_initiation: bool = False) -> None:
        """
        Submits the report to the platform.
        T049: Hard block on auto-submission.
        """
        if not explicit_human_initiation:
            log.critical("Blocked automated report submission attempt", package_id=package.id)
            raise AutoSubmitGuardrailError("Automated report submission is strictly prohibited (T049).")
            
        log.info("Report submitted manually by operator", package_id=package.id)
        # Proceed with submission logic (stub)
