from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quantpilot.packages.db.repositories import RepositoryError
from quantpilot.services.api.routers import autopilot, harness, level_1_2, operator, orders, policies, portfolio, reports, signals


app = FastAPI(title="QuantPilot Operator Pre-Harness", version="0.1.0")

# Local development only: allow the Vite dev server origin to call the
# pre-harness API. Never widen this to wildcard origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
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
app.include_router(operator.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
