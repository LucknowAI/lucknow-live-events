"""The novelty floor — an economic stop condition for page descent.

The mentor's note was *"if some results are in page 2, 3, 4… the next search query should
take results from next pages."* Two separate needs hide in that, and only the first is
obvious:

**(a) Depth within a run.** Keep asking for the next page while the pages are still worth
paying for. "Worth paying for" is measurable: the share of results we have never seen
before. When that share drops below `novelty_floor`, the page has stopped paying for
itself and the walk stops. This is not a quality judgement — a page can be full of
perfectly good results we already have.

**(b) Continuity across runs.** If a template was still productive at page 3 last run,
starting the next run at page 1 re-buys three pages of results already in the database. So
the cursor persists: `next_page` resumes deeper when novelty was good, and resets to 1
when it was not — because a reset is exactly what catches pages Google has newly indexed.

Two consecutive unproductive runs mark the query `exhausted`, and an exhausted query is
skipped entirely until the reset window passes. That skip is where most of the saving
actually comes from: not paying at all beats paying less.

This module is pure. It makes decisions about numbers; the caller does the searching and
the persisting, which is what makes every rule here testable without a network or a
database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StopReason(StrEnum):
    NOVELTY_FLOOR = "novelty_floor"
    MAX_PAGES = "max_pages"
    NO_RESULTS = "no_results"
    EXHAUSTED = "exhausted"
    UNSUPPORTED = "unsupported"
    PROVIDER_ERROR = "provider_error"
    BUDGET = "budget"
    PAGE_LIMIT = "page_limit"


@dataclass(frozen=True, slots=True)
class PageVerdict:
    """What to do after scoring one page."""

    should_continue: bool
    reason: StopReason | None
    novelty_ratio: float


def score_page(*, results_on_page: int, novel_on_page: int, novelty_floor: float) -> PageVerdict:
    """Decide whether the next page is worth buying, given how this one performed."""
    if results_on_page == 0:
        return PageVerdict(should_continue=False, reason=StopReason.NO_RESULTS, novelty_ratio=0.0)

    ratio = novel_on_page / results_on_page
    if ratio < novelty_floor:
        return PageVerdict(
            should_continue=False, reason=StopReason.NOVELTY_FLOOR, novelty_ratio=ratio
        )
    return PageVerdict(should_continue=True, reason=None, novelty_ratio=ratio)


def next_cursor_page(*, last_page: int, novelty_ratio: float, novelty_floor: float) -> int:
    """Where the *next run* should start this query.

    Resume one page deeper while the query is still productive; otherwise go back to page
    1. Restarting is not a defeat — page 1 is where newly indexed pages appear, and a
    query that has run out of depth still gains new results at the top.
    """
    if novelty_ratio >= novelty_floor:
        return last_page + 1
    return 1


def should_mark_exhausted(*, novelty_ratio: float, previous_ratio: float | None) -> bool:
    """Exhausted after **two** consecutive unproductive runs, not one.

    One barren run is noise — an indexing lag, a quiet week, a transient partial result
    set. Retiring a working template on a single bad sample is how discovery silently
    loses a source.
    """
    return novelty_ratio == 0.0 and previous_ratio == 0.0
