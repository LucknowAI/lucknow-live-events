"""Celery signal handlers for structured task lifecycle logging."""
from __future__ import annotations

import time

from celery.signals import task_failure, task_postrun, task_prerun, worker_process_init

from api.core.logging import bind_context, clear_context, get_logger, setup_logging

_task_start_times: dict[str, float] = {}


@worker_process_init.connect
def _init_worker_logging(**_kwargs: object) -> None:
    setup_logging(service_name="worker")
    get_logger(__name__).info("worker.startup")


@task_prerun.connect
def _task_prerun(task_id: str, task: object, **_kwargs: object) -> None:
    request = getattr(task, "request", None)
    headers: dict = getattr(request, "headers", None) or {}
    correlation_id = headers.get("correlation_id") or task_id
    task_name = getattr(task, "name", "unknown")

    bind_context(
        task_id=task_id,
        task_name=task_name,
        correlation_id=correlation_id,
        service="worker",
    )
    _task_start_times[task_id] = time.perf_counter()
    get_logger(__name__).info("celery.task_started", task_id=task_id, task_name=task_name)


@task_postrun.connect
def _task_postrun(task_id: str, task: object, state: str, **_kwargs: object) -> None:
    started = _task_start_times.pop(task_id, None)
    duration_ms = round((time.perf_counter() - started) * 1000, 2) if started else None
    task_name = getattr(task, "name", "unknown")
    get_logger(__name__).info(
        "celery.task_finished",
        task_id=task_id,
        task_name=task_name,
        state=state,
        duration_ms=duration_ms,
    )
    clear_context()


@task_failure.connect
def _task_failure(
    task_id: str,
    exception: BaseException,
    traceback: object,
    einfo: object,
    **_kwargs: object,
) -> None:
    _task_start_times.pop(task_id, None)
    get_logger(__name__).exception(
        "celery.task_failed",
        task_id=task_id,
        error=str(exception),
    )
    clear_context()
