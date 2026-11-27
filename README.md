# Lucknow Tech Events

> A community-built aggregator of tech meetups, hackathons, workshops and conferences in **Lucknow, Uttar Pradesh, India**.

[![Next.js](https://img.shields.io/badge/Next.js-15-black?logo=next.js)](https://nextjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Python-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql)](https://postgresql.org)

Lucknow Tech Events reads public event listings from **configured community/platform sources**, filters and normalizes them, and publishes accepted events through a FastAPI backend and a Next.js frontend. The core ingestion flow is deterministic: it uses platform adapters and Python rules, **not AI-powered discovery or extraction**.

This documentation describes the current `develop` branch and its [v1 core flow](https://github.com/LucknowAI/lucknow-live-events/milestone/1). See [CONTRIBUTING.md](CONTRIBUTING.md) before picking up an issue.

## How the system works

~~~text
PostgreSQL: enabled sources with supported platform keys
                           |
                daily cron at 06:00 IST
                           |
                 python -m engine.run
                           |
          platform adapters (Bevy / Luma / Unstop)
        list_events -> fetch_detail -> to_event
                           |
                NormalizedEvent + rules
                 | rejected: log reason
                 v
             delta / content hash
          new     changed     unchanged
                 |          |
                 v          | (no content write)
              store.py <----'
                 |
     PostgreSQL: events, ingest_runs, event_changes
                 |
        cleanup of expired / missing events
                 |
             FastAPI /api/v1
                 |
            Next.js events UI
~~~

Every engine run fetches each enabled source whose `platform` has a registered adapter. The adapter reads public platform data and produces a common `NormalizedEvent`. Ordered rules validate dates, Lucknow relevance and online-event eligibility. A stable content hash distinguishes new, changed and unchanged events; only changed data is written. Errors on one source do not prevent other sources from running. The runner exits with a nonzero status if any source fails.

Production schedules the engine once daily using [`deploy/crontab`](deploy/crontab) and [`deploy/run-engine.sh`](deploy/run-engine.sh). Optionally, `HEALTHCHECK_URL` reports success or failure to healthchecks.io.

### Tech stack

| Layer | Current implementation |
|---|---|
| Frontend | Next.js 15, React, TypeScript, Tailwind CSS |
| API | FastAPI, Python 3.12, SQLAlchemy async |
| Ingestion | Async Python, HTTPX, Pydantic, platform adapters and ordered rules |
| Database | PostgreSQL 16 with Alembic migrations |
| Scheduler | System cron, one run per day |
| Local tooling | Docker Compose, `uv` for Python, `pnpm` for the frontend |

### Repository layout

~~~text
lucknow-live-events/
├── apps/web/                    # Next.js site, events and calendar pages
├── backend/
│   ├── api/                     # FastAPI routes, schemas, services and models
│   ├── engine/
│   │   ├── run.py               # CLI entry point / source orchestration
│   │   ├── sources/             # bevy.py, luma.py, unstop.py, adapter registry
│   │   ├── models.py            # NormalizedEvent schema
│   │   ├── rules.py             # Event acceptance rules
│   │   ├── delta.py             # Content hash and change classification
│   │   ├── store.py             # Database writes and change history
│   │   ├── cleanup.py           # Missing and expired event handling
│   │   └── http.py              # Shared HTTP client and retries
│   ├── alembic/                 # Database migrations
│   └── tests/                   # Engine fixtures and backend tests
├── scripts/seed_sources.py      # Initial source rows
├── deploy/                      # Cron, engine launcher and log rotation
├── docker/                      # Development / production Compose
├── .env.example.dev             # Development environment template
└── .env.example.prod            # Production environment template
~~~

## Local development

### Prerequisites

- Git and Docker (or Podman) with the Compose plugin
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) to run backend tests on your host
- Node.js 20+ and [`pnpm`](https://pnpm.io/) when running the frontend outside Docker

### 1. Clone and configure

The v1 engine is on `develop`, so clone that branch:

~~~bash
git clone --branch develop https://github.com/LucknowAI/lucknow-live-events.git
cd lucknow-live-events
cp .env.example.dev .env.dev
~~~

In `.env.dev`, set a local PostgreSQL password and **both** database URLs using the same credentials. For example, with a throwaway password used only on your own development machine:

~~~dotenv
POSTGRES_DB=lucknow_events
POSTGRES_USER=user
POSTGRES_PASSWORD=choose_a_local_password
DATABASE_URL=postgresql+asyncpg://user:choose_a_local_password@postgres:5432/lucknow_events
ALEMBIC_DATABASE_URL=postgresql+psycopg2://user:choose_a_local_password@postgres:5432/lucknow_events
~~~

The hostname `postgres` is correct **inside Compose**. For commands run directly on the host, use `localhost` instead. URL-encode reserved characters in real database passwords. Do not commit filled environment files. No model-provider key is needed for ingestion; optional `LLM_*` settings may remain blank.

### 2. Start the services

From the repository root:

~~~bash
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml up -d --build
~~~

| Service | Address |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| Interactive API docs | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |

Only `postgres`, `api` and `web` are part of the development Compose stack. You can inspect logs with:

~~~bash
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml logs -f api web
~~~

### 3. Apply migrations and seed sources

~~~bash
make migrate
make seed
~~~

`make seed` inserts or updates the initial source rows. **Not every seeded platform is currently implemented**: the runner only processes rows whose `platform` matches the adapter registry (`bevy`, `luma` or `unstop`). A source row using an unregistered platform is skipped even if `enabled=true`. See [Adding a source](#add-a-source-db-row) below for a supported example.

### 4. Preview, then run the engine

~~~bash
# Fetch and classify without writing events or ingest history
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec api python -m engine.run --dry-run

# Save accepted events, record ingest history, and run cleanup
make crawl-all

# Limit a run to one registered platform
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec api python -m engine.run --source unstop
~~~

`python -m engine.run` must be executed with `backend/` on the Python path, which is already true inside the `api` container. Run it locally from `backend/` after installing dependencies and supplying a valid `DATABASE_URL`. `--source` accepts a **platform key**, not a source name; it fails if that platform has no enabled rows.

Visit http://localhost:3000/events to inspect saved events. The API also exposes `GET /api/v1/events` with upcoming, ongoing and past status filtering.

### Backend tests

Use a **separate empty test database**, never your development or production event database:

~~~bash
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml up -d postgres
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec postgres createdb -U user lucknow_events_test

# Replace YOUR_LOCAL_PASSWORD with the password from .env.dev.
export TEST_DATABASE_URL='postgresql+asyncpg://user:YOUR_LOCAL_PASSWORD@localhost:5432/lucknow_events_test'
make test
~~~

`make test` runs `uv run --frozen pytest` from `backend/` and expects `TEST_DATABASE_URL` to use `postgresql+asyncpg` and a database name ending in `_test`. Alembic initializes the test schema and tests roll back their transactions. Pass extra arguments with `make test TEST_ARGS='-v'`. For formatting and lint checks, run `cd backend && uvx ruff check .` and `uvx ruff format --check .`.

---

## Environment Variables

Use separate, ignored files for each environment:

- Development: copy `.env.example.dev` to `.env.dev` and pass `--env-file .env.dev` with `docker/docker-compose.dev.yml` (also used by `make dev`).
- Production: copy `.env.example.prod` to `.env.prod` and pass `--env-file .env.prod` with `docker/docker-compose.prod.yml`. This Compose file runs the backend services; configure the separately hosted frontend with the public URL settings below before building it.
- Without Compose: copy `.env.example` to `backend/.env` or export the backend variables in your shell. Use a localhost database URL. Put only frontend URL/site settings in `apps/web/.env.local`; Next.js does not read the repository-root env file.

Run Compose commands from the repository root. `--env-file` supplies Compose interpolation; each Compose file also loads its matching file into the backend services. Shell variables take precedence, so avoid mixing exported production values with development commands. Both templates intentionally leave credentials and database URLs blank. Fill them privately before starting services. Compose rejects a missing database password/URL, and production also requires the admin hash and signing key.

| Variable | Purpose |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Container database settings; the password has no default |
| `DATABASE_URL` | Async PostgreSQL URL with matching credentials (`postgresql+asyncpg://…`) |
| `ALEMBIC_DATABASE_URL` | Sync URL for offline migrations (`postgresql+psycopg2://…`); online migrations use `DATABASE_URL` |
| `HEALTHCHECK_URL` | healthchecks.io ping URL used by the daily engine run; blank disables the ping |
| `JWT_SECRET` | Private backend signing key; use a long random value supplied securely, never `NEXT_PUBLIC_*` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH` | Backend login identity and bcrypt password hash |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Optional future text-completion helper; leave blank for normal operation. The base URL includes the provider's API prefix (such as `/v1`). |
| `CORS_ORIGINS` | Comma-separated frontend origins |
| `DEBUG`, `LOG_LEVEL` | Development/production diagnostics; production Compose forces debug off |
| `STORAGE_TYPE`, `LOCAL_STORAGE_PATH` | Snapshot storage configuration |
| `NEXT_PUBLIC_API_URL`, `INTERNAL_API_URL` | Public API endpoint and server-side/proxy endpoint, including `/api/v1`; available when building and running the frontend |
| `NEXT_PUBLIC_SITE_NAME`, `NEXT_PUBLIC_SITE_URL` | Public site metadata |

Percent-encode reserved password characters in database URLs. Single-quote bcrypt hash values in env files to preserve their literal `$` characters; do not double the dollar signs. Never copy backend secrets into frontend variables or commit filled env files.

`await llm.complete(prompt, system=None)` is available for future features through an OpenAI-compatible `/chat/completions` endpoint. No API route or engine code calls it. It raises `LLMDisabled` without a key; explicitly configure all three `LLM_*` settings before calling it. Provider errors propagate, with no automatic retries or fabricated fallback text.

Open `/mission-control/login` to sign in. There is no query-string key: a key bundled in browser JavaScript cannot protect admin access. The backend still verifies the admin password and requires a valid admin JWT on protected endpoints. With a missing/blank `JWT_SECRET`, login returns 503 and token verification rejects requests; public endpoints remain available. Missing `ADMIN_PASSWORD_HASH` also disables login.

These files describe source configuration only. Applying new database credentials to an existing PostgreSQL volume does not change its password. Existing deployments must retain their configured credentials; changing live settings or rotating credentials is a separate operator action.

---

## Running Services Individually

```bash
# API only
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml up api postgres

# Frontend only (after installing deps)
cd apps/web && pnpm install && pnpm dev

# Engine only (manual run; production uses system cron)
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec api python -m engine.run
```

---

## Database Migrations

```bash
# Apply all pending migrations
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec api alembic upgrade head

# Create a new migration
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec api alembic revision --autogenerate -m "your_description"

# Downgrade one step
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec api alembic downgrade -1
```

---

## Deployment

| Piece | Runs on |
|---|---|
| Frontend | Vercel (`apps/web`) |
| API | Render (`render.yaml`) or the Compose backend |
| PostgreSQL | Neon or Compose Postgres |
| Daily engine | system cron on a VPS or another long-lived host |

The engine is a plain command, not a queue worker. Cron runs
`python -m engine.run` once a day at 06:00 IST. The command pings
healthchecks.io when the run finishes, so a failed or missed run alerts you.

### VPS setup for the engine

Use a dedicated `lucknow` user and keep the checkout at
`/opt/lucknow-live-events`.

1. Install `git`, Python 3.12 and `uv` (on the service user's `PATH`, for
   example `/usr/local/bin/uv`), then create the service user and the
   checkout directory:

   ```bash
   sudo useradd --system --user-group --create-home \
     --home-dir /var/lib/lucknow --shell /bin/sh lucknow
   sudo install -d -o lucknow -g lucknow /opt/lucknow-live-events
   sudo timedatectl set-timezone UTC
   ```

   Keep the server clock on UTC. The crontab entry below uses `00:30 UTC`,
   which is `06:00 Asia/Kolkata`; Debian and Ubuntu cron do not reliably
   support `CRON_TZ`.

2. Clone the repository and install the locked dependencies:

   ```bash
   sudo -u lucknow git clone --branch develop \
     https://github.com/LucknowAI/lucknow-live-events.git \
     /opt/lucknow-live-events
   cd /opt/lucknow-live-events/backend
   sudo -u lucknow uv sync --frozen --no-dev
   ```

3. Create the private env file at `/etc/lucknow-live-events/engine.env`.
   Root owns it; the `lucknow` user reads it through the group:

   ```bash
   sudo install -d -m 0755 /etc/lucknow-live-events
   sudo install -m 0640 -o root -g lucknow /dev/null /etc/lucknow-live-events/engine.env
   sudo "${EDITOR:-vi}" /etc/lucknow-live-events/engine.env
   ```

   It needs at least:

   ```dotenv
   DATABASE_URL=postgresql+asyncpg://…
   ALEMBIC_DATABASE_URL=postgresql+psycopg2://…
   HEALTHCHECK_URL=https://hc-ping.com/…
   ```

   `HEALTHCHECK_URL` is a bearer token; keep the file private. Quote values
   that contain characters your shell treats specially.

4. Apply migrations and seed the source rows once:

   ```bash
   sudo -u lucknow sh -c '
     set -a; . /etc/lucknow-live-events/engine.env; set +a
     cd /opt/lucknow-live-events/backend
     .venv/bin/alembic upgrade head
     .venv/bin/python ../scripts/seed_sources.py
   '
   ```

   The seed holds only platforms with an adapter (`bevy`, `luma`, `unstop`).
   Disable any row you do not want to run.

5. Create the log directory, then test the engine without saving events:

   ```bash
   sudo install -d -o lucknow -g lucknow /var/log/lucknow-live-events
   sudo -u lucknow /opt/lucknow-live-events/deploy/run-engine.sh --dry-run
   sudo tail -n 20 /var/log/lucknow-live-events/engine.log
   ```

6. Create a healthchecks.io check with a period of **1 day** and a grace of
   **1 hour**, then put its ping URL in `HEALTHCHECK_URL`. A run that never
   starts is caught by the grace period, so create the check before scheduling
   cron.

7. Run the engine once and confirm the check turns green:

   ```bash
   sudo -u lucknow /opt/lucknow-live-events/deploy/run-engine.sh
   ```

8. Install the cron and log rotation files:

   ```bash
   sudo install -m 0644 /opt/lucknow-live-events/deploy/crontab /etc/cron.d/lucknow-live-events
   sudo install -m 0644 /opt/lucknow-live-events/deploy/logrotate /etc/logrotate.d/lucknow-live-events
   ```

   Cron calls `/opt/lucknow-live-events/deploy/run-engine.sh` as the `lucknow`
   user at `00:30 UTC`. The script loads the env file, appends to
   `/var/log/lucknow-live-events/engine.log`, and exits with the engine's
   status. Logrotate keeps 14 daily files.

9. Failure drill. In your database console (Neon SQL editor or `psql`), add a
   temporary Bevy source with a bad chapter id:

   ```sql
   INSERT INTO sources (name, platform, base_url, config_json)
   VALUES (
     'Broken Bevy (drill)',
     'bevy',
     'https://gdg.community.dev',
     '{"chapter_id": "not-a-number"}'
   );
   ```

   Run only Bevy sources and confirm the command exits `1` and the check turns
   red:

   ```bash
   sudo -u lucknow /opt/lucknow-live-events/deploy/run-engine.sh --source bevy
   echo "exit=$?"
   ```

   Remove the drill row, then run the engine again to return the check to green:

   ```sql
   DELETE FROM sources WHERE name = 'Broken Bevy (drill)';
   ```

   A source that fails never marks its events as missing.

---

## Pipeline internals

### Supported platforms

| `sources.platform` | Adapter | Discovery scope and configuration |
|---|---|---|
| `bevy` | [`backend/engine/sources/bevy.py`](backend/engine/sources/bevy.py) | GDG / Bevy chapter's public API. Set numeric `config_json.chapter_id`; one source row per chapter. |
| `luma` | [`backend/engine/sources/luma.py`](backend/engine/sources/luma.py) | Luma public discovery by `latitude` / `longitude`, with accepted `categories` or `allowed_hosts` in `config_json`. |
| `unstop` | [`backend/engine/sources/unstop.py`](backend/engine/sources/unstop.py) | Unstop public opportunities for Lucknow. Set `config_json.opportunity_types` to a list drawn from `hackathons`, `competitions` and `workshops`. |

`scripts/seed_sources.py` seeds only these three platforms. A row for any other platform does **not** run until its adapter is implemented and registered. The platform key is significant: for example, a `gdg` source is not automatically treated as `bevy`. A daily cron run is independent of any historical `crawl_interval_hours` metadata on a source row.

### Add a source (DB row)

You do not need to change engine code to track another community on an already-supported platform. Insert an enabled row in the `sources` table, with a registered `platform`, matching `base_url` and the adapter-specific `config_json`. For example, connect to your **development** database:

~~~bash
docker compose --env-file .env.dev -f docker/docker-compose.dev.yml exec postgres psql -U user -d lucknow_events
~~~

Then run this SQL, replacing the illustrative chapter id and URL with **a real Lucknow Bevy chapter**:

~~~sql
INSERT INTO sources (name, platform, base_url, enabled, config_json)
VALUES (
  'Lucknow Bevy chapter',
  'bevy',
  'https://gdg.community.dev/YOUR-CHAPTER/',
  TRUE,
  '{"chapter_id": 1227, "lucknow_community": true}'::jsonb
);
~~~

The sample `chapter_id` is illustrative, not a verified identifier for Lucknow; get the chapter's real id from its page's public data. `lucknow_community: true` permits relevant online events from that community. Set `enabled=false` to pause a source. After creating a row, preview `--source bevy --dry-run` before doing a write run. The admin sources UI/API is another way to manage rows if your account has the required authorization.

### Add a platform (adapter file)

If a platform is missing, first open/claim an issue following [CONTRIBUTING.md](CONTRIBUTING.md). An implementation needs:

1. A new module at `backend/engine/sources/<platform>.py` implementing the adapter interface in [`base.py`](backend/engine/sources/base.py): `list_events(config)`, `external_id(item)`, `fetch_detail(item)` and `to_event(raw, config)`.
2. Mapped [`NormalizedEvent`](backend/engine/models.py) values, or `Rejection(reason)` for an excluded item. Keep HTTP calls in [`engine.http`](backend/engine/http.py) and DB writes in `store.py`.
3. Registration of an **instance** in [`engine/sources/__init__.py`](backend/engine/sources/__init__.py), with a key matching `sources.platform` and the event's `source_platform`.
4. Saved representative API fixtures under `backend/tests/fixtures/` and offline unit tests under `backend/tests/engine/`. No live network dependency in unit tests.
5. At least one enabled source row with that platform key and its required `config_json`.

### Acceptance rules

The runner processes sources through `list_events`, `fetch_detail` and `to_event` before applying [`engine/rules.py`](backend/engine/rules.py). In order, the rules require:

1. A nonempty title and sensible event dates; already-ended events outside the allowed window are excluded.
2. An in-person location in Lucknow, either from address text or within **40 km** of the city reference coordinates.
3. For online events, either `config_json.lucknow_community=true` or an organizer explicitly included in `config_json.allowed_organizers`.

Rejected events are counted by reason in `ingest_runs`. Invalid individual items do not necessarily fail the whole source. The runner logs each source's fetched, rejected, new, updated and unchanged counts.

### Change detection, storage and cleanup

[`delta.py`](backend/engine/delta.py) hashes the normalized event's content and classifies it as **new**, **changed** or **unchanged**. [`store.py`](backend/engine/store.py) inserts new events, updates changed ones and records their field-level differences in `event_changes`; unchanged events need no content rewrite. Seeing the same external ID on a later listing refreshes its last-seen timestamp without counting as a content edit.

Runs record success or failure in `ingest_runs`. A source that fails does not mark its existing events as missing. [`cleanup.py`](backend/engine/cleanup.py) removes events ended over **30 days** ago and hides still-active events only after **two successful source runs** no longer listing them. A successful reappearance clears that expiry marker.

`--dry-run` fetches, validates and classifies but writes neither event content nor ingest-run records. Use it before enabling a new source. A source failure returns a nonzero exit status and can trigger the optional healthcheck failure ping.

---

## How to contribute

Bug fixes, documentation improvements and new platform adapters are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guidelines.

1. **Choose an issue.** Find an unassigned [open issue](https://github.com/LucknowAI/lucknow-live-events/issues), or open one describing your proposed change. Comment to request it and wait for a maintainer to assign you before starting.
2. **Fork and branch from `develop`.** Replace `YOUR_USERNAME` with your GitHub username and choose a descriptive `docs/`, `feat/` or `fix/` branch name:

   ~~~bash
   git clone --branch develop https://github.com/YOUR_USERNAME/lucknow-live-events.git
   cd lucknow-live-events
   git remote add upstream https://github.com/LucknowAI/lucknow-live-events.git
   git fetch upstream
   git switch -c feat/your-change upstream/develop
   ~~~

3. **Set up and make a focused change.** Follow [Local development](#local-development) and address one issue per PR. Include regression coverage; adapter changes need recorded fixtures and offline tests. Never commit secrets or private environment files.
4. **Run the relevant checks.** For backend changes, run `cd backend && uvx ruff check . && uvx ruff format --check .`, then follow [Backend tests](#backend-tests) to run tests with a dedicated test database. For frontend changes, run `cd apps/web && pnpm install && pnpm lint && pnpm format && pnpm build` from the repository root.
5. **Commit and push to your fork.** Use [Conventional Commits](CONTRIBUTING.md#commit-messages), then push your branch, for example: `git push -u origin feat/your-change`.
6. **Open a PR against `LucknowAI/lucknow-live-events:develop`.** Fill in the [PR template](.github/pull_request_template.md), describe what changed and how you verified it, and link the assigned issue with `Closes #<number>`. Respond to review feedback on the same branch.

The initial source list is not the canonical registry of supported platforms. Check [`backend/engine/sources/__init__.py`](backend/engine/sources/__init__.py) when documenting or using the engine.

---

*Built with ❤️ by the Lucknow tech community.*
