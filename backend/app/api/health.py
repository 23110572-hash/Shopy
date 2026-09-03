"""Sanitized readiness and provider-capability endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.app.config import Settings
from backend.app.database import Database, DatabaseUnavailable
from backend.app.dependencies import get_database, get_runtime_settings

router = APIRouter(tags=["health"])
DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
ConfigurationStatus = Literal["configured", "not_configured"]


class CapabilityStatus(BaseModel):
    provider: str
    status: ConfigurationStatus


class RazorpayHealthResponse(BaseModel):
    provider: Literal["razorpay"] = "razorpay"
    status: ConfigurationStatus
    api_status: ConfigurationStatus
    webhook_status: ConfigurationStatus
    checkout_ready: bool
    webhook_ready: bool
    mode: Literal["test"] = "test"
    network_probe: Literal["not_implemented"] = "not_implemented"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str
    database: Literal["ready"]
    openrouter: CapabilityStatus
    razorpay: RazorpayHealthResponse


def _razorpay_health(settings: Settings) -> RazorpayHealthResponse:
    api_ready = settings.razorpay_api_configured
    webhook_secret_ready = settings.razorpay_webhook_configured
    webhook_ready = api_ready and webhook_secret_ready
    return RazorpayHealthResponse(
        status="configured" if webhook_ready else "not_configured",
        api_status="configured" if api_ready else "not_configured",
        webhook_status="configured" if webhook_secret_ready else "not_configured",
        checkout_ready=api_ready,
        webhook_ready=webhook_ready,
    )


@router.get("/health", response_model=HealthResponse)
async def health(
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> HealthResponse | JSONResponse:
    try:
        await database.ping()
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "service": settings.app_name,
                "database": "unavailable",
            },
        )

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env.value,
        database="ready",
        openrouter=CapabilityStatus(
            provider=settings.llm_provider.value,
            status="configured" if settings.openrouter_configured else "not_configured",
        ),
        razorpay=_razorpay_health(settings),
    )


@router.get("/health/razorpay", response_model=RazorpayHealthResponse)
async def razorpay_health(settings: SettingsDependency) -> RazorpayHealthResponse:
    return _razorpay_health(settings)
