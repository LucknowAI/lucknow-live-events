"""Pure discovery logic: URL normalization, classification, templates, the novelty walk.

No network, no database, no provider. Everything here is a function of its inputs, which
is the point of keeping `modules/discovery` free of IO — these are the rules that decide
what the engine believes, and they should be checkable in milliseconds.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from v1.config.schema import LocalityConfig, TemplateConfig, TemplateTier, UrlRulesConfig
from v1.contracts.errors import ConfigError
from v1.contracts.verdicts import DecidedBy, UrlClassification, UrlVerdict
from v1.modules.discovery.pagination import (
    StopReason,
    next_cursor_page,
    score_page,
    should_mark_exhausted,
)
from v1.modules.discovery.templates import build_context, render
from v1.modules.discovery.url_filter import UrlClassifier, apply_triage, build_triage_content
from v1.modules.discovery.urls import host_matches, host_of, normalize_url, url_hash

# ------------------------------------------------------------------- normalization


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Scheme and host are case-insensitive; the path is not, and event slugs live there.
        (
            "HTTPS://Example.COM/Events/Details/My-Event",
            "https://example.com/Events/Details/My-Event",
        ),
        ("https://www.example.com/e/abc", "https://example.com/e/abc"),
        ("https://example.com:443/e/abc", "https://example.com/e/abc"),
        ("http://example.com:80/e/abc", "http://example.com/e/abc"),
        ("https://example.com:8443/e/abc", "https://example.com:8443/e/abc"),
        ("https://example.com/e/abc/", "https://example.com/e/abc"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com/e/abc#agenda", "https://example.com/e/abc"),
        # Tracking parameters carry no content, so two spellings are the same page.
        (
            "https://example.com/e/abc?utm_source=x&utm_medium=y&id=7",
            "https://example.com/e/abc?id=7",
        ),
        ("https://example.com/e/abc?fbclid=Z", "https://example.com/e/abc"),
        # Parameter order is not significant to any router we target.
        ("https://example.com/e?b=2&a=1", "https://example.com/e?a=1&b=2"),
    ],
)
def test_normalize_url_table(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "ftp://example.com/x", "file:///etc/passwd", "https:///x"]
)
def test_normalize_url_rejects_unusable(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(raw)


def test_url_hash_is_computed_after_normalization() -> None:
    """Two spellings of one page must produce one `discovered_url` row."""
    a = url_hash(normalize_url("https://WWW.Example.com/e/abc/?utm_source=news#top"))
    b = url_hash(normalize_url("https://example.com/e/abc"))
    assert a == b


def test_host_matches_is_not_substring_matching() -> None:
    """The bug that turns a blocklist into a permit-list for anyone with the right domain."""
    assert host_matches("facebook.com", "facebook.com")
    assert host_matches("m.facebook.com", "facebook.com")
    assert not host_matches("notfacebook.com", "facebook.com")
    assert not host_matches("facebook.com.evil.example", "facebook.com")


def test_host_of_strips_www() -> None:
    assert host_of("https://www.Example.com/x") == "example.com"


# -------------------------------------------------------------------- classifier


@pytest.fixture
def classifier() -> UrlClassifier:
    return UrlClassifier(
        UrlRulesConfig(
            blocked_hosts=["facebook.com", "linkedin.com"],
            listing_patterns=[r"/events/?$", r"[?&]page=\d+", r"/browse"],
            event_patterns=[r"/events/details/", r"/communities/[^/]+/events/[^/]+"],
        )
    )


def test_blocked_host_is_irrelevant_not_listing(classifier: UrlClassifier) -> None:
    """The two are distinguished on purpose: a listing on an unknown domain is a source
    lead, a Facebook page is not."""
    result = classifier.classify("https://m.facebook.com/events/12345")
    assert result.verdict is UrlVerdict.IRRELEVANT
    assert result.rule_id.startswith("blocked_host:")


def test_listing_beats_event_pattern(classifier: UrlClassifier) -> None:
    """A directory misfiled as one event gets fetched, extracted and produces garbage."""
    result = classifier.classify("https://gdg.community.dev/events/?page=2")
    assert result.verdict is UrlVerdict.LISTING
    assert "page" in result.reason


def test_event_page_recognised(classifier: UrlClassifier) -> None:
    result = classifier.classify("https://gdg.community.dev/events/details/some-event/")
    assert result.verdict is UrlVerdict.EVENT_PAGE
    assert result.url == "https://gdg.community.dev/events/details/some-event"


def test_unmatched_url_is_unknown_not_guessed(classifier: UrlClassifier) -> None:
    result = classifier.classify("https://college.example.edu/fest2026")
    assert result.verdict is UrlVerdict.UNKNOWN
    assert result.rule_id == "no_match"


def test_malformed_url_never_raises(classifier: UrlClassifier) -> None:
    """One bad URL in a SERP page must not abort the other nine."""
    result = classifier.classify("javascript:alert(1)")
    assert result.verdict is UrlVerdict.IRRELEVANT
    assert result.rule_id == "malformed"


# ------------------------------------------------------------------ triage folding


def _pending(count: int) -> list[UrlClassification]:
    return [
        UrlClassification(
            url=f"https://example.com/{index}",
            verdict=UrlVerdict.UNKNOWN,
            reason="queued",
            rule_id="no_match",
        )
        for index in range(count)
    ]


def test_apply_triage_folds_by_index() -> None:
    resolved = apply_triage(_pending(3), [(0, "event_page", "single event"), (2, "listing", "dir")])
    assert resolved[0].verdict is UrlVerdict.EVENT_PAGE
    assert resolved[0].decided_by is DecidedBy.LLM
    # The model skipped index 1; it must stay UNKNOWN rather than inherit a neighbour's.
    assert resolved[1].verdict is UrlVerdict.UNKNOWN
    assert resolved[2].verdict is UrlVerdict.LISTING


def test_apply_triage_ignores_out_of_range_and_unknown_labels() -> None:
    resolved = apply_triage(_pending(2), [(9, "event_page", "x"), (0, "definitely", "y")])
    assert all(item.verdict is UrlVerdict.UNKNOWN for item in resolved)


def test_triage_content_is_indexed_not_url_echoing() -> None:
    """Asking a model to echo a 2 KB URL wastes tokens and invites a mangled copy."""
    content = build_triage_content([("https://example.com/a", "Title\nwith newline")])
    assert content.startswith("0. url: https://example.com/a")
    assert "\n" not in content.split("title: ")[1]


# --------------------------------------------------------------------- templates


@pytest.fixture
def locality() -> LocalityConfig:
    return LocalityConfig(
        city_keywords=["Testville", "Old Town"],
        community_names=["Testville Devs"],
        institution_names=["Testville Institute"],
    )


def test_render_interpolates_config_not_literals(locality: LocalityConfig) -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    context = build_context(locality, timezone="Asia/Kolkata", now=now)
    template = TemplateConfig(
        id="t", tier=TemplateTier.A, query="site:x.com ({city_keywords}) after:{date_floor}"
    )
    rendered = render(template, context)
    assert rendered == 'site:x.com ("Testville" OR "Old Town") after:2026-06-24'


def test_month_window_spans_two_months(locality: LocalityConfig) -> None:
    context = build_context(
        locality,
        timezone="Asia/Kolkata",
        now=datetime(2026, 12, 20, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    assert context["month_window"] == '"December 2026" OR "January 2027"'


def test_unknown_placeholder_is_a_render_failure(locality: LocalityConfig) -> None:
    """A malformed dork does not error at Google — it silently matches nothing."""
    context = build_context(locality, timezone="Asia/Kolkata")
    template = TemplateConfig(id="t", tier=TemplateTier.A, query="x {nope}")
    with pytest.raises(ConfigError, match="unknown placeholder"):
        render(template, context)


def test_empty_placeholder_is_a_render_failure() -> None:
    """`({community_names})` with no communities configured becomes `()`, which matches
    nothing while still costing a credit."""
    context = build_context(LocalityConfig(), timezone="Asia/Kolkata")
    template = TemplateConfig(id="t", tier=TemplateTier.A, query="({community_names}) x")
    with pytest.raises(ConfigError, match="resolved to empty"):
        render(template, context)


# ------------------------------------------------------------- novelty / cursors


def test_page_stops_at_the_novelty_floor() -> None:
    verdict = score_page(results_on_page=10, novel_on_page=1, novelty_floor=0.15)
    assert not verdict.should_continue
    assert verdict.reason is StopReason.NOVELTY_FLOOR


def test_productive_page_continues() -> None:
    assert score_page(results_on_page=10, novel_on_page=6, novelty_floor=0.15).should_continue


def test_empty_page_stops_without_dividing_by_zero() -> None:
    verdict = score_page(results_on_page=0, novel_on_page=0, novelty_floor=0.15)
    assert not verdict.should_continue
    assert verdict.reason is StopReason.NO_RESULTS


def test_cursor_resumes_deeper_while_productive() -> None:
    assert next_cursor_page(last_page=3, novelty_ratio=0.5, novelty_floor=0.15) == 4


def test_cursor_resets_to_one_when_unproductive() -> None:
    """Page 1 is where newly indexed pages appear, so a reset is not a defeat."""
    assert next_cursor_page(last_page=3, novelty_ratio=0.0, novelty_floor=0.15) == 1


def test_exhaustion_needs_two_consecutive_barren_runs() -> None:
    """One barren run is noise; retiring a template on a single sample loses a source."""
    assert not should_mark_exhausted(novelty_ratio=0.0, previous_ratio=None)
    assert not should_mark_exhausted(novelty_ratio=0.0, previous_ratio=0.4)
    assert should_mark_exhausted(novelty_ratio=0.0, previous_ratio=0.0)
