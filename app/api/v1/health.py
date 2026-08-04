"""Public health and readiness endpoints (no authentication)."""

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_health_service
from app.services.health_service import HealthService

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    """GET /health/live body."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["alive"]


class ReadyResponse(BaseModel):
    """GET /health/ready body when ready."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"]


class NotReadyResponse(BaseModel):
    """GET /health/ready body when not ready."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_ready"]


class DatabaseOkResponse(BaseModel):
    """GET /health/database body when the database responds."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class DatabaseUnavailableResponse(BaseModel):
    """GET /health/database body when the database check fails."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["unavailable"]


class WorkerResponse(BaseModel):
    """GET /health/worker body."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    response_model=LiveResponse,
    summary="Liveness probe",
    description="Process liveness only. Does not touch the database.",
)
def live() -> LiveResponse:
    """Return alive without opening a database session."""
    return LiveResponse.model_validate(HealthService.live())


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": NotReadyResponse},
    },
    summary="Readiness probe",
    description="Database connectivity plus SQLAlchemy mapper configuration.",
)
def ready(
    health_service: HealthService = Depends(get_health_service),
) -> ReadyResponse | JSONResponse:
    """Return ready, or 503 not_ready, without leaking internals."""
    payload = health_service.ready_status()
    if payload["status"] == "ready":
        return ReadyResponse.model_validate(payload)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=NotReadyResponse.model_validate(payload).model_dump(),
    )


@router.get(
    "/database",
    response_model=DatabaseOkResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": DatabaseUnavailableResponse
        },
    },
    summary="Database connectivity probe",
    description="Lightweight SELECT 1. Never returns connection strings.",
)
def database(
    health_service: HealthService = Depends(get_health_service),
) -> DatabaseOkResponse | JSONResponse:
    """Return database status only."""
    payload = health_service.database_status()
    if payload["status"] == "ok":
        return DatabaseOkResponse.model_validate(payload)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=DatabaseUnavailableResponse.model_validate(payload).model_dump(),
    )


@router.get(
    "/worker",
    status_code=status.HTTP_200_OK,
    response_model=WorkerResponse,
    summary="Worker configuration capability",
    description="Reports whether publish-worker settings are valid. No IPC.",
)
def worker() -> WorkerResponse:
    """Return worker capability from configuration only."""
    return WorkerResponse.model_validate(HealthService.worker_status())
