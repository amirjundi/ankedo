"""Hate-speech categories, in one place.

Previously hardcoded in four: the Ettok model's CHOICES, the committee's Pydantic
schema, the curator template, and the sheet validator. Adding a category meant four
edits across two repositories, so in practice nobody added one and the taxonomy stayed
smaller than the research.

**The list below comes from the Duhok survey's own Q10/Q12 taxonomy, not from a
generic hate-speech schema.** The counts are how many of 67 respondents named each
form, and they matter: the two most-reported forms — mockery (44) and questioning a
community's authenticity (43) — had no category at all under the original four. A
term that does not fit its category ends up filed as something else, and the reporting
then understates exactly what the research found most.

Adding one is a single edit here. Both the classifier schema and the curator tooling
read from this module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    slug: str
    en: str
    ar: str
    # Respondents out of 67 who named this form, where the survey asked.
    reported_by: int | None = None
    note: str = ""


CATEGORIES: tuple[Category, ...] = (
    Category("slur", "Direct insult or slur", "شتائم مباشرة", 35),
    Category(
        "mockery", "Mockery or ridicule", "استهزاء / سخرية", 44,
        "The most-reported form in the survey. Rarely uses a slur, so a "
        "keyword-only system misses it almost entirely.",
    ),
    Category(
        "authenticity_denial", "Questioning belonging or authenticity",
        "تشكيك في الانتماء أو الأصالة", 43,
        "'You are not really Iraqi', 'brought here by the British', 'not an "
        "authentic people'. Second-most reported.",
    ),
    Category(
        "collective_accusation", "Collective accusation", "اتهامات جماعية", 37,
        "Blaming a whole community for an act, or for problems generally.",
    ),
    Category(
        "disloyalty", "Accusation of treason or collaboration",
        "وصف جماعة بأنها خائنة أو عميلة", 28,
        "خيانة, الخونة, عملاء, اتباع الانكليز — frequent in the survey's own term list.",
    ),
    Category(
        "foreignness", "Described as foreign or intruders", "وصف جماعة بأنها دخيلة", 31,
        "دخلاء. Overlaps authenticity_denial; use this when the claim is about "
        "origin rather than legitimacy.",
    ),
    Category(
        "delegitimization", "Delegitimizing collective demands",
        "نزع الشرعية عن مطالب جماعية", 18,
        "Denying a community's right to political or cultural claims.",
    ),
    Category("dehumanization", "Dehumanization", "تجريد من الإنسانية", None,
             "Comparison to animals, vermin, filth, or evil."),
    Category("exclusion_call", "Calls for exclusion", "دعوات للإقصاء", 22,
             "Expulsion, denial of services, removal from an area."),
    Category("incitement", "Incitement to violence", "تحريض", 33,
             "Direct or indirect. حلال قتلهم is the clearest case in the data."),
    Category("threat", "Threat of harm", "تهديد", None),
    # Not hate speech. Present so a reviewer can label why an item was cleared —
    # and so those labels can be counted, since they are the false positives that
    # matter most.
    Category("counter_speech", "Refuting or condemning hate", "رفض الإساءة", None,
             "NOT hate speech. Someone quoting a libel in order to reject it."),
    Category("news_reporting", "News or documentation", "تغطية إخبارية", None,
             "NOT hate speech."),
    Category("none", "None", "لا ينطبق", None),
)

BY_SLUG: dict[str, Category] = {c.slug: c for c in CATEGORIES}

# Categories that describe hate. The rest explain why something was cleared.
HATEFUL = tuple(
    c.slug for c in CATEGORIES if c.slug not in ("counter_speech", "news_reporting", "none")
)

VISUAL_FORMS: tuple[Category, ...] = (
    Category("symbol_desecration", "Desecration of symbols, flags or sacred sites",
             "إساءة لرموز أو أعلام أو مقدسات", 40),
    Category("ai_generated", "AI-generated imagery", "صور مولَّدة بالذكاء الاصطناعي", 39,
             "Second-most reported visual form. Worth tracking separately — it is "
             "cheap to produce at volume."),
    Category("meme", "Offensive satirical meme", "ميمز ساخرة مسيئة", 34),
    Category("manipulated_photo", "Edited or fabricated photograph",
             "صور معدَّلة أو مفبركة", 28),
    Category("out_of_context", "Old or historical image used out of context",
             "صور خارج سياقها", 23),
    Category("caricature", "Offensive caricature", "رسوم كاريكاتورية", 18),
    Category("incitement_graphic", "Inciting poster or graphic",
             "ملصقات أو تصاميم تحريضية", 14),
    Category("deepfake", "Deepfake video", "فيديوهات مفبركة بعمق", 11),
)


def slugs() -> list[str]:
    return [c.slug for c in CATEGORIES]


def visual_slugs() -> list[str]:
    return [c.slug for c in VISUAL_FORMS]
