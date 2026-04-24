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
    )
    db.add(submission)
    await db.flush()

    # Immediately process the URL via the ingestion pipeline.
    # The task will mark the submission accepted/skipped/needs_review.
    submission.status = "queued"
    await db.commit()
    await db.refresh(submission)

    try:
        from workers.celery_app import celery_app

        celery_app.send_task(
            "workers.tasks.submissions.process_manual_submission",
            args=[str(submission.id), event_url],
        )
    except Exception:
        # If celery isn't running, keep it queued so it can be retried later.
        pass

    return submission

