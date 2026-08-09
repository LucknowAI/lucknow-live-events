"""Fixture replay — the provider CI uses, and the default when no key is present.

Development cannot burn a one-time free allocation. Serper's 2,500 credits do not renew,
so a test suite that hit the network would spend the project's entire free tier on
assertions. This provider makes discovery testable at zero cost and byte-identical on
every run, which is why the parent plan calls it *"a first-class requirement, not a test
nicety."*

**Recording.** With `record_from: serper` and `V1_SEARCH_ALLOW_LIVE=1`, a missing fixture
is fetched once from the live provider and written to disk. The recording call goes
through the source provider's **own funnel** — `search()`, not some private method — so it
passes the budget check, respects the cap, and writes a `search_call` ledger row
attributed to the live provider. A recorder that reached past the funnel would let
`record_from` in a config file spend money the cap never saw, which is precisely the defect
found in the LLM layer's first review.

Without `record_from`, a missing fixture is a hard `FixtureMissing` naming the exact key
and path. That is the correct behaviour in CI: a silent live call is how a test suite
starts costing money.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from v1 import resolve_path
from v1.contracts.errors import FixtureMissing
from v1.contracts.search import SearchQuery, SerpResult
from v1.platform.logging import get_logger
from v1.providers.search.base import BaseSearchProvider, RawSearch

logger = get_logger("v1.search.fixture")


class FixtureSearchProvider(BaseSearchProvider):
    """Replays recorded SERP responses from disk."""

    adapter = "fixture"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._record_source: BaseSearchProvider | None = None

    def set_record_source(self, provider: BaseSearchProvider | None) -> None:
        """Wired by the registry, and only when live spend is explicitly permitted."""
        self._record_source = provider

    @property
    def fixture_dir(self) -> Path:
        return resolve_path(self.config.fixture_dir or "tests/v1/fixtures/serp")

    def fixture_path(self, query: SearchQuery) -> Path:
        return self.fixture_dir / f"{self.fixture_key(query)}.json"

    @staticmethod
    def fixture_key(query: SearchQuery) -> str:
        """Keyed by the query itself, not by which provider recorded it.

        A recording of `site:example.com after:2026-01-01` page 1 is the same evidence
        whether Serper or DataForSEO produced it, and keying by provider would mean
        re-recording the whole corpus the day the chain order changes.
        """
        return query.cache_key("fixture")

    async def _search(self, query: SearchQuery) -> RawSearch:
        path = self.fixture_path(query)
        if path.is_file():
            return self._load(path, query)

        if self._record_source is None:
            raise FixtureMissing(
                f"no recorded SERP response for this query at {path}. Record it with "
                f"`record_from` on the {self.name!r} provider plus V1_SEARCH_ALLOW_LIVE=1, "
                "or add the file by hand",
                path=str(path),
                query=query.q,
                page=query.page,
            )

        return await self._record_fixture(path, query)

    def _load(self, path: Path, query: SearchQuery) -> RawSearch:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FixtureMissing(
                f"recorded SERP fixture {path} could not be read: {exc}", path=str(path)
            ) from exc

        rows = payload.get("results", []) if isinstance(payload, dict) else []
        results = [
            SerpResult.model_validate({**row, "page": query.page, "template_id": query.template_id})
            for row in rows
            if isinstance(row, dict)
        ]
        return RawSearch(results=results, credits=payload.get("credits"), raw=payload)

    async def _record_fixture(self, path: Path, query: SearchQuery) -> RawSearch:
        """Named `_record_fixture`, not `_record`.

        `BaseSearchProvider._record` is the ledger writer that the funnel calls in its
        `finally` block. A subclass method with that name silently overrides it, and the
        failure surfaces as a `TypeError` about keyword arguments on the *accounting*
        path — long after the recording worked. The LLM fixture provider hit exactly this.
        """
        source = self._record_source
        assert source is not None  # guarded by the caller

        logger.warning(
            "serp_fixture_recording",
            provider=self.name,
            record_from=source.name,
            query=query.q,
            page=query.page,
            path=str(path),
            detail="a live, billable query is being made to create this fixture",
        )
        # The source's full funnel: budget gate, retries, its own ledger row. Not `_search`.
        response = await source.search(query)

        payload = {
            "_recorded_from": source.name,
            "_query": query.model_dump(mode="json"),
            "credits": response.credits,
            "results": [result.model_dump(mode="json") for result in response.results],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            # A failed write must not fail the query — we already have the results and
            # already paid for them. Logged so an unwritable fixture directory is visible
            # rather than showing up as a bill that never stops.
            logger.exception("serp_fixture_write_failed", path=str(path))

        return RawSearch(results=list(response.results), credits=response.credits, raw=payload)
