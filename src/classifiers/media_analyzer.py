"""Classify images and video frames directly, rather than only reading text off them.

Memes are the dominant vector for this kind of hate speech, and OCR loses most of what
makes them hateful. A picture pairing a community's religious symbol with vermin
imagery carries no text at all; a caption transcribed out of a meme reads as innocuous
without the image it sat on. `post_processor.py` previously wrote a literal
`"Stub OCR extracted text"` and nothing looked at pixels.

Same relational rule as the text classifier (FR-CL-2): the question is whether the
image expresses hostility toward the group the post concerns, not whether it is
offensive in the abstract. And the same asymmetry — a community's own religious
imagery, a news photograph, and a meme attacking that community can be visually
similar, so the target group in context does the work.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.committee.schemas import Category, Verdict
from src.classifiers.llm_client import LLMClient, LLMError
from src.core.settings import get_settings

log = structlog.get_logger()

PROMPT_VERSION = "media-v1"

# Cap on frames sampled from a video. Each is a multimodal call, and a long video
# would otherwise cost more than the rest of a post's classification combined.
MAX_FRAMES = 3


class MediaAnalysis(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    category: Category
    severity: int = Field(ge=0, le=4)
    # Text visible in the image. Replaces OCR — the model reads the words and sees
    # where they sit, which is usually what determines the meaning.
    visible_text: str | None = Field(
        default=None, description="Any text in the image, transcribed verbatim."
    )
    depicts_group: str | None = Field(
        default=None, description="Slug of the group depicted or referenced, if any."
    )
    imagery_description: str = Field(
        description="What is actually shown, in plain terms, for a reviewer who cannot see it."
    )
    rationale: str


SYSTEM = """You analyse images from social media for a human rights organisation \
monitoring hate speech against minority communities in Iraq (Yazidi, Christian, \
Shabak, Kaka'i, Sabian-Mandaean, Turkmen, Faili Kurd, Baha'i).

THE QUESTION YOU ANSWER

Given a post concerning group X, does this image express hostility, dehumanization, \
or a known hateful trope toward X — including through symbolism, juxtaposition, or \
visual coding?

Not "is this image offensive in the abstract". Context decides.

WHAT TO LOOK FOR

- text inside the image, which memes use to carry the payload — transcribe it verbatim
- dehumanizing juxtaposition: a community, or its symbols, placed beside animals, \
vermin, filth, or fire
- desecration of religious symbols, sites, or dress
- edited or manipulated photographs that ridicule or falsify
- symbols and gestures associated with groups that have attacked these communities

WHAT IS NOT HATE SPEECH

- a community's own religious imagery, festivals, dress, or symbols shown normally
- news photographs documenting violence, including graphic ones
- historical or memorial images, which look similar to attack imagery and are its \
opposite in intent
- images with no relation to any minority group

If the post concerns no minority group and the image carries no explicit hate, answer \
benign. If you are unsure, answer ambiguous — a human will decide.

Describe the imagery plainly. A reviewer may be working from your description rather \
than reopening the image, and may be avoiding the image deliberately."""


class MediaAnalyzer:
    """Multimodal classification of post media."""

    def __init__(self, session: AsyncSession, llm: LLMClient | None = None):
        self.session = session
        self.settings = get_settings()
        self.llm = llm or LLMClient(session)

    async def analyze(
        self,
        images: list[bytes],
        *,
        parent_post_text: str = "",
        target_groups: list[str] | None = None,
        case_id: str | None = None,
        post_id: str | None = None,
    ) -> dict | None:
        """Classify up to MAX_FRAMES images as a single item.

        Returns None when there is nothing to look at. Frames from one video are sent
        together so the model judges the sequence rather than isolated stills.
        """
        if not images:
            return None

        groups = ", ".join(target_groups or []) or "none detected"
        prompt = (
            f"POST TEXT (may be empty for an image-only post):\n{parent_post_text or '(none)'}\n\n"
            f"GROUP(S) THE POST CONCERNS: {groups}\n\n"
            f"IMAGES ATTACHED: {len(images[:MAX_FRAMES])}"
        )

        try:
            analysis = await self.llm.generate(
                model=self.settings.vision_model,
                prompt=prompt,
                schema=MediaAnalysis,
                purpose="media",
                prompt_version=PROMPT_VERSION,
                system_instruction=SYSTEM,
                images=images[:MAX_FRAMES],
                case_id=case_id,
                post_id=post_id,
            )
        except LLMError as exc:
            # Media failure must not lose the post: the text pipeline still runs, and
            # the gap is recorded rather than silently treated as "nothing found".
            log.warning("Media analysis failed", post_id=post_id, error=str(exc))
            return {"analysis_failed": True, "error": str(exc)[:300]}

        result = {
            "verdict": analysis.verdict.value,
            "confidence": analysis.confidence,
            "category": analysis.category.value,
            "severity": analysis.severity,
            "visible_text": analysis.visible_text,
            "depicts_group": analysis.depicts_group,
            "imagery_description": analysis.imagery_description,
            "rationale": analysis.rationale,
            "model": self.settings.vision_model,
            "prompt_version": PROMPT_VERSION,
            "images_analyzed": len(images[:MAX_FRAMES]),
        }
        log.info(
            "Media analyzed", post_id=post_id, verdict=result["verdict"], images=result["images_analyzed"]
        )
        return result


def merge_with_text(text_result: dict, media_result: dict | None) -> dict:
    """Combine text and media verdicts.

    The more severe verdict wins. An image-only meme attacking a community is hate
    speech regardless of an innocuous caption — and captions are frequently chosen to
    be innocuous precisely so the post reads as benign to a text classifier.
    """
    if not media_result or media_result.get("analysis_failed"):
        return text_result

    order = {"benign": 0, "ambiguous": 1, "hate": 2}
    text_rank = order.get(text_result["verdict"], 0)
    media_rank = order.get(media_result["verdict"], 0)

    if media_rank <= text_rank:
        merged = dict(text_result)
    else:
        merged = dict(text_result)
        merged["verdict"] = media_result["verdict"]
        merged["hate_speech_flag"] = media_result["verdict"] == "hate"
        merged["confidence"] = media_result["confidence"]
        merged["classification_score"] = (
            media_result["confidence"] if media_result["verdict"] == "hate" else 0.0
        )
        merged["category"] = media_result["category"]
        merged["severity"] = max(text_result.get("severity", 0), media_result["severity"])
        merged["decided_by"] = "media"

    merged.setdefault("decided_by", "text")
    merged["media_analysis"] = media_result
    return merged
