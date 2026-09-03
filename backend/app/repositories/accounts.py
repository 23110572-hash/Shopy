"""Account identity, session, and shopping-agent control persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.account import AuthSession, ShoppingAgentControls
from backend.app.models.user import User


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_by_email(self, normalized_email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(func.lower(func.trim(User.email)) == normalized_email)
        )
        return result.scalar_one_or_none()

    async def get_user(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_principal_by_token_digest(
        self,
        token_digest: str,
        now: datetime,
    ) -> tuple[User, AuthSession] | None:
        result = await self._session.execute(
            select(User, AuthSession)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                AuthSession.token_digest == token_digest,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                User.is_active.is_(True),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return row.tuple()

    async def get_controls(self, user_id: UUID) -> ShoppingAgentControls | None:
        return await self._session.get(ShoppingAgentControls, user_id)

    async def get_or_create_controls(self, user_id: UUID) -> ShoppingAgentControls:
        controls = await self.get_controls(user_id)
        if controls is None:
            controls = ShoppingAgentControls(user_id=user_id)
            self._session.add(controls)
            await self._session.flush()
        return controls

    async def get_controls_for_update(self, user_id: UUID) -> ShoppingAgentControls | None:
        result = await self._session.execute(
            select(ShoppingAgentControls)
            .where(ShoppingAgentControls.user_id == user_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()
