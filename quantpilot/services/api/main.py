from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quantpilot.packages.db.repositories import RepositoryError
from quantpilot.services.api.routers import (
    autopilot,
    execution,
    harness,
    level_1_2,
    notifications,
    operator,
    orders,
    policies,
    portfolio,
    reports,
    signals,
    strategy_studio,
    strategy_tickets,
)


LOCAL_WEB_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
)

DEPLOYED_WEB_ORIGINS = (
    "https://web-h90tnddev-shyeon-s-projects.vercel.app",
    "https://web-cmsqztddr-shyeon-s-projects.vercel.app",
    "https://web-kappa-weld-pqacwzcni7.vercel.app",
)


def _split_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())


def allowed_cors_origins() -> list[str]:
    """Return exact browser origins allowed to call the local pre-harness API."""

    configured_origins = _split_origins(os.environ.get("QUANTPILOT_API_ALLOWED_ORIGINS"))
    return list(dict.fromkeys((*LOCAL_WEB_ORIGINS, *DEPLOYED_WEB_ORIGINS, *configured_origins)))


app = FastAPI(title="QuantPilot Operator Pre-Harness", version="0.1.0")

# Allow only known UI origins to call the pre-harness API. Never widen this to
# wildcard origins; add exact private deployment origins through the env var.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RepositoryError)
async def repository_error_handler(request: Request, exc: RepositoryError) -> JSONResponse:
    message = str(exc)
    status_code = 404 if message.startswith("missing item:") else 409
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {
                "error": message,
                "path": request.url.path,
            }
        },
    )


app.include_router(harness.router, prefix="/api")
app.include_router(policies.router, prefix="/api")
app.include_router(level_1_2.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(autopilot.router, prefix="/api")
app.include_router(execution.router, prefix="/api")
app.include_router(strategy_tickets.router, prefix="/api")
app.include_router(strategy_studio.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(operator.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
