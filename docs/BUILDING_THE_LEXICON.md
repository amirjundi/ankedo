# Building the Hate-Speech Lexicon

How to turn field data into something the agent can use. Written after reading both
files in `Ettok.net/HateSpeechData/`.

---

## Short answer on format

**Your raw files are the right shape for collection and the wrong shape for the
system.** Keep collecting exactly as you are. Add a curation step between.

```
FIELD DATA (xlsx)          CURATED (yaml)              SYSTEM (database)
what people reported   →   what a human confirmed  →   what the agent matches
     ↑ never edit              ↑ this is the work         ↑ generated, never hand-edited
```

Three stages exist because the xlsx contains things that cannot be matched against
text, and dropping them would lose the most valuable material. More on that below.

---

## What is actually in your two files

### `تفريغ جلسة حوارية لليزيديين.xlsx` — Yazidi focus group

76 rows, two columns: `المصطلح` (term) and `السياق` (context).

**Column A mixes two different kinds of thing**, and they belong in different places:

| Row | Column A | What it is |
|---|---|---|
| 2 | `سبايا/سبيا` | an **utterance** — a word people type |
| 3 | `عبدة الشيطان` | an **utterance** |
| 4 | `التمييز بالإجازات في الدوام` | a **pattern** — "discrimination over holiday leave". Nobody types this |
| 10 | `الحكم/التعميم على كل اليزيديين بناءان على فرد يزيدي واحد` | a **pattern** — collective blame from one individual |

Roughly 40% of column A describes a pattern rather than quoting one. **This is not a
defect in the data — it is the most valuable part of it.** Patterns become *tropes*;
utterances become *lexicon terms*. A system with only the utterances would miss most
of what your analysts documented.

Column B often rescues an utterance from a pattern row. Row 4's description is
unmatchable, but its context — `ليش يعطون لليزيديين عطلة لصيامهم` — is a real sentence.
**Always read column B before discarding a row.**

### `استبيان_خطاب_الكراهية_الرقمي...xlsx` — Duhok KoBo survey

67 responses, 261 columns. Almost all are demographics and multiple choice. **Two
columns carry the lexicon material:**

| Column | Question | Answered |
|---|---|---|
| `CT` | يرجى ذكر الكلمة أو التعبير | 31/67 |
| `BE` | 10.5 يرجى وصف صورة أو محتوى بصري مسيء | 40/67 |

`BE` is visual-content description and feeds **image tropes**, not text terms. The
agent classifies images directly, so those descriptions are training material for what
to look for — e.g. *"فيديو لشخص كردي كال يتبول على قبر وعلى القبر كان يوجد صليب"*
(desecration of a Christian grave) is a visual trope with no text at all.

---

## Four things the data shows that change the design

**1. This is not a Yazidi-only lexicon.** The survey names at least four target
groups: Assyrian/Christian (`نساطرة`, `نسطوري`, `نصرانيين`, `الاشورين`), Yazidi
(`الايزيدين`), and — notably — **Kurds themselves** (`الكورد هم غجر الفرس`,
`المحتلين الكورد`, `قوم من الجن`). Every group can be both target and source. The
taxonomy must not assume otherwise.

**2. The same trope attacks multiple groups.** `عبدة الشيطان` and
`اعوذ بالله من الشيطان الرجيم` appear in the survey attributed against **both** Yazidis
and Christians. This is why lexicon and trope entries link to *many* groups, not one.

**3. Kurdish-script terms are already present.** One response gives
`شەیتان، شەیتانۆک، پیس، گەنی، قەومێ پیس، شەیتان پەریس` — Sorani. The lexicon is
bilingual from day one, and the normalizer deliberately preserves Kurdish hamza
carriers (ئ ێ ۆ ڕ) because `ئێزیدی` is a word, not a misspelling.

**4. Respondents typo, and the typos are real data.** The survey contains
`الشبطان` (for `الشيطان`), `بالبه` (for `بالله`), and four spellings of one term:
`نساطرة` / `نسطوري` / `نسطوريين` / `نصاطره`. **Do not "correct" these away.** People
type them that way online, so they belong in `variants`. The normalizer folds
orthographic variation automatically; it does not fold genuine misspellings.

---

## The curation step

Two files to produce, both in `packs/iraq-minorities/`.

### Rule 1 — every entry needs a source

`ankedo pack verify` rejects a lexicon entry without one. Provenance is what lets a
report survive challenge: "this term came from the Duhok focus group, row 3" is
defensible; an unsourced word list is not.

### Rule 2 — a term is only a term if someone types it

If it cannot appear verbatim in a comment, it is a trope. Test: *could I paste this
into a search box and find real posts?*

- `عبدة الشيطان` → term ✓
- `التمييز بالإجازات في الدوام` → trope ✓

### Rule 3 — every trope needs a benign counterpart

**This is the rule that decides whether the system is usable.** A trope with only
positive examples over-fires. `اعوذ بالله من الشيطان الرجيم` is ordinary devout speech
on almost every post in Iraq; it is a libel only on Yazidi-related content.

Ship it without the benign half and the system flags the prayers of the community it
exists to protect — which is worse than missing hate, because the harm is ours.

`ankedo pack verify` **fails** a trope with an empty `negative_examples`.

### Rule 4 — collect counter-speech deliberately

Someone writing `الإيزيديون ليسوا عبدة الشيطان، هذا افتراء` is *defending* the
community using the exact words of the libel. Flagging them is the most damaging false
positive this system can produce. Put these in `counter_speech_examples`.

---

## File formats

### `lexicon.yaml` — words people type

```yaml
entries:
  - term: "عبدة الشيطان"
    target_groups: [yazidi, christian-iraqi]   # both, per the survey
    dialect: [iraqi, msa]
    script: [arabic]
    is_explicit: true          # flags regardless of what the post is about
    severity: 4
    variants: ["عبده شيطان", "عبدة الشبطان"]   # real spellings from the data
    never_flag_when: [news_quotation, academic, counter_speech]
    source: "duhok-focus-group-row-3"
    added_by: "<curator>"

  # A term aimed at everyone equally uses scope instead of a group list.
  - term: "حلال قتلهم"
    scope: universal
    is_explicit: true
    severity: 4                # direct incitement — the most severe thing in your data
    source: "duhok-survey-row-54"
```

**`is_explicit` is the field that matters most.**

- `true` → flags regardless of the post's topic (FR-CL-4). Use for unambiguous slurs.
- `false` → only counts when the group is present in context. Raises review priority;
  never flags alone.

When unsure, use `false`. A false positive silences someone; a raised priority costs a
reviewer thirty seconds.

**`scope: universal`** must be written explicitly. An entry with no groups listed is
treated as an error, not as "applies to everyone" — that default would let one bad row
flag everything.

### `tropes.yaml` — patterns, and context-dependent phrases

```yaml
entries:
  - trope_id: yazidi-devil-worship
    target_groups: [yazidi, christian-iraqi]
    surface_forms:
      - text: "اعوذ بالله من الشيطان الرجيم"
        register: religious_formula     # benign alone — needs the pair
      - text: "عبدة الشيطان"
        register: direct_epithet
    activation:
      requires_target_group: true       # never fires without group context
      post_topic_any: [yazidi, sinjar, lalish]
      negation_cancels: true            # "they are NOT devil worshippers" must not fire
    implicature: >
      Invokes the devil-worship libel — a defamation historically used to justify
      violence, including the 2014 Sinjar genocide.
    severity: 4

    positive_examples:
      - comment_text: "اعوذ بالله من الشيطان الرجيم"
        parent_post_text: "مراسم دينية إيزيدية في معبد لالش"
        why: "the formula is aimed at Yazidi religious practice"

    negative_examples:            # REQUIRED — verify fails without these
      - comment_text: "اعوذ بالله من الشيطان الرجيم"
        parent_post_text: "شاهدوا هذا الثعبان الضخم في الحديقة"
        why: "identical text, unrelated post, no target group in context"

    counter_speech_examples:
      - comment_text: "الإيزيديون ليسوا عبدة الشيطان، هذا افتراء"
        why: "quotes the libel to refute it — must never flag"

    confirmed_in_cases: []
    source: "duhok-focus-group-row-3"
```

**Pattern tropes** — the 40% of column A that is not an utterance — use the same shape
with no `surface_forms`. The model is asked to recognise the pattern rather than match
a string:

```yaml
  - trope_id: collective-blame-from-one-individual
    target_groups: [yazidi, christian-iraqi, kurdish]
    surface_forms: []               # nothing literal to match
    implicature: >
      Treating one member's act or statement as proof of a trait shared by the whole
      community.
    activation:
      requires_target_group: true
    positive_examples:
      - comment_text: "كل اليزيديين عنصريين لان واحد يزيدي طلع بفيديو وحجا على المسلمين"
        why: "one individual's video generalised to the whole community"
    negative_examples:
      - comment_text: "هذا الشخص تصرف بشكل عنصري"
        why: "criticises an individual without generalising to the group"
    source: "duhok-focus-group-row-10"
```

**Visual tropes** from survey column `BE` need no text at all:

```yaml
  - trope_id: christian-grave-desecration
    target_groups: [christian-iraqi]
    surface_forms: []
    is_visual: true
    implicature: "Desecration of Christian graves or the cross, filmed or photographed."
    positive_examples:
      - imagery: "person urinating on a grave marked with a cross"
        why: "desecration of a religious symbol, recorded to circulate"
    negative_examples:
      - imagery: "news photograph documenting damage to a cemetery"
        why: "documents desecration rather than celebrating it — opposite intent"
    source: "duhok-survey-row-BE"
```

### `gold_eval.jsonl` — how accuracy is measured

One JSON object per line. **Each context-dependent trope needs its minimal pair here,
in both directions.**

```json
{"id": "gold-0001", "comment_text": "اعوذ بالله من الشيطان الرجيم", "parent_post_text": "مراسم دينية إيزيدية في معبد لالش", "target_group": "yazidi", "dialect": "iraqi", "label": "hate", "category": "dehumanization", "severity": 4, "annotators": [{"id": "a1", "label": "hate"}, {"id": "a2", "label": "hate"}], "hard_case": true, "why": "trope fires only because the parent post concerns Yazidis", "source": "duhok-focus-group"}
{"id": "gold-0002", "comment_text": "اعوذ بالله من الشيطان الرجيم", "parent_post_text": "شاهدوا هذا الثعبان الضخم في الحديقة", "target_group": null, "label": "benign", "severity": 0, "annotators": [{"id": "a1", "label": "benign"}, {"id": "a2", "label": "benign"}], "hard_case": true, "why": "identical text, no target group in context", "source": "duhok-focus-group"}
```

`label` is `hate` | `benign` | `ambiguous`. **`ambiguous` is a real answer** — coded
speech can be sincere, and the system routes genuine ambiguity to a human. It is not
scored as an error in either direction.

**`annotators` needs two entries on a held-out slice.** Two people labelling the same
item independently is what produces Cohen's κ. If κ < 0.6, the *definition* is broken,
not the model, and more data will not fix it.

---

## Workflow

```bash
# 1. Curate by hand into packs/iraq-minorities/*.yaml — this is the real work
# 2. Check structure before it touches anything
ankedo pack verify

# 3. Load
ankedo pack install
ankedo eval load

# 4. Check the labelling holds up between annotators
ankedo eval kappa

# 5. Measure — per target group, never in aggregate
ankedo eval run
```

`eval run` reports precision and recall **per group**, and every group must clear the
bar. An average lets strong performance on one community hide failure on another —
which is exactly the harm this project exists to prevent.

---

## How much data

| Unit | Minimum | Why |
|---|---|---|
| Labelled items per target group | 300–500 | below this, per-group precision has confidence intervals too wide to act on |
| Minimal pairs per context-dependent trope | 30–50 | enough to show the model where the boundary sits |
| Double-annotated items per group | ≥100 | enough for a stable κ |
| Negative-to-positive ratio | at least 1:1, ideally 1:2 | the ratio that stops over-flagging |

**A group with 20 examples has no measurable precision.** Cover three groups properly
rather than eight badly. Your survey data supports starting with **Yazidi** and
**Assyrian/Christian** — those have the most material.

---

## What not to do

**Don't aim for "99% accurate."** Trained annotators agree on hate speech at roughly
κ = 0.6–0.8. When experts disagree on one item in four, there is no 99%-clean ground
truth to hit. The real target is an operating point: high recall into the review queue,
high precision on auto-flag, ambiguity to a human.

**Don't measure accuracy.** If 2% of comments are hateful, a classifier answering
"benign" every time scores 98% and is worthless. Precision and recall, per group.

**Don't build from generic word lists.** Public Arabic corpora (OSACT-5, ADHAR,
L-HSAB, AraSafe) are useful for dialect breadth and explicit-slur coverage, but contain
**none** of the Iraqi minority coded speech that makes this project worth doing. Check
their licences before putting anything from them in a pack.

**Don't let the agent write its own lexicon.** It proposes; a curator accepts. An agent
learning from its own outputs drifts: it flags a term, adds it, flags more, adds more —
and within weeks the dictionary is its own errors amplified.

---

## Where the files live

| File | Purpose | Edited by |
|---|---|---|
| `Ettok.net/HateSpeechData/*.xlsx` | raw field data | nobody — archive it |
| `packs/iraq-minorities/lexicon.yaml` | curated terms | curators |
| `packs/iraq-minorities/tropes.yaml` | curated patterns | curators |
| `packs/iraq-minorities/gold_eval.jsonl` | accuracy measurement | annotators |
| Ettok dashboard | live lexicon | curators, day to day |

The platform is the source of truth once running — `ankedo platform sync-lexicon`
pulls it down each run, so one dashboard edit reaches every agent. The YAML files are
for bootstrapping and for offline work.

---

## Open questions for the domain experts

1. **Which groups are in scope for v1?** The survey names Yazidi, Assyrian/Christian,
   and Kurds-as-target. Pick two or three and cover them properly.
2. **What is the severity scale?** Currently 0–4 and configurable. `حلال قتلهم` (direct
   incitement to kill) and `نسطوري` (a slur) are clearly not the same level — where are
   the boundaries, and which levels escalate automatically?
3. **Who are the annotators, and can two of them label the same 100 items?** Without
   that, κ is uncomputable and the gold set cannot be trusted as ground truth.
