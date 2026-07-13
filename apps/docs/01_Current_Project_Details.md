# 01 — Project Audit: `lucknow-live-events`

**Author role:** Principal Software Engineer / Technical Architect (onboarding)
**Repository:** `github.com/LucknowAI/lucknow-live-events`
**Scope:** Read-only comprehension audit. No code changes, no redesign, no recommendations beyond documenting existing debt/risk.
**Method:** Full read of backend (`api`, `ingestion`, `ai`, `workers`, `alembic`), frontend (`apps/web`), and build/deploy config.

---

## 1. Executive Summary

`lucknow-live-events` is an automated aggregator for tech events in Lucknow, India. Its defining characteristic is that event data is **discovered and structured by AI rather than manually curated**: a Gemini-powered agent uses Google Search grounding to find individual event URLs, a second Gemini agent extracts structured fields from each page, a confidence-scored pipeline decides whether to publish or route to moderation, and a Next.js frontend serves the results.

The system is a **modular monolith split across three runtimes**: a FastAPI HTTP API, a Celery worker + beat scheduler for the ingestion pipeline, and a Next.js frontend. PostgreSQL 16 is the single datastore; Redis is the Celery broker; Google Gemini is the extraction/classification engine; Playwright renders JavaScript-heavy pages.

The codebase is **single-tenant** (hard-coded to Lucknow), **pre-1.0** (5 commits, 0 stars/forks at time of audit), and shows the hallmarks of a fast-moving solo/small-team MVP: coherent architecture and clean layering, but accumulating inconsistencies (multiple conflicting date-sentinel conventions, four different Gemini model strings), no automated tests, and a production deploy config that does not include the worker that runs the core pipeline.

**One-line mental model:** *A well-layered AI-ingestion pipeline feeding a read-only Next.js showcase, currently held together by careful heuristics and destined to break quietly where its stateful assumptions meet its stateless/ephemeral deployment.*

---

## 2. Product Understanding

- **Who it's for:** the Lucknow tech community (students, developers, community organizers) looking for one place to find upcoming hackathons, workshops, conferences, meetups, and college fests.
- **Core promise:** "If it's happening in the city, it's on our list" — comprehensive, low-effort discovery without manual data entry.
- **Value delivered:** aggregated, filterable, calendar-viewable event listings plus subscribable feeds (JSON + ICS).
- **Explicitly *not* a booking platform:** events link out (`registration_url` / `canonical_url`) to wherever registration actually happens. There is no RSVP, ticketing, payment, or capacity logic anywhere in the codebase.
- **Content boundary:** tech events in/around Lucknow, plus online events hosted by Lucknow-based communities. Relevance to Lucknow is itself a scored, enforced property.

---

## 3. Feature Inventory

| Area | Feature | Status (as observed) |
|---|---|---|
| Public | Event list with filters (query, date range, mode, type, topic, locality, community, free, student-friendly) | Implemented |
| Public | Event detail page (`/events/[slug]`) with JSON-LD structured data | Implemented |
| Public | Calendar view (excludes Date-TBA events) | Implemented |
| Public | Curated lists: Featured, This Week, Student-Friendly, Past/Completed | Implemented |
| Public | Facets: topics, communities, localities (with counts) | Implemented |
| Public | Communities directory, About, Topics pages | Implemented |
| Public | ICS + JSON subscribable feeds | Implemented (on-request) |
| Public | Public event-URL submission form (rate-limited 5/hour) | Implemented |
| Public | Community-link submission form | Implemented (model supports it) |
| Admin | JWT login (single admin account) | Implemented |
| Admin | "Mission Control" dashboard: Stats, Sources, Moderation queue, Events, Event Queue, Discovery tabs | Implemented (frontend `/mission-control`) |
| Admin | Manually trigger AI discovery (default + custom queries) | Implemented |
| Admin | Direct URL submit through pipeline | Implemented |
| Pipeline | AI discovery agent (Gemini + Google Search grounding, every 12h) | Implemented |
| Pipeline | Scrape → deterministic parse → AI extraction → normalize → relevance → dedup → publish decision | Implemented |
| Pipeline | Confidence-scored publish / Date-TBA / moderation routing | Implemented |
| Pipeline | Watchlist refresh of single-event URLs | Implemented |
| Pipeline | Expire past events; re-scrape bad-date events | Implemented |
| Pipeline | Feed materialization to storage/CDN | **Stub only** (`rebuild_all_feeds` is a no-op) |
| Pipeline | Platform-specific source adapters (GDG, Commudle, lu.ma, Meetup, etc.) | **Not built** (only `generic` + `static` exist) |
| SEO | `robots.ts`, `sitemap.ts`, per-event JSON-LD, OG metadata | Implemented |

---

## 4. Technology Stack

**Backend**
- Python 3.12, FastAPI ≥0.115, Uvicorn
- SQLAlchemy 2.0 (async ORM), asyncpg (runtime), psycopg2 (Alembic), Alembic migrations
- Celery ≥5.4 + Redis ≥5.0 (broker + result backend)
- Pydantic v2 / pydantic-settings
- Google Gemini via `google-genai` ≥1.0
- Playwright + `playwright-stealth` (Chromium) for JS rendering; httpx for plain fetches
- `structlog` (JSON logging), `slowapi` (rate limiting), `python-jose` + `bcrypt` (auth), `icalendar`, `python-slugify`, `python-dateutil`, `pytz`
- Package manager: `uv` (with `uv.lock`); tooling: `ruff`

**Frontend**
- Next.js **16.2.4** (App Router), React **19.0.0**, TypeScript 5.4
- Tailwind CSS **v4** (design tokens via `@theme` in CSS; `tailwind.config.ts` is type-check only)
- SWR (client data fetching), `axios` (declared, but `lib/api.ts` uses native `fetch`), `date-fns`, `lucide-react`, `clsx`, `tailwind-merge`, `@vercel/analytics`
- Package manager: `pnpm`

> **Note:** README advertises "Next.js 15" and "Gemini 3.0 Flash"; the actual pinned versions are Next.js 16.2.4, and Gemini model strings vary across files (see §12, §19).

**Infrastructure (from config/comments)**
- Frontend: Vercel (region `bom1` / Mumbai)
- Backend: Render (`render.yaml`) and/or Google Cloud Run (Dockerfile targets `$PORT`) — ambiguous
- DB: Neon (Postgres); Redis: Upstash — inferred from `render.yaml` comments
- Object storage: local FS (default) or Cloudflare R2 (stub)

---

## 5. Folder Structure

```
lucknow-live-events/
├── apps/web/                     # Next.js 16 frontend
│   ├── app/                      # App Router: /, events, calendar, communities,
│   │                             #   topics, about, submit, admin, mission-control
│   │   ├── events/[slug]/        # event detail
│   │   ├── mission-control/      # admin dashboard + _components/*Tab.tsx
│   │   └── sitemap.ts, robots.ts
│   ├── components/               # EventCard, Sidebar, forms, JSON-LD, skeletons
│   └── lib/                      # api.ts, admin-api.ts, events-query.ts, utils
├── backend/
│   ├── ai/                       # Gemini agents: extraction, classification,
│   │                             #   moderation; gemini_client
│   ├── api/
│   │   ├── core/                 # config, database, deps (auth), security, limiter
│   │   ├── models/               # SQLAlchemy ORM: event, source, raw_event,
│   │   │                         #   submission, moderation, crawl, base
│   │   ├── routers/              # events, feeds, submissions, discovery, admin/*
│   │   ├── schemas/              # Pydantic request/response
│   │   └── services/             # event, admin, submission, discovery services
│   ├── ingestion/
│   │   ├── adapters/             # base, generic (Playwright), static, playwright_util
│   │   ├── normalizers/          # date, location, text
│   │   ├── pipeline.py           # the 9-step ingestion pipeline (755 LoC)
│   │   ├── dedup.py, relevance.py, publish_score.py, storage.py, location_data.py
│   ├── workers/                  # celery_app, schedules, tasks/*, utils
│   └── alembic/versions/         # 4 migrations
├── docker/docker-compose.dev.yml # postgres, redis, api, worker, beat, flower, web
├── scripts/                      # seed_sources.py (referenced), check_db.py
├── render.yaml, vercel.json, Makefile, .env.example
```

Structure is clean and conventional. `apps/web` + `backend` monorepo with clear module boundaries inside the backend (`ai` / `api` / `ingestion` / `workers`).

---

## 6. Frontend Architecture

- **Framework:** Next.js App Router with a mix of Server Components (SSR/RSC data fetching) and Client Components (`ClientLayoutWrapper`, SWR-driven interactive views).
- **Data access:** `lib/api.ts` exposes `eventService` and `facetService`. In the browser it always calls the **relative** path `/api/v1`, which `next.config.js` **rewrites** to proxy to the backend (hides backend URL, avoids CORS). Server-side it uses `INTERNAL_API_URL` (Docker) or `NEXT_PUBLIC_API_URL` (prod), falling back to `localhost:8000` with a warning.
- **Caching intent:** generic fetches use `next: { revalidate: 60 }`; the events list explicitly uses `cache: 'no-store'` (always fresh).
- **Admin:** `lib/admin-api.ts` (separate client) powers the `/mission-control` dashboard, composed of tab components (`StatsPanel`, `SourcesTab`, `ModerationTab`, `EventsTab`, `EventQueueTab`, `DiscoveryTab`). There are **two admin login routes**: `/admin/login` and `/mission-control/login`.
- **Styling:** Tailwind v4 with dark theme forced at the `<html>` level; Inter font via `next/font`.
- **SEO:** per-event JSON-LD (`EventJsonLd`), dynamic `sitemap.ts`, `robots.ts`, metadata templates.
- **Analytics:** Vercel Analytics.

---

## 7. Backend Architecture

- **App wiring (`api/main.py`):** FastAPI app, structlog JSON logging, SlowAPI rate-limit middleware, CORS from `CORS_ORIGINS`, `/health` endpoint, all routers mounted under `/api/v1`. The Celery app is imported in a `try/except` so the API still boots where Redis is absent (e.g. Vercel), at the cost of background dispatch.
- **Layering:** `routers` (HTTP) → `services` (business logic / queries) → `models` (ORM). Pydantic `schemas` define request/response contracts. Clean separation; no obvious leakage of ORM objects past the service boundary except feeds returning ORM rows directly.
- **DB access (`api/core/database.py`):** async engine; **`NullPool` on all cloud environments** (no connection reuse), default pool only when `DOCKER_ENV=1`. Custom SSL handling to work around asyncpg not parsing `sslmode`.
- **Auth (`core/security.py`, `core/deps.py`):** single-admin model. Login checks `ADMIN_EMAIL` + bcrypt `ADMIN_PASSWORD_HASH`, issues an HS256 JWT with `role: admin`, 60-min expiry, no refresh token. `get_current_admin` validates the bearer token and enforces `role == admin`. **All admin routers depend on this guard** (auth/login excepted) — the admin API is JWT-protected; only the frontend `/mission-control` *route* is unlinked, not the API.
- **Rate limiting:** SlowAPI keyed by remote address, **in-memory storage** (per-instance).

---

## 8. API Inventory

All under `/api/v1`. Tenant/city is implicit (single-tenant).

**Public — Events (`/events`)**

| Method | Path | Purpose |
|---|---|---|
| GET | `/events` | List with filters + pagination (`page`, `limit`≤200) |
| GET | `/events/featured` | Up to 5 featured |
| GET | `/events/this-week` | Next 7 days |
| GET | `/events/student-friendly` | Student-friendly + free, next 30 days |
| GET | `/events/past?days=` | Completed events, last N days (≤90) |
| GET | `/events/{slug}` | Event detail (404 if missing) |

**Public — Discovery facets (`/`)**

| GET | `/topics` · `/communities` · `/localities` | Facet name + count lists |

**Public — Feeds (`/feeds`)**

| GET | `/feeds/events.json` · `/feeds/events.ics` | Subscribable feeds (built per-request) |

**Public — Submissions (`/submissions`)**

| POST | `/submissions` | Submit event URL (rate-limited 5/hour) |

**Admin (`/admin`, JWT required)**

| POST | `/admin/auth/login` | Obtain JWT (no auth) |
| GET | `/admin/stats` | Dashboard stats |
| — | `/admin/sources` | Source CRUD |
| — | `/admin/moderation` | Moderation queue review |
| — | `/admin/events` | Event admin (override/feature/unpublish) |
| POST | `/admin/discovery/run` | Trigger discovery (default queries) |
| POST | `/admin/discovery/run-custom` | Trigger discovery (custom queries) |
| POST | `/admin/discovery/submit-url` | Direct URL submit |

**System**

| GET | `/health` | Liveness (`{ok: true}`) |

---

## 9. Database Schema

Single-tenant PostgreSQL 16. UUID primary keys. Six domain tables.

**`events`** — the published surface.
Key fields: `slug` (unique, indexed), `title`, `description`/`short_description`, `start_at` (**NOT NULL**, indexed), `end_at`, `timezone` (default `Asia/Kolkata`), `city` (default `Lucknow`), `locality`, `venue_name`, `address`, `lat`/`lng`, `mode`, `event_type`, `topics_json`/`audience_json` (JSONB), `organizer_name`, `community_name`, `source_platform`, `canonical_url` (**NOT NULL, no unique constraint**), `registration_url`, `poster_url`, `is_free`/`is_featured`/`is_cancelled`/`is_student_friendly`/`date_tba` (bools), `relevance_score`, `publish_score`, `raw_event_id` (FK), `published_at` (indexed), `expires_at`, `search_vector` (TSVECTOR, GIN index `idx_events_search`, maintained by trigger migration).

**`raw_events`** — pre-publish records. `source_id` (FK), `external_id`, `raw_payload_json`, `ai_extracted_json`, `extraction_method`, `extraction_confidence`, `ai_flags`, `processed`, `pipeline_status`. 1:1 → `events`.

**`sources`** — crawl targets. `name`, `platform`, `base_url`, `enabled`, `status` (`active`/`whitelisted`/`blacklisted`), `crawl_strategy`, `config_json` (JSONB; holds `watchlist`, `always_refresh` flags), `crawl_interval_hours` (default 6), `trust_score` (default 0.7), failure tracking (`consecutive_failures`, `last_*_at`).

**`manual_submissions`** — public/admin submissions. `submission_type` (`event`/`community`), submitter name/email, `event_url`, `poster_key`, community fields, `notes`, `status`.

**`moderation_queue`** — `entity_type`/`entity_id` (loose reference, no FK), `reason`, `severity`, `status`, `ai_verdict` (JSONB), `notes`, `resolved_at`.

**`crawl_runs`** — telemetry per pipeline run. `source_id` (FK), `celery_task_id`, timing, `status`, counts (`pages_fetched`, `events_found`, `events_new`, `events_published`, `events_queued`), `error_summary`.

**Relationships:** `Source 1—* RawEvent 1—1 Event`; `Source 1—* CrawlRun`. `moderation_queue` references entities by string, not FK.

**Migrations (4):** initial schema · search-vector trigger · date-TBA + community submissions · source status field.

---

## 10. Event Processing Pipeline

Orchestrated in `ingestion/pipeline.py` (`run_source_pipeline` → `_process_source` → `_process_raw_event`). Steps as implemented:

1. **Load source**, open a `CrawlRun` telemetry row.
2. **Fetch** pages via the source's adapter (`generic` or `static`).
3. **Snapshot + hash:** store raw bytes to object storage keyed by `schema_version:url_hash`; if the stored `.hash` matches the new content hash (and `always_refresh` is off), **skip unchanged**.
4. **RawEvent upsert** (by `source_id` + `external_id`).
5. **Deterministic parse** (`_deterministic_parse`) → partial fields + confidence (fraction of 5 key fields found).
6. **Page-type guardrail** (`_classify_generic_page`): JSON-LD `Event` / URL patterns / text-length heuristics → `detail` / `listing` / `noise`; non-detail → moderation.
7. **Conditional AI extraction:** if confidence < 0.60 (or generic platform with `_cleaned_text`), call the Gemini extraction agent; else optionally classify.
8. **Normalize** city/locality/dates/description/URLs.
9. **Relevance score** (`compute_relevance`) — reject `< 0.3` (unless `static`).
10. **Junk-title rejection** (regex against CSS/JS/JSON noise).
11. **Missing date handling:** grounded date search (a further Gemini call); else publish as **Date-TBA** using a **far-future sentinel `start_at`** (`now.year + 1`) if `confidence ≥ 0.45` and `relevance ≥ 0.50`; else moderation.
12. **Deduplicate** (`find_duplicate`): exact `canonical_url` match; else case-insensitive title `ILIKE` within ±12h; else identical-title match. Duplicates trigger a conservative in-place refresh.
13. **Publish decision:** weighted `compute_publish_score` (source trust 0.25, extraction 0.20, location 0.20, completeness 0.15, relevance 0.15, dedup 0.05) vs a **dynamic threshold** (0.60/0.68/0.75 by source trust). Near-miss → one re-classification retry.
14. **Publish** (`_publish_event`, unique-slug loop) or **queue for moderation**.
15. **Enqueue feed rebuild** (`rebuild_all_feeds` — currently a stub).

**Scheduling (`workers/schedules.py`, Celery beat, IST):** crawl-all every 12h · rebuild-feeds every 30min (no-op) · expire-past-events daily 3am · auto-discover every 12h · refresh-watchlist every 12h.

**Maintenance tasks (`workers/tasks/crawl.py`):** `expire_past_events` (3-pass expire/delete) · `rescrape_single_event` · `rescrape_bad_dates` (finds `start_at > 2050`, re-extracts, deletes if still bad).

---

## 11. Scraping Pipeline

- **Adapters** implement `BaseAdapter.fetch()` → `list[ScrapedPage]` and `extract_raw_events()`.
  - **`GenericAdapter`** (`platform="generic"`): renders the page with **Playwright** (`playwright_render`), extracts best image, meta description, and JSON-LD, produces a single `_cleaned_text` payload (capped at `MAX_EXTRACTION_CHARS`) for the AI extraction agent. This is the primary real-world path.
  - **`StaticAdapter`** (`platform="static"`): dev/testing adapter that reads fully-structured events from `source.config_json.events` — lets the end-to-end flow run without external sites or AI quota.
- **Discovery vs. crawl are separate mechanisms.** Discovery (`workers/tasks/discovery.py`) does **not** use adapters; it asks Gemini (with Google Search grounding) to return individual event URLs, filters listing pages via a blocklist regex, and inserts each as a `manual_submission` (submitter `agent@nawab.ai`), which the submission task then feeds into the pipeline.
- **Platform-specific adapters** (GDG, Commudle, lu.ma, Meetup, Unstop, Devfolio, Townscript) described in the README are **not implemented** — only `generic` and `static` exist. `MEETUP_CLIENT_ID/SECRET` env vars exist with no consuming code.

---

## 12. AI Components

All via `ai/gemini_client.py` (`get_client` is an `lru_cache`'d `genai.Client`; `json_config` builds a JSON-schema-constrained generation config from a Pydantic model, temperature 0.1, max 2048 tokens).

- **Extraction agent (`ai/extraction_agent.py`):** primary parser. Pydantic `GeminiExtractionOutput` (title, description, ISO dates, city/locality/venue/address, mode, event_type, topics, audience, organizer/community, registration_url, price, flags, `confidence`, `not_an_event`). System prompt heavily prioritizes exact date/time extraction and forbids fabricating dates. Also exposes `grounded_date_search` (a focused date-only grounded call).
- **Classification agent (`ai/classification_agent.py`):** enriches an already-extracted event with `event_type`, `topics` (≤5), `audience`, `is_student_friendly`, and a Lucknow-relevance score, using a hard-coded list of known Lucknow communities.
- **Moderation agent (`ai/moderation_agent.py`):** triages manual submissions (`decision`, `reason`, `spam_likelihood`, `tech_relevance`) with Lucknow context baked into the prompt.
- **Discovery agent (`workers/tasks/discovery.py`):** Gemini + `GoogleSearch` tool; designs its own search strategy over a 4-month window; returns raw URLs parsed from free text.

**Model strings are inconsistent across the repo:** `.env.example` and `config.py` default `gemini-3.0-flash`; `render.yaml` `gemini-3-flash-preview`; discovery task **hard-codes** `gemini-2.0-flash` (ignoring `settings.GEMINI_MODEL`). `AI_MODE`/`AI_FALLBACK_TO_MOCK` support a mock mode.

---

## 13. Build Process

- **Backend:** `python:3.12-slim` Docker image; installs deps via **`uv sync --frozen`**, then `email-validator` + `playwright-stealth` via pip, then **Playwright Chromium with OS deps**. Entrypoint `uvicorn api.main:app` on `${PORT:-8000}`. A parallel `requirements.txt` exists "for Vercel Python runtime."
- **Frontend:** `pnpm install` → `pnpm build` (`next build`); Vercel config pins framework and region `bom1`.
- **Local dev:** `Makefile` targets (`dev`, `down`, `migrate`, `seed`, `crawl-all`, `test`, `lint`, `format`, `shell`, `logs`) wrap `docker compose`. `docker-compose.dev.yml` brings up postgres, redis, api (reload), worker (`-c 8`), beat, flower, and web.
- **Tooling:** `ruff` (lint + format, line length 100); `pyproject.toml` declares `pytest` with `testpaths=["tests"]`.

---

## 14. Deployment Process

- **Frontend → Vercel** (`vercel.json`, region `bom1`). Browser calls hit relative `/api/v1`, rewritten to the backend.
- **Backend → Render** (`render.yaml`): a **single `type: web` Docker service** (the API), `plan: free`, health check `/health`, `LOCAL_STORAGE_PATH=/tmp/snapshots`, secrets injected via dashboard (Neon `DATABASE_URL`, Upstash `REDIS_URL`, `GEMINI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD_HASH`).
  - **`render.yaml` defines no Celery worker and no beat service.** In the committed prod config there is no runtime that executes the ingestion/discovery/expiry schedule.
- **Backend Dockerfile** targets Cloud Run conventions (`$PORT`) and bundles Chromium — suggesting Cloud Run was also a considered/used target. Deployment target is **ambiguous** (Render vs Cloud Run) across config and comments.
- A stray **`backend/vercel.json`** exists (FastAPI on Vercel), inconsistent with the Celery/Playwright backend model.

---

## 15. Environment Variables

| Variable | Consumed by | Notes |
|---|---|---|
| `DATABASE_URL` | API + worker | async URL; custom SSL handling |
| `ALEMBIC_DATABASE_URL` | Alembic only | sync URL; optional |
| `REDIS_URL` | Celery | broker + result backend |
| `GEMINI_API_KEY` | AI agents | required for real AI |
| `GEMINI_MODEL` | config (partially) | inconsistent value across files; discovery ignores it |
| `AI_MODE`, `AI_FALLBACK_TO_MOCK` | AI | mock support |
| `STORAGE_TYPE` | storage | `local` / `r2` |
| `LOCAL_STORAGE_PATH` | LocalStorage | `/tmp/snapshots` in prod (ephemeral) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | `R2Storage` | in `.env.example`, **not declared in `Settings`** (see §19) |
| `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` | auth | default secret `change_me` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH` | auth | single admin |
| `MEETUP_CLIENT_ID`, `MEETUP_CLIENT_SECRET` | — | **no consuming code** (dead) |
| `CORS_ORIGINS`, `DEBUG`, `LOG_LEVEL` | API | comma-separated origins |
| `NEXT_PUBLIC_API_URL`, `INTERNAL_API_URL` | frontend | browser vs SSR base URLs |
| `NEXT_PUBLIC_SITE_NAME`, `NEXT_PUBLIC_SITE_URL` | frontend | branding/SEO |
| `DOCKER_ENV` | database | toggles connection pooling |

---

## 16. External Integrations

- **Google Gemini** (`google-genai`) — extraction, classification, moderation, discovery; uses the **Google Search grounding** tool.
- **Playwright / Chromium** — headless rendering of JS-heavy event pages.
- **PostgreSQL** (Neon in prod) · **Redis** (Upstash in prod).
- **Vercel** (hosting + Analytics) · **Render** and/or **Cloud Run** (backend) · **Cloudflare R2** (optional storage, stubbed).
- **`icalendar`** — ICS feed generation.
- **Meetup API** — env placeholders only, unused.

---

## 17. Current User Flow

**Visitor:** lands on `/` → browses featured/this-week or navigates to `/events` → filters by topic/mode/locality/etc. (URL-param driven) → opens an event detail page → clicks out to the external registration/canonical URL. Optionally subscribes to the ICS feed or adds to calendar, or submits an event URL via `/submit`.

**Admin:** logs in at `/mission-control/login` → dashboard tabs to review stats, manage sources, work the moderation queue, edit/feature events, and trigger discovery runs (default or custom queries) or direct URL submits.

---

## 18. Data Flow

```
Discovery agent (Gemini+Search)  ─┐
Public/admin submission form     ─┼─►  manual_submissions  ─►  submission task
Scheduled source crawl           ─┘                              │
                                                                 ▼
        adapter.fetch (Playwright)  ─►  snapshot+hash (object storage)
                                                                 │
                                       deterministic parse ──► AI extraction (Gemini)
                                                                 │
                          normalize ─► relevance ─► dedup ─► publish score
                                                                 │
                                 ┌───────────────┬───────────────┤
                                 ▼               ▼               ▼
                            events (live)   Date-TBA        moderation_queue
                                 │
                         FastAPI read APIs ─► Next.js (rewrite proxy) ─► browser
                                 │
                         JSON / ICS feeds
```

Telemetry for every crawl lands in `crawl_runs`. Raw payloads and AI output are retained in `raw_events`.

---

## 19. Technical Debt

Documented factually (no remediation proposed here):

1. **Conflicting date-sentinel conventions (three).** TBA publish uses `start_at = now.year + 1` (~2027); `_maybe_refresh_existing_event` treats `year ≥ 2090` as "defaulted"; `crawl.py` uses `_JUNK_DATE_YEAR = 2050` for expiry/deletion/re-scrape. A 2027 TBA sentinel matches none of the cleanup thresholds.
2. **Four Gemini model strings** across `.env.example`, `config.py`, `render.yaml`, and a hard-coded `gemini-2.0-flash` in discovery. The `gemini-3.0-flash` default is not a valid public model string.
3. **`R2Storage` reads settings that don't exist.** `storage.py` references `settings.R2_ACCOUNT_ID` etc., but the `Settings` class (with `extra="ignore"`) never declares them → `AttributeError` if `STORAGE_TYPE=r2`.
4. **`rebuild_all_feeds` is a stub** but is scheduled every 30 min and enqueued on every publish — no-op churn; feeds are actually built per-request.
5. **No `canonical_url` uniqueness at the DB level.** Dedup is application-only; `find_duplicate` uses substring `ILIKE` on titles (over-merge risk) with unescaped `LIKE` metacharacters.
6. **Ephemeral snapshot storage in prod** (`/tmp/snapshots` on Render free) undermines the hash-based "skip unchanged" optimization the pipeline depends on for cost control.
7. **`render.yaml` has no worker/beat service** — the scheduled pipeline has no committed prod runtime.
8. **Zero automated tests** despite `pytest` config and a `make test` target pointing at a non-existent `tests/` directory.
9. **Async execution inconsistency:** `workers/utils.run_async` (shared helper) vs discovery's inline `asyncio.get_event_loop().run_until_complete()` — both rely on the deprecated `get_event_loop()`.
10. **`NullPool` on all cloud envs** — every request opens a fresh DB connection; **in-memory SlowAPI** rate limiting is per-instance.
11. **Dead config:** `MEETUP_CLIENT_ID/SECRET`; stray `backend/vercel.json`; duplicate admin login routes (`/admin/login` + `/mission-control/login`).
12. **Search mixes indexed FTS with `ILIKE '%q%'`** in one `OR`, defeating the GIN index.
13. **README drift:** claims Next.js 15, Gemini 3.0 Flash, and lists platform adapters that don't exist; clone URL points to a different personal repo.

---

## 20. Risks

- **Silent pipeline stoppage.** With no worker in `render.yaml`, ephemeral snapshot storage, and a no-op feed rebuild, the ingestion pipeline can stop producing events with no alerting. There is no "no events in N hours" health signal.
- **Gemini quota exhaustion.** Broken change-detection + AI extraction firing on nearly every generic event per crawl can burn free-tier quota quickly.
- **Data-trust risk.** Auto-publish at moderate confidence plus the TBA sentinel scheme can surface wrong or fake-dated events; for a discovery product, incorrect data directly erodes user trust.
- **Duplicate/merge risk.** No DB unique key + substring title matching means both accidental duplicates and accidental over-merges are possible, especially with three near-simultaneous beat tasks.
- **Single admin credential**, default `JWT_SECRET=change_me`, no rotation/refresh — a lapse in env hygiene is a full admin compromise.
- **Deployment ambiguity** (Render vs Cloud Run) raises the chance of drift between what's documented and what actually runs.
- **Playwright in the API image** inflates cold-start/image size on the web service even though rendering is a worker concern.

---

## 21. Open Questions

1. Where does the pipeline actually run in production, given `render.yaml` defines only the web service? (Manual runs? A separate unlisted worker? Cloud Run?)
2. Is the backend deployed on Render, Cloud Run, or both? Which is authoritative?
3. Is `STORAGE_TYPE=r2` ever used in prod, given the missing settings fields would crash it?
4. Which Gemini model is intended as the source of truth across the four strings?
5. Are the two admin login routes intentional, or is one legacy?
6. Is community-submission moderation wired end-to-end, or only modeled?
7. What is the intended cache/CDN story for feeds, given `rebuild_all_feeds` is a stub?
8. Are platform-specific adapters abandoned, deferred, or in a branch not audited here?

---

## 22. Assumptions

- The audited `main` branch reflects the intended current state (5 commits observed).
- Prod infra inferences (Neon, Upstash, Render, Cloud Run) are drawn from config comments and may not match live reality.
- Frontend behavior is inferred from route/component structure and the API client; I did not run the app.
- "Mission Control" (frontend) is the operational admin surface; `/admin/login` is treated as secondary/legacy.
- Absence of a `tests/` directory in the tree means no test suite exists (not merely un-audited).
- The single-admin auth model is the intended access-control design for the current phase.

---

## 23. Missing Documentation

- No architecture/onboarding doc in-repo beyond the README (which has drifted from the code).
- No runbook for operating the pipeline (how discovery is triggered/monitored in prod, how to recover from a stuck queue).
- No deployment doc reconciling Render vs Cloud Run vs Vercel, or documenting which services must run.
- No data-model / ERD documentation; schema must be read from ORM + migrations.
- No documented confidence-threshold rationale or tuning guide for the publish decision.
- No contributor guide for adding a new source adapter (the extension point exists but is undocumented).
- No `.env` documentation distinguishing required vs optional vs dead variables.
- No test or CI documentation (no CI workflow observed).

---

# Project Health Report

Ratings reflect the codebase **as it currently exists**, weighted for a pre-1.0 community MVP.

| Dimension | Score | Explanation |
|---|---|---|
| **Architecture Quality** | **7/10** | Clean modular-monolith split (API / worker / frontend) with sensible layering (routers → services → models) and a well-thought-out, multi-stage confidence-scored pipeline. Loses points for the prod deploy omitting the worker, ambiguous backend target, and stateful assumptions (snapshot hashing) resting on ephemeral infra. |
| **Maintainability** | **5/10** | Readable, consistently styled code with clear module boundaries and good use of `structlog` telemetry. Dragged down by drift and duplication: three date-sentinel conventions, four model strings, dead config, duplicate login routes, and a stubbed task that's still scheduled and enqueued. |
| **Scalability** | **5/10** | Read path is simple and cacheable and the workload is modest; the design *can* scale reads. But `NullPool` (no connection reuse), per-instance in-memory rate limiting, per-request feed generation, and broken snapshot-based change detection are ceilings/inefficiencies. Appropriate for current volume, not yet for spikes. |
| **Code Quality** | **6/10** | Type hints throughout, Pydantic contracts, async I/O, `ruff` configured, thoughtful guardrails (page-type classifier, junk-title regex, grounded-date fallback). Held back by the deprecated event-loop pattern, unescaped `LIKE`, index-defeating search `OR`, and latent crashers (R2 settings). |
| **Documentation Quality** | **4/10** | README is thorough and well-written but has **drifted from the code** (versions, models, adapters, clone URL), which is worse than sparse-but-accurate. No runbook, ERD, deployment, or contributor docs. |
| **Testing Quality** | **1/10** | No tests exist despite `pytest` config and a `make test` target pointing at a missing directory. The pure decision functions (`publish_score`, `relevance`, `dedup`) — which gate what goes live — are entirely unverified. |
| **Developer Experience** | **7/10** | Strong local DX: one-command `make dev` brings up the full stack (db, redis, api, worker, beat, flower, web), Flower for task visibility, Makefile shortcuts, `uv` + `ruff`, clear env example, Next.js rewrite proxy that "just works" across environments. Friction comes from undocumented prod operation and the config inconsistencies a new dev would trip over. |

**Overall:** a **coherent, above-average MVP architecture** with genuinely thoughtful ingestion logic, currently undermined by **testing absence, documentation drift, and a production deployment that doesn't run its own pipeline.** The bones are sound; the risk is operational silence and quiet data-quality decay rather than structural collapse.

---

*End of audit. No changes made; no remediation performed. This document is comprehension-only, per scope.*
