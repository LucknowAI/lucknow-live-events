from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.core.logging import get_logger
from api.models.submission import ManualSubmission
from workers.dispatch import enqueue

log = get_logger(__name__)


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

    submission.status = "queued"
    await db.commit()
    await db.refresh(submission)

    log.info(
        "submission.created",
        submission_id=str(submission.id),
        event_url=event_url,
        submitter_name=submitter_name,
    )

    try:
        from workers.tasks.submissions import process_manual_submission

        task = enqueue(process_manual_submission, str(submission.id), event_url)
        log.info(
            "submission.task_queued",
            submission_id=str(submission.id),
            task_id=task.id,
            event_url=event_url,
        )
    except Exception as exc:
        log.warning(
            "submission.task_queue_failed",
            submission_id=str(submission.id),
            event_url=event_url,
            error=str(exc),
        )

    return submission
