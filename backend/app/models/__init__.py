"""Import all ORM models so metadata is complete for Alembic."""

from backend.app.models.account import AuthSession, ShoppingAgentControls
from backend.app.models.base import Base
from backend.app.models.commerce import (
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
from backend.app.models.merchant import Merchant
from backend.app.models.product import Product, ProductCategory
from backend.app.models.purchase_run import PurchaseRun
from backend.app.models.user import User, UserRole

__all__ = [
    "AuditEntry",
    "AuthSession",
    "Base",
    "Merchant",
    "PaymentAttempt",
    "PaymentStatus",
    "Product",
    "ProductCategory",
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
