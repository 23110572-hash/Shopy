"""Shopy FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.account import router as account_router
from app.api.agent import router as agent_router
from app.api.catalog import product_router
from app.api.catalog import router as catalog_router
from app.api.checkout import router as checkout_router
from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.database import Database, DatabaseUnavailable


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(runtime_settings)
        try:
            await database.ping()
        except DatabaseUnavailable:
            await database.dispose()
            raise RuntimeError("Database startup readiness check failed") from None

        application.state.settings = runtime_settings
        application.state.database = database
        try:
            yield
        finally:
            await database.dispose()

    application = FastAPI(
        title="Shopy",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.allowed_frontend_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Accept", "Content-Type", "Idempotency-Key", "X-CSRF-Token"],
    )
    application.include_router(health_router)
    application.include_router(catalog_router)
    application.include_router(product_router)
    application.include_router(agent_router)
    application.include_router(account_router)
    application.include_router(checkout_router)
    return application


app = create_app()
