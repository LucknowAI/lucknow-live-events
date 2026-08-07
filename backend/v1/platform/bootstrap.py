"""Idempotent startup bootstrap for database-backed runs.

Only the tenant row, and only when a database is actually in use. Called on app startup and
safe to call repeatedly — the engine may be started as an API process, a job process, or a
test, and none of them may assume another one ran first.

The tenant id is derived from the slug (`v1.models.tenant.tenant_uuid`), which is what lets
this run *under* the RLS policy instead of around it: the GUC is set from a computed value
before the first statement, so no privileged "read the tenant table first" path exists.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from v1.config.schema import TenantConfig
from v1.models import Tenant, tenant_uuid
from v1.platform.db import session_scope
from v1.platform.logging import get_logger

logger = get_logger("v1.bootstrap")


async def ensure_tenant(tenant: TenantConfig) -> None:
    """Create or update the single tenant row from config."""
    tenant_id = tenant_uuid(tenant.slug)
    statement = (
        insert(Tenant)
        .values(
            id=tenant_id,
            slug=tenant.slug,
            name=tenant.name,
            timezone=tenant.timezone,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=[Tenant.slug],
            set_={"name": tenant.name, "timezone": tenant.timezone, "is_active": True},
        )
    )
    async with session_scope(str(tenant_id)) as session:
        await session.execute(statement)
    logger.info("tenant_ready", tenant=tenant.slug, tenant_id=str(tenant_id))
