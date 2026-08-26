"""Drive one collection pass over the accounts that are due.

The orchestration loop processed queues but nothing ever filled them. This is the
step that does: pick due accounts, browse them through a real browser session, and
enqueue what is found.

Three things shape the design:

* **Due-ness, not a fixed schedule.** Each account carries its own
  `crawl_interval_seconds`, set from case state and health. An account that was
  crawled two minutes ago is skipped even if the cycle comes round again.
* **One browser session per account.** Sessions and cookies are per-identity;
  sharing a browser across accounts links them together, which is exactly what the
  worker-account model exists to avoid.
* **Failure is expected and specific.** Selectors break, checkpoints appear, sessions
  expire. `ResilientCollector` routes each of those differently, and a platform-wide
  failure pauses that platform rather than burning every account against it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.browsers.browser_factory import BrowserFactory
from src.browsers.resilient_collector import ResilientCollector
from src.core.queue_manager import QueueManager
from src.core.settings import get_settings
from src.models.agent_worker_account import AccountStage, AccountState, AgentWorkerAccount
from src.models.comment import Comment
from src.models.post import Post
from src.models.tracked_account import AccountStatus, TrackedAccount
from src.platforms.registry import get_adapter

log = structlog.get_logger()


@dataclass
class CollectionStats:
    """Counts the platform needs for hate-density reporting (contract amendment §1).

    `comments_scanned` is the denominator, and it exists nowhere else — Ettok only
    ever sees what was flagged, never what was looked at and cleared.
    """

    posts_scanned: int = 0
    comments_scanned: int = 0
    accounts_attempted: int = 0
    accounts_blocked: int = 0
    accounts_captcha: int = 0
    used_vision: int = 0
    per_platform: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def bump(self, platform: str, key: str, amount: int = 1) -> None:
        slot = self.per_platform.setdefault(
            platform, {"posts_scanned": 0, "comments_scanned": 0, "accounts_blocked": 0}
        )
        slot[key] = slot.get(key, 0) + amount


class CollectionRunner:
    """One pass over all due tracked accounts."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.queue_manager = QueueManager(session)

    async def run(self, *, max_accounts: int | None = None) -> CollectionStats:
        stats = CollectionStats()
        accounts = await self._due_accounts(limit=max_accounts)
        if not accounts:
            log.info("No accounts due for collection")
            return stats

        paused: set[str] = set()

        for account in accounts:
            if account.platform in paused:
                continue

            worker = await self._worker_for(account.platform)
            if worker is None:
                log.warning("No healthy worker account", platform=account.platform)
                stats.errors.append(f"{account.platform}: no healthy worker account")
                paused.add(account.platform)
                continue

            stats.accounts_attempted += 1
            try:
                await self._collect_one(account, worker, stats)
            except Exception as exc:
                log.exception("Collection failed", account=account.handle, error=str(exc))
                stats.errors.append(f"{account.handle}: {exc}")

        await self.session.commit()
        log.info(
            "Collection pass complete",
            posts=stats.posts_scanned,
            comments=stats.comments_scanned,
            accounts=stats.accounts_attempted,
        )
        return stats

    async def _collect_one(
        self, account: TrackedAccount, worker: AgentWorkerAccount, stats: CollectionStats
    ) -> None:
        adapter = get_adapter(account.platform)
        browser = BrowserFactory.create_worker(
            platform=account.platform, account_id=worker.id, proxy=worker.proxy
        )

        # Apply whatever the tuner has decided. A spike shortens the delay, and
        # before this the adjustment went nowhere: the worker read the static env
        # value and paced exactly as it always had.
        from src.core.self_tuner import SelfTuner

        tuner = SelfTuner(self.session)
        browser.set_pacing(
            await tuner.current("pacing_min_delay_seconds"),
            await tuner.current("pacing_max_delay_seconds"),
        )

        await browser.start()
        try:
            collector = ResilientCollector(self.session, browser.page, adapter)

            posts = await adapter.fetch_new_posts(
                browser.page, account.page_url, max_posts=self.settings.max_posts_per_account
            )

            for post_data in posts:
                post = await self._upsert_post(account, post_data)
                if post is None:  # already seen
                    continue
                stats.posts_scanned += 1
                stats.bump(account.platform, "posts_scanned")

                outcome = await collector.fetch_comments(
                    post.url, max_comments=self.settings.max_comments_per_post
                )
                if outcome.needs_human:
                    stats.accounts_captcha += 1
                    stats.bump(account.platform, "accounts_blocked")
                    return  # stop this account; a human has been asked
                if outcome.used_vision:
                    stats.used_vision += 1

                for comment_data in outcome.items:
                    self.session.add(
                        Comment(
                            post_id=post.id,
                            platform_comment_id=comment_data["platform_comment_id"],
                            text=comment_data.get("text"),
                            author_name=comment_data.get("author_name"),
                        )
                    )
                    stats.comments_scanned += 1
                    stats.bump(account.platform, "comments_scanned")

                await self.queue_manager.enqueue_discovery(
                    tracked_account_id=account.id,
                    post_id=post.id,
                    case_id=account.linked_case_id,
                    priority=account.priority,
                )
                await browser.pacing_delay()

            account.last_crawled_at = datetime.now(timezone.utc).isoformat()
        finally:
            await browser.stop()

    async def _upsert_post(self, account: TrackedAccount, data: dict) -> Post | None:
        """Return a new Post, or None when this one was already collected."""
        existing = (
            await self.session.execute(
                select(Post).where(
                    Post.tracked_account_id == account.id,
                    Post.platform_post_id == data["platform_post_id"],
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None

        post = Post(
            tracked_account_id=account.id,
            case_id=account.linked_case_id,
            platform=account.platform,
            platform_post_id=data["platform_post_id"],
            url=data["url"],
            content_text=data.get("content_text"),
            content_media_urls=data.get("media_urls") or [],
            author_name=data.get("author_name"),
            collected_at=datetime.now(timezone.utc).isoformat(),
        )
        self.session.add(post)
        await self.session.flush()
        return post

    async def _due_accounts(self, limit: int | None = None) -> list[TrackedAccount]:
        """Accounts whose own interval has elapsed, most urgent first."""
        stmt = (
            select(TrackedAccount)
            .where(TrackedAccount.status == AccountStatus.ACTIVE)
            .order_by(TrackedAccount.priority.desc())
        )
        candidates = (await self.session.execute(stmt)).scalars().all()

        now = datetime.now(timezone.utc)
        due = []
        for account in candidates:
            if not account.page_url:
                continue
            if account.last_crawled_at:
                last = datetime.fromisoformat(account.last_crawled_at.replace("Z", "+00:00"))
                if now - last < timedelta(seconds=account.crawl_interval_seconds):
                    continue
            due.append(account)
            if limit and len(due) >= limit:
                break
        return due

    async def _worker_for(self, platform: str) -> AgentWorkerAccount | None:
        """Least recently used healthy account, so load spreads (FR-AC-3)."""
        stmt = (
            select(AgentWorkerAccount)
            .where(
                AgentWorkerAccount.platform == platform,
                AgentWorkerAccount.state == AccountState.HEALTHY,
                AgentWorkerAccount.stage.in_([AccountStage.ACTIVE, AccountStage.WARM_UP]),
            )
            .order_by(AgentWorkerAccount.last_used_at.asc().nullsfirst())
        )
        return (await self.session.execute(stmt)).scalars().first()
