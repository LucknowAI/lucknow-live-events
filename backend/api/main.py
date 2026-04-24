from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.core.config import settings
from api.core.limiter import limiter
from api.routers import router as api_v1_router

# Ensure the Celery app is initialized so that @shared_task decorators
# (imported lazily inside admin endpoints) bind to our Redis broker,
# not the default AMQP transport.
# On Vercel (or any env without Redis) this import is skipped gracefully;
# the API still serves HTTP traffic, but background tasks won't be dispatched.
try:
    import workers.celery_app as _celery  # noqa: F401
except Exception:  # pragma: no cover
    pass


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    )


configure_logging()
log = structlog.get_logger(__name__)


app = FastAPI(title="Lucknow Tech Events API", debug=settings.DEBUG)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"ok": True}
