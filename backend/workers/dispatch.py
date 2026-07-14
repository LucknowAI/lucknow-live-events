"""Enqueue Celery tasks with correlation ID propagation for log tracing."""
from __future__ import annotations

import uuid
from typing import Any

from celery import Task

from api.core.logging import get_correlation_id


def enqueue(task: Task, *args: Any, correlation_id: str | None = None, **kwargs: Any):
    """Dispatch a Celery task, attaching correlation_id in headers for tracing."""
    cid = correlation_id or get_correlation_id() or str(uuid.uuid4())
    extra_headers = kwargs.pop("headers", None) or {}
    headers = {**extra_headers, "correlation_id": cid}
    return task.apply_async(args=args, kwargs=kwargs, headers=headers)
