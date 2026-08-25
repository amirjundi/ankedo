# Lexicon Data Shape — VPS to Agent

The exact shape at every hop: what a curator fills in, what the VPS stores, what
crosses the wire, and what the agent matches with.

Companion to `BUILDING_THE_LEXICON.md`, which covers *how to decide* what goes in.
This covers *what it looks like*.

---

## The four hops

```
1. CURATION FILE          packs/iraq-minorities/lexicon.yaml
   yaml, in git                    │  ankedo pack install  (bootstrap only)
                                   ▼
2. VPS DATABASE           Postgres on the Ettok VPS
   the source of truth     hate_speech_lexicon / hate_speech_tropes
                                   │  GET /api/hermes/lexicon/
                                   ▼
3. WIRE                   JSON over HTTPS, bearer auth
   the contract                    │  ankedo platform sync-lexicon
                                   ▼
4. AGENT CACHE            SQLite on the agent PC
   disposable              lexicon_entries / trope_entries
```

**Hop 2 is the source of truth.** Curators work in the Ettok dashboard day to day; one
edit reaches every agent on its next sync. Hop 1 is for bootstrapping and offline work.
Hop 4 is a cache — deleting it loses nothing.

Why the source of truth is on the VPS and not the agent: the agent runs on a PC on
residential WiFi. A stolen or reimaged machine must leak nothing that is not already on
the server.

---

## Hop 2 — What lives on the VPS

### `hate_speech_lexicon` — words people type

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | the agent keys its cache on this |
| `term` | varchar(500) | the surface form, exactly as typed online |
| `language` | varchar(5) | `ar` \| `ku` |
| `category` | varchar(50) | `slur` \| `threat` \| `dehumanization` \| `incitement` |
| `target_group` | varchar(100) | **free text today — see the caveat below** |
| `severity_weight` | smallint | 1–10 |
| `is_regex` | bool | matched case-insensitively when true |
| `is_active` | bool | soft delete; inactive terms stop being served |
| `notes` | text | provenance — which transcript row or survey response |
| `created_by` | FK user | who added it |
| `created_at` | datetime | |

**Fields that need adding** (agreed with the Ettok session, pending the §7 migration):

| Column | Why |
|---|---|
| `is_explicit` | bool. `true` flags regardless of the post's topic; `false` only counts when the target group is present. **The single most important field** — see below |
| `variants` | JSON list. Real misspellings and obfuscations, e.g. `["عبده شيطان", "عبدة الشبطان"]` |
| `never_flag_when` | JSON list. `[news_quotation, academic, counter_speech]` |
| `pack_version` | varchar. Fills the version each classification records (FR-CL-14) |
| `target_groups` | M2M to a `TargetGroup` table, replacing the free-text column |

### `hate_speech_tropes` — patterns and context-dependent phrases

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(200) | human label, e.g. "Devil-worship libel" |
| `description` | text | what the classifier should look for |
| `example` | text | an utterance attested in the field data |
| `target_group` | varchar(100) | free text today |
| `is_visual` | bool | pattern appears in images; a text pass cannot see it |
| `severity_weight` | smallint | 1–10 |
| `is_active` | bool | |
| `requires_target_group` | bool | **defaults true** |
| `activation_topics` | JSON list | topics in the *parent post* that satisfy activation |
| `surface_forms` | JSON list | literal strings worth matching before the LLM is asked |
| `negation_cancels` | bool | suppresses counter-speech |
| `negative_examples` | JSON list | **the benign half of the minimal pair** |

### The one caveat that matters

**`target_group` is a free-text `varchar` on both tables.** So `Yazidi`, `yazidi`, and
`الإيزيديين` are three different groups, and a trope registered against one will never
fire for content tagged another — with no error anywhere.

The interim mitigation is `target_group_slug`, added by the Ettok session:
`slugify(value, allow_unicode=True)`, served on every entry. It folds case and spacing,
so `Yazidi` → `yazidi`. It does **not** fold across languages: `الإيزيديين` still lands
on its own slug.

The agent reports unresolved values rather than dropping them, so
`ankedo platform sync-lexicon` prints exactly which of your `target_group` values do
not map. **Run it once and that output is the work list for the proper migration.**

---

## Hop 3 — What crosses the wire

`GET /api/hermes/lexicon/` — optional repeated `?language=ar&language=ku`.

```json
{
  "terms": [
    {
      "id": 12,
      "term": "عبدة الشيطان",
      "language": "ar",
      "category": "dehumanization",
      "target_group": "Yazidi",
      "target_group_slug": "yazidi",
      "severity_weight": 8,
      "is_regex": false
    }
  ],
  "tropes": [ /* same array GET tropes/ returns — one call fetches both */ ],
  "total": 1
}
```

`GET /api/hermes/tropes/` — optional repeated `?target_group=yazidi`.

```json
{
  "tropes": [
    {
      "id": 4,
      "name": "Devil-worship libel",
      "description": "Invokes the devil-worship libel against Yazidis.",
      "example": "اعوذ بالله من الشيطان الرجيم",
      "target_group": "Yazidi",
      "target_group_slug": "yazidi",
      "requires_target_group": true,
      "activation_topics": ["yazidi", "sinjar", "lalish"],
      "surface_forms": ["اعوذ بالله من الشيطان الرجيم", "عبدة الشيطان"],
      "negation_cancels": true,
      "negative_examples": ["اعوذ بالله من الشيطان الرجيم — on an unrelated post"],
      "severity_weight": 8,
      "is_visual": false
    }
  ],
  "total": 1
}
```

Auth on both, per `AGENT_CONTRACT.md`:

```
Authorization: Bearer <agent key>
X-Agent-Id: ankedo-local-01
```

`401` unknown/revoked key, `403` valid key without the `hate_speech_scan` scope. The
agent stops and alerts on either — it never retries, because retrying a revoked key
cannot succeed and looks hostile.

---

## Hop 4 — What the agent does with it

`ankedo platform sync-lexicon` and `sync-tropes` reconcile into local SQLite:

- **Keyed on `id`** as `platform_id`, so re-syncing updates rather than duplicates.
- **Terms you stop returning are deactivated, not deleted** — a bad sync stays
  recoverable and the local audit trail survives.
- **Uncompilable regexes are skipped**, never fatal to the run.
- **Unresolved `target_group` values are counted and reported**, never silently dropped.
- The lexicon compiles once into a cached token-boundary matcher, invalidated by a
  fingerprint of the table.

Freshness: `lexicon_max_stale_hours` defaults to 24. A residential connection dropping
for an hour must not mean an hour of unmonitored content, and the lexicon changes on
the order of days. Every submission is stamped with the lexicon version actually used,
so the platform can reconstruct what was judged against what. Set it to `0` for strict
per-run semantics.

---

## The two fields that decide whether this works

Everything else is plumbing. These two decide whether the system is usable.

### `is_explicit` on a lexicon term

```
is_explicit: true   →  flags regardless of what the post is about
is_explicit: false  →  only counts when the target group is in context;
                       raises review priority, never flags alone
```

Use `true` only for terms that are abuse in any context. Use `false` for anything whose
meaning depends on who the post is about.

**When unsure, use `false`.** A wrong `true` silences someone; a wrong `false` costs a
reviewer thirty seconds.

### `activation_topics` + `requires_target_group` on a trope

This is the mechanism the whole system turns on.

```
requires_target_group: true, activation_topics: [yazidi, sinjar, lalish]
```

means: fire **only** when the parent post concerns one of those. On any other post the
surface form is a *candidate* — it raises review priority and does not flag.

**An empty `activation_topics` means "no gate configured yet", never "always active."**
The agent already reads it that way, and there is a test pinning it. If it were read
permissively, `اعوذ بالله من الشيطان الرجيم` would flag every devout comment in Iraq.

Your 19 tropes currently ship with `activation_topics: []` and
`negative_examples: []` — schema in place, content pending. Until a curator fills them,
those tropes surface candidates and never flag. That is the correct behaviour for
unfinished data, and `ankedo platform sync-tropes` prints one warning per unbackfilled
trope so it stays visible rather than looking finished.

---

## Worked example, end to end

A curator adds one term in the Ettok dashboard:

**1. Dashboard form**
```
term:            عبدة الشيطان
language:        ar
category:        dehumanization
target_group:    Yazidi
severity_weight: 8
is_explicit:     ✓
notes:           duhok-focus-group row 3 — most reported slur against Yazidis
```

**2. VPS row**
```
id=12  term='عبدة الشيطان'  language='ar'  category='dehumanization'
target_group='Yazidi'  severity_weight=8  is_active=true
```

**3. Wire, on the agent's next run**
```json
{"id": 12, "term": "عبدة الشيطان", "language": "ar",
 "category": "dehumanization", "target_group": "Yazidi",
 "target_group_slug": "yazidi", "severity_weight": 8, "is_regex": false}
```

**4. Agent cache**
```
platform_id=12  term='عبدة الشيطان'  target_groups=[yazidi]
is_explicit=true  severity=8  source='ettok:12'  pack_source='ettok-platform'
```

Normalized once into the matcher, so `عبده شيطان` and `عبدة الشبطان` both hit the same
entry. Every classification that matches it records `lexicon_version`, which is what
makes the verdict reproducible months later.

---

## Column-by-column: your xlsx → the database tables

**Yes — what you extract has to land in these columns.** The mapping is not one-to-one,
and the places it isn't are where the judgement happens.

### `تفريغ جلسة حوارية لليزيديين.xlsx` (76 rows)

| xlsx | → table | → column | Notes |
|---|---|---|---|
| `A` المصطلح, **when it is an utterance** | `hate_speech_lexicon` | `term` | e.g. `سبايا/سبيا`, `عبدة الشيطان`, `ايزيديو` |
| `A` المصطلح, **when it describes a pattern** | `hate_speech_tropes` | `description` | e.g. `التمييز بالإجازات في الدوام` — nobody types this |
| `B` السياق, on a **term** row | `hate_speech_lexicon` | `notes` | the usage example, useful to a reviewer |
| `B` السياق, on a **pattern** row | `hate_speech_tropes` | `example` | **often the real utterance** — read before discarding |
| — | both | `target_group` | `yazidi` for this whole file |
| — | both | `language` | `ar` (a few rows are Kurdish — check per row) |
| — | both | `notes` | `duhok-focus-group-row-<N>` |
| **you decide** | `hate_speech_lexicon` | `category` | slur / threat / dehumanization / incitement |
| **you decide** | `hate_speech_lexicon` | `severity_weight` | 1–10 |
| **you decide** | `hate_speech_lexicon` | `is_explicit` | see the two-fields section above |
| **you decide** | `hate_speech_tropes` | `activation_topics` | `[yazidi, sinjar, lalish]` |
| **you decide** | `hate_speech_tropes` | `negative_examples` | the benign counterpart — required |

The split on column A is the whole job. A row goes to `lexicon` if you could paste it
into a search box and find real posts; otherwise it goes to `tropes`.

Worked examples from your actual rows:

```
Row 3:  A=عبدة الشيطان              B=اليزيديون يعبدنون الشيطآن
        → lexicon.term = عبدة الشيطان
          lexicon.notes = duhok-focus-group-row-3 | اليزيديون يعبدنون الشيطآن

Row 4:  A=التمييز بالإجازات في الدوام   B=ليش يعطون لليزيديين عطلة لصيامهم
        → tropes.description = discrimination over holiday leave for Yazidi fasting
          tropes.example    = ليش يعطون لليزيديين عطلة لصيامهم     ← rescued from B
          tropes.surface_forms = []          (nothing literal to match)

Row 10: A=الحكم/التعميم على كل اليزيديين بناءان على فرد يزيدي واحد
        B=اي كل اليزيديين عنصريين لان واحد يزيدي طلع بفيديو
        → tropes.description = collective blame from one individual
          tropes.example     = اي كل اليزيديين عنصريين لان واحد يزيدي طلع بفيديو
```

### `استبيان_خطاب_الكراهية_الرقمي...xlsx` (67 responses, 261 columns)

Only two columns carry lexicon material. Everything else is demographics and multiple
choice — useful for the report, not for the lexicon.

| xlsx | → table | → column | Notes |
|---|---|---|---|
| `CT` يرجى ذكر الكلمة أو التعبير | `hate_speech_lexicon` | `term` | **split the cell first** — see below |
| `BE` وصف صورة أو محتوى بصري مسيء | `hate_speech_tropes` | `description` + `is_visual=true` | image/meme patterns, no text to match |
| — | both | `notes` | `duhok-survey-row-<N>` |

**Column `CT` needs splitting.** One cell routinely holds several terms, separated
inconsistently — `،` `,` `/` `_` and newlines all appear:

```
Row 1:  ناسطوري، عبدة الشبطان، اعوذ بالبه من الشيطان الرجيم، نصرانيين
        → 4 lexicon rows, target_group = christian-iraqi
        → note the typos: الشبطان (for الشيطان), بالبه (for بالله)
          those go in `variants`, NOT corrected away — people type them that way

Row 3:  الاشورين نساطرة/الاشورين ليسو شعوب اصيلة/الاشورين دخلاء من قبل البريطانيين
        → these are CLAIMS, not terms → hate_speech_tropes
          "Assyrians are not an authentic people / were brought by the British"

Row 51: شەیتان، شيطان ، شەیتانۆک ، پیس ، گەنی، قەومێ پیس، شەیتان پەریس
        → 7 lexicon rows, language = ku  ← Kurdish, do not file as Arabic

Row 54: حلال قتلهم
        → lexicon.term, category = incitement, severity_weight = 10
          the most severe item in your data — direct incitement to kill
```

**Target group is not uniform in this file.** Unlike the focus group, the survey covers
several communities and you must read each response to assign it:

| Terms seen | `target_group` |
|---|---|
| `نساطرة`, `نسطوري`, `نصرانيين`, `الاشورين` | `christian-iraqi` |
| `عبدة الشيطان`, `الايزيدين`, `طاووس ملك` | `yazidi` |
| `الكورد هم غجر الفرس`, `المحتلين الكورد`, `قوم من الجن` | `kurdish` |

**One term, several groups.** `عبدة الشيطان` and `اعوذ بالله من الشيطان الرجيم` appear
in this survey used against **both** Yazidis and Christians. Create **one** entry linked
to both groups — not two entries. That is what the many-to-many link exists for, and
duplicating instead means a later edit fixes one copy and silently leaves the other.

### What has no xlsx source and must be authored

These four cannot be extracted from your files, and three of them are what make the
system work:

| Column | Why it isn't in the data |
|---|---|
| `is_explicit` | a judgement about how context-dependent the term is |
| `activation_topics` | the field data records *what* was said, not *when it counts* |
| `negative_examples` | nobody writes down the benign case — it is unremarkable |
| `severity_weight` | requires a scale nobody has defined yet (SRS §7 Q2) |

`negative_examples` is the one to plan time for. Every context-dependent trope needs
the same phrase in a harmless setting, and those have to be found or written
deliberately. Without them the trope over-fires on ordinary speech.

---

## For non-technical staff: the Excel workbook

Curators should not be asked to write YAML. `docs/lexicon_data_entry_template.xlsx`
is a data-entry form whose columns map one-to-one onto the database tables.

```bash
python tools/make_lexicon_template.py docs/lexicon_data_entry_template.xlsx
```

**Four sheets:**

| Sheet | Contents |
|---|---|
| `ابدأ هنا · START HERE` | instructions, Arabic and English |
| `LEXICON · المصطلحات` | one row per term, 11 columns |
| `TROPES · الأنماط` | one row per pattern, 11 columns |
| `REFERENCE · المرجع` | valid group slugs, categories, severity bands |

Right-to-left layout, frozen header, dropdowns on every constrained field, and a
hover note on each column explaining what it means and how to decide. Header colours
carry meaning:

- **red** — required; a row missing one is rejected
- **purple** — needs the curator's judgement; not present in the source files
- **grey rows** — worked examples from the real Duhok data, marked `EXAMPLE —` in the
  source column so the importer skips them. Curators can leave them in place as a
  reference.

### Handing the filled sheet back

```bash
# validate only — safe to run repeatedly while curating
python tools/import_lexicon_sheet.py filled.xlsx --check

# convert to pack YAML once it is clean
python tools/import_lexicon_sheet.py filled.xlsx packs/iraq-minorities
ankedo pack verify && ankedo pack install
```

The validator rejects a workbook rather than importing bad rows, because each rule
maps to a way the classifier fails *silently* later:

| Rejected | Because |
|---|---|
| missing `source` | the term cannot be defended when a report is challenged |
| unknown `target_group` | the term never matches its trope, with no error anywhere |
| `is_explicit` blank | the field that decides whether ordinary speech gets flagged |
| trope without `negative_example` | the pattern will fire on harmless speech |
| trope without `activation_topics` | the pattern never fires; the row does nothing |
| the same term twice | two rows drift apart when someone edits only one |

It also warns without blocking — for instance, a severity-9 term with no
`never_flag_when` will flag journalists quoting it.

---

## Getting your existing data onto the VPS

The importer already handles both of your files:

```bash
# On the VPS, from news_platform/
python manage.py import_lexicon ../HateSpeechData/*.xlsx --dry-run   # triage first
python manage.py import_lexicon ../HateSpeechData/*.xlsx
```

`--dry-run` matters. About 40% of the focus-group transcript's term column describes a
pattern rather than quoting one, and no heuristic separates those reliably — those rows
belong in `hate_speech_tropes`, not the lexicon. Review the dry-run output, fold what
survives into the curated file, and import that.

The importer's language, category and target-group guesses are keyword heuristics:
right most of the time, and cheap to correct in the dashboard afterwards.

Then, on the agent:

```bash
ankedo platform sync-lexicon    # prints unresolved target groups — keep that output
ankedo platform sync-tropes     # prints one warning per unbackfilled trope
ankedo platform status          # confirms cache freshness
```

---

## Checklist before the agent is pointed at real content

- [ ] Every term has a `notes` provenance — verify rejects entries without one
- [ ] `is_explicit` set deliberately on every term, defaulting to `false` when unsure
- [ ] Every context-dependent trope has `activation_topics` **and** `negative_examples`
- [ ] `sync-lexicon` reports zero unresolved target groups, or the remainder are known
- [ ] `ankedo eval kappa` returns κ ≥ 0.6 on a double-annotated slice
- [ ] `ankedo eval run` clears the threshold **for every group**, not on average
