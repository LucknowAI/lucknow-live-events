from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.submission import ManualSubmission


async def create_submission(
    db: AsyncSession,
    *,
    event_url: str,
    submitter_name: str | None,
    submitter_email: str | None,
    notes: str | None,
) -> ManualSubmission:
    submission = ManualSubmission(
        event_url=event_url,
        submitter_name=submitter_name,
        submitter_email=submitter_email,
        notes=notes,
        status="pending",
    )
    db.add(submission)
    # Stored for admin review only; nothing processes it yet.
    await db.commit()
    await db.refresh(submission)
    return submission
