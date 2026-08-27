"""Classify a handful of known cases against the real model and say what happened.

Run this after filling the workbook, before trusting anything the agent reports.
`ankedo test-llm` proves the endpoint answers; this proves the endpoint plus the
dictionary plus the tropes plus the committee produce the verdicts a human would.

    python tools/live_classification_check.py                 # installed pack
    python tools/live_classification_check.py --pack packs/iraq-minorities

It uses whatever is configured in .env — no credentials live in this file — and writes
into a temporary database, so it cannot disturb collected evidence.

**The minimal pairs are the point.** Anything flags a slur. What separates a
monitoring tool from a keyword filter is whether the same word comes out differently
when the context changes, and a keyword filter that cannot do that accuses the wrong
people. Four of the ten cases below exist only to be *cleared*: نجس about water,
عملاء about a government, a warm comment under a religious post, and someone quoting
a libel in order to reject it. If those start failing, the agent has become a filter
that flags any sentence mentioning a minority — which in a human-rights record is
worse than missing hate, because it manufactures accusations against the people the
system exists to protect.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LALISH = "مراسم دينية إيزيدية في معبد لالش"
CHURCH = "خبر: افتتاح كنيسة بعد ترميمها في سهل نينوى"
POLITICS = "منشور سياسي عن أداء الحكومة"
WATER = "شكوى من تلوث مياه الشرب في الحي"

CASES: list[tuple[str, str, str, list[str], str]] = [
    ("explicit dehumanisation", LALISH, "هذوله نجس وما يصير تاكل من ايدهم", ["yazidi"], "hate"),
    ("incitement", LALISH, "حلال قتلهم كلهم", ["yazidi"], "hate"),
    ("loyalty smear, group named", CHURCH, "هذوله عملاء الغرب، شنو يريدون بالعراق",
     ["christian-iraqi"], "hate"),
    ("SAME SMEAR, no group named", POLITICS, "هذوله عملاء وما يفكرون بالبلد", [], "benign"),
    ("impurity, about people", LALISH, "لا تشترون منهم، هذوله نجس", ["yazidi"], "hate"),
    ("SAME WORD, about water", WATER, "الماي وصلنا نجس، ما ينشرب أبدا", [], "benign"),
    ("mockery, contains no slur", LALISH,
     "شوفوا هاي الطقوس المضحكة، يدورون حول حجر ويسمونها عبادة", ["yazidi"], "hate"),
    ("counter-speech quoting a libel", LALISH,
     "يسمونهم عبدة الشيطان وهذا افتراء، الإيزيدية دين قديم ومحترم", ["yazidi"], "benign"),
    ("ordinary warm comment", LALISH, "زرت لالش قبل سنة، مكان هادئ وناسه طيبين",
     ["yazidi"], "benign"),
    ("Kurdish script", LALISH, "ئەوانە بێ دین ن و شەیتان پەریس ن", ["yazidi"], "hate"),
]


async def run(pack_dir: Path | None, workbook: Path | None) -> int:
    work = Path(tempfile.mkdtemp())
    try:
        import os

        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(work / 'check.db').as_posix()}"

        from src.core.settings import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        if not (settings.openai_api_key or settings.gemini_api_key):
            print("No model configured. Run `ankedo setup` first.")
            return 2

        from src.classifiers.committee.orchestrator import CommitteeOrchestrator
        from src.classifiers.context_bundle import ContextBundle
        from src.core.database import get_session, init_db

        await init_db()

        async with get_session() as session:
            if pack_dir:
                staged = work / "pack"
                shutil.copytree(pack_dir, staged)
                if workbook:
                    subprocess.run(
                        [sys.executable, "tools/import_lexicon_sheet.py",
                         str(workbook), str(staged)],
                        check=True, capture_output=True,
                    )
                from src.packs.loader import install_pack

                print(f"pack: {await install_pack(session, staged)}\n")

            committee = CommitteeOrchestrator(session)
            correct = errors = 0

            for label, post, comment, groups, expected in CASES:
                started = time.time()
                try:
                    result = await committee.run(
                        ContextBundle(comment_text=comment, parent_post_text=post,
                                      target_groups=list(groups))
                    )
                except Exception as exc:
                    errors += 1
                    print(f"  ERROR {label:32} {type(exc).__name__}: {str(exc)[:60]}")
                    continue

                got = "hate" if result["hate_speech_flag"] else "benign"
                correct += got == expected
                trace = result["trace"]
                marks = []
                if trace.get("lexicon_hits"):
                    marks.append(f"lex={[h['matched'] for h in trace['lexicon_hits']]}")
                if trace.get("tropes_fired"):
                    marks.append(f"trope={[t.get('trope_id') for t in trace['tropes_fired']]}")
                if trace.get("exemption"):
                    marks.append(f"EXEMPT={trace['exemption']['signal']}")
                print(
                    f"  {'PASS' if got == expected else 'FAIL'}  {label:32} "
                    f"{got:7}(want {expected:7}) conf={result['confidence']:.2f} "
                    f"{time.time() - started:5.1f}s  {' '.join(marks)}"
                )

        print(f"\n{correct}/{len(CASES)} matched a human's reading, {errors} error(s)")
        if errors:
            print("Errors are the endpoint, not the classifier — check `ankedo test-llm`.")
        return 0 if correct == len(CASES) and not errors else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    # The terms being printed are Arabic and Kurdish, and a Windows console defaults
    # to a codepage that cannot encode either — the run died on UnicodeEncodeError
    # while printing a passing result, which reads as the classifier failing when it
    # had just succeeded. errors="replace" so an undisplayable glyph costs a character
    # rather than the report.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=Path("packs/iraq-minorities"),
                        help="pack to install first; omit with --no-pack to use the live DB's data")
    parser.add_argument("--workbook", type=Path, default=None,
                        help="import this workbook into the pack before installing")
    parser.add_argument("--no-pack", action="store_true")
    args = parser.parse_args()

    return asyncio.run(run(None if args.no_pack else args.pack, args.workbook))


if __name__ == "__main__":
    raise SystemExit(main())
