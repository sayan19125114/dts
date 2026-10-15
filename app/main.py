"""FastAPI app: security headers, routers, static frontend, health."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import admin, analytics, attachments, audit, auth, bugs, collab, comments, dashboard, extended, orgs, projects, releases, reports, sla
from app.api.v1.deps import get_auth_context  # noqa
from app.core.config import get_settings
from app.core.logging_config import configure_logging, request_id_var
from app.db.base import Base
from app.db.session import get_engine
import uuid

settings = get_settings()
configure_logging(settings.debug)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

    # Trusted hosts
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    # CORS explicit origins only
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            resp = await call_next(request)
            resp.headers["X-Request-ID"] = rid
            return resp
        finally:
            request_id_var.reset(token)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # limit body size ~ 12MB
        if request.headers.get("content-length"):
            try:
                if int(request.headers["content-length"]) > 12 * 1024 * 1024:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            except ValueError:
                pass
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        # CSP restrictive, no inline scripts (we use external files)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return resp

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        from fastapi.exceptions import HTTPException as FE

        if isinstance(exc, FE):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        log.exception("unhandled error on %s", request.url.path)
        if settings.debug:
            return JSONResponse({"detail": f"Internal error: {type(exc).__name__}"}, status_code=500)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/api/health")
    def health():
        return {"ok": True, "app": settings.app_name}

    @app.get("/api/v1/health")
    def health_v1():
        return {"ok": True}

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(projects.router, prefix="/api/v1/projects", tags=["projects"])
    # bugs router contains both /projects/... and /bugs/... paths; mount at /api/v1
    app.include_router(bugs.router, prefix="/api/v1", tags=["bugs"])
    app.include_router(comments.router, prefix="/api/v1", tags=["comments"])
    app.include_router(attachments.router, prefix="/api/v1", tags=["attachments"])
    app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
    app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])
    app.include_router(sla.router, prefix="/api/v1", tags=["sla"])
    app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])
    app.include_router(releases.router, prefix="/api/v1", tags=["releases"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(audit.router, prefix="/api/v1/admin/audit", tags=["audit"])
    app.include_router(orgs.router, prefix="/api/v1/orgs", tags=["orgs"])
    app.include_router(extended.router, prefix="/api/v1", tags=["extended"])
    app.include_router(collab.router, prefix="/api/v1", tags=["collab"])

    # static frontend (same origin)
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    if (static_dir / "index.html").exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def root():
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/{path:path}", include_in_schema=False)
        def spa_fallback(path: str):
            # serve API 404s as JSON; otherwise SPA
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = static_dir / path
            if path and f.is_file():
                return FileResponse(str(f))
            return FileResponse(str(static_dir / "index.html"))
    else:

        @app.get("/", include_in_schema=False)
        def root_no_frontend():
            return {"app": settings.app_name, "docs": "/api/docs"}

    return app


app = create_app()
