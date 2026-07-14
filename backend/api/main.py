from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.core.config import settings
from api.core.limiter import limiter
from api.core.logging import get_logger, setup_logging
from api.middleware.request_logging import RequestLoggingMiddleware
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

setup_logging(service_name="api")
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "api.startup",
        environment=settings.ENVIRONMENT,
        debug=settings.DEBUG,
        log_level=settings.LOG_LEVEL,
    )
    yield
    log.info("api.shutdown")


app = FastAPI(title="Lucknow Tech Events API", debug=settings.DEBUG, lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(RequestLoggingMiddleware)

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
