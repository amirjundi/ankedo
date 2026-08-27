"""No dashboard page may ship invented data.

Eight of nine pages rendered hardcoded arrays behind a `setTimeout` that imitated a
network delay. The fixtures were not lorem ipsum: `@hate_network` with 38 offences,
"Devil-worship trope (اعوذ بالله من الشيطان الرجيم)" against a named group, a 12.3%
false-positive rate, 42.5 items per second. On pages titled Evidence and Intelligence
Hub, that is a screen full of findings about real-looking accounts that nobody found.

The danger is specific to this system. An operator cannot tell a confident fixture
from a real result, and the output here is meant to become a human-rights record. A
demonstration that looks like evidence is a false accusation with a nice stylesheet.

This test greps the built frontend source. It is crude, and it is the only thing that
would have caught the original fault — every one of those pages rendered perfectly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGES = Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages"

# `setTimeout` is legitimate for debouncing and polling; it is the *combination* with
# a local fixture array that fakes a fetch. These patterns target the fixture itself.
FIXTURE_PATTERNS = [
    (re.compile(r"\bconst\s+STUB_\w+\s*=", re.MULTILINE), "a STUB_ fixture array"),
    (re.compile(r"//\s*(Would|Stub)\s+fetch", re.IGNORECASE), "a 'would fetch' comment"),
    (re.compile(r"//\s*Would\s+POST", re.IGNORECASE), "a 'would POST' comment"),
]


def _pages():
    assert PAGES.is_dir(), f"page directory not found: {PAGES}"
    return sorted(PAGES.glob("*.jsx"))


def test_the_pages_directory_is_where_it_is_expected():
    assert _pages(), "no pages found — this test would pass vacuously"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_no_page_carries_its_own_fixture(page):
    source = page.read_text(encoding="utf-8")
    for pattern, description in FIXTURE_PATTERNS:
        assert not pattern.search(source), (
            f"{page.name} contains {description}. Every page must read from the API; "
            f"an empty result is the correct answer when there is nothing to show."
        )


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_every_page_talks_to_the_shared_api_client(page):
    """The token rule — omit the header entirely when there is no token — lives in
    api.js. A page doing its own fetch would eventually get that subtly wrong, and
    `Bearer ` with an empty token fails as "Failed to fetch", which reads exactly like
    the agent being down."""
    source = page.read_text(encoding="utf-8")

    if "fetch(" not in source:
        return
    assert "from '../api'" in source, (
        f"{page.name} calls fetch directly instead of going through api.js"
    )
