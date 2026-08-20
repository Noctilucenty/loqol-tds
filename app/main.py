"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import agent_routes, auth_routes, seller_routes, voice_routes
from .services import FrozenDisclosure
from .tds.values import ValueError_
from .tds.fieldmap import coverage, validate

log = logging.getLogger("loqol")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = settings()
    init_db()

    # The binding table is the one thing whose breakage would be silent: a
    # mistyped field name produces a form that looks filled and is not. It is
    # cheap to check, so it is checked on every boot.
    problems = validate()
    if problems:
        raise RuntimeError("TDS field bindings are invalid:\n  " + "\n  ".join(problems))

    for warning in cfg.check_production_ready():
        log.warning("config: %s", warning)

    cov = coverage()
    log.info(
        "TDS ready: %d widgets, %d bound to questions, %d to signer roles",
        cov["widgets_total"], cov["bound_by_questions"], cov["handled_by_signer_roles"],
    )
    yield


app = FastAPI(
    title="Loqol Disclosures",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(auth_routes.router)
app.include_router(agent_routes.router)
app.include_router(seller_routes.router)
app.include_router(voice_routes.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # The seller's link is a bearer credential sitting in the URL, so it must not
    # travel in a Referer header to anything the page loads or links to.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    if request.url.path.startswith("/api/s/") or request.url.path.startswith("/s/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(FrozenDisclosure)
async def frozen_disclosure(request: Request, exc: FrozenDisclosure):
    """A write that lands after the document was sent is a conflict, not a crash.

    Registered app-wide rather than per route: the guard lives in `write_answer`,
    so every current and future caller is covered without each one remembering to
    catch it.
    """
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(ValueError_)
async def bad_answer_value(request: Request, exc: ValueError_):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/health")
def health():
    cfg = settings()
    cov = coverage()
    return {
        "status": "ok",
        "environment": cfg.environment,
        "voice": cfg.voice_enabled,
        "docuseal": cfg.docuseal_enabled,
        "docusealTemplate": cfg.docuseal_template_id,
        "coverage": cov,
    }


@app.exception_handler(404)
async def spa_fallback(request: Request, exc):
    """Serve the single-page app for browser routes, keep JSON for the API."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "Not found")}, status_code=404)
    index = STATIC / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {"detail": "Frontend is not built. Run: cd web && npm install && npm run build"},
        status_code=404,
    )


if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
