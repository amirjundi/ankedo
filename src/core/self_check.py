"""
Self-Check Utilities.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger()


class SelfCheck:
    """System self-checks."""

    @staticmethod
    def verify_fingerprint_consistency(account_id: str, current_fingerprint: dict, stored_fingerprint: dict) -> bool:
        """T092: Verify each account presents the same fingerprint across sessions."""
        if current_fingerprint != stored_fingerprint:
            log.error(
                "Fingerprint inconsistency detected (SC-022)", 
                account_id=account_id,
                current=current_fingerprint,
                stored=stored_fingerprint
            )
            # In a real system we would quarantine the account
            return False
            
        log.info("Fingerprint consistency check passed", account_id=account_id)
        return True
