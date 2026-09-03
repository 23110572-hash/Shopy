"""Persistence for buyer delivery addresses and cart orders."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orders import CustomerOrder, CustomerOrderItem, DeliveryAddress


class DeliveryAddressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: UUID) -> Sequence[DeliveryAddress]:
        result = await self._session.execute(
            select(DeliveryAddress)
            .where(
                DeliveryAddress.user_id == user_id,
                DeliveryAddress.is_archived.is_(False),
            )
            .order_by(DeliveryAddress.is_default.desc(), DeliveryAddress.created_at.desc())
        )
        return result.scalars().all()

    async def get_owned(
        self,
        address_id: UUID,
        *,
        user_id: UUID,
        for_update: bool = False,
    ) -> DeliveryAddress | None:
        statement = select(DeliveryAddress).where(
            DeliveryAddress.id == address_id,
            DeliveryAddress.user_id == user_id,
            DeliveryAddress.is_archived.is_(False),
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def count_for_user(self, user_id: UUID) -> int:
        result = await self._session.execute(
            select(DeliveryAddress.id).where(
                DeliveryAddress.user_id == user_id,
                DeliveryAddress.is_archived.is_(False),
            )
        )
        return len(result.scalars().all())

    async def clear_default(self, user_id: UUID, *, except_id: UUID | None = None) -> None:
        """Release the existing default so the partial unique index stays satisfied."""
        filters = [
            DeliveryAddress.user_id == user_id,
            DeliveryAddress.is_default.is_(True),
        ]
        if except_id is not None:
            filters.append(DeliveryAddress.id != except_id)
        await self._session.execute(
            update(DeliveryAddress).where(*filters).values(is_default=False)
        )
        await self._session.flush()


class CustomerOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned(
        self,
        order_id: UUID,
        *,
        user_id: UUID,
        for_update: bool = False,
    ) -> CustomerOrder | None:
        statement = select(CustomerOrder).where(
            CustomerOrder.id == order_id,
            CustomerOrder.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_provider_order_id(
        self,
        provider_order_id: str,
        *,
        for_update: bool = False,
    ) -> CustomerOrder | None:
        statement = select(CustomerOrder).where(
            CustomerOrder.provider_order_id == provider_order_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID, *, limit: int = 50) -> Sequence[CustomerOrder]:
        result = await self._session.execute(
            select(CustomerOrder)
            .where(CustomerOrder.user_id == user_id)
            .order_by(CustomerOrder.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_items(self, order_id: UUID) -> Sequence[CustomerOrderItem]:
        result = await self._session.execute(
            select(CustomerOrderItem)
            .where(CustomerOrderItem.order_id == order_id)
            .order_by(CustomerOrderItem.created_at, CustomerOrderItem.sku)
        )
        return result.scalars().all()

    async def items_for_orders(
        self,
        order_ids: Sequence[UUID],
    ) -> dict[UUID, list[CustomerOrderItem]]:
        if not order_ids:
            return {}
        result = await self._session.execute(
            select(CustomerOrderItem)
            .where(CustomerOrderItem.order_id.in_(order_ids))
            .order_by(CustomerOrderItem.created_at, CustomerOrderItem.sku)
        )
        grouped: dict[UUID, list[CustomerOrderItem]] = {order_id: [] for order_id in order_ids}
        for item in result.scalars().all():
            grouped.setdefault(item.order_id, []).append(item)
        return grouped
