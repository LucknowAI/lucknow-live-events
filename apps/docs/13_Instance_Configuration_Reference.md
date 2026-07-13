# 13 — Instance Configuration Reference

> **Companion to:** `04_ADR.md` (ADR-013 configuration over code), `10_Architecture_Evolution.md` (§8), `11_Multi_Tenant_Architecture.md`, `12_Plugin_Development_Guide.md`
> **Scope:** The single authoritative spec for defining a deployment. Merges the requested Deployment Config Guide + Instance Config Spec + Config Reference into one doc.
> **Principle:** *An instance is data, not a fork.* Everything a deployment needs comes from here — no code changes (ADR-013).

---

## 1. Layering & precedence

```
platform defaults  →  tenant instance config  →  environment secrets
     (built-in)            (deployments/<tenant>/instance.yaml)     (referenced, never inlined)
```

- **Defaults** ship with the platform (sensible baseline).
- **Instance config** overrides defaults; it is declarative, **schema-validated**, and **versioned** (`schema_version`).
- **Secrets** are *referenced* by name (`secret_ref`) and injected from the environment/secret manager — never written into config files.

---

## 2. The configuration domains

| Domain | Controls | Config-over-code guarantee |
|---|---|---|
| **Tenant identity** | id, type, display name, domain(s), timezone, languages | No hard-coded city/timezone in logic |
| **Branding & theme** | logo, colors, theme, social links, SEO metadata | Frontend reads these; no per-city CSS forks |
| **Features** | feature flags (semantic search, profiles, notifications, analytics…) | Capabilities toggled per deployment |
| **Taxonomy** | categories, formats, audiences | No hard-coded category lists in code |
| **Sources** | which Source plugins + their targets | Add a source without touching core |
| **Providers** | plugin selection per capability (AI/search/notify/auth/analytics/storage) | Swap providers by config (ADR-014) |
| **Trust policy** | signal weights, publish threshold | Tunable per deployment (ADR-006) |
| **Localization** | languages, default locale, string overrides | Multi-language deployments (e.g., en+hi) |

---

## 3. Reference schema (annotated)

```yaml
schema_version: 1

tenant:
  id: lucknow                      # unique, stable, lowercase
  type: city                       # city | campus | community | organization | country
  display_name: "Lucknow Tech Events"
  domains: ["lucknowtech.events"]  # first is canonical; used for tenant resolution (multi-tenant)
  timezone: "Asia/Kolkata"
  languages: ["en", "hi"]          # first = default locale

branding:
  logo: "assets/logo.svg"
  favicon: "assets/favicon.png"
  colors: { primary: "#e94560", accent: "#1a1a2e", background: "#0f0f1a" }
  theme: "dark"                    # dark | light | system   (fixes 01's forced-dark UX debt)
  social: { twitter: "@...", linkedin: "...", github: "..." }
  seo: { title_template: "%s | Lucknow Tech Events", description: "..." }

features:                          # per-deployment flags
  semantic_search: false
  organizer_profiles: true
  reputation: true
  notifications: true
  analytics: true
  public_submissions: true

taxonomy:
  categories: ["AI", "Cloud", "Web Development", "Data Science", "Cybersecurity", "Robotics"]
  formats:    ["Meetup", "Workshop", "Hackathon", "Conference", "Webinar", "Networking"]
  audiences:  ["Beginner", "Intermediate", "Advanced"]

sources:                           # Source plugins + targets (see 12)
  - plugin: source-gdg
    enabled: true
    config: { chapters: ["gdg-lucknow", "gdg-oncampus-iiitl"] }
  - plugin: source-luma
    enabled: true
    config: { calendars: ["lucknow-ai-labs"] }
  - plugin: source-ics
    enabled: true
    config: { feeds: ["https://.../events.ics"] }

providers:                         # one plugin per capability (see 12)
  ai:        { plugin: ai-gemini,          secret_ref: GEMINI_API_KEY, model: "gemini-3-flash-preview" }
  search:    { plugin: search-postgres }
  notify:    { plugin: notify-email,       secret_ref: SMTP_URL }
  auth:      { plugin: auth-jwt,           secret_ref: JWT_SECRET }
  analytics: { plugin: analytics-postgres }
  storage:   { plugin: storage-s3,         secret_ref: S3_CREDENTIALS }

trust_policy:                      # ADR-006 weights, per tenant, versioned
  policy_version: 1
  weights:
    completeness: 0.25
    link_valid:   0.20
    freshness:    0.15
    dedup_conf:   0.15
    ai_conf:      0.15
    organizer:    0.10
  publish_threshold: 0.60
  featured_rule: "trust>=0.8 AND freshness<=14d AND completeness>=0.9"

compliance:                        # per-deployment legal/privacy posture (from 08 / E00-S2)
  respect_robots: true
  source_permissions: "documented"  # link to per-source ToS posture
  data_region: "asia-south1"
  privacy_contact: "privacy@lucknowtech.events"
```

---

## 4. Validation rules

- Config **must** validate against the versioned schema at provisioning and on reload; invalid config **fails the deployment loudly** (no silent fallback — a lesson from `01`'s config chaos).
- Referenced plugins must exist in the registry and satisfy the required **plugin-API version** (`12`).
- Every `secret_ref` must resolve at boot; missing secrets fail fast.
- `tenant.id` and `domains` must be unique across a multi-tenant deployment.

---

## 5. Hot-reload vs migration-gated

| Change | Applies |
|---|---|
| Branding, social, SEO, feature flags, trust weights, taxonomy | **Hot-reload** (safe) |
| Sources, providers | Reload + re-provision plugin (safe, may re-index) |
| Tenant id, domains, schema_version | **Migration-gated** (controlled change) |

---

## 6. The reference deployment

`deployments/lucknow/instance.yaml` is the **canonical worked example**; `deployments/_example/` is a copy-to-launch template. The best proof of "configuration over code" is that a stranger can launch a new city by copying `_example/`, editing values, and supplying provider secrets — with **zero source changes**.

> **Tenet:** if launching a new community requires editing code, this document has failed. Everything a deployment needs is here.
