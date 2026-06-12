from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from quantpilot.packages.db.repositories import RepositoryError
from quantpilot.services.api.routers import harness, level_1_2, orders, policies, portfolio, reports, signals


app = FastAPI(title="QuantPilot Operator Pre-Harness", version="0.1.0")


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
app.include_router(reports.router, prefix="/api")
