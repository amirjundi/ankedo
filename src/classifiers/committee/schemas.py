"""Structured output schemas for the committee.

Parsed rather than free text, so a malformed answer fails loudly instead of being
misread. Field descriptions are part of the prompt the model sees — they are
instructions, not documentation, and are worded accordingly.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from src.classifiers.categories import CATEGORIES


class Verdict(str, Enum):
    HATE = "hate"
    BENIGN = "benign"
    # A real and useful answer. Coded speech can be sincere, and FR-CL-11 routes
    # genuine ambiguity to a human rather than forcing a confident guess.
    AMBIGUOUS = "ambiguous"


# Built from src/classifiers/categories.py, which holds the taxonomy the Duhok
# research actually produced. Adding a category is one edit there, not here.
Category = Enum("Category", {c.slug.upper(): c.slug for c in CATEGORIES}, type=str)
Category.__doc__ = (
    "What kind of hate speech this is. Includes the non-hateful labels "
    "counter_speech and news_reporting, so a cleared item still records why."
)


class TriageDecision(BaseModel):
    requires_specialist: bool = Field(
        description=(
            "True if this comment could plausibly express hostility toward the group "
            "the post concerns, including indirectly. When unsure, answer true — a "
            "wrong false here means the item is never examined again."
        )
    )
    rationale: str = Field(description="One sentence explaining the decision.")


class SpecialistDecision(BaseModel):
    verdict: Verdict
    confidence: float = Field(
        ge=0.0, le=1.0, description="0-1 confidence in the verdict."
    )
    category: Category
    target_group: str | None = Field(
        default=None, description="Slug of the group targeted, or null."
    )
    severity: int = Field(ge=0, le=4, description="0 none, 4 most severe.")
    relies_on_context: bool = Field(
        description=(
            "True if the comment reads as benign in isolation and is hostile only "
            "because of what it replies to."
        )
    )
    rationale: str = Field(
        description="Explain by reference to the parent post, not the comment alone."
    )


class CriticDecision(BaseModel):
    agrees_with_specialist: bool
    concern: str | None = Field(
        default=None,
        description="If disagreeing, what the specialist got wrong. Null if agreeing.",
    )
    suggested_verdict: Verdict | None = Field(
        default=None, description="Your verdict if you disagree, else null."
    )
    rationale: str


class TargetGroupDetection(BaseModel):
    target_groups: list[str] = Field(
        default_factory=list,
        description="Slugs of minority groups this post concerns. Empty if none.",
    )
    rationale: str
