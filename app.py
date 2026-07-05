"""
app.py — DevPulse entry point.

Architecture: single-process, single-port.
FastAPI handles /v1/* REST + /health.
Streamlit serves the control-plane UI at /.

Run: uvicorn app:app --reload --port 8000
  OR: streamlit run app.py  (for UI-first dev)

Production: uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
NOTE: rate limiting is in-memory — use workers=1 or add Redis if scaling.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from core.config import get_settings
from core.database import init_db
from core.logging import get_logger, setup_logging
from executors.registry import init_executor_registry

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, executor registry, tool registry, file watcher.
    Shutdown: stop file watcher cleanly.
    """
    app.state.start_time = time.time()

    # 1. DB
    await init_db(settings.db_path)

    # 2. Executor whitelist — must happen before registry.load_all()
    init_executor_registry()

    # 3. Tool registry
    from core.registry import registry
    registry.load_all(settings.tools_dir)

    # 4. Hot-reload file watcher
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
    from pathlib import Path

    class _YamlHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory and str(event.src_path).endswith(".yaml"):
                logger.info("hot_reload_triggered", extra={"extra": {"file": event.src_path}})
                registry.reload_tool(Path(event.src_path))

        def on_created(self, event):
            self.on_modified(event)

    observer = Observer()
    observer.schedule(_YamlHandler(), str(settings.tools_dir), recursive=False)
    observer.start()
    logger.info("file_watcher_started", extra={"extra": {"dir": str(settings.tools_dir)}})

    logger.info("devpulse_ready", extra={"extra": {
        "tools": registry.total_loaded,
        "enabled": registry.total_enabled,
        "version": settings.app_version,
    }})

    yield  # ── app is running ──

    observer.stop()
    observer.join()
    logger.info("devpulse_shutdown")


app = FastAPI(
    title="DevPulse",
    description="AI Function-as-a-Service Platform for Developer Workflows",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

# CORS — tighten allowed_origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else ["https://your-domain.com"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# Trusted host — prevents host-header injection
if settings.is_production:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["your-domain.com"])


@app.middleware("http")
async def request_size_limit(request: Request, call_next) -> Response:
    """Reject bodies larger than max_request_body_bytes (default 64KB).
    Wraps int() in try/except — a malformed Content-Length header like
    'Content-Length: abc' would otherwise raise an unhandled ValueError
    and return a confusing 500 instead of a clean 400.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            cl_int = int(content_length)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_content_length", "received": content_length},
            )
        if cl_int > settings.max_request_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"error": "request_too_large", "max_bytes": settings.max_request_body_bytes},
            )
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    start = time.monotonic()
    response = await call_next(request)
    latency_ms = (time.monotonic() - start) * 1000
    logger.info("http_request", extra={"extra": {
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "latency_ms": round(latency_ms, 1),
        "ip": request.client.host if request.client else None,
    }})
    return response


# ── Routes ────────────────────────────────────────────────────────────────────

from api.routes import router  # noqa: E402
app.include_router(router, prefix="/v1")
