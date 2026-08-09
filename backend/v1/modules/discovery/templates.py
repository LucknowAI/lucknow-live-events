"""Render the dork template bank into concrete queries.

Templates are **data** (`templates.yaml`), not code. That is ADR-019, and the reason is
V0: its discovery prompt asks an LLM to invent queries, so nobody has ever seen the
queries, nobody can diff them between runs, and nobody can explain why a source stopped
appearing. A rendered template is a string you can paste into Google.

Placeholders are filled from config alone — no city name, no community name and no date
literal is in this file:

| Placeholder | Filled with |
|---|---|
| `{date_floor}` | `after:` cut-off, `YYYY-MM-DD`, in the tenant's timezone |
| `{month_window}` | `"August 2026" OR "September 2026"` — the current and next month |
| `{year}` | Current year in the tenant's timezone |
| `{city_keywords}` | `"A" OR "B"` from `locality.city_keywords` |
| `{community_names}` | `"A" OR "B"` from `locality.community_names` |
| `{institution_names}` | `"A" OR "B"` from `locality.institution_names` |

An unknown placeholder is a **render failure**, not a query containing a literal `{foo}`.
A malformed dork does not error at Google — it silently matches nothing, and a template
that quietly stops returning results looks exactly like a source that went quiet.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from v1.config.schema import LocalityConfig, TemplateConfig
from v1.contracts.errors import ConfigError

DATE_FLOOR_DAYS = 45
"""How far back `after:` reaches. An event page Google indexed more than six weeks ago is
almost certainly a past event, and excluding it costs nothing — the filtering happens
inside the query, so we never pay to retrieve and discard the result."""

MONTH_WINDOW_MONTHS = 2

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def _or_group(values: list[str]) -> str:
    """`"A" OR "B"` — quoted so multi-word names stay phrases."""
    quoted = [f'"{value}"' for value in values if value.strip()]
    return " OR ".join(quoted)


def _month_window(now: datetime, months: int = MONTH_WINDOW_MONTHS) -> str:
    labels: list[str] = []
    cursor = now
    for _ in range(months):
        labels.append(cursor.strftime("%B %Y"))
        # Step into the next month by jumping past the end of this one. Cheaper and less
        # error-prone than month arithmetic, and correct for every month length.
        cursor = (cursor.replace(day=28) + timedelta(days=7)).replace(day=1)
    return _or_group(labels)


def build_context(
    locality: LocalityConfig, *, timezone: str, now: datetime | None = None
) -> dict[str, str]:
    """Everything a template may interpolate, for one run."""
    moment = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone))
    return {
        "date_floor": (moment - timedelta(days=DATE_FLOOR_DAYS)).strftime("%Y-%m-%d"),
        "month_window": _month_window(moment),
        "year": moment.strftime("%Y"),
        "city_keywords": _or_group(locality.city_keywords),
        "community_names": _or_group(locality.community_names),
        "institution_names": _or_group(locality.institution_names),
    }


def render(template: TemplateConfig, context: dict[str, str]) -> str:
    """Render one template, failing loudly on an unknown or empty placeholder."""
    missing = [name for name in _PLACEHOLDER_RE.findall(template.query) if name not in context]
    if missing:
        raise ConfigError(
            f"template {template.id!r} uses unknown placeholder(s) {missing}; available: "
            f"{sorted(context)}",
            template_id=template.id,
        )

    empty = [name for name in _PLACEHOLDER_RE.findall(template.query) if not context[name].strip()]
    if empty:
        # An empty `{community_names}` turns `("A" OR "B") after:...` into `() after:...`,
        # which Google answers with nothing at all. Silently issuing that query would burn
        # a credit to learn nothing.
        raise ConfigError(
            f"template {template.id!r} interpolates {empty}, which resolved to empty. "
            "Fill the matching `locality` list in the instance config, or disable the "
            "template",
            template_id=template.id,
        )

    return _PLACEHOLDER_RE.sub(lambda match: context[match.group(1)], template.query).strip()
