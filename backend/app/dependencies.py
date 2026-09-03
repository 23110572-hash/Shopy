"""FastAPI dependency boundaries."""

from fastapi import Request

from app.config import Settings
from app.database import Database


def get_database(request: Request) -> Database:
    database = getattr(request.app.state, "database", None)
    if not isinstance(database, Database):
        raise RuntimeError("Database is not initialized")
    return database


def get_runtime_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("Settings are not initialized")
    return settings
