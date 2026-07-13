# 00 — Document Index: City Event Hub Platform

The complete documentation set. Read in numeric order for the full story: audit → product → architecture → roadmap → review → platform evolution → backlog.

## Core document set (read in order)

| # | File | What it is |
|---|------|-----------|
| 01 | `01_Project_Audit.md` | Onboarding audit of the existing V0 codebase — stack, schema, findings, health scores. |
| 02 | `02_Product_Vision.md` | Mission, vision, principles, positioning. *(Evolved to platform.)* |
| 03 | `03_PRD.md` | Personas, journeys, epics, business rules, acceptance criteria. |
| 04 | `04_Architecture_Decision_Record.md` | The load-bearing architecture decisions (ADR-001–016). |
| 05 | `05_Domain_Model.md` | Entities, relationships, lifecycle, invariants. *(Tenant-centric.)* |
| 06 | `06_System_Architecture.md` | Production, technology-concrete architecture with justifications. |
| 07 | `07_Implementation_Roadmap.md` | Phased execution plan, milestones, sprints. |
| 08 | `08_Final_Architecture_Review.md` | Independent adversarial review of 01–07. |
| 09 | `09_Development_Backlog.md` | Full epic/story/task/subtask breakdown. |
| 10 | `10_Architecture_Evolution.md` | Platform-evolution change record (all evolution deliverables). |

## Platform contracts (created in the evolution)

| # | File | What it is |
|---|------|-----------|
| 11 | `11_Multi_Tenant_Architecture.md` | Tenancy model, isolation (RLS), dual deployment modes. |
| 12 | `12_Plugin_Development_Guide.md` | Plugin ports, registry, conformance, how to build one. |
| 13 | `13_Instance_Configuration_Reference.md` | The full instance-config schema (configuration over code). |
| 14 | `14_Contribution_Guide.md` | Repo structure, contribution workflow, versioning, governance. |

## Backlog / import files

| File | Use |
|------|-----|
| `09_Development_Backlog.csv` | Canonical, tool-agnostic backlog (best for Linear / re-export). |
| **`09_Development_Backlog.FINAL.csv`** | **Recommended for Jira import now** — no Fix Version dependency; milestones as `ms-*` labels; works on any space. |
| `09_Development_Backlog.jira.csv` | Jira-tuned (Epic Name/Link + Fix Version) — only for a **Company-managed Software** space. |
| `jira_pass1_epics.csv` / `jira_pass2_children.csv` / `jira_epic_key_map.csv` | Two-pass Parent-based import (keeps Epic hierarchy). |
| `jira_flat_no_hierarchy.csv` | Flat fallback; bulk-parent later. |

## Superseded

| File | Note |
|------|------|
| `city-events-platform-PRD.md` | Early combined PRD draft — superseded by 02–07. Kept for history. |

---

**Start here:** read `01` → `10` in order. **To load work into Jira:** import `09_Development_Backlog.FINAL.csv`.
