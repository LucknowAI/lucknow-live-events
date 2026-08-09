"""DataForSEO — the secondary provider.

Same Google index as Serper, so the dorks keep working, at roughly $0.60/1k in the async
"standard" mode. Two modes, both implemented, because the ~3× price gap between them is a
real decision rather than an implementation detail:

| mode | flow | price |
|---|---|---|
| `standard` | `task_post` → poll `task_get` | ~$0.60/1k, seconds of latency |
| `live` | one synchronous POST | ~$2.00/1k, no polling |

    POST https://api.dataforseo.com/v3/serp/google/organic/live/advanced
    Authorization: Basic base64(login:password)
    [{"keyword": "...", "language_code": "en", "location_code": 2356, "depth": 10}]

**The finding this adapter is mostly about: HTTP status is not the whole story.**
DataForSEO reports failure in three places, and only one of them is the HTTP code. The
envelope carries `status_code`/`status_message`, and *each task* carries its own
`status_code`; task-level errors routinely arrive under HTTP 200. Verified 2026-08-08 with
deliberately bad credentials:

    → HTTP 401, {"status_code": 40100, "status_message": "You are not authorized…",
                 "cost": 0, "tasks": null}

An adapter that called `raise_for_status()` and read `tasks[0].result[0].items` would see
a failed task as an empty result set — which in a discovery pipeline is indistinguishable
from "nothing new today". So all three levels are checked, and the error names which one
failed. `20000` is Ok and `20100` is Task Created; everything else in 10000–60000 is an
error. (https://docs.dataforseo.com/v3/appendix-errors/)

**Pagination.** There is no `page` parameter — depth is controlled by `depth`, and the
`page` of each item comes back on the item. So page N is requested as `depth = page * num`
and the items for that page are selected from the response. Deeper pages therefore cost
more here than at Serper, which is one more reason this is the fallback and not the
primary.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from v1.contracts.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from v1.contracts.search import SearchQuery, SerpResult
from v1.providers.search.base import BaseSearchProvider, RawSearch

DEFAULT_BASE_URL = "https://api.dataforseo.com/v3"
LIVE_PATH = "/serp/google/organic/live/advanced"
TASK_POST_PATH = "/serp/google/organic/task_post"
TASK_GET_PATH = "/serp/google/organic/task_get/advanced"

STATUS_OK = 20000
STATUS_TASK_CREATED = 20100
STATUS_TASK_IN_QUEUE = 40602
STATUS_TASK_NOT_FOUND = 40501

MAX_DEPTH = 100
"""One task covers at most 100 results. Beyond that DataForSEO bills additional pages."""


class DataForSeoProvider(BaseSearchProvider):
    adapter = "dataforseo"

    @property
    def _base(self) -> str:
        return (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")

    @property
    def _auth_header(self) -> dict[str, str]:
        """Basic auth over `login:password`.

        DataForSEO's credential is a pair, not a token, so `api_key_ref` resolves to the
        literal `login:password` string. Validated here rather than at config load because
        config only ever sees the *name* of the variable — the shape of its value is this
        adapter's business.
        """
        credential = (self._api_key or "").strip()
        if ":" not in credential:
            raise ProviderAuthError(
                f"{self.name}: DataForSEO expects the secret to be 'login:password'; the "
                "resolved value has no colon",
                provider=self.name,
            )
        encoded = base64.b64encode(credential.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}

    # ---------------------------------------------------------------------- the call

    async def _search(self, query: SearchQuery) -> RawSearch:
        if self.config.mode is not None and self.config.mode.value == "standard":
            payload = await self._search_standard(query)
        else:
            payload = await self._search_live(query)
        return self._extract(payload, query)

    def _task_body(self, query: SearchQuery) -> list[dict[str, Any]]:
        depth = min(query.page * query.num, MAX_DEPTH)
        body: dict[str, Any] = {
            "keyword": query.q,
            "language_code": query.hl,
            "location_name": self.config.extra_params.get("location_name"),
            "location_code": self.config.extra_params.get("location_code"),
            "depth": depth,
            "device": "desktop",
            "os": "windows",
        }
        # DataForSEO rejects a task carrying both, and rejects one carrying neither.
        if body["location_name"] and body["location_code"]:
            body.pop("location_name")
        body = {key: value for key, value in body.items() if value is not None}
        extra = {
            key: value
            for key, value in self.config.extra_params.items()
            if key not in {"location_name", "location_code"}
        }
        return [{**body, **extra}]

    async def _search_live(self, query: SearchQuery) -> dict[str, Any]:
        payload = await self.post_json(
            f"{self._base}{LIVE_PATH}",
            json_body=self._task_body(query),
            headers=self._auth_header,
        )
        return self._checked_envelope(payload, expect={STATUS_OK})

    async def _search_standard(self, query: SearchQuery) -> dict[str, Any]:
        posted = self._checked_envelope(
            await self.post_json(
                f"{self._base}{TASK_POST_PATH}",
                json_body=self._task_body(query),
                headers=self._auth_header,
            ),
            expect={STATUS_OK, STATUS_TASK_CREATED},
        )
        task_id = self._task_id(posted)

        deadline = asyncio.get_running_loop().time() + self.config.poll_timeout_s
        while True:
            await asyncio.sleep(self.config.poll_interval_s)
            payload = await self.get_json(
                f"{self._base}{TASK_GET_PATH}/{task_id}", headers=self._auth_header
            )
            envelope = self._checked_envelope(
                payload, expect={STATUS_OK}, pending={STATUS_TASK_IN_QUEUE}
            )
            if envelope is not None:
                return envelope
            if asyncio.get_running_loop().time() >= deadline:
                raise ProviderUnavailable(
                    f"{self.name}: task {task_id} was still queued after "
                    f"{self.config.poll_timeout_s}s",
                    provider=self.name,
                    task_id=task_id,
                )

    # ------------------------------------------------------------------- validation

    def _checked_envelope(
        self,
        payload: Any,
        *,
        expect: set[int],
        pending: set[int] | None = None,
    ) -> Any:
        """Validate the envelope and the per-task status. See the module docstring.

        Returns `None` when the task is legitimately still pending (standard mode only),
        so the caller can keep polling without treating it as a failure.
        """
        if not isinstance(payload, dict):
            raise ProviderError(
                f"{self.name} returned {type(payload).__name__} at the top level",
                provider=self.name,
            )

        status = payload.get("status_code")
        message = str(payload.get("status_message") or "")
        if status not in expect:
            raise self._status_error(status, message, level="envelope")

        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ProviderError(
                f"{self.name}: envelope reported {status} but carried no tasks",
                provider=self.name,
                status_code=status,
            )

        task = tasks[0]
        if not isinstance(task, dict):
            raise ProviderError(f"{self.name}: task entry was not an object", provider=self.name)

        task_status = task.get("status_code")
        task_message = str(task.get("status_message") or "")
        if pending and task_status in pending:
            return None
        if task_status not in expect:
            raise self._status_error(task_status, task_message, level="task")
        return payload

    def _status_error(self, status: Any, message: str, *, level: str) -> ProviderError:
        context = {"provider": self.name, "status_code": status, "level": level}
        detail = f"{self.name} {level} status {status}: {message}"
        if status in {40100, 40101, 40200}:
            return ProviderAuthError(detail, **context)
        if status in {40202, 40203}:
            return RateLimited(detail, **context)
        if isinstance(status, int) and 50000 <= status < 60000:
            return ProviderUnavailable(detail, **context)
        return ProviderError(detail, **context)

    @staticmethod
    def _task_id(payload: dict[str, Any]) -> str:
        task = payload["tasks"][0]
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ProviderError("dataforseo task_post returned no task id")
        return task_id

    # --------------------------------------------------------------------- results

    def _extract(self, payload: dict[str, Any], query: SearchQuery) -> RawSearch:
        task = payload["tasks"][0]
        result = task.get("result")
        if not isinstance(result, list) or not result:
            return RawSearch(results=[], raw=payload)

        first = result[0]
        items = first.get("items") if isinstance(first, dict) else None
        if not isinstance(items, list):
            return RawSearch(results=[], raw=payload)

        results: list[SerpResult] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "organic":
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            # `depth` returns everything up to the requested page, so page N is a slice.
            item_page = item.get("page")
            if isinstance(item_page, int) and item_page != query.page:
                continue
            results.append(
                SerpResult(
                    url=url.strip(),
                    title=str(item.get("title") or "")[:1000],
                    snippet=(str(item["description"])[:4000] if item.get("description") else None),
                    # `rank_absolute` counts every SERP element, so it stays correct when
                    # an AI overview or a featured snippet pushes the organic block down.
                    position=int(item.get("rank_absolute") or item.get("rank_group") or 1),
                    page=query.page,
                    template_id=query.template_id,
                )
            )

        cost = payload.get("cost")
        return RawSearch(
            results=results,
            # DataForSEO reports a dollar cost rather than credits. Kept out of `credits`
            # (which means "vendor credit units") and left to the config price so one
            # accounting rule covers every provider.
            credits=None,
            raw={**payload, "_reported_cost_usd": cost},
        )
