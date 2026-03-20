"""
navig.api.main — FastAPI application entry point for the NAVIG telemetry service.

Usage:
  pip install "navig-core[api]"
  uvicorn navig.api.main:app --host 0.0.0.0 --port 8765 --reload

Or via navig itself (when navig service telemetry is implemented):
  navig service telemetry start
"""
from fastapi import FastAPI

from navig.api.routes.telemetry import router as telemetry_router

app = FastAPI(
    title="NAVIG Telemetry API",
    description="Anonymous install telemetry and usage statistics for NAVIG OS.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(telemetry_router)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Liveness probe — used by load balancers and monitoring."""
    return {"status": "ok"}
