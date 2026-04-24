from __future__ import annotations

import structlog
from celery import shared_task

from workers.utils import run_async


log = structlog.get_logger(__name__)


@shared_task(name="workers.tasks.pipeline.run_pipeline_for_source")
def run_pipeline_for_source(source_id: str) -> dict:
    log.info("run_pipeline_for_source.start", source_id=source_id)
    from ingestion.pipeline import run_source_pipeline

    counts = run_async(run_source_pipeline(source_id))
    log.info("run_pipeline_for_source.done", source_id=source_id, **counts)
    return counts
