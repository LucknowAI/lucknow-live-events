"""`tenant` — the scope root (ADR-012).

Exactly one row in this deployment, seeded from `instance.yaml`. It exists now, with
`tenant_id` on every other table and an RLS policy from creation, because `10 §4` is
explicit that retrofitting tenancy after the canonical model exists is far more
expensive. We are building the *seam*, not multi-tenant operations.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from v1.models.base import Base, created_at_column, uuid_pk

TENANT_NAMESPACE = uuid.UUID("6f0d2f6e-2b4b-5d7a-9c3e-1a2b3c4d5e6f")
"""Fixed namespace for deriving a tenant UUID from its slug."""


def tenant_uuid(slug: str) -> uuid.UUID:
    """Derive a tenant's id from its slug, deterministically.

    This is what makes RLS bootstrappable. The `tenant` table is itself under an RLS
    policy of `id = current_setting('engine_v1.tenant_id')`, so a process cannot read the
    tenant row to discover the id it needs in order to be allowed to read the tenant row.
    Deriving the id from config-known data breaks the cycle: the process computes the
    UUID, sets the session GUC, and only then touches the database — no privileged
    "look up the tenant first" path, and therefore no hole in the policy.
    """
    return uuid.uuid5(TENANT_NAMESPACE, slug)


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Tenant {self.slug}>"
