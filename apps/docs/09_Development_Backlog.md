# 09 — Development Backlog: Lucknow Event Hub

> **Author role:** Senior Engineering Manager
> **Basis:** Frozen documents `01`–`08`. **No redesign** — this is decomposition only.
> **Import:** A flattened, ready-to-import companion file — `09_Development_Backlog.csv` — accompanies this doc (columns map to Jira / Linear / GitHub Projects). This markdown is the human-readable master.

---

## How to read this backlog

**Hierarchy:** `Epic → Story → Task → Subtask`. IDs are stable keys usable as external references on import.

- Epics: `E##` · Stories: `E##-S#` · Tasks: `E##-S#-T#` · Subtasks: checklist inside the task.
- Every **Task** carries: **Objective · Files to Modify · Dependencies · Acceptance Criteria · Testing Requirements · Complexity · Priority.**
- **Complexity:** story points (Fibonacci 1/2/3/5/8/13). **Priority:** P0 (blocker) · P1 (important) · P2 (later).

**Granularity policy (honoring frozen `07 §1`, "detail one phase ahead"):**
Gates + Phase 0 + Phase 1 → full task/subtask depth. Phase 2 → story/task. Phase 3–4 → epic/story outline. Detailed tasks for later phases are authored at each phase-close review.

**Implementation order = document order below.** Do not start an epic before its dependencies are Done.

---

## Epic index (implementation order)

| # | Epic | Phase | Milestone | Priority | Pts (≈) | Depends on |
|---|---|---|---|---|---|---|
| **E00** | Pre-Build Gates (from `08`) | −1 | Gate | P0 | 26 | — |
| **E01** | Pipeline Reliability & Scheduling | 0 | M0 | P0 | 21 | E00 |
| **E02** | Data Integrity & Dedup Hardening | 0 | M0 | P0 | 8 | E00 |
| **E03** | Config, Secrets & AI Model Consolidation | 0 | M0 | P0 | 11 | E00 |
| **E04** | Platform Hardening (pooling, rate limit) | 0 | M0 | P0 | 8 | E00 |
| **E05** | Security Isolation (SSRF, renderer) | 0 | M0 | P0 | 13 | E00 |
| **E06** | Testing & CI Foundation | 0 | M0 | P0 | 13 | — |
| **E07** | Observability & SLOs | 0 | M0 | P1 | 8 | E01 |
| **E08** | Domain Model Migration (V0→canonical) | 1 | M1 | P0 | 26 | E01,E02,E06 |
| **E09** | Lifecycle State-Machine Engine | 1 | M1 | P0 | 21 | E08 |
| **E10** | Unified Ingestion & Structuring | 1 | M1 | P0 | 16 | E08 |
| **E11** | Canonicalization: Dedup / Merge / Split | 1 | M1 | P0 | 21 | E08,E00-S4 |
| **E12** | AI Enrichment via Model Gateway | 1 | M2 | P1 | 16 | E03,E09 |
| **E13** | Trust Score Engine | 1 | M2 | P0 | 16 | E11,E12 |
| **E14** | Auto-Generated Surfaces / Projections | 1 | M2 | P1 | 13 | E13 |
| **E15** | Search-First (lexical + filter + rank) | 2 | M3 | P1 | 16 | E14,E13 |
| **E16** | Organizer Identity & Reputation | 2 | M4 | P1 | 21 | E08,E13 |
| **E17** | RBAC, Steward Audit & Access | 2 | M4 | P1 | 13 | E05 |
| **E18** | Semantic / NL Search | 3 | M5 | P2 | 16 | E15 |
| **E19** | Proactive Discovery (notifications/digest) | 3 | M5 | P2 | 16 | E16 |
| **E20** | Analytics Engine | 3 | M5 | P2 | 21 | E14 |
| **E21** | Ecosystem Platform (API/assistant/reports) | 4 | M6 | P2 | 34 | E18,E20 |
| **E22** | Multi-City Federation | 4 | M6 | P2 | 13 | E08 |

---

# E00 — Pre-Build Gates
*Phase −1 · Gate · P0 · these are the frozen `08` conditions that must clear before build epics start.*

## E00-S1 — Demand validation experiment
**E00-S1-T1 — Ship minimal aggregator to ~100 real users & measure return usage**
- **Objective:** Prove repeat demand before investing in the pipeline (`08 §4`).
- **Files to Modify:** *none in core repo* — use existing V0 surface + a manual/curated event list; add a lightweight analytics event (`apps/web/app/layout.tsx` already wires Vercel Analytics).
- **Dependencies:** —
- **Acceptance Criteria:** Experiment run for ≥2 weeks with ≥100 real Lucknow users; return-usage and search/click metrics captured; documented continue/kill decision.
- **Testing Requirements:** N/A (product experiment); instrument event tracking verified firing.
- **Complexity:** 8 · **Priority:** P0
- **Subtasks:** ☐ define single kill/continue metric ☐ recruit via community channels ☐ instrument return-visit tracking ☐ write decision memo.

## E00-S2 — Legal, ToS & DPDP privacy position
**E00-S2-T1 — Author scraping/ToS/robots policy + DPDP consent model**
- **Objective:** Establish a defensible legal/privacy posture for aggregating third-party data and organizer PII (`08 §2.2`).
- **Files to Modify:** `docs/legal/COMPLIANCE.md` (new); `backend/ingestion/adapters/base.py` (robots/politeness hook — spec only, no redesign).
- **Dependencies:** —
- **Acceptance Criteria:** Written policy covers per-source ToS posture, robots.txt handling, takedown process, DPDP consent basis for organizer data, and data license for the graph; reviewed by a legal advisor or documented risk acceptance.
- **Testing Requirements:** N/A (document); checklist review.
- **Complexity:** 5 · **Priority:** P0
- **Subtasks:** ☐ per-source ToS matrix ☐ takedown workflow ☐ DPDP basis for organizer profiles ☐ graph data license.

## E00-S3 — Input-security threat model
**E00-S3-T1 — Document threat model: prompt injection, amplification DoS, stored XSS**
- **Objective:** Define defenses for a system whose entire input is hostile web content (`08 §2.5`).
- **Files to Modify:** `docs/security/THREAT_MODEL.md` (new).
- **Dependencies:** —
- **Acceptance Criteria:** Documented controls for scraped-text-as-data (never instructions), schema-constrained AI output, submission cost quotas + circuit breaker, and description sanitization; each control mapped to an implementing epic (E05/E12).
- **Testing Requirements:** N/A (document); traceability to E05/E12 tasks.
- **Complexity:** 5 · **Priority:** P0

## E00-S4 — Deduplication specification + labeled eval set
**E00-S4-T1 — Specify dedup algorithm and build a labeled evaluation set**
- **Objective:** Turn the under-specified core capability into a concrete, testable spec (`08 §2.4`).
- **Files to Modify:** `docs/spec/DEDUP.md` (new); `backend/tests/fixtures/dedup/` (new labeled dataset).
- **Dependencies:** —
- **Acceptance Criteria:** Spec defines blocking keys, similarity features, thresholds, recurring-series handling, and fact-conflict resolution; ≥50 labeled pairs (match/non-match) committed as the acceptance gate for E11.
- **Testing Requirements:** Eval harness stub that scores precision/recall against the labeled set.
- **Complexity:** 5 · **Priority:** P0

## E00-S5 — V1 scope-trim sign-off
**E00-S5-T1 — Confirm trimmed V1 scope (push-first ingestion; no semantic search/trust-engine-lite)**
- **Objective:** Record the frozen scope decisions from `08 §4` so build epics inherit them.
- **Files to Modify:** `docs/DECISIONS.md` (new).
- **Dependencies:** E00-S1
- **Acceptance Criteria:** Documented, signed-off scope: trusted-source/push ingestion primary; semantic search deferred to P3; V1 trust = transparent boolean signals surfaced as badges (not raw score).
- **Testing Requirements:** N/A.
- **Complexity:** 3 · **Priority:** P0

---

# E01 — Pipeline Reliability & Scheduling
*Phase 0 · M0 · P0 · from `07` P0-04/05/06/07.*

## E01-S1 — Durable scheduling & worker plane
**E01-S1-T1 — Stand up durable scheduler → queue → worker (single-fire)**
- **Objective:** Fix the frozen finding that prod had no committed worker/beat (`01 §14`, `07` P0-05).
- **Files to Modify:** `render.yaml`; `docker/docker-compose.dev.yml`; `backend/workers/celery_app.py`; `backend/workers/schedules.py`; infra config (new `infra/scheduler.*`).
- **Dependencies:** E00
- **Acceptance Criteria:** Discovery/crawl/expiry run on schedule in prod, verifiable in logs; no duplicate scheduled executions; worker autoscaling wired.
- **Testing Requirements:** Integration test: scheduled task enqueues once; worker consumes; idempotent on double-fire.
- **Complexity:** 8 · **Priority:** P0
- **Subtasks:** ☐ managed scheduler ☐ worker service in deploy config ☐ single-fire/leader guard ☐ verify in staging.

**E01-S1-T2 — Fix async execution (remove deprecated get_event_loop)**
- **Objective:** `07` P0-07 — safe async on 3.12 prefork.
- **Files to Modify:** `backend/workers/tasks/discovery.py`; `backend/workers/utils.py`.
- **Dependencies:** —
- **Acceptance Criteria:** All Celery tasks use a safe runner; no deprecation warnings; discovery task runs cleanly.
- **Testing Requirements:** Unit test on the async runner; task smoke test.
- **Complexity:** 2 · **Priority:** P1

## E01-S2 — Cost-safe change detection
**E01-S2-T1 — Move change-detection from ephemeral FS to DB content-hash**
- **Objective:** `07` P0-04 — stop re-processing unchanged pages / quota burn.
- **Files to Modify:** `backend/ingestion/pipeline.py`; `backend/ingestion/storage.py`; `backend/api/models/raw_event.py` (add `content_hash`); new Alembic migration.
- **Dependencies:** E01-S1
- **Acceptance Criteria:** `raw_capture.content_hash` persisted; unchanged pages skip AI extraction; verified across restart/redeploy.
- **Testing Requirements:** Integration: same page twice → one extraction; changed page → re-extraction.
- **Complexity:** 5 · **Priority:** P0

## E01-S3 — Pipeline health alerting
**E01-S3-T1 — "No events published in N hours" + "discovery success = 0" alert**
- **Objective:** `07` P0-06 — the #1 silent-failure guard (`08 §3`).
- **Files to Modify:** `backend/platform/observability/*` (new); alerting config `infra/alerts.*`.
- **Dependencies:** E01-S1
- **Acceptance Criteria:** Alert fires when published/hour = 0 for N hours or discovery success = 0; runbook linked.
- **Testing Requirements:** Synthetic test triggering the alert condition.
- **Complexity:** 3 · **Priority:** P0

---

# E02 — Data Integrity & Dedup Hardening
*Phase 0 · M0 · P0 · from `07` P0-08.*

## E02-S1 — DB-level idempotency
**E02-S1-T1 — Add unique constraint on canonical_url + escape LIKE in dedup**
- **Objective:** Fix double-insert race + substring over-merge (`01 §19`, `07` P0-08).
- **Files to Modify:** `backend/ingestion/dedup.py`; `backend/api/models/event.py`; new Alembic migration.
- **Dependencies:** E00-S4
- **Acceptance Criteria:** Concurrent crawls cannot double-insert; substring-title over-merge removed; LIKE metacharacters escaped.
- **Testing Requirements:** Unit tests for LIKE escaping + over-merge cases; integration test for concurrent insert.
- **Complexity:** 5 · **Priority:** P0
- **Subtasks:** ☐ pre-migration collision cleanup ☐ add unique index ☐ rewrite match query ☐ tests.

**E02-S1-T2 — Reconcile the three date-sentinel conventions (interim)**
- **Objective:** Stop the 2027/2050/2090 conflict corrupting cleanup (`01 §19`). Interim fix before E08 nullable dates.
- **Files to Modify:** `backend/ingestion/pipeline.py`; `backend/workers/tasks/crawl.py`.
- **Dependencies:** —
- **Acceptance Criteria:** One sentinel convention until E08 replaces it; cleanup thresholds match the sentinel.
- **Testing Requirements:** Unit test: TBA event is caught by cleanup; no orphaned sentinels.
- **Complexity:** 3 · **Priority:** P1

---

# E03 — Config, Secrets & AI Model Consolidation
*Phase 0 · M0 · P0 · from `07` P0-01/13.*

## E03-S1 — Single model configuration
**E03-S1-T1 — Route all AI calls through one configurable model string**
- **Objective:** Kill the four-model-string chaos + hardcode (`01 §12/19`, `07` P0-01).
- **Files to Modify:** `backend/api/core/config.py`; `backend/ai/gemini_client.py`; `backend/ai/extraction_agent.py`; `backend/ai/classification_agent.py`; `backend/ai/moderation_agent.py`; `backend/workers/tasks/discovery.py`; `.env.example`; `render.yaml`.
- **Dependencies:** E00
- **Acceptance Criteria:** All callers read one model setting; startup fails on invalid/empty; grep shows zero hardcoded model strings.
- **Testing Requirements:** Unit test: startup validation; mock gateway honors configured model.
- **Complexity:** 5 · **Priority:** P0

## E03-S2 — Config & secrets hygiene
**E03-S2-T1 — Remove dead/broken config; secrets to manager**
- **Objective:** `07` P0-13 — default `JWT_SECRET`, dead Meetup vars, undeclared R2 settings, README drift.
- **Files to Modify:** `backend/api/core/config.py`; `backend/ingestion/storage.py`; `.env.example`; `render.yaml`; `README.md`; `apps/web` (stray `backend/vercel.json` removal).
- **Dependencies:** —
- **Acceptance Criteria:** No default secret in prod; secrets via manager; `STORAGE_TYPE=r2` no longer crashes (R2 settings declared); dead vars removed; README/clone URL corrected; duplicate admin login route resolved.
- **Testing Requirements:** Config load test with r2 selected; smoke test admin login single-route.
- **Complexity:** 3 · **Priority:** P1
- **Subtasks:** ☐ declare R2 settings ☐ remove Meetup vars ☐ remove stray vercel.json ☐ fix README ☐ dedupe admin login.

**E03-S2-T2 — Fix facet counts loaded into app memory**
- **Objective:** Remove O(n) in-Python facet aggregation (`08 §2.6`).
- **Files to Modify:** `backend/api/services/discovery_service.py`.
- **Dependencies:** —
- **Acceptance Criteria:** Topic/community/locality facets computed in-DB with proper aggregation/indexing.
- **Testing Requirements:** Query test verifying DB-side aggregation; no full-table load into Python.
- **Complexity:** 3 · **Priority:** P2

---

# E04 — Platform Hardening
*Phase 0 · M0 · P0 · from `07` P0-02/03.*

## E04-S1 — Connection pooling
**E04-S1-T1 — Introduce PgBouncer; remove NullPool-per-request**
- **Objective:** Fix per-request new connection (`01 §7`, `07` P0-03).
- **Files to Modify:** `backend/api/core/database.py`; `render.yaml`/`infra`; `docker/docker-compose.dev.yml`.
- **Dependencies:** —
- **Acceptance Criteria:** App connects via pooler; stable connection count under concurrent reads.
- **Testing Requirements:** Load test showing bounded connections.
- **Complexity:** 3 · **Priority:** P0

## E04-S2 — Distributed rate limiting
**E04-S2-T1 — Replace in-memory limiter with Redis-backed distributed limiter**
- **Objective:** `07` P0-02 — global limits across instances.
- **Files to Modify:** `backend/api/core/limiter.py`; `backend/api/main.py`; `backend/api/routers/submissions.py`.
- **Dependencies:** —
- **Acceptance Criteria:** Limits enforced globally (verified with 2+ instances); tiers for read/submission/auth.
- **Testing Requirements:** Integration test across two app instances hitting shared Redis.
- **Complexity:** 5 · **Priority:** P0

---

# E05 — Security Isolation
*Phase 0 · M0 · P0 · from `07` P0-11/12 + `08` input-security.*

## E05-S1 — SSRF sandbox
**E05-S1-T1 — Egress-restricted renderer sandbox + URL allow/deny**
- **Objective:** Block SSRF on arbitrary URL fetch (`06 §19`, `08 §3`).
- **Files to Modify:** `backend/ingestion/adapters/playwright_util.py`; `backend/ingestion/adapters/base.py`; renderer infra config.
- **Dependencies:** E00-S3
- **Acceptance Criteria:** Internal IP ranges/metadata endpoints unreachable from renderers; timeouts + response-size caps enforced.
- **Testing Requirements:** Security test suite: attempts to fetch internal/metadata addresses are blocked.
- **Complexity:** 5 · **Priority:** P0

## E05-S2 — Renderer isolation
**E05-S2-T1 — Move Playwright to a dedicated worker image/pool**
- **Objective:** `07` P0-12 — keep Chromium out of the API image and hot path.
- **Files to Modify:** `backend/Dockerfile`; new `backend/Dockerfile.renderer`; `docker/docker-compose.dev.yml`; deploy config.
- **Dependencies:** E01-S1
- **Acceptance Criteria:** API image excludes Chromium; renderers run in isolated pool with warm floor.
- **Testing Requirements:** Build test (API image size drop); renderer smoke test.
- **Complexity:** 3 · **Priority:** P1

## E05-S3 — Content-injection defenses
**E05-S3-T1 — Sanitize scraped content + submission cost circuit-breaker**
- **Objective:** Stored-XSS + amplification-DoS controls (`08 §2.5`).
- **Files to Modify:** `backend/ingestion/normalizers/text.py`; `backend/api/routers/submissions.py`; `backend/api/services/submission_service.py`; frontend render points (`apps/web/app/events/[slug]/page.tsx`).
- **Dependencies:** E00-S3, E04-S2
- **Acceptance Criteria:** Scraped descriptions sanitized before storage/render; submission pipeline has per-source/day cost quota + breaker on abuse.
- **Testing Requirements:** XSS payload test (not rendered as HTML); abuse test trips breaker.
- **Complexity:** 5 · **Priority:** P0

---

# E06 — Testing & CI Foundation
*Phase 0 · M0 · P0 · from `07` P0-09/10.*

## E06-S1 — CI pipeline
**E06-S1-T1 — CI: lint + typecheck + tests + dependency/image scan, gated on PR**
- **Objective:** `07` P0-09 — no merge without green checks (`01 §Testing 1/10`).
- **Files to Modify:** `.github/workflows/ci.yml` (new); `backend/pyproject.toml`; `apps/web/package.json`.
- **Dependencies:** —
- **Acceptance Criteria:** PRs blocked on failing lint/tests; SAST + dep + image scan run; `backend/tests/` exists and executes.
- **Testing Requirements:** Meta: a failing test blocks merge (verified).
- **Complexity:** 5 · **Priority:** P0

## E06-S2 — Decision-function unit tests
**E06-S2-T1 — Unit tests for dedup, relevance, publish-score, validation**
- **Objective:** `07` P0-10 — test the functions that decide what publishes.
- **Files to Modify:** `backend/tests/unit/test_dedup.py`, `test_relevance.py`, `test_publish_score.py` (new); targets in `backend/ingestion/*`.
- **Dependencies:** E06-S1, E00-S4
- **Acceptance Criteria:** ≥90% branch coverage on those functions; boundary + edge cases covered; dedup tests run against the E00-S4 labeled set.
- **Testing Requirements:** These *are* the tests; run in CI.
- **Complexity:** 5 · **Priority:** P0

## E06-S3 — Golden dataset harness
**E06-S3-T1 — Labeled fixture set + pipeline regression harness (mocked AI)**
- **Objective:** Regression net for extraction/dedup/trust (`07 §11`).
- **Files to Modify:** `backend/tests/fixtures/pages/` (new); `backend/tests/integration/test_pipeline_golden.py` (new); AI mock in `backend/tests/mocks/`.
- **Dependencies:** E06-S1
- **Acceptance Criteria:** Pipeline runs end-to-end against fixtures with a deterministic AI mock; regressions fail CI.
- **Testing Requirements:** Harness itself; deterministic, no live LLM.
- **Complexity:** 3 · **Priority:** P1

---

# E07 — Observability & SLOs
*Phase 0 · M0 · P1 · from `07` OBS.*

## E07-S1 — Metrics, tracing, SLOs
**E07-S1-T1 — RED/USE metrics + correlation-id tracing across API→queue→worker→gateway**
- **Objective:** `06 §17/18` — see the async lifecycle end-to-end.
- **Files to Modify:** `backend/platform/observability/*` (new); `backend/api/main.py`; `backend/workers/*`; `backend/ai/gemini_client.py`.
- **Dependencies:** E01-S1
- **Acceptance Criteria:** RED per endpoint/worker; USE for resources; a single event's journey traceable by correlation id; SLO dashboard live.
- **Testing Requirements:** Trace-propagation test across a queue boundary.
- **Complexity:** 5 · **Priority:** P1

**E07-S1-T2 — Pipeline & data-quality dashboards**
- **Objective:** Published/hour, discovery success, queue/DLQ depth, dead-link rate, moderation backlog.
- **Files to Modify:** `infra/dashboards/*` (new).
- **Dependencies:** E07-S1-T1
- **Acceptance Criteria:** Dashboards render the pipeline-health and data-quality signals from `06 §17`.
- **Testing Requirements:** Smoke: metrics populate under synthetic load.
- **Complexity:** 3 · **Priority:** P1

---

# E08 — Domain Model Migration (V0 → Canonical)
*Phase 1 · M1 · P0 · from `07` DM-01..04. Expand→contract, reversible (`07 §10`).*

## E08-S1 — City dimension
**E08-S1-T1 — Add City entity; backfill Lucknow**
- **Objective:** First-class city scope (ADR-011).
- **Files to Modify:** `backend/api/models/` (new `city.py`); new Alembic migration; `backend/contexts/*` scoping.
- **Dependencies:** E02
- **Acceptance Criteria:** `city` exists; all rows backfilled to Lucknow; city on canonical identity.
- **Testing Requirements:** Migration up/down test; backfill count assertion.
- **Complexity:** 5 · **Priority:** P0

## E08-S2 — Canonical event with real date semantics
**E08-S2-T1 — Introduce canonical_event; nullable start_at + date_status; migrate sentinels**
- **Objective:** Replace sentinel-date hack (`01 §19`, `07` DM-02).
- **Files to Modify:** `backend/api/models/event.py`; new Alembic migration; `backend/api/services/event_service.py`; calendar/list ordering.
- **Dependencies:** E08-S1, E02-S1-T2
- **Acceptance Criteria:** `start_at` nullable + `date_status` enum; sentinels migrated to `tba`/null; calendar excludes TBA; lists order `NULLS LAST`.
- **Testing Requirements:** Migration test; unit tests for TBA ordering/exclusion.
- **Complexity:** 8 · **Priority:** P0

## E08-S3 — Source observation (evidence)
**E08-S3-T1 — Add source_observation table; backfill from raw_events**
- **Objective:** Merge-not-duplicate provenance (ADR-002, `07` DM-03).
- **Files to Modify:** `backend/api/models/` (new `source_observation.py`); `backend/api/models/raw_event.py`; new Alembic migration + backfill.
- **Dependencies:** E08-S2
- **Acceptance Criteria:** Each canonical event ≥1 observation; existing raw_events mapped preserving provenance.
- **Testing Requirements:** Backfill integrity test (no orphans; counts reconcile).
- **Complexity:** 8 · **Priority:** P0

## E08-S4 — Composite constraints
**E08-S4-T1 — (city_id, canonical_url) & (city_id, slug) unique**
- **Objective:** Per-city idempotency (`07` DM-04).
- **Files to Modify:** `backend/api/models/event.py`; new Alembic migration (post-cleanup).
- **Dependencies:** E08-S1, E02-S1-T1
- **Acceptance Criteria:** Constraints enforced after collision cleanup; dedup relies on DB backstop.
- **Testing Requirements:** Integration: duplicate insert rejected per city.
- **Complexity:** 5 · **Priority:** P0

---

# E09 — Lifecycle State-Machine Engine
*Phase 1 · M1 · P0 · from `07` LC-01. Decompose `pipeline.py` (`07 §9`).*

## E09-S1 — Persisted, resumable pipeline
**E09-S1-T1 — Persisted pipeline_state + idempotent, resumable stages**
- **Objective:** Turn the linear script into a resilient workflow (`06 §14`).
- **Files to Modify:** `backend/contexts/lifecycle/*` (new, extracted from `backend/ingestion/pipeline.py`); `backend/api/models/raw_event.py`; migration for `pipeline_state`.
- **Dependencies:** E08-S3
- **Acceptance Criteria:** Each event has persisted state; crashed worker resumes; every stage re-runnable; "stuck in state X" queryable.
- **Testing Requirements:** Integration: kill mid-stage → resume completes; each stage idempotent under double-delivery.
- **Complexity:** 13 · **Priority:** P0
- **Subtasks:** ☐ define states/transitions ☐ extract stages from pipeline.py ☐ persistence + resume ☐ idempotency keys ☐ state metrics.

## E09-S2 — Queue lanes
**E09-S2-T1 — Per-weight queue lanes (heavy render / light / priority) + DLQ**
- **Objective:** Prevent head-of-line blocking (`06 §9`).
- **Files to Modify:** `backend/platform/queue/*` (new); `backend/workers/celery_app.py`; `backend/workers/tasks/*`.
- **Dependencies:** E09-S1
- **Acceptance Criteria:** Render bursts don't block light/priority work; poison messages park in DLQ.
- **Testing Requirements:** Integration: heavy-lane backlog doesn't delay priority lane; DLQ receives poison message.
- **Complexity:** 5 · **Priority:** P1

---

# E10 — Unified Ingestion & Structuring
*Phase 1 · M1 · P0 · from `07` B-01/02.*

## E10-S1 — Adapter contract
**E10-S1-T1 — Formalize pluggable source-adapter contract**
- **Objective:** Add sources by config, not lifecycle change (ADR-003).
- **Files to Modify:** `backend/contexts/ingestion/adapters/*` (from `backend/ingestion/adapters/`); `backend/ingestion/adapters/base.py`.
- **Dependencies:** E09-S1
- **Acceptance Criteria:** New source = config + adapter, no lifecycle edits; generic + static conform to the contract.
- **Testing Requirements:** Contract test all adapters satisfy; a stub new-source adapter passes without core edits.
- **Complexity:** 5 · **Priority:** P1

## E10-S2 — Validation stage
**E10-S2-T1 — Validation: real / upcoming / tech-relevant / registration-link-valid**
- **Objective:** Nothing invalid publishes (`03` B3).
- **Files to Modify:** `backend/contexts/lifecycle/validate.py` (new); `backend/ingestion/normalizers/*`.
- **Dependencies:** E09-S1
- **Acceptance Criteria:** Non-events rejected; unreachable registration link blocks publish (→ moderation).
- **Testing Requirements:** Unit tests per rule; integration with a dead-link fixture.
- **Complexity:** 5 · **Priority:** P0

---

# E11 — Canonicalization: Dedup / Merge / Split
*Phase 1 · M1 · P0 · from `07` C-01/02/03. Gated by E00-S4 spec + eval set.*

## E11-S1 — Similarity & matching
**E11-S1-T1 — Similarity scoring + precision-first match rules**
- **Objective:** Decide same-real-world-event (`05 §6`).
- **Files to Modify:** `backend/contexts/event/dedup.py` (from `backend/ingestion/dedup.py`).
- **Dependencies:** E08-S3, E00-S4
- **Acceptance Criteria:** Combined similarity across title/date/venue/organizer/URL + AI score; thresholds meet E00-S4 eval precision target; recurring-series handled per spec.
- **Testing Requirements:** Precision/recall against E00-S4 labeled set in CI (gating).
- **Complexity:** 8 · **Priority:** P0

## E11-S2 — Merge & split
**E11-S2-T1 — Merge into canonical with provenance; reversible split**
- **Objective:** Preserve evidence; reversible (ADR-002).
- **Files to Modify:** `backend/contexts/event/merge.py` (new); `backend/api/models/source_observation.py`.
- **Dependencies:** E11-S1
- **Acceptance Criteria:** Merge preserves all evidence + records reason; steward can split a wrong merge back to distinct events.
- **Testing Requirements:** Integration: merge then split restores prior state; provenance intact.
- **Complexity:** 8 · **Priority:** P0

## E11-S3 — Steward review surface
**E11-S3-T1 — Uncertain-match review UI + API**
- **Objective:** Human decides ambiguous cases (`05 §6`).
- **Files to Modify:** `backend/api/routers/admin/*`; `apps/web/app/mission-control/_components/*` (new review tab).
- **Dependencies:** E11-S1
- **Acceptance Criteria:** Uncertain merges queue with AI reasoning + evidence shown; approve/correct/reject updates canonical.
- **Testing Requirements:** API contract test; e2e steward decision updates record.
- **Complexity:** 5 · **Priority:** P1

---

# E12 — AI Enrichment via Model Gateway
*Phase 1 · M2 · P1 · from `07` D-01/02.*

## E12-S1 — Model Gateway
**E12-S1-T1 — Provider-agnostic gateway: batching, content-hash cache, guardrails, routing**
- **Objective:** Contain model volatility; enable cost controls (`06 §13`).
- **Files to Modify:** `backend/platform/gateway/*` (new, wrapping `backend/ai/gemini_client.py`); `backend/ai/*` callers.
- **Dependencies:** E03-S1
- **Acceptance Criteria:** One entry point; unchanged inputs served from cache; schema-constrained outputs; provider fallback; batch path exists.
- **Testing Requirements:** Unit: cache hit avoids call; schema violation rejected; fallback path exercised (mocked).
- **Complexity:** 8 · **Priority:** P1

## E12-S2 — Enrichment
**E12-S2-T1 — Category / tags / audience / format / summary / embedding**
- **Objective:** Auto-classify published events (`03` D1/D2).
- **Files to Modify:** `backend/contexts/enrichment/*` (from `backend/ai/classification_agent.py`); `backend/api/models/event.py` (embedding column, P3-ready).
- **Dependencies:** E12-S1
- **Acceptance Criteria:** Published events auto-classified; each enrichment explainable/attributable.
- **Testing Requirements:** Unit with mocked gateway; explainability field populated.
- **Complexity:** 5 · **Priority:** P1

---

# E13 — Trust Score Engine
*Phase 1 · M2 · P0 · from `07` E-01/02/03. Per `08 §4`, surface as badges, store score internally.*

## E13-S1 — Scoring
**E13-S1-T1 — Signals → versioned weighted score + JSONB breakdown**
- **Objective:** Transparent, explainable, deterministic trust (`05 §7`).
- **Files to Modify:** `backend/contexts/trust/*` (from `backend/ingestion/publish_score.py`,`relevance.py`); `backend/api/models/` (new `trust_score.py`); migration.
- **Dependencies:** E11-S2, E12-S2
- **Acceptance Criteria:** Deterministic; `policy_version` stored; signal breakdown persisted; "why this score?" answerable.
- **Testing Requirements:** Unit tests per signal; determinism test; version recorded.
- **Complexity:** 8 · **Priority:** P0

## E13-S2 — Gate, Featured & policy config
**E13-S2-T1 — Publish gate by threshold + objective Featured + config weights**
- **Objective:** Gate publishing; objective Featured (`04` ADR-006).
- **Files to Modify:** `backend/contexts/trust/policy.py` (new); `backend/contexts/lifecycle/publish.py`.
- **Dependencies:** E13-S1
- **Acceptance Criteria:** Below threshold → moderation; Featured is a pure function of trust+freshness+completeness (no manual entries); weights tunable via config, versioned.
- **Testing Requirements:** Unit: threshold gating; Featured rule; config change reflected + versioned.
- **Complexity:** 5 · **Priority:** P0

**E13-S2-T2 — Surface trust as qualitative badges (not raw number)**
- **Objective:** Honor `08 §2.7` UX finding.
- **Files to Modify:** `apps/web/components/EventCard.tsx`; `apps/web/app/events/[slug]/page.tsx`; `apps/web/lib/api.ts`.
- **Dependencies:** E13-S1
- **Acceptance Criteria:** Users see badges ("date confirmed," "verified organizer," "link checked"), not `0.72`; raw score internal only.
- **Testing Requirements:** Component test rendering badges from signal breakdown.
- **Complexity:** 3 · **Priority:** P1

---

# E14 — Auto-Generated Surfaces / Projections
*Phase 1 · M2 · P1 · from `07` G-01/02/03.*

## E14-S1 — Projection read model
**E14-S1-T1 — Materialize search_document from canonical**
- **Objective:** No runtime joins on hot path (ADR-007).
- **Files to Modify:** `backend/contexts/search/projection.py` (new); migration.
- **Dependencies:** E13-S2
- **Acceptance Criteria:** One denormalized row per published event; rebuilt on publish/update.
- **Testing Requirements:** Integration: publish → projection present & correct.
- **Complexity:** 5 · **Priority:** P1

## E14-S2 — Feeds & cache purge
**E14-S2-T1 — Feeds (JSON/ICS) as CDN objects; purge on publish**
- **Objective:** Replace stub `rebuild_all_feeds` (`01`, `07` G-02).
- **Files to Modify:** `backend/workers/tasks/feeds.py`; `backend/api/routers/feeds.py`; `backend/platform/cache/*`.
- **Dependencies:** E14-S1
- **Acceptance Criteria:** Feeds served as static objects; update within one TTL of publish; affected cache keys purged.
- **Testing Requirements:** Integration: publish → feed object updated; cache key purged.
- **Complexity:** 5 · **Priority:** P1

## E14-S3 — Generated surfaces
**E14-S3-T1 — Pages/cards/calendar generated from canonical (+ ISR revalidate)**
- **Objective:** No hand-authored surfaces (ADR-007).
- **Files to Modify:** `apps/web/app/events/*`, `apps/web/app/calendar/page.tsx`, `apps/web/components/*`; on-demand revalidation webhook.
- **Dependencies:** E14-S1
- **Acceptance Criteria:** All surfaces render from canonical; publish/update triggers revalidation; registration points out.
- **Testing Requirements:** e2e: publish → page/card/calendar reflect within TTL.
- **Complexity:** 3 · **Priority:** P1

---

# E15 — Search-First (lexical + filter + rank)
*Phase 2 · M3 · P1 · from `07` F. Stories only (detail at Phase-1 close).*

- **E15-S1 — FTS + filters on `search_document`** — Files: `backend/contexts/search/*`, `backend/api/routers/events.py`. AC: keyword+filter search served from projection. Pts 5 · P1.
- **E15-S2 — Ranking = relevance × trust × freshness (explainable)** — Files: `backend/contexts/search/rank.py`. AC: transparent ordering; "why" available. Pts 5 · P1.
- **E15-S3 — Fix indexed search (remove ILIKE OR-branch defeating GIN)** — Files: `backend/api/services/event_service.py`. AC: search uses GIN, no seq scan. Pts 2 · P1.
- **E15-S4 — Search health metrics** — Files: `backend/platform/observability/*`. AC: success-rate/latency tracked. Pts 3 · P2.

---

# E16 — Organizer Identity & Reputation
*Phase 2 · M4 · P1 · from `07` H. Stories only. Note: gated by `08` DPDP position (E00-S2).*

- **E16-S1 — Organizer & Venue entities + backfill** — Pts 5 · P1 · dep E08.
- **E16-S2 — Auto organizer profiles (upcoming/past/categories/links)** — Pts 5 · P1.
- **E16-S3 — Organizer claim & verify flow** — Pts 5 · P1.
- **E16-S4 — Reputation v1 (history-derived signals)** — Pts 8 · P1 · dep E13. **Blocked until E00-S2 privacy sign-off.**

---

# E17 — RBAC, Steward Audit & Access
*Phase 2 · M4 · P1 · from `07` SEC + `08 §2.5`.*

- **E17-S1 — RBAC roles (steward/admin/super-admin) + refresh tokens** — Files: `backend/api/core/security.py`,`deps.py`. Pts 5 · P1.
- **E17-S2 — Steward action audit log + 2FA + policy-change approval** — Files: `backend/contexts/admin/*`. AC: every merge/trust-policy/featured action logged; 2FA on steward accounts; policy changes require approval. Pts 8 · P1 · (closes `08` insider-risk finding).

---

# E18 — Semantic / NL Search  *(Phase 3 · M5 · P2 — epic/story outline)*
- E18-S1 Embeddings + pgvector HNSW index — Pts 5.
- E18-S2 Hybrid search (RRF fusion) + rerank by trust/freshness — Pts 8.
- E18-S3 NL query understanding — Pts 5.
- *(Deferred per `08 §4` until V1 has users; detail at Phase-2 close.)*

# E19 — Proactive Discovery  *(Phase 3 · M5 · P2)*
- E19-S1 Subscriptions (category/organizer/venue) — Pts 5.
- E19-S2 Weekly digest generation + delivery (opt-in/unsubscribe per `08`) — Pts 8.
- E19-S3 Notifications — Pts 5.

# E20 — Analytics Engine  *(Phase 3 · M5 · P2)*
- E20-S1 Interaction capture (append-only) — Pts 5.
- E20-S2 Organizer analytics (views/CTR/visibility) — Pts 8.
- E20-S3 Attendee analytics (trending/recommendations) — Pts 8.

# E21 — Ecosystem Platform  *(Phase 4 · M6 · P2)*
- E21-S1 Public API + keys/quotas — Pts 8.
- E21-S2 Conversational assistant over graph — Pts 13.
- E21-S3 Sponsor analytics dashboards (gated by `08` data-license) — Pts 8.
- E21-S4 City ecosystem reports — Pts 5.

# E22 — Multi-City Federation  *(Phase 4 · M6 · P2)*
- E22-S1 Per-city isolation (RLS/policy/sources) + 2nd-city proof — Pts 13 · dep E08.

---

## Platform Evolution Addendum (from `10_Architecture_Evolution.md`)

> Added by the Platform Evolution. **E08 (Domain Model Migration) is re-scoped: City → Tenant** (add `tenant_id` + RLS to every table, not just a city column — same migration, generalized noun). **E22 (Federation) folds into E23.** Four new epics below. Sequencing: **E23 + E24 alongside/just after E08–E14 (Phase 1)**; **E25/E26 trail into Phase 2–3.** Rule: build a plugin port only at its second implementation (ADR-014).

| # | Epic | Phase | Milestone | Priority | Pts (≈) | Depends on |
|---|---|---|---|---|---|---|
| **E23** | Multi-Tenancy (tenant entity, resolution, RLS, isolation) | 1 | M1 | P0 | 21 | E08 |
| **E24** | Instance Configuration (schema, loader, de-Lucknow-ing) | 1 | M2 | P0 | 21 | E23 |
| **E25** | Plugin System (ports, registry, conformance, SDK) | 2 | M4 | P1 | 26 | E10,E12,E24 |
| **E26** | Open-Source Readiness (deploy, docs, versioning, workflow) | 2 | M4 | P1 | 16 | E24 |

**E23 — Multi-Tenancy**
- E23-S1-T1 — Generalize E08 migration City→Tenant (`tenant` + `instance_configuration` tables; `tenant_id` on all tables; `(tenant_id,…)` unique constraints). *Files:* `core/domain/tenant.*`, all models, migration. *AC:* every table tenant-scoped; Lucknow backfilled. Pts 8 · P0.
- E23-S2-T1 — Tenant-resolution middleware (domain→context; fixed-in-config for single-tenant). *AC:* correct tenant per request; 404 on unknown. Pts 5 · P0.
- E23-S3-T1 — RLS policies + tenant-context in workers. *AC:* DB-enforced isolation; jobs carry tenant. Pts 5 · P0.
- E23-S4-T1 — **Tenant-isolation test suite (CI-gating)** + missing-policy detector. *AC:* cross-tenant read impossible; test fails if any table lacks a policy. Pts 3 · P0.

**E24 — Instance Configuration**
- E24-S1-T1 — Config schema + validated loader + precedence (defaults→config→secrets). *Files:* `core/config/*`, `13`. *AC:* invalid config fails loudly; secrets by ref. Pts 8 · P0.
- E24-S2-T1 — **Remove hard-coded Lucknow assumptions** (city defaults, Indic-community lists in prompts, `Asia/Kolkata`) → per-tenant config. *Files:* `ingestion/relevance.py`, AI prompts, models. *AC:* config-over-code CI gate passes; no tenant literal in business logic. Pts 8 · P0.
- E24-S3-T1 — Themeable frontend from tenant config (branding/theme/locale/SEO); light-mode support. *Files:* `apps/web/*`. *AC:* branding/theme/language driven by config. Pts 5 · P1.

**E25 — Plugin System**
- E25-S1 — Define ports (source/ai/notify/auth/search/analytics/storage) + registry. Pts 8 · P1.
- E25-S2 — Plugin SDK + conformance harness. Pts 8 · P1.
- E25-S3 — Refactor existing providers (storage, model-gateway, source adapters) behind ports. Pts 8 · P1.
- E25-S4 — 2nd implementations to justify ports (e.g., `ai-local`, `notify-whatsapp`, `search-opensearch`) — build on demand. Pts 5 · P2.

**E26 — Open-Source Readiness**
- E26-S1 — One-command deploy from a config bundle + `_example/` template. Pts 5 · P1.
- E26-S2 — Docs site (01–14) + quickstart. Pts 5 · P1.
- E26-S3 — Versioning (core semver + plugin-API semver) + compatibility matrix. Pts 3 · P1.
- E26-S4 — Contribution workflow (CONTRIBUTING, PR templates, good-first-issues, config-over-code CI gate). Pts 3 · P1.

---

## Import notes

- **`09_Development_Backlog.csv`** contains every item above (Gates+P0+P1 at task depth; P2+ at story depth) with columns: `Key, Type, Parent, Title, Objective, Files, Dependencies, AcceptanceCriteria, Testing, Complexity, Priority, Phase, Milestone, Labels`.
- **Jira:** map `Type`→Issue Type, `Parent`→Epic Link/Parent, `Complexity`→Story Points, `Priority`→Priority, `Labels`→Labels.
- **Linear:** map `Type`→(Epic=Project/Milestone, Story/Task=Issue, Subtask=sub-issue), `Complexity`→Estimate, `Priority`→Priority.
- **GitHub Projects:** import as issues; `Parent`/`Dependencies` as issue references; `Labels`, `Milestone`, and a single-select `Phase` field.
- **Ordering:** import top-to-bottom = implementation order. Do not start an epic before its `Depends on` epics are Done.
