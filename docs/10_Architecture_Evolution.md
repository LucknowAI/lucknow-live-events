# 10 — Architecture Evolution: Lucknow App → City Event Hub Platform

> **Author role:** Principal Architect (evolution lead)
> **Nature:** An **amendment record**, not a rewrite. It generalizes the approved baseline (`01`–`09`) into a reusable, open-source, multi-tenant platform while preserving the product vision.
> **New companion docs:** `11_Multi_Tenant_Architecture.md` · `12_Plugin_Development_Guide.md` · `13_Instance_Configuration_Reference.md` · `14_Contribution_Guide.md`

---

## 1. Architecture Change Summary

**Strategic decision:** the project becomes an **open-source platform**. *Lucknow Event Hub* → *City Event Hub Platform*. Lucknow is the **first deployment and reference instance**, not the product. Any city, campus, community, organization, or country can deploy its own instance **from configuration alone** — no code changes.

**Five load-bearing changes** (each formalized as a new ADR in `04`):

| # | Change | ADR | Nature |
|---|---|---|---|
| 1 | **Tenant** replaces **City** as the first-class deployment/namespace entity (`city` becomes a tenant *type*). | ADR-012 (amends 011) | Domain generalization |
| 2 | **Configuration over code** — an instance is defined entirely by data (branding, domain, timezone, languages, features, sources, categories, providers). No tenant-specific business logic. | ADR-013 | New principle |
| 3 | **Plugin architecture (ports & adapters)** for scrapers, AI, notifications, auth, search, analytics (storage already abstracted). Core depends on contracts, not implementations. | ADR-014 | New pattern |
| 4 | **Dual deployment mode** — single-tenant self-host and multi-tenant hosted from **one codebase**, shared-schema + Row-Level Security. | ADR-015 | New topology |
| 5 | **Open-source native** — core/plugins/config separation, contribution workflow, versioning, plugin SDK. | ADR-016 | New posture |

**What did *not* change:** the event-atom model (ADR-001/002), the single-lifecycle pipeline (ADR-003), discovery-not-management (ADR-004), AI-enriches-rules-decide (ADR-005), transparent trust (ADR-006), auto-generated surfaces (ADR-007), search-first (ADR-009), and the graph-as-asset thesis (ADR-010) all hold — they simply now live **within a tenant**. The workload-sizing thesis in `06 §0` is untouched: multi-tenancy at this volume is an *isolation and configuration* concern, not a throughput one.

**Contradiction resolved:** `08 §2.1 C3` flagged "scope oscillation — 'just Lucknow' vs federation reappearing." This evolution **resolves it**: multi-deployment is now the explicit product. (Conversely, `08`'s core caution — validate demand, don't over-build — applies *more* strongly; see §5.)

---

## 2. Updated Documents (changelog with rationale)

> Principle: **update only affected sections; preserve the rest; explain why.** Foundational docs were edited in place; large downstream docs carry an amendment banner pointing here so they stay stable and in-sync (per `08`'s warning against doc sprawl).

| Doc | Change | Why |
|---|---|---|
| **`01_Project_Audit`** | **No changes required.** | It is a historical snapshot of V0. The strategic decision doesn't alter what V0 *was*. Its findings (hard-coded Lucknow assumptions, no tenant concept) now read as *debt to remove* — noted in §4. |
| **`02_Product_Vision`** | **Edited in place:** title (→ *City Event Hub Platform*), Mission, Vision, Positioning, Design Principles (+4: config-over-code, tenant-boundary, everything-pluggable, OSS), Future Vision (federation promoted from horizon to core), Success Metrics (+ platform-level Adoption & Community metrics). Core Philosophy preserved (valid per-deployment). | The product identity itself changed; these are the sections that assert identity. |
| **`03_PRD`** | **Amendment banner added:** +2 personas (Instance Operator, Plugin Developer), +3 epics (Multi-Tenancy & Config, Plugin System, OSS Tooling), +3 business rules (tenant-scoping, config-over-code, plugin-contract). Existing personas/epics/rules now operate within a tenant. | New audiences (deployers, contributors) and capabilities (tenancy, plugins) are genuine new requirements. |
| **`04_ADR`** | **Edited in place:** ADR-011 marked *amended*; appended **ADR-012…016**; extended the cross-cutting table. | ADRs are append-only by design; this is the canonical place to record the decisions. |
| **`05_Domain_Model`** | **Edited in place:** modeling stance (tenancy overlay), entities (+Tenant, +InstanceConfiguration; City → tenant type), ER diagram (Tenant as root), invariants (+tenant isolation, +config-determinism), glossary. | The domain *root* changed from City to Tenant — the deepest structural edit. |
| **`06_System_Architecture`** | **Amendment banner added:** tenant-resolution middleware + plugin registry (§2/§3), `tenant_id` + RLS (§7), provider abstractions → plugin ports (§9/§12/§13/§16/§18), config strategy → `13` (§23), dual deployment mode (§5/§24), tenant-prefixed cache/queue keys. Subsystem mechanics unchanged. | The architecture must express isolation, config, and plugins; detail lives in the focused new docs so this large doc stays stable. |
| **`07_Implementation_Roadmap`** | **Amendment banner added:** DM epic generalizes City → Tenant; +4 epic families (PLT-Tenancy, PLT-Config, PLT-Plugins, PLT-OSS); sequencing guidance (tenancy+config early; plugin-ify on 2nd implementation). | New work streams must be planned; retrofitting tenancy late is expensive. |
| **`08_Final_Architecture_Review`** | **No changes required** (historical review). *Noted:* this evolution resolves its C3 contradiction and amplifies its over-build caution. | A point-in-time critique shouldn't be retconned; its implications are carried here (§5). |
| **`09_Development_Backlog` (+CSV)** | **Addendum added** (`09` §Platform Addendum) + **new CSV rows**: epics E23 (Tenancy), E24 (Instance Config), E25 (Plugin System), E26 (OSS Readiness); E08 generalized City→Tenant; E22 federation folded into E23. | The backlog must remain the single importable execution surface. |

**New documents created:** see §3.

---

## 3. New Documents (created — long-term architectural value)

| Doc | Purpose | Why it earns its existence |
|---|---|---|
| **`11_Multi_Tenant_Architecture.md`** | Tenant model, resolution, isolation (RLS), dual deployment modes, tenant lifecycle. | The isolation contract every deployment depends on; too detailed to bloat `06`. |
| **`12_Plugin_Development_Guide.md`** | The six plugin ports, their contracts, the registry, conformance testing, and how to build/ship a plugin. | The extensibility contract — the heart of the OSS ecosystem. |
| **`13_Instance_Configuration_Reference.md`** | The complete instance-configuration schema (branding, domain, timezone, languages, features, sources, categories, providers) + validation + examples. | *This is "configuration over code."* Merges the requested Deployment Config Guide + Instance Config Spec + Config Reference into one authoritative doc (avoiding sprawl). |
| **`14_Contribution_Guide.md`** | Repo structure, contribution workflow, versioning (core + plugin API semver), testing, release, governance. | The OSS operating manual; reduces the bus-factor risk from `08`. |

*Deliberately **not** created as separate docs:* "Extension Development Guide" (merged into `12`), "Deployment Configuration Guide" / "Configuration Reference" (merged into `13`). Fewer, denser docs are more maintainable — heeding `08`.

---

## 4. Migration Impact Assessment

**From the V0 reality in `01` and the P0/P1 plan in `07`/`09`:**

| Area | Impact | Effort | Notes |
|---|---|---|---|
| **Schema** | Add `tenant`, `instance_configuration`; add `tenant_id` to every table; RLS policies; generalize unique constraints to `(tenant_id, …)`. | Medium | Folds cleanly into the already-planned `E08` migration (which added `city`). Do it **once**, as tenant not city. |
| **Data backfill** | Backfill all existing rows to a single `lucknow` tenant. | Low | Same shape as the planned city backfill. |
| **Tenant resolution** | New middleware (domain → tenant); single-tenant mode reads tenant from config. | Medium | New, but small and well-bounded. |
| **Hard-coded Lucknow assumptions** | Remove from business logic (e.g., `city="Lucknow"` defaults, Lucknow community lists in AI prompts, `Asia/Kolkata` defaults). | **Medium-High** | The real debt. `01` shows these are scattered across `relevance.py`, prompts, models. Must move to per-tenant config. |
| **Config loading** | New instance-config schema + loader + validation. | Medium | Net-new (`13`). |
| **Plugin ports** | Formalize existing abstractions (storage, AI/model-gateway, source adapters) into versioned ports; add registry. | Medium | Mostly refactor of existing seams; *defer* ports without a 2nd implementation. |
| **Frontend** | Read branding/theme/locale from tenant config; per-tenant metadata/SEO. | Medium | Themeable shell. |
| **Ops** | Same components; add tenant-prefixed cache/queue keys, per-tenant secrets for providers. | Low-Medium | No new scale problem. |

**Sequencing recommendation:** do **Tenancy + Instance Config during/right after P1 (Canonical Core)**. Retrofitting `tenant_id` after the canonical model is built is far more expensive than building it in. Plugin-ification and OSS tooling can trail into P2/P3. **Net added effort ≈ +30–40% on P1**, most of it absorbed by re-scoping the already-planned domain migration from city to tenant.

---

## 5. Risks Introduced

| Risk | Severity | Mitigation |
|---|---|---|
| **Scope amplification** — an OSS multi-tenant platform is *more* ambitious than a single app; `08`'s "0 users, over-built" warning intensifies. | **High** | `08`'s demand-validation gate (E00-S1) still comes first. Build the **single-tenant path** to excellence; treat multi-tenant/plugins as *seams designed now, activated later*. Do not let platformization delay a usable Lucknow instance. |
| **Premature abstraction** — building six plugin ports before second implementations exist. | High | ADR-014 rule: **a port earns existence at the 2nd implementation.** Ship official providers behind interfaces, but don't over-generalize speculatively. |
| **Tenant isolation leak** — a missing RLS policy or un-scoped query exposes cross-tenant data. | **High** | RLS as backstop + tenant-context middleware + **mandatory isolation tests** in CI (see `11`). |
| **Maintainer burden** — OSS reviews, versioning, plugin support, docs; bus-factor already flagged in `08`. | High | `14` defines lightweight governance; conformance tests reduce review load; keep the plugin surface small initially. |
| **Config complexity** — a sprawling, unvalidated config schema becomes its own footgun (echoes `01`'s config chaos). | Medium | Strict, versioned, **validated** schema with sensible defaults and a `lucknow` reference config (`13`). |
| **Security surface grows** — third-party community plugins run in the pipeline. | Medium | Plugin trust tiers, capability limits, and the SSRF/injection controls from `06 §19` apply to all source plugins. |
| **Legal/DPDP multiplied across deployments** — each instance scrapes and profiles; the `08` legal gap now spans many communities. | Medium-High | `E00-S2` compliance position becomes a **per-deployment checklist** shipped with the platform. |

---

## 6. Benefits of the New Architecture

- **Reusability / leverage:** one codebase powers unlimited communities; a fix or feature reaches all of them.
- **Adoption path:** communities self-host; the platform grows by deployment, not just by one city's traffic.
- **Maintainability:** configuration-over-code means no per-city forks to maintain (the worst failure mode of civic software).
- **Extensibility:** plugin ports let communities swap providers (local LLM, WhatsApp notifications, regional auth) without touching core.
- **Contribution leverage:** an OSS ecosystem reduces bus-factor and distributes source-adapter maintenance to the communities that need them.
- **Federation for free:** a new tenant is config + a domain, not a deployment (ADR-015) — directly enabling Delhi/Bengaluru/campus/community instances.
- **Contradiction resolved:** the `08` scope oscillation is settled — multi-deployment is the intent, coherently.
- **No scale penalty:** at this volume, tenancy adds isolation/config, not throughput cost (`06 §0` thesis intact).

---

## 7. Recommended Repository Structure

Monorepo, clean **core / plugins / deployments / docs** separation. Core never imports a concrete provider.

```
city-event-hub/
├── core/                         # tenant-agnostic, provider-agnostic
│   ├── domain/                   # Tenant, CanonicalEvent, evidence, trust, taxonomy
│   ├── lifecycle/                # the fixed ingestion state machine
│   ├── ports/                    # PLUGIN INTERFACES (source, ai, notify, auth, search, analytics, storage)
│   ├── tenancy/                  # tenant resolution, context, RLS helpers
│   ├── config/                   # instance-config schema, loader, validation
│   └── registry/                 # plugin discovery & selection
├── plugins/                      # official implementations (each behind a port)
│   ├── source-commudle/  source-luma/  source-gdg/  source-ics/
│   ├── ai-gemini/  ai-openai/  ai-local/
│   ├── notify-email/  notify-whatsapp/  notify-webhook/
│   ├── auth-jwt/  auth-oauth/
│   ├── search-postgres/  search-opensearch/
│   ├── analytics-postgres/  analytics-warehouse/
│   └── storage-s3/  storage-local/
├── apps/
│   └── web/                      # themeable Next.js shell (reads tenant branding)
├── deployments/                  # ONE config bundle per instance — NO code
│   ├── lucknow/                  # reference deployment (config + secrets template)
│   ├── _example/                 # copy-this-to-launch template
│   └── ...                       # delhi/, bengaluru/, campus-x/ ...
├── sdk/                          # plugin SDK + conformance test harness
├── docs/                         # docs site (01–14 + guides)
│   └── deploy/  plugins/  config/  contributing/
├── infra/                        # deploy manifests, migrations, scheduler
└── examples/                     # sample plugins, sample configs
```

**Rule:** `core/` may depend on `ports/` only. `plugins/*` depend on `sdk/` + one port. `deployments/*` contain **zero code** — config + secrets references only. This layout *is* "configuration over code" made physical.

---

## 8. Recommended Configuration Structure

An instance = one validated config bundle (full schema in `13`). Shape:

```yaml
# deployments/<tenant>/instance.yaml
tenant:
  id: lucknow
  type: city                 # city | campus | community | organization | country
  display_name: "Lucknow Tech Events"
  domain: "lucknowtech.events"
  timezone: "Asia/Kolkata"
  languages: ["en", "hi"]    # first is default
branding:
  logo: "assets/logo.svg"
  colors: { primary: "#e94560", accent: "#1a1a2e" }
  theme: "dark"
  social: { twitter: "...", linkedin: "..." }
features:                    # feature flags per deployment
  semantic_search: false
  organizer_profiles: true
  notifications: true
  analytics: true
taxonomy:
  categories: ["AI", "Cloud", "Web", "Cybersecurity", "Data"]
  formats: ["Meetup", "Workshop", "Hackathon", "Conference", "Webinar"]
sources:                     # which scraper plugins + their targets
  - plugin: source-gdg
    config: { chapters: ["gdg-lucknow"] }
  - plugin: source-luma
    config: { calendars: ["..."] }
providers:                   # plugin selection per capability
  ai:        { plugin: ai-gemini,        secret_ref: GEMINI_API_KEY }
  search:    { plugin: search-postgres }
  notify:    { plugin: notify-email,     secret_ref: SMTP_URL }
  auth:      { plugin: auth-jwt }
  analytics: { plugin: analytics-postgres }
  storage:   { plugin: storage-s3,       secret_ref: S3_CREDS }
trust_policy:                # tunable weights (ADR-006), per tenant
  weights: { completeness: 0.25, link_valid: 0.2, freshness: 0.15, ... }
  publish_threshold: 0.6
```

**Layering:** `platform defaults → tenant config → environment secrets`. Config is **declarative, validated, and versioned**; secrets are referenced, never inlined.

---

## 9. Open-Source Readiness Assessment

Scored 1–5 for the *current baseline's* readiness to become a healthy OSS platform, with the gap to close.

| Dimension | Now | Target | Gap to close |
|---|---|---|---|
| **Modularity** | 3 | 5 | Extract `core/ports/`; enforce core-imports-only-ports. |
| **Extensibility** | 2 | 5 | Ship the plugin registry + SDK + conformance suites (`12`). |
| **Configurability** | 1 | 5 | Build the instance-config schema/loader; remove hard-coded Lucknow (`13`, §4). |
| **Documentation** | 3 | 4 | Have `01`–`14`; add a docs *site* + quickstart; keep them from drifting (`08`). |
| **Contribution workflow** | 1 | 4 | `14`: CONTRIBUTING, PR templates, good-first-issues, plugin tutorial. |
| **Testing** | 1 | 4 | `08`/`09` test debt + **plugin conformance tests** + **tenant-isolation tests**. |
| **Deployment** | 2 | 5 | One-command deploy from a config bundle; `_example/` template. |
| **Versioning** | 1 | 4 | Semver for **core** and a separate **plugin-API** version; compatibility policy. |
| **Governance** | 1 | 3 | Lightweight maintainer/decision model; license choice. |

**Headline:** the *architecture* is OSS-ready; the *tooling and hygiene* are not yet. The gap is execution (config loader, plugin SDK, tests, docs site), not design.

---

## 10. Final Recommendations

1. **Preserve the vision; generalize the noun.** Everything that made the product good (event-atom, one lifecycle, trust, discovery-not-management) is intact — now tenant-scoped. Do not let platformization dilute it.
2. **Ship single-tenant excellence first.** Build tenancy as a *seam* (one tenant = the self-host case) and make Lucknow superb. Multi-tenant hosting and a plugin marketplace are activated later. This directly honors `08`: don't out-build demand.
3. **Do tenancy + config early, plugins late.** `tenant_id` + RLS + config loader belong in P1 (cheap now, costly to retrofit). Plugin ports get built *as second implementations appear* (ADR-014), not speculatively.
4. **Make "configuration over code" a CI gate.** Add a check that fails if business logic references a specific tenant/city/provider. This keeps the platform reusable by construction.
5. **Mandatory tenant-isolation tests.** A cross-tenant leak is the worst failure of a multi-tenant platform; test it like a security control.
6. **Ship a reference deployment (`deployments/lucknow/`) and an `_example/`.** The best documentation for "configuration over code" is a working config a stranger can copy.
7. **Resource the OSS burden or scope it down.** `08`'s bus-factor risk is now bigger. Either commit maintainer time (reviews, versioning, docs) or keep the plugin surface deliberately small until contributors arrive.
8. **Carry the legal/DPDP position into every deployment.** Turn `E00-S2` into a per-instance compliance checklist that ships with the platform — each community inherits the obligation.

> **Closing:** This evolution turns a city app into a **platform pattern**. The architecture supports it cleanly because the earlier docs already designed for a tenant seam (ADR-011) and provider abstraction (model gateway, storage). The danger is not technical — it's ambition outrunning evidence. Generalize the design now; activate the platform features on the schedule that real adoption earns.
