"""Ownership-safe persistence for Shopy Agent conversations."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import (
    AgentConversation,
    AgentConversationStatus,
    AgentConversationTurn,
)


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: UUID, title: str) -> AgentConversation:
        conversation = AgentConversation(user_id=user_id, title=title[:160])
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get_owned(
        self,
        conversation_id: UUID,
        *,
        user_id: UUID,
        for_update: bool = False,
    ) -> AgentConversation | None:
        statement = select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_user(
        self, user_id: UUID, *, limit: int = 50
    ) -> Sequence[AgentConversation]:
        result = await self._session.execute(
            select(AgentConversation)
            .where(AgentConversation.user_id == user_id)
            .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_turn_by_client_id(
        self, *, conversation_id: UUID, client_turn_id: UUID
    ) -> AgentConversationTurn | None:
        result = await self._session.execute(
            select(AgentConversationTurn).where(
                AgentConversationTurn.conversation_id == conversation_id,
                AgentConversationTurn.client_turn_id == client_turn_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_turn(
        self, *, conversation_id: UUID, turn_id: UUID
    ) -> AgentConversationTurn | None:
        result = await self._session.execute(
            select(AgentConversationTurn).where(
                AgentConversationTurn.id == turn_id,
                AgentConversationTurn.conversation_id == conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_turns(
        self, conversation_id: UUID, *, limit: int = 40
    ) -> Sequence[AgentConversationTurn]:
        result = await self._session.execute(
            select(AgentConversationTurn)
            .where(AgentConversationTurn.conversation_id == conversation_id)
            .order_by(AgentConversationTurn.sequence_number)
            .limit(limit)
        )
        return result.scalars().all()

    async def close(self, conversation: AgentConversation) -> None:
        conversation.status = AgentConversationStatus.CLOSED.value
        await self._session.flush()
