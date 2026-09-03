"""Cart checkout: server-priced multi-item orders paid by COD or Razorpay.

Trust boundaries enforced here:

* The browser sends product identities and quantities only. Every price, line
  total, and grand total is recomputed from the locked ``products`` row.
* An address must belong to the requesting buyer.
* A prepaid order is only confirmed after Razorpay's signature verifies *and*
  the payment is re-fetched from Razorpay. The callback payload is never
  treated as proof of payment on its own.
* Stock is decremented exactly once per order, guarded by
  ``inventory_committed`` under a row lock.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database import Database
from app.gateways.razorpay import (
    ProviderPayment,
    RazorpayAmbiguousWriteError,
    RazorpayGatewayError,
    RazorpayRejectedError,
    RazorpayStandardCheckoutGateway,
)
from app.models.merchant import Merchant
from app.models.orders import (
    CustomerOrder,
    CustomerOrderItem,
    DeliveryAddress,
    OrderPaymentStatus,
    OrderStatus,
    PaymentMethod,
)
from app.models.user import User
from app.repositories.orders import CustomerOrderRepository, DeliveryAddressRepository
from app.repositories.products import ProductRepository
from app.schemas.orders import (
    ConfirmOrderPaymentRequest,
    DeliveryAddressInput,
    OrderItemResponse,
    OrderResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
    RazorpayOrderHandoff,
    ShippingAddressSnapshot,
)

# Free delivery for every cart; kept explicit so totals stay auditable.
SHIPPING_PAISE = 0
MAX_SAVED_ADDRESSES = 15
# Razorpay requires at least 100 paise for an order.
MIN_PREPAID_TOTAL_PAISE = 100


class OrderServiceError(RuntimeError):
    """Sanitized cart-checkout failure with an HTTP status and stable code."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


def _order_number(order_id: UUID) -> str:
    return f"SHOPY-{order_id.hex[:10].upper()}"


def _address_snapshot(address: DeliveryAddress) -> dict[str, str | None]:
    return {
        "full_name": address.full_name,
        "phone": address.phone,
        "line1": address.line1,
        "line2": address.line2,
        "landmark": address.landmark,
        "city": address.city,
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
    }


class OrderService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    # ---------------------------------------------------------------- addresses

    async def list_addresses(self, user_id: UUID) -> list[DeliveryAddress]:
        async with self._database.session() as session:
            return list(await DeliveryAddressRepository(session).list_for_user(user_id))

    async def create_address(
        self,
        user_id: UUID,
        payload: DeliveryAddressInput,
    ) -> DeliveryAddress:
        async with self._database.session() as session:
            repository = DeliveryAddressRepository(session)
            saved_count = await repository.count_for_user(user_id)
            if saved_count >= MAX_SAVED_ADDRESSES:
                raise OrderServiceError(
                    "ADDRESS_LIMIT_REACHED",
                    f"You can save up to {MAX_SAVED_ADDRESSES} addresses",
                    status_code=409,
                )
            make_default = payload.is_default or saved_count == 0
            if make_default:
                await repository.clear_default(user_id)
            address = DeliveryAddress(
                user_id=user_id,
                full_name=payload.full_name,
                phone=payload.phone,
                line1=payload.line1,
                line2=payload.line2,
                landmark=payload.landmark,
                city=payload.city,
                state=payload.state,
                postal_code=payload.postal_code,
                country="IN",
                is_default=make_default,
            )
            session.add(address)
            await session.commit()
            await session.refresh(address)
            return address

    async def update_address(
        self,
        user_id: UUID,
        address_id: UUID,
        payload: DeliveryAddressInput,
    ) -> DeliveryAddress:
        async with self._database.session() as session:
            repository = DeliveryAddressRepository(session)
            address = await repository.get_owned(address_id, user_id=user_id, for_update=True)
            if address is None:
                raise OrderServiceError(
                    "ADDRESS_NOT_FOUND", "Delivery address not found", status_code=404
                )
            if payload.is_default and not address.is_default:
                await repository.clear_default(user_id, except_id=address_id)
            address.full_name = payload.full_name
            address.phone = payload.phone
            address.line1 = payload.line1
            address.line2 = payload.line2
            address.landmark = payload.landmark
            address.city = payload.city
            address.state = payload.state
            address.postal_code = payload.postal_code
            if payload.is_default:
                address.is_default = True
            await session.commit()
            await session.refresh(address)
            return address

    async def delete_address(self, user_id: UUID, address_id: UUID) -> None:
        async with self._database.session() as session:
            repository = DeliveryAddressRepository(session)
            address = await repository.get_owned(address_id, user_id=user_id, for_update=True)
            if address is None:
                raise OrderServiceError(
                    "ADDRESS_NOT_FOUND", "Delivery address not found", status_code=404
                )
            # Archive instead of deleting: placed orders reference this row.
            address.is_archived = True
            address.is_default = False
            await session.commit()

    # ------------------------------------------------------------ order placing

    async def place_order(
        self,
        *,
        buyer: User,
        payload: PlaceOrderRequest,
    ) -> PlaceOrderResponse:
        prepaid = payload.payment_method == PaymentMethod.RAZORPAY.value
        if prepaid and not self._settings.razorpay_api_configured:
            raise OrderServiceError(
                "RAZORPAY_NOT_CONFIGURED",
                "Online payment is not configured. Choose cash on delivery.",
                status_code=503,
            )

        order_id = uuid4()
        async with self._database.session() as session:
            address = await DeliveryAddressRepository(session).get_owned(
                payload.address_id, user_id=buyer.id
            )
            if address is None:
                raise OrderServiceError(
                    "ADDRESS_NOT_FOUND",
                    "Select a saved delivery address",
                    status_code=404,
                )

            products = ProductRepository(session)
            items: list[CustomerOrderItem] = []
            subtotal = 0
            merchant_id: UUID | None = None

            for line in payload.items:
                # Lock the row so price and stock cannot shift mid-order.
                product = await products.get_for_checkout(line.product_id)
                if product is None or not product.is_active:
                    raise OrderServiceError(
                        "PRODUCT_UNAVAILABLE",
                        "A product in your cart is no longer available",
                        status_code=409,
                    )
                if product.inventory_quantity < line.quantity:
                    raise OrderServiceError(
                        "INSUFFICIENT_STOCK",
                        f"Only {product.inventory_quantity} left of {product.title}",
                        status_code=409,
                    )
                line_total = product.offer_price_paise * line.quantity
                subtotal += line_total
                merchant_id = merchant_id or product.merchant_id
                items.append(
                    CustomerOrderItem(
                        order_id=order_id,
                        product_id=product.id,
                        merchant_id=product.merchant_id,
                        product_version=product.version,
                        sku=product.sku,
                        title=product.title,
                        brand=product.brand,
                        model=product.model,
                        category=product.category.value,
                        unit_amount_paise=product.offer_price_paise,
                        quantity=line.quantity,
                        line_total_paise=line_total,
                    )
                )

            total = subtotal + SHIPPING_PAISE
            if prepaid and total < MIN_PREPAID_TOTAL_PAISE:
                raise OrderServiceError(
                    "AMOUNT_TOO_SMALL",
                    "Online payment requires a total of at least ₹1",
                    status_code=422,
                )

            now = _now()
            order = CustomerOrder(
                id=order_id,
                order_number=_order_number(order_id),
                user_id=buyer.id,
                delivery_address_id=address.id,
                shipping_address=_address_snapshot(address),
                payment_method=payload.payment_method,
                payment_status=OrderPaymentStatus.PENDING.value,
                status=(
                    OrderStatus.PENDING_PAYMENT.value
                    if prepaid
                    else OrderStatus.CONFIRMED.value
                ),
                item_count=sum(line.quantity for line in payload.items),
                subtotal_paise=subtotal,
                shipping_paise=SHIPPING_PAISE,
                total_paise=total,
                currency="INR",
                receipt=f"cart_{order_id.hex}",
                placed_at=None if prepaid else now,
            )
            session.add(order)
            session.add_all(items)
            await session.flush()

            if not prepaid:
                # Cash on delivery is a firm order, so stock is taken now.
                await self._commit_inventory(session, order)

            await session.commit()

            snapshot = await self._response(session, order, message=None)

        if not prepaid:
            return PlaceOrderResponse(order=snapshot, razorpay=None)

        return await self._attach_razorpay_order(buyer=buyer, order_id=order_id)

    async def _attach_razorpay_order(
        self,
        *,
        buyer: User,
        order_id: UUID,
    ) -> PlaceOrderResponse:
        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_owned(
                order_id, user_id=buyer.id
            )
            if order is None:
                raise OrderServiceError("ORDER_NOT_FOUND", "Order not found", status_code=404)
            amount = order.total_paise
            receipt = order.receipt
            merchant_name = await self._merchant_name(session, order_id)
            existing_provider_order = order.provider_order_id

        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            if existing_provider_order is None:
                try:
                    provider_order = await gateway.create_order(
                        amount_paise=amount,
                        receipt=receipt,
                        notes={
                            "shopy_order_id": str(order_id),
                            "shopy_order_kind": "cart",
                        },
                    )
                except RazorpayRejectedError as exc:
                    await self._fail_order(
                        order_id, "Razorpay rejected this order. No payment was taken."
                    )
                    raise OrderServiceError(
                        "ORDER_REJECTED",
                        "Razorpay rejected this order. No payment was taken.",
                        status_code=502,
                    ) from exc
                except RazorpayAmbiguousWriteError as exc:
                    raise OrderServiceError(
                        "PROVIDER_UNCERTAIN",
                        "Razorpay did not confirm the order. Check your orders before retrying.",
                        status_code=503,
                    ) from exc
                except RazorpayGatewayError as exc:
                    raise OrderServiceError(
                        "PROVIDER_UNAVAILABLE",
                        "Razorpay is temporarily unavailable. Try again shortly.",
                        status_code=503,
                    ) from exc

                if (
                    provider_order.amount_paise != amount
                    or provider_order.currency != "INR"
                    or not 1 <= len(provider_order.order_id) <= 64
                ):
                    raise OrderServiceError(
                        "ORDER_MISMATCH",
                        "Razorpay returned an order that does not match. Do not pay.",
                        status_code=502,
                    )

                async with self._database.session() as session:
                    stored = await CustomerOrderRepository(session).get_owned(
                        order_id, user_id=buyer.id, for_update=True
                    )
                    if stored is None:
                        raise OrderServiceError(
                            "ORDER_NOT_FOUND", "Order not found", status_code=404
                        )
                    stored.provider_order_id = (
                        stored.provider_order_id or provider_order.order_id
                    )
                    await session.commit()
                    provider_order_id = stored.provider_order_id
            else:
                provider_order_id = existing_provider_order
        finally:
            await gateway.aclose()

        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_owned(
                order_id, user_id=buyer.id
            )
            if order is None:
                raise OrderServiceError("ORDER_NOT_FOUND", "Order not found", status_code=404)
            snapshot = await self._response(
                session,
                order,
                message="Complete payment in Razorpay Test Mode to confirm this order.",
            )
            contact = str(order.shipping_address.get("phone") or "")

        key_id, _ = self._settings.require_razorpay_api()
        return PlaceOrderResponse(
            order=snapshot,
            razorpay=RazorpayOrderHandoff(
                key_id=key_id,
                provider_order_id=provider_order_id,
                amount_paise=snapshot.total_paise,
                merchant_name=merchant_name,
                description=f"{snapshot.item_count} item(s) · {snapshot.order_number}",
                prefill_name=buyer.display_name,
                prefill_email=buyer.email,
                prefill_contact=contact,
            ),
        )

    # ------------------------------------------------------------- confirmation

    async def confirm_payment(
        self,
        *,
        buyer: User,
        order_id: UUID,
        callback: ConfirmOrderPaymentRequest,
    ) -> OrderResponse:
        if not self._settings.razorpay_api_configured:
            raise OrderServiceError(
                "RAZORPAY_NOT_CONFIGURED", "Online payment is not configured", status_code=503
            )

        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_owned(
                order_id, user_id=buyer.id
            )
            if order is None:
                raise OrderServiceError("ORDER_NOT_FOUND", "Order not found", status_code=404)
            if order.payment_method != PaymentMethod.RAZORPAY.value:
                raise OrderServiceError(
                    "NOT_PREPAID", "This order is not an online payment order", status_code=409
                )
            if order.provider_order_id != callback.razorpay_order_id:
                raise OrderServiceError(
                    "ORDER_MISMATCH",
                    "The payment does not belong to this order",
                    status_code=409,
                )
            already_paid = order.payment_status == OrderPaymentStatus.PAID.value

        if already_paid:
            return await self.get_order(buyer_user_id=buyer.id, order_id=order_id)

        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            if not gateway.verify_checkout_signature(
                order_id=callback.razorpay_order_id,
                payment_id=callback.razorpay_payment_id,
                signature=callback.razorpay_signature,
            ):
                raise OrderServiceError(
                    "INVALID_SIGNATURE",
                    "Payment signature verification failed",
                    status_code=400,
                )
            try:
                payment = await gateway.fetch_payment(
                    payment_id=callback.razorpay_payment_id
                )
            except RazorpayGatewayError as exc:
                raise OrderServiceError(
                    "PROVIDER_UNAVAILABLE",
                    "Payment could not be verified yet. Use refresh to check again.",
                    status_code=503,
                ) from exc
            await self._apply_payment(order_id, payment, signature_verified=True)
        finally:
            await gateway.aclose()

        return await self.get_order(buyer_user_id=buyer.id, order_id=order_id)

    async def reconcile(self, *, buyer: User, order_id: UUID) -> OrderResponse:
        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_owned(
                order_id, user_id=buyer.id
            )
            if order is None:
                raise OrderServiceError("ORDER_NOT_FOUND", "Order not found", status_code=404)
            provider_order_id = order.provider_order_id
            settled = order.payment_status != OrderPaymentStatus.PENDING.value

        if settled or provider_order_id is None:
            return await self.get_order(buyer_user_id=buyer.id, order_id=order_id)

        await self._settle_from_provider(provider_order_id)
        return await self.get_order(buyer_user_id=buyer.id, order_id=order_id)

    async def settle_provider_order(self, provider_order_id: str) -> bool:
        """Webhook entry point. Returns whether a cart order was matched."""
        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_by_provider_order_id(
                provider_order_id
            )
            if order is None:
                return False
        await self._settle_from_provider(provider_order_id)
        return True

    async def _settle_from_provider(self, provider_order_id: str) -> None:
        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            payments = await gateway.fetch_order_payments(order_id=provider_order_id)
        except RazorpayGatewayError:
            return
        finally:
            await gateway.aclose()

        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_by_provider_order_id(
                provider_order_id
            )
            if order is None:
                return
            order_id = order.id

        for payment in sorted(payments, key=lambda item: item.created_at_epoch or 0):
            await self._apply_payment(order_id, payment, signature_verified=False)

    async def _apply_payment(
        self,
        order_id: UUID,
        payment: ProviderPayment,
        *,
        signature_verified: bool,
    ) -> None:
        normalized = payment.status.strip().lower()
        captured = payment.captured or normalized == "captured"
        failed = normalized == "failed"

        async with self._database.session() as session:
            locked = await session.execute(
                select(CustomerOrder).where(CustomerOrder.id == order_id).with_for_update()
            )
            order = locked.scalar_one_or_none()
            if order is None or order.payment_status == OrderPaymentStatus.PAID.value:
                return

            # Never trust the webhook or callback body over these invariants.
            if (
                payment.order_id != order.provider_order_id
                or payment.amount_paise != order.total_paise
                or payment.currency != order.currency
            ):
                order.failure_reason = (
                    "Razorpay payment facts did not match this order. It needs manual review."
                )
                await session.commit()
                return

            if signature_verified:
                order.provider_signature_verified = True

            now = _now()
            if captured:
                order.provider_payment_id = order.provider_payment_id or payment.payment_id
                order.payment_status = OrderPaymentStatus.PAID.value
                order.status = OrderStatus.CONFIRMED.value
                order.paid_at = order.paid_at or now
                order.placed_at = order.placed_at or now
                order.failure_reason = None
                await self._commit_inventory(session, order, allow_short=True)
            elif failed:
                order.payment_status = OrderPaymentStatus.FAILED.value
                order.status = OrderStatus.PAYMENT_FAILED.value
                order.failure_reason = (
                    payment.error_description or "Razorpay reported that the payment failed."
                )
            else:
                # Authorized but not yet captured: leave the order awaiting payment.
                order.provider_payment_id = order.provider_payment_id or payment.payment_id

            await session.commit()

    async def _commit_inventory(
        self,
        session: AsyncSession,
        order: CustomerOrder,
        *,
        allow_short: bool = False,
    ) -> None:
        """Decrement stock once per order.

        ``allow_short`` is used after money has already been captured: the order
        stays confirmed and is flagged for manual fulfilment rather than silently
        driving inventory negative.
        """
        if order.inventory_committed:
            return
        products = ProductRepository(session)
        shortfalls: list[str] = []
        for item in await CustomerOrderRepository(session).list_items(order.id):
            product = await products.get_for_checkout(item.product_id)
            if product is None or product.inventory_quantity < item.quantity:
                if not allow_short:
                    raise OrderServiceError(
                        "INSUFFICIENT_STOCK",
                        f"Only limited stock left of {item.title}",
                        status_code=409,
                    )
                shortfalls.append(item.title)
                continue
            product.inventory_quantity -= item.quantity
        order.inventory_committed = True
        if shortfalls:
            order.failure_reason = (
                "Payment captured. These items need manual fulfilment: "
                + ", ".join(shortfalls)
            )
        await session.flush()

    async def _fail_order(self, order_id: UUID, reason: str) -> None:
        async with self._database.session() as session:
            order = await session.get(CustomerOrder, order_id)
            if order is None:
                return
            if order.payment_status == OrderPaymentStatus.PAID.value:
                return
            order.status = OrderStatus.PAYMENT_FAILED.value
            order.failure_reason = reason
            await session.commit()

    # ----------------------------------------------------------------- querying

    async def get_order(self, *, buyer_user_id: UUID, order_id: UUID) -> OrderResponse:
        async with self._database.session() as session:
            order = await CustomerOrderRepository(session).get_owned(
                order_id, user_id=buyer_user_id
            )
            if order is None:
                raise OrderServiceError("ORDER_NOT_FOUND", "Order not found", status_code=404)
            return await self._response(session, order, message=None)

    async def list_orders(self, *, buyer_user_id: UUID) -> list[OrderResponse]:
        async with self._database.session() as session:
            repository = CustomerOrderRepository(session)
            orders = list(await repository.list_for_user(buyer_user_id))
            grouped = await repository.items_for_orders([order.id for order in orders])
            return [
                self._build_response(order, grouped.get(order.id, []), message=None)
                for order in orders
            ]

    async def _merchant_name(self, session: AsyncSession, order_id: UUID) -> str:
        items = await CustomerOrderRepository(session).list_items(order_id)
        if not items:
            return "Shopy"
        merchant = await session.get(Merchant, items[0].merchant_id)
        return merchant.name if merchant is not None else "Shopy"

    async def _response(
        self,
        session: AsyncSession,
        order: CustomerOrder,
        *,
        message: str | None,
    ) -> OrderResponse:
        items = await CustomerOrderRepository(session).list_items(order.id)
        return self._build_response(order, list(items), message=message)

    def _build_response(
        self,
        order: CustomerOrder,
        items: list[CustomerOrderItem],
        *,
        message: str | None,
    ) -> OrderResponse:
        return OrderResponse(
            id=order.id,
            order_number=order.order_number,
            status=order.status,
            payment_method=order.payment_method,
            payment_status=order.payment_status,
            item_count=order.item_count,
            subtotal_paise=order.subtotal_paise,
            shipping_paise=order.shipping_paise,
            total_paise=order.total_paise,
            shipping_address=ShippingAddressSnapshot.model_validate(order.shipping_address),
            items=[OrderItemResponse.model_validate(item) for item in items],
            placed_at=order.placed_at,
            paid_at=order.paid_at,
            failure_reason=order.failure_reason,
            created_at=order.created_at,
            message=message or _status_message(order),
        )


def _status_message(order: CustomerOrder) -> str:
    if order.status == OrderStatus.CONFIRMED.value:
        if order.payment_method == PaymentMethod.COD.value:
            return "Order confirmed. Pay cash when it is delivered."
        return "Payment received. Your order is confirmed."
    if order.status == OrderStatus.PENDING_PAYMENT.value:
        return "Waiting for payment to complete."
    if order.status == OrderStatus.PAYMENT_FAILED.value:
        return order.failure_reason or "The payment did not go through."
    return "This order was cancelled."
