"""Run non-destructive foundation acceptance checks against configured Neon."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from app.domain.money import MoneyPaise
from app.domain.purchase_state import (
    InvalidPurchaseTransition,
    PurchaseState,
    ensure_transition,
)
from app.main import create_app

EXPECTED_TABLES = {
    "alembic_version",
    "auth_sessions",
    "merchants",
    "products",
    "purchase_runs",
    "shopping_agent_controls",
    "users",
}
EXPECTED_REVISION = "20260903_0005"


def validate_configuration() -> None:
    settings = get_settings()
    password = make_url(settings.database_url.get_secret_value()).password
    if password is not None:
        assert password not in repr(settings)

    try:
        Settings(
            database_url=settings.database_url,
            razorpay_key_id=SecretStr("rzp_live_forbidden"),
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Razorpay live key was accepted")

    missing_openrouter = Settings(
        database_url=settings.database_url,
        migration_database_url=settings.migration_database_url,
        openrouter_api_key=None,
        openrouter_model=None,
    )
    try:
        missing_openrouter.require_openrouter()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Missing OpenRouter configuration was accepted")


def validate_domain_contracts() -> None:
    assert int(MoneyPaise(89_900)) == 89_900

    for invalid in (True, 899.0, "89900", -1):
        try:
            MoneyPaise(invalid)  # type: ignore[arg-type]
        except (TypeError, ValidationError, ValueError):
            pass
        else:
            raise AssertionError(f"Invalid money value was accepted: {type(invalid).__name__}")

    assert (
        ensure_transition(
            PurchaseState.RECEIVED,
            PurchaseState.INTENT_PARSED,
            provider_write_started=False,
        )
        is PurchaseState.INTENT_PARSED
    )
    try:
        ensure_transition(
            PurchaseState.PAYMENT_UNKNOWN,
            PurchaseState.SEARCHING,
            provider_write_started=True,
        )
    except InvalidPurchaseTransition:
        pass
    else:
        raise AssertionError("Candidate replanning resumed after a provider write")


def validate_schema_and_restart_persistence() -> None:
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_migration_url)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert EXPECTED_TABLES.issubset(tables)
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == EXPECTED_REVISION
    engine.dispose()

    user_id = uuid4()
    merchant_id = uuid4()
    run_id = uuid4()
    suffix = uuid4().hex
    idempotency_key = f"foundation-validation-{suffix}"
    inserted = False

    try:
        first_engine = create_engine(settings.sqlalchemy_migration_url)
        with first_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, display_name, role)
                    VALUES (:id, :email, :display_name, 'buyer')
                    """
                ),
                {
                    "id": user_id,
                    "email": f"foundation-{suffix}@example.invalid",
                    "display_name": "Foundation Validation",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO merchants (id, owner_user_id, name, slug)
                    VALUES (:id, :owner_user_id, :name, :slug)
                    """
                ),
                {
                    "id": merchant_id,
                    "owner_user_id": user_id,
                    "name": "Foundation Validation Merchant",
                    "slug": f"foundation-{suffix}",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO purchase_runs (
                        id, buyer_user_id, merchant_id, idempotency_key, command
                    )
                    VALUES (:id, :buyer_user_id, :merchant_id, :idempotency_key, :command)
                    """
                ),
                {
                    "id": run_id,
                    "buyer_user_id": user_id,
                    "merchant_id": merchant_id,
                    "idempotency_key": idempotency_key,
                    "command": "foundation persistence validation",
                },
            )
        first_engine.dispose()
        inserted = True

        restarted_engine = create_engine(settings.sqlalchemy_migration_url)
        with restarted_engine.connect() as connection:
            persisted_state = connection.scalar(
                text("SELECT state FROM purchase_runs WHERE id = :id"), {"id": run_id}
            )
            assert persisted_state == PurchaseState.RECEIVED.value
        restarted_engine.dispose()
    finally:
        if inserted:
            cleanup_engine = create_engine(settings.sqlalchemy_migration_url)
            with cleanup_engine.begin() as connection:
                connection.execute(text("DELETE FROM purchase_runs WHERE id = :id"), {"id": run_id})
                connection.execute(
                    text("DELETE FROM merchants WHERE id = :id"), {"id": merchant_id}
                )
                connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            cleanup_engine.dispose()


async def validate_api() -> None:
    application = create_app(get_settings())
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://validation.local"
        ) as client:
            response = await client.get("/health")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"
            assert body["database"] == "ready"
            expected_openrouter = (
                "configured" if get_settings().openrouter_configured else "not_configured"
            )
            expected_razorpay = (
                "configured" if get_settings().razorpay_configured else "not_configured"
            )
            assert body["openrouter"]["status"] == expected_openrouter
            assert body["razorpay"]["status"] == expected_razorpay

            razorpay = await client.get("/health/razorpay")
            assert razorpay.status_code == 200
            assert razorpay.json()["status"] == expected_razorpay
            assert razorpay.json()["mode"] == "test"

            catalogue = await client.get("/api/catalog", params={"limit": 100})
            assert catalogue.status_code == 200
            catalogue_body = catalogue.json()
            assert catalogue_body["total"] == 100
            assert len(catalogue_body["items"]) == 100
            assert catalogue_body["category_counts"] == {
                "smartphones": 20,
                "speakers": 20,
                "headphones": 20,
                "laptops": 20,
                "tablets": 20,
            }
            assert all(
                item["source_url"].startswith("https://")
                for item in catalogue_body["items"]
            )

            agent = await client.post(
                "/api/agent/chat",
                json={
                    "message": "Find wireless headphones under ₹100,000",
                    "limit": 4,
                },
            )
            assert agent.status_code == 200
            agent_body = agent.json()
            assert agent_body["agent_name"] == "Shopy Agent"
            assert agent_body["catalogue_backed"] is True
            assert agent_body["checkout_available"] is False
            assert agent_body["intent"]["category"] == "headphones"
            assert agent_body["intent"]["max_price_paise"] == 10_000_000
            assert agent_body["recommendations"]
            assert all(
                recommendation["product"]["category"] == "headphones"
                and recommendation["product"]["in_stock"] is True
                and recommendation["product"]["offer_price_paise"] <= 10_000_000
                and recommendation["reasons"]
                for recommendation in agent_body["recommendations"]
            )

            validation_email = f"account-validation-{uuid4().hex}@example.invalid"
            try:
                signup = await client.post(
                    "/api/auth/signup",
                    json={
                        "email": validation_email,
                        "display_name": "Account Validation",
                        "password": "Validation47291",
                    },
                )
                assert signup.status_code == 201
                assert signup.json()["profile"]["email"] == validation_email
                assert "shopy_session" in client.cookies
                csrf_token = client.cookies.get("shopy_csrf")
                assert csrf_token
                csrf_headers = {"X-CSRF-Token": csrf_token}

                profile = await client.get("/api/account/profile")
                assert profile.status_code == 200
                assert profile.json()["display_name"] == "Account Validation"

                controls = await client.put(
                    "/api/account/agent-controls",
                    headers=csrf_headers,
                    json={
                        "agent_enabled": True,
                        "recommendation_price_ceiling_paise": 5_000_000,
                        "per_purchase_limit_paise": 6_000_000,
                        "daily_spend_limit_paise": 10_000_000,
                        "monthly_spend_limit_paise": 50_000_000,
                        "approval_required_above_paise": 3_000_000,
                        "category_allowlist": ["headphones"],
                        "max_recommendations": 2,
                        "max_replans": 3,
                        "allow_substitutions": True,
                    },
                )
                assert controls.status_code == 200
                assert controls.json()["purchase_authority"] == "not_active"

                controlled_agent = await client.post(
                    "/api/agent/chat",
                    json={"message": "Find headphones under ₹100,000", "limit": 8},
                )
                assert controlled_agent.status_code == 200
                controlled_body = controlled_agent.json()
                assert controlled_body["account_controls_applied"] is True
                assert controlled_body["intent"]["max_price_paise"] == 5_000_000
                assert 1 <= len(controlled_body["recommendations"]) <= 2
                assert all(
                    item["product"]["offer_price_paise"] <= 5_000_000
                    for item in controlled_body["recommendations"]
                )

                orders = await client.get("/api/account/orders")
                transactions = await client.get("/api/account/transactions")
                assert orders.status_code == 200
                assert transactions.status_code == 200
                assert orders.json()["availability"] == "unavailable"
                assert transactions.json()["availability"] == "unavailable"
                assert orders.json()["items"] == []
                assert transactions.json()["items"] == []

                logout = await client.post("/api/auth/logout", headers=csrf_headers)
                assert logout.status_code == 200
                assert (await client.get("/api/account/profile")).status_code == 401
            finally:
                cleanup_engine = create_engine(get_settings().sqlalchemy_migration_url)
                with cleanup_engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM users WHERE email = :email"),
                        {"email": validation_email},
                    )
                cleanup_engine.dispose()


def main() -> None:
    validate_configuration()
    validate_domain_contracts()
    validate_schema_and_restart_persistence()
    asyncio.run(validate_api())
    print("Foundation validation passed: config, domain, schema, persistence, and API")


if __name__ == "__main__":
    main()
