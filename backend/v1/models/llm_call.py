"""`llm_call` — the LLM spend ledger.

One row per `complete_structured` call, written in a `finally` block so a failed or
budget-blocked call is recorded too. Two things depend on this table being complete:

* the **budget circuit breaker**, which sums `cost_usd` over the current day;
* answering "why did this cost that much" without guessing — `attempts`, `outcome` and
  `strategy` are on the row, so a provider whose schema handling is weak shows up as a
  repair-attempt rate, not as a mystery bill.

`cost_usd` is NUMERIC because it is summed against a cap. Float drift on money is a bug
waiting for a slow day.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from v1.models.base import Base, created_at_column, uuid_pk


class LLMCall(Base):
    __tablename__ = "llm_call"
    __table_args__ = (
        # The budget breaker's query: spend for one tenant within a time window.
        Index("ix_llm_call_tenant_created", "tenant_id", "created_at"),
        # "which task is burning the budget" without a full scan.
        Index("ix_llm_call_tenant_task_created", "tenant_id", "task", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()

    task: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)

    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal(0))
    cost_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """True when the provider reported no token usage and we estimated it."""

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<LLMCall {self.profile}/{self.task} {self.outcome} ${self.cost_usd}>"
