"""Ingestion Engine V1 — a parallel, self-contained engine.

Isolation rules (ADR-023, staged in `docs/mentorship/engine-v1-adr-appendix.md`):

* imports nothing from the V0 packages (`api`, `ai`, `ingestion`, `workers`);
* owns Postgres schema `engine_v1` and its own Alembic version table;
* runs as its own ASGI app (`v1.api.app`), not mounted into the V0 app;
* reads its own config file and its own `V1_`-prefixed environment.

Enforced mechanically by the import-linter contracts in `backend/.importlinter`.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"
"""Engine version. Phase 1: LLM provider layer."""

PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parent
"""`backend/`. Relative paths in the instance config resolve against this, so a config
value means the same thing regardless of the process working directory."""


def resolve_path(value: str | Path) -> Path:
    """Resolve a config-supplied path: absolute stays, relative anchors at `backend/`."""
    path = Path(value)
    return path if path.is_absolute() else (BACKEND_ROOT / path)
