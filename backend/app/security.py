"""Password hashing, opaque sessions, cookie handling, and CSRF enforcement."""

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status

from backend.app.config import AppEnvironment, Settings
from backend.app.database import Database
from backend.app.dependencies import get_database, get_runtime_settings
from backend.app.models.account import AuthSession
from backend.app.models.user import User
from backend.app.repositories.accounts import AccountRepository

SESSION_COOKIE = "shopy_session"
CSRF_COOKIE = "shopy_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(days=7)
_PASSWORD_HASHER = PasswordHasher()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash("not-a-real-shopy-account-47291")


@dataclass(frozen=True)
class AuthPrincipal:
    user: User
    session: AuthSession


@dataclass(frozen=True)
class SessionCredentials:
    session_token: str
    csrf_token: str
    expires_at: datetime


DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session_credentials() -> SessionCredentials:
    return SessionCredentials(
        session_token=secrets.token_urlsafe(48),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + SESSION_TTL,
    )


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_PASSWORD_HASHER.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _DUMMY_PASSWORD_HASH

    def verify() -> bool:
        try:
            verified = bool(_PASSWORD_HASHER.verify(candidate_hash, password))
            return verified and password_hash is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(verify)


def set_auth_cookies(
    response: Response,
    settings: Settings,
    credentials: SessionCredentials,
) -> None:
    secure = settings.app_env is AppEnvironment.PRODUCTION
    max_age = int(SESSION_TTL.total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        credentials.session_token,
        max_age=max_age,
        expires=credentials.expires_at,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        credentials.csrf_token,
        max_age=max_age,
        expires=credentials.expires_at,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    secure = settings.app_env is AppEnvironment.PRODUCTION
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="lax")


async def get_optional_principal(
    request: Request,
    database: DatabaseDependency,
) -> AuthPrincipal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    async with database.session() as session:
        result = await AccountRepository(session).get_principal_by_token_digest(
            digest_token(token), datetime.now(UTC)
        )
        if result is None:
            return None
        user, auth_session = result
        return AuthPrincipal(user=user, session=auth_session)


async def get_current_principal(
    principal: Annotated[AuthPrincipal | None, Depends(get_optional_principal)],
) -> AuthPrincipal:
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to access your Shopy account",
        )
    return principal


async def require_csrf(
    request: Request,
    settings: SettingsDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> AuthPrincipal:
    header_token = request.headers.get(CSRF_HEADER)
    cookie_token = request.cookies.get(CSRF_COOKIE)
    valid_token = (
        bool(header_token)
        and bool(cookie_token)
        and secrets.compare_digest(header_token or "", cookie_token or "")
        and secrets.compare_digest(
            digest_token(header_token or ""), principal.session.csrf_digest
        )
    )
    if not valid_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")

    origin = request.headers.get("Origin")
    if (
        origin is not None
        and origin.rstrip("/") not in settings.allowed_frontend_origins
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed",
        )
    return principal


OptionalPrincipalDependency = Annotated[AuthPrincipal | None, Depends(get_optional_principal)]
CurrentPrincipalDependency = Annotated[AuthPrincipal, Depends(get_current_principal)]
CsrfPrincipalDependency = Annotated[AuthPrincipal, Depends(require_csrf)]
