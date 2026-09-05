"""Import all ORM models so metadata is complete for Alembic."""

from app.models.account import AuthSession, ShoppingAgentControls
from app.models.agent_order import AgentFulfillmentOrder, AgentFulfillmentStatus
from app.models.base import Base
from app.models.commerce import (
    AuditEntry,
    PaymentAttempt,
    PaymentStatus,
    ProviderOrderOperationState,
    PurchaseQuote,
    PurchaseReservation,
    RazorpayOrder,
    ReservationStatus,
    WebhookEvent,
    WebhookProcessingStatus,
)
from app.models.conversation import (
    AgentConversation,
    AgentConversationStatus,
    AgentConversationTurn,
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
from app.models.product import CatalogCategory, CatalogCategoryRelation, Product
from app.models.purchase_run import PurchaseRun
from app.models.user import User, UserRole

__all__ = [
    "AgentConversation",
    "AgentConversationStatus",
    "AgentConversationTurn",
    "AgentFulfillmentOrder",
    "AgentFulfillmentStatus",
    "AuditEntry",
    "AuthSession",
    "Base",
    "CatalogCategory",
    "CatalogCategoryRelation",
    "CustomerOrder",
    "CustomerOrderItem",
    "DeliveryAddress",
    "Merchant",
    "OrderPaymentStatus",
    "OrderStatus",
    "PaymentAttempt",
    "PaymentMethod",
    "PaymentStatus",
    "Product",
    "ProviderOrderOperationState",
    "PurchaseQuote",
    "PurchaseReservation",
    "PurchaseRun",
    "RazorpayOrder",
    "ReservationStatus",
    "ShoppingAgentControls",
    "User",
    "UserRole",
    "WebhookEvent",
    "WebhookProcessingStatus",
]
