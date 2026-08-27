"""
Orchestration Loop - The core daemon that coordinates all agents and workers.
"""
from __future__ import annotations

import asyncio
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.case_manager import CaseManager
from src.core.queue_manager import QueueManager
from src.core.settings import get_settings
from src.models.case import Case, CaseState
from src.models.post import Post, QueueState as PostQueueState
from src.models.queue_item import QueueItem, QueueStage
from src.models.tracked_account import AccountStatus, TrackedAccount
from src.notifications.dispatcher import NotificationDispatcher
from src.models.agent_notification import AgentNotification, NotificationStatus
from src.core.discovery_engine import DiscoveryEngine
from src.models.agent_worker_account import AgentWorkerAccount, AccountState

log = structlog.get_logger()


class OrchestrationLoop:
    """Main continuous daemon loop."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.case_manager = CaseManager(session)
        self.dispatcher = NotificationDispatcher(session)
        self.discovery = DiscoveryEngine(session)
        self.queue_manager = QueueManager(session)
        # Latest CollectionStats, carrying the comments_scanned denominator the
        # platform needs for hate density (contract amendment §1).
        self.last_collection = None
        # Whether the operator has already been told the browser is down. Reset on the
        # first successful pass, so a recurrence is reported rather than swallowed.
        self._browser_alert_sent = False

    async def _schedule_cases(self) -> None:
        """Evaluate case lifecycle and prioritize active cases."""
        stmt = select(Case).where(Case.state.in_([CaseState.ACTIVE, CaseState.COOLING]))
        result = await self.session.execute(stmt)
        cases = result.scalars().all()
        
        for case in cases:
            # Update lifecycle states
            await self.case_manager.evaluate_lifecycle(case)
            
            # Here we would dispatch collectors for high-priority targets
            if case.state == CaseState.ACTIVE:
                log.debug("Scheduling high-frequency crawl for ACTIVE case", case_id=case.id)
            elif case.state == CaseState.COOLING:
                log.debug("Scheduling reduced-frequency crawl for COOLING case", case_id=case.id)

    async def _handle_notifications(self) -> None:
        """T064/T066: Check for needed escalations and process admin responses."""
        await self.dispatcher.check_escalations()
        
        # Stub: check for newly resolved notifications and apply admin response to agent behavior
        stmt = select(AgentNotification).where(
            AgentNotification.status == NotificationStatus.RESOLVED
        )
        # Process them...

    async def _check_alerts(self) -> None:
        """T073: Monitors healthy-account count and fires AgentNotification if below threshold."""
        stmt = select(AgentWorkerAccount.platform).where(AgentWorkerAccount.state == AccountState.HEALTHY)
        result = await self.session.execute(stmt)
        platforms = [p for (p,) in result.all()]
        
        counts = {
            "facebook": platforms.count("facebook"),
            "tiktok": platforms.count("tiktok"),
            "instagram": platforms.count("instagram")
        }
        
        for platform, count in counts.items():
            if count < self.settings.min_healthy_accounts_per_platform:
                await self.dispatcher.send(
                    type_="CapacityAlert",
                    context={"platform": platform, "healthy_count": count},
                    question=f"Low capacity on {platform}. Current: {count}. Minimum required: {self.settings.min_healthy_accounts_per_platform}",
                    urgency="High",
                    suggested_actions=["Add new worker accounts", "Review quarantined accounts"],
                )

    async def _sync_workers(self) -> None:
        """T075: Set each account's crawl interval from case state and health.

        Scaling here is about *pacing*, not process count. Crawling faster than the
        platform tolerates loses accounts, and a lost account costs far more than a
        slow cycle — so an active case shortens the interval and a degraded account
        lengthens it.
        """
        stmt = select(TrackedAccount).where(TrackedAccount.status == AccountStatus.ACTIVE)
        accounts = (await self.session.execute(stmt)).scalars().all()

        for account in accounts:
            interval = self.settings.loop_interval_seconds
            if account.linked_case_id:
                case = await self.session.get(Case, account.linked_case_id)
                if case and case.state in (CaseState.ACTIVE, CaseState.REACTIVATED):
                    interval = max(60, interval // 2)
                elif case and case.state == CaseState.DORMANT:
                    interval *= 8  # keyword watch only (FR-CM-3)
            account.crawl_interval_seconds = interval

        await self.session.commit()

    async def _collect(self) -> None:
        """Fetch from accounts that are due, and keep the run statistics.

        Collection failures are contained here: a browser that cannot start or a
        platform that blocks us must not stop case scheduling, notifications or
        capacity alerts from running.
        """
        from src.browsers.camoufox_worker import BrowserUnavailable
        from src.core.collection_runner import CollectionRunner

        try:
            self.last_collection = await CollectionRunner(self.session).run()
        except BrowserUnavailable as exc:
            # Distinct from a pass that found nothing: the agent has no eyes at all,
            # and until now that looked identical in the logs. The dead man's switch
            # would eventually fire CollectionSilent, six hours later and without
            # naming the cause.
            self.last_collection = None
            log.error("Browser unavailable — collection cannot run", error=str(exc))
            if not self._browser_alert_sent:
                # Once, not every 60 seconds. A notification the operator learns to
                # ignore is worse than none.
                self._browser_alert_sent = True
                await self.dispatcher.send(
                    type_="ToolBroken",
                    context={"tool": "browser", "error": str(exc)},
                    question="Collection is stopped: the browser will not start.",
                    urgency="Critical",
                    suggested_actions=[
                        "Run: ankedo doctor --fix",
                        "Ask the agent in chat to repair the browser",
                        "Run: ankedo doctor  (to see the cause)",
                    ],
                )
        except Exception as exc:
            log.exception("Collection pass failed", error=str(exc))
            self.last_collection = None
        else:
            # Re-arm, so a browser that breaks again is reported again.
            self._browser_alert_sent = False

    async def _process_discovery(self) -> None:
        """Fetch comments for newly discovered posts, then hand them to classification.

        This stage did not exist. Crawled posts were enqueued at Discovery,
        PostProcessor — the thing that moves an item from Processing to Classification
        — was never instantiated anywhere, and _process_queues drained Classification
        only. Every crawled item sat at Discovery permanently, which is why nothing had
        ever been classified.

        A browser is needed here, unlike the capture path: Discovery means the post was
        seen but its comments were not fetched. If the browser will not start, the
        items stay queued for the next cycle rather than being lost.
        """
        from src.browsers.browser_factory import BrowserFactory
        from src.browsers.camoufox_worker import BrowserUnavailable
        from src.core.post_processor import PostProcessor
        from src.platforms.registry import get_adapter

        processed = 0
        browsers: dict[str, object] = {}

        try:
            while processed < self.settings.max_review_batch_size:
                item = await self.queue_manager.dequeue(
                    QueueStage.DISCOVERY, worker_id="loop"
                )
                if item is None:
                    break

                post = (
                    await self.session.execute(
                        select(Post).where(Post.id == item.post_id)
                    )
                ).scalar_one_or_none()
                if post is None:
                    await self.queue_manager.mark_done(item)
                    continue

                # Processing is where the fetch happens; Discovery is only the record
                # that the post exists.
                await self.queue_manager.promote(item, QueueStage.PROCESSING)

                try:
                    if post.platform not in browsers:
                        worker = BrowserFactory.create_worker(post.platform, "loop", None)
                        await worker.start()
                        browsers[post.platform] = worker
                    browser = browsers[post.platform]

                    await PostProcessor(
                        self.session, self.queue_manager, browser, get_adapter(post.platform)
                    ).process_item(item)
                except BrowserUnavailable:
                    # Put it back: the post is fine, the agent has no eyes. _collect
                    # raises the alarm about that; losing the item as well would be a
                    # second failure caused by the first.
                    await self.queue_manager.promote(item, QueueStage.DISCOVERY)
                    raise
                except Exception as exc:
                    log.exception("Post processing failed", post_id=post.id, error=str(exc))
                    await self.queue_manager.release(item, f"processing failed: {exc}")

                processed += 1
        finally:
            for worker in browsers.values():
                try:
                    await worker.stop()
                except Exception:  # noqa: BLE001 — teardown must not mask the cause
                    pass

        if processed:
            log.info("Discovery queue processed", items=processed)

    async def _process_queues(self) -> None:
        """T083: Drain the classification queue.

        The budget guard is deliberately allowed to stop the cycle rather than being
        swallowed: FR-AG-7 lists cost limits among the guardrails the agent may not
        override, so exhausting the budget must halt work, not degrade quietly.
        """
        from src.classifiers.classification_worker import ClassificationWorker
        from src.core.budget import BudgetExceededError

        worker = ClassificationWorker(self.session, self.queue_manager)
        processed = 0

        while processed < self.settings.max_review_batch_size:
            item = await self.queue_manager.dequeue(QueueStage.CLASSIFICATION, worker_id="loop")
            if item is None:
                break
            try:
                await worker.process_item(item)
            except BudgetExceededError:
                log.error("Budget exhausted — pausing classification for this cycle")
                raise
            except Exception as exc:
                log.exception("Classification failed", queue_item=item.id, error=str(exc))
                # Give it back. Without this the item stays claimed forever and is
                # never seen again — the whole batch is lost every time the model
                # endpoint is unavailable.
                await self.queue_manager.release(item, f"classification failed: {exc}")
            processed += 1

        if processed:
            log.info("Classification queue processed", items=processed)

    async def _evaluate_expansion(self) -> None:
        """T084 / FR-AG-3: expand into reply threads only where hate is dense.

        Crawling every reply everywhere is what gets accounts blocked. Density is the
        signal that a thread is worth the extra requests.
        """
        stmt = select(Post).where(
            Post.comments_total >= self.settings.expansion_min_comments,
            Post.queue_state == PostQueueState.DONE,
        )
        for post in (await self.session.execute(stmt)).scalars():
            if not post.comments_total:
                continue
            density = post.comments_flagged / post.comments_total
            if density >= self.settings.expansion_hate_density and post.queue_priority < 10:
                post.queue_priority = 10
                log.info(
                    "Thread marked for expansion",
                    post_id=post.id,
                    density=round(density, 2),
                )
        await self.session.commit()

    async def _prevent_reviewer_overload(self) -> None:
        """T085 / FR-AG-4: batch borderline items rather than flooding the queue.

        Reviewers are the bottleneck, and an unbounded queue during an incident is
        how a monitoring programme quietly stops functioning. Above the cap the
        borderline band waits; high-confidence items are never held back.
        """
        stmt = select(func.count(QueueItem.id)).where(QueueItem.stage == QueueStage.REVIEW)
        depth = (await self.session.execute(stmt)).scalar_one()

        if depth > self.settings.max_review_batch_size:
            log.warning("Review queue above batch size, holding borderline items", depth=depth)
            await self.dispatcher.send(
                type_="ReviewerOverload",
                context={"queue_depth": depth, "batch_size": self.settings.max_review_batch_size},
                question=(
                    f"Review queue is at {depth} items. Borderline items are being held "
                    "so high-confidence ones stay visible."
                ),
                urgency="Medium",
                suggested_actions=["Add reviewers", "Raise auto-flag threshold", "Acknowledge"],
            )

    async def _check_trends(self) -> None:
        """Escalate on a spike, within the limits the agent cannot raise.

        FR-AG-5: reactivating a dormant case needs case-manager confirmation unless
        the pattern is trusted. FR-AG-7: a spike may raise crawl rate up to the
        configured ceiling and no further — an incident must never be able to talk
        the agent past a guardrail, because that is exactly when it would.
        """
        from src.core.self_tuner import SelfTuner
        from src.core.trend_detector import TrendDetector

        spikes = await TrendDetector(self.session).detect()
        if not spikes:
            return

        tuner = SelfTuner(self.session)
        for spike in spikes:
            stmt = select(Case).where(
                Case.state.in_([CaseState.COOLING, CaseState.DORMANT])
            )
            for case in (await self.session.execute(stmt)).scalars():
                if not case.target_group or case.target_group.slug != spike.target_group:
                    continue
                if self.settings.auto_reactivate_cases:
                    case.state = CaseState.REACTIVATED
                    log.warning("Case auto-reactivated by spike", case_id=case.id)
                else:
                    await self.dispatcher.send(
                        type_="CaseReactivationProposal",
                        context={"case_id": case.id, "spike": spike.describe()},
                        question=f"Reactivate this case? {spike.describe()}",
                        urgency="High",
                        suggested_actions=["Reactivate", "Ignore", "Adjust threshold"],
                    )

            # Shorter pacing while a spike lasts — clamped by the tuner's bounds.
            #
            # Both ends, not just the minimum. Delays are drawn from a Gaussian
            # between the two, so shrinking the floor from 2.5s to 0.8s while leaving
            # the ceiling at 8s moves the mean by well under a second: the agent
            # crawled at essentially its normal rate through a spike it had just
            # detected and raised an alert about.
            multiplier = self.settings.crawl_multiplier_on_spike
            for key, floor in (
                ("pacing_min_delay_seconds", 1.0),
                ("pacing_max_delay_seconds", 3.0),
            ):
                current = await tuner.current(key)
                await tuner.adjust(
                    key,
                    max(floor, current / multiplier),
                    f"spike: {spike.describe()}",
                )

            await self.dispatcher.send(
                type_="HateSpeechSpike",
                context={
                    "target_group": spike.target_group,
                    "platform": spike.platform,
                    "density": spike.current,
                    "baseline": spike.baseline,
                    "zscore": spike.zscore,
                },
                question=spike.describe(),
                urgency="High",
                suggested_actions=["Open a case", "Increase monitoring", "Acknowledge"],
            )

    async def _check_liveness(self) -> None:
        """Alert if collection has gone quiet.

        A monitoring tool that stops collecting looks identical to one monitoring a
        quiet period. Without this the failure is invisible until someone asks why
        there have been no reports.
        """
        from datetime import datetime, timedelta, timezone

        stmt = select(TrackedAccount).where(TrackedAccount.status == AccountStatus.ACTIVE)
        accounts = (await self.session.execute(stmt)).scalars().all()
        if not accounts:
            return

        crawled = [a.last_crawled_at for a in accounts if a.last_crawled_at]
        if not crawled:
            return

        latest = max(datetime.fromisoformat(c.replace("Z", "+00:00")) for c in crawled)
        silent_for = datetime.now(timezone.utc) - latest

        if silent_for > timedelta(hours=self.settings.dead_mans_switch_hours):
            log.error("Collection has gone silent", hours=round(silent_for.total_seconds() / 3600))
            await self.dispatcher.send(
                type_="CollectionSilent",
                context={"hours_since_last_crawl": silent_for.total_seconds() / 3600},
                question=(
                    f"No content collected for "
                    f"{silent_for.total_seconds() / 3600:.0f} hours. Monitoring may have "
                    "stopped without failing visibly."
                ),
                urgency="Critical",
                suggested_actions=["Check worker accounts", "Check network", "Check logs"],
            )

    async def _drain_outbox(self) -> None:
        """Send what classification queued for the platform.

        Runs every cycle rather than at submission time: the outbox exists precisely
        because the connection is unreliable, so the sender must be something that
        comes back around on its own, not something that happens once and gives up.

        Silent when the platform is not configured. An agent can run perfectly well
        with no platform attached — it classifies into its own database — and logging
        an error every minute for a deployment choice would bury the real ones.
        """
        if not self.settings.ettok_base_url:
            return

        from src.ettok.client import AgentKeyRejected, EttokClient
        from src.ettok.outbox import drain

        try:
            async with EttokClient() as client:
                await drain(self.session, client)
        except AgentKeyRejected:
            # The drain stops itself and keeps the queue intact. Nothing here can fix
            # a revoked key, so tell the operator instead of retrying into a wall.
            log.error("Platform rejected the agent key — submissions are paused")
            await self.dispatcher.send(
                type_="ToolBroken",
                context={"tool": "platform", "error": "agent key rejected"},
                question=(
                    "Submissions are paused: the platform refused the agent key. "
                    "Work is queued and nothing is lost."
                ),
                urgency="Critical",
                suggested_actions=[
                    "Check ETTOK_AGENT_KEY in .env",
                    "Regenerate the key on the platform — only its hash is stored, "
                    "so a lost key cannot be recovered",
                ],
            )
        except Exception as exc:
            # An unreachable platform is the expected case, not an exception worth
            # taking the cycle down for. The items stay Pending and go again next time.
            log.warning("Outbox drain failed", error=str(exc)[:200])

    async def _queue_scan_log(self) -> None:
        """Record what this pass looked at, including what it could not reach.

        The denominator lives only here. The platform never learns what was read and
        cleared, only what was flagged, so without `comments_scanned` hate density is
        uncomputable — a run flagging 4 items out of 8,420 comments reads as 100%
        rather than 0.05%. `coverage` carries what was *not* seen, because a report
        that cannot state its own gaps invites the objection that absence of evidence
        was treated as evidence of absence.
        """
        stats = self.last_collection
        if stats is None or not self.settings.ettok_base_url:
            return

        from src.ettok.outbox import enqueue
        from src.ettok.submit import build_scan_log
        from src.models.outbox import OutboxKind

        platforms = [p for p in (stats.per_platform or {}) if not p.startswith("_")]
        payload = build_scan_log(
            stats,
            duration_seconds=int(getattr(stats, "duration_seconds", 0) or 0),
            platforms=platforms,
        )
        await enqueue(self.session, OutboxKind.SCAN_LOG, payload)
        await self.session.commit()

    async def _run_learning(self) -> None:
        """Turn reviewed decisions into proposals for the curator.

        The worker existed and was never instantiated, so the one path by which human
        review could influence the agent's rules was open at both ends and connected
        in the middle to nothing. It proposes only — writing the local lexicon is
        refused inside the worker — so the failure mode here is a wasted cycle, not a
        classifier that rewrites its own rules.
        """
        from src.learning.learning_loop_worker import LearningLoopWorker

        try:
            await LearningLoopWorker(self.session).run_cycle()
        except Exception as exc:
            log.warning("Learning cycle failed", error=str(exc)[:200])

    async def run_cycle(self) -> None:
        """Run one full tick of the orchestration loop."""
        log.info("Starting orchestration cycle")
        
        # 0. Sync workers dynamically (T075)
        await self._sync_workers()
        
        # 1. Schedule Cases (T036)
        await self._schedule_cases()

        # 1b. Collect from due accounts — nothing else has anything to do without this
        await self._collect()

        # 1b. Fetch comments for what discovery found, so it can be classified. Without
        # this step the queue only ever fills; nothing moves an item to Classification.
        await self._process_discovery()

        # 2. Process Notifications (T064, T066)
        await self._handle_notifications()
        
        # 3. Queue Processing & Guardrails (T083)
        await self._process_queues()
        
        # 4. Content Expansion (T084)
        await self._evaluate_expansion()
        
        # 5. Overload Prevention (T085)
        await self._prevent_reviewer_overload()

        # 6. Autonomous Discovery (T070)
        await self.discovery.run_discovery()
        
        # 7. Check alerts (T073)
        await self._check_alerts()

        # 8. Trend detection and escalation
        await self._check_trends()

        # 8a. Record the pass, denominators included, before anything is sent.
        await self._queue_scan_log()

        # 8a2. Propose lexicon and trope changes from what reviewers decided.
        await self._run_learning()

        # 8b. Ship finished verdicts. After classification, so work done this
        # cycle goes out in this cycle rather than waiting for the next one.
        await self._drain_outbox()

        # 9. Dead man's switch — silent failure is how monitoring tools die unnoticed
        await self._check_liveness()
        
        log.info("Orchestration cycle complete")

    async def run_forever(self) -> None:
        """Run continuously."""
        # Anything still claimed belongs to a process that is no longer running —
        # this one has not started yet. QueueManager has had this recovery since the
        # beginning and nothing ever called it, so a kill mid-classification stranded
        # the item permanently.
        try:
            requeued = await self.queue_manager.requeue_inflight_on_restart()
            if requeued:
                log.info("Recovered items stranded by a previous run", count=requeued)
        except Exception as exc:  # recovery must never stop the agent starting
            log.warning("Could not recover stranded items", error=str(exc))

        while True:
            try:
                await self.run_cycle()
            except Exception as e:
                log.exception("Error in orchestration cycle", error=str(e))
            finally:
                await asyncio.sleep(self.settings.loop_interval_seconds)
