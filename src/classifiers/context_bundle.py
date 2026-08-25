"""Build the context bundle — the actual input to classification.

SRS §4.4.0: the unit of classification is not the comment text, it is the comment
together with its context. Much hate speech in this domain carries no hateful surface
text; the hostility exists only in relation to what the comment replies to and which
group that content concerns.

The worked example: `اعوذ بالله من الشيطان الرجيم` is a routine pious phrase. On a post
about Yazidis it invokes the devil-worship trope. Identical text, opposite verdict.
Any design that judges the comment alone either flags all devout speech, or misses
the attack.

Target group resolution follows FR-CL-3's two signals:
  * case-supplied (strong) — an item crawled under a registered case inherits it
  * detected — a lightweight pass over the parent post, so hate is caught in threads
    not yet registered as a case
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.group_resolver import GroupResolver
from src.models.case import Case
from src.models.comment import Comment
from src.models.post import Post

log = structlog.get_logger()


@dataclass
class ContextBundle:
    """FR-CL-1 classifier input."""

    comment_text: str
    parent_post_text: str
    target_groups: list[str] = field(default_factory=list)
    target_group_source: str | None = None  # "case" | "detected" | None
    thread_context: list[str] = field(default_factory=list)
    dialect: str | None = None
    post_url: str | None = None
    platform: str | None = None
    case_id: str | None = None
    # Set when the post is image-only or media carried the message.
    media_text: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def as_prompt_context(self) -> str:
        """Render for a prompt.

        The parent post comes first and is labelled, because the relational question
        (FR-CL-2) is meaningless if the model reads the comment first and forms a
        judgement before seeing what it replies to.
        """
        groups = ", ".join(self.target_groups) if self.target_groups else "none detected"
        lines = [
            f"PARENT POST (what the comment replies to):\n{self.parent_post_text or '(no text)'}",
        ]
        if self.media_text:
            lines.append(f"TEXT EXTRACTED FROM POST MEDIA:\n{self.media_text}")
        lines.append(f"GROUP(S) THE POST CONCERNS: {groups}")
        if self.thread_context:
            joined = "\n".join(f"- {c}" for c in self.thread_context)
            lines.append(f"EARLIER COMMENTS IN THE THREAD:\n{joined}")
        if self.dialect:
            lines.append(f"DIALECT: {self.dialect}")
        lines.append(f"COMMENT UNDER ASSESSMENT:\n{self.comment_text}")
        return "\n\n".join(lines)


async def build_bundle(
    session: AsyncSession,
    post: Post,
    comment: Comment | None = None,
    *,
    thread_limit: int = 5,
) -> ContextBundle:
    """Assemble the bundle for a post, or for one comment on it."""
    post_text = post.content_text or ""
    media_text = post.ocr_text or None

    target_groups: list[str] = []
    source: str | None = None

    # FR-CL-3: the case-supplied group is the strong signal and wins.
    if post.case_id:
        case = (
            await session.execute(select(Case).where(Case.id == post.case_id))
        ).scalar_one_or_none()
        if case and case.target_group:
            target_groups = [case.target_group.slug]
            source = "case"

    if not target_groups:
        resolver = GroupResolver(session)
        detected = await resolver.resolve_all(f"{post_text}\n{media_text or ''}")
        if detected:
            target_groups = detected
            source = "detected"

    # Persist what we resolved so the post carries its own provenance.
    if target_groups and not post.target_group_id:
        group = await GroupResolver(session).get(target_groups[0])
        if group:
            post.target_group_id = group.id
            post.target_group_source = source

    thread_context: list[str] = []
    if comment is not None:
        stmt = (
            select(Comment)
            .where(Comment.post_id == post.id, Comment.id != comment.id)
            .order_by(Comment.created_at)
            .limit(thread_limit)
        )
        thread_context = [c.text for c in (await session.execute(stmt)).scalars() if c.text]

    return ContextBundle(
        comment_text=(comment.text if comment else post_text) or "",
        parent_post_text=post_text if comment else "",
        target_groups=target_groups,
        target_group_source=source,
        thread_context=thread_context,
        dialect=post.dialect,
        post_url=post.url,
        platform=post.platform,
        case_id=post.case_id,
        media_text=media_text,
    )
